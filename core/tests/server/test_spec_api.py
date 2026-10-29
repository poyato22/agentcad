"""GET/PUT /projects/{id}/spec and GET /projects/{id}/studies (admin token only)."""
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import mises.engineering.tools as eng_tools
from mises.server.app import create_app
from mises.types import ToolOutput

from .conftest import AUTH, TOKEN

PID = "proj_default"

SPEC = {
    "goal": "5 inch drone frame",
    "requirements": [
        {"id": "mass_budget", "kind": "max_mass_g", "target": 120, "priority": 5,
         "source": "user"},
        {"id": "arm_safety", "kind": "min_safety_factor", "target": 2.0,
         "load_case_id": "crash_drop", "priority": 4},
    ],
    "load_cases": [
        {"id": "crash_drop", "name": "crash", "fix": "bottom", "load": "top",
         "newtons": 120, "direction": "-z", "estimate": True},
    ],
    "answers": {"use": "freestyle"},
}


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def test_spec_routes_need_token(client):
    assert client.get(f"/projects/{PID}/spec").status_code == 401
    assert client.put(f"/projects/{PID}/spec", json=SPEC).status_code == 401
    assert client.get(f"/projects/{PID}/studies").status_code == 401


def test_empty_spec(client):
    r = client.get(f"/projects/{PID}/spec", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["spec"]["requirements"] == []


def test_put_and_get_spec(client):
    r = client.put(f"/projects/{PID}/spec", headers=AUTH, json=SPEC)
    assert r.status_code == 200, r.text
    assert len(r.json()["spec"]["requirements"]) == 2
    got = client.get(f"/projects/{PID}/spec", headers=AUTH).json()
    assert len(got["spec"]["load_cases"]) == 1 and got["spec"]["load_cases"][0]["estimate"]


def test_put_invalid_spec_is_422(client):
    bad = {**SPEC, "load_cases": []}  # arm_safety references a missing load case
    r = client.put(f"/projects/{PID}/spec", headers=AUTH, json=bad)
    assert r.status_code == 422
    assert "invalid spec" in r.json()["message"]


def test_studies_empty(client):
    assert client.get(f"/projects/{PID}/studies", headers=AUTH).json() == []


# ---- the spec panel's tool routes (Plan 5 A7): spec_check and study_apply through the Gate

SID = "study_0123456789ab"


def _part(rt, part_id="frame_x", mass=80.0):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "vars.json").write_text(json.dumps({"wall": 3.0}))
    (d / "metrics.json").write_text(json.dumps({"mass_g": mass, "bbox": {"size": [10, 10, 10]}}))
    return d


def _study(rt, pid=PID, status="done"):
    its = [{"n": 0, "vars": {"wall": 2.0}, "metrics": {"mass_g": 60.0}, "feasible": True,
            "pareto": True},
           {"n": 1, "vars": {"wall": 1.0}, "metrics": {"mass_g": 40.0}, "feasible": False,
            "pareto": False}]
    rt.store.create_study(SID, pid, {"id": SID, "project_id": pid, "part_id": "frame_x",
                                     "run_id": None, "created_at": 1.0, "status": status,
                                     "variables": [{"name": "wall", "lo": 1, "hi": 4}],
                                     "objective": "min_mass", "iterations": its, "best_id": 0},
                          status)


@pytest.fixture
def applied(monkeypatch):
    calls = []

    async def fake_set_vars(ctx, args):
        calls.append((ctx.run_id, args.part_id, dict(args.vars)))
        return ToolOutput(summary=f"set {args.part_id}", data={"part_id": args.part_id})
    monkeypatch.setattr(eng_tools, "mirror_part_set_vars", SimpleNamespace(handler=fake_set_vars))
    return calls


def test_apply_variant_runs_study_apply_through_the_gate(rt, client, applied):
    _part(rt)
    _study(rt)
    r = client.post(f"/projects/{PID}/studies/{SID}/apply", headers=AUTH, json={"iteration": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["study_id"] == SID and body["iteration"] == 0 and body["part_id"] == "frame_x"
    assert body["summary"].startswith(f"applied {SID} #0")
    assert [c[1:] for c in applied] == [("frame_x", {"wall": 2.0})]
    run = rt.store.get_run(applied[0][0])   # its own finished "ui" run, recorded as Steps
    assert run.provider == "ui" and run.status == "done"
    names = [s.payload.get("name") for s in rt.store.steps(run.id) if s.kind == "tool_call"]
    assert names == ["study_apply"]


def test_apply_refuses_infeasible_unknown_and_other_builds(rt, client, applied):
    _part(rt)
    _study(rt)
    r = client.post(f"/projects/{PID}/studies/{SID}/apply", headers=AUTH, json={"iteration": 1})
    assert r.status_code == 400 and "infeasible" in r.json()["message"]
    r = client.post(f"/projects/{PID}/studies/{SID}/apply", headers=AUTH, json={"iteration": 7})
    assert r.status_code == 404
    r = client.post(f"/projects/other/studies/{SID}/apply", headers=AUTH, json={"iteration": 0})
    assert r.status_code == 404
    r = client.post(f"/projects/{PID}/studies/bogus/apply", headers=AUTH, json={"iteration": 0})
    assert r.status_code == 400
    assert applied == []


def test_apply_stale_version_is_409(rt, client, applied):
    d = _part(rt)
    (d / "version.json").write_text(json.dumps({"version": 5}))
    _study(rt)
    r = client.post(f"/projects/{PID}/studies/{SID}/apply", headers=AUTH,
                    json={"iteration": 0, "base_version": 4})
    assert r.status_code == 409 and r.json()["error"] == "stale_version"
    assert applied == []


def test_tool_routes_need_token(client):
    r = client.post(f"/projects/{PID}/studies/{SID}/apply", json={"iteration": 0})
    assert r.status_code == 401
    assert client.get(f"/projects/{PID}/spec/check?part_id=frame_x").status_code == 401


def test_spec_check_measures_the_part(rt, client):
    _part(rt, mass=130.0)
    client.put(f"/projects/{PID}/spec", headers=AUTH, json=SPEC)
    r = client.get(f"/projects/{PID}/spec/check?part_id=frame_x", headers=AUTH)
    assert r.status_code == 200, r.text
    rows = {x["id"]: x for x in r.json()["requirements"]}
    assert rows["mass_budget"]["measured"] == 130.0 and rows["mass_budget"]["status"] == "fail"
    assert rows["arm_safety"]["status"] == "not_checked"
    assert r.json()["overall"] == "fail" and r.json()["part_id"] == "frame_x"


def test_spec_check_unknown_part(client):
    r = client.get(f"/projects/{PID}/spec/check?part_id=ghost_part", headers=AUTH)
    assert r.status_code == 404
    r = client.get(f"/projects/{PID}/spec/check?part_id=Bad!", headers=AUTH)
    assert r.status_code == 400
