"""Plan 13 H4: the Studies page's whole-assembly routes: kinds (the proposal), start a study as
a "studies" run through the Gate (refused before a run exists for bad settings), the build's
records, one record, and the FEM hand-off's refusals."""
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mises.server.app import create_app

from .conftest import AUTH, TOKEN

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dyn import quad  # noqa: E402


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def build(rt):
    pid = rt.store.create_project("robot dog").id
    quad.write_parts(rt.home)
    quad.assembly(pid, rt.assembly_store)
    return pid


def _wait_done(client, pid, n=1, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rows = client.get(f"/assembly_studies?project_id={pid}", headers=AUTH).json()
        if len(rows) >= n and all(r["status"] != "running" for r in rows):
            return rows
        time.sleep(0.1)
    raise AssertionError(f"still running: {rows}")


def test_kinds_recommend_walk_for_a_legged_build(client, build):
    r = client.get(f"/assembly_studies/kinds?project_id={build}", headers=AUTH)
    assert r.status_code == 200, r.text
    rows = r.json()["kinds"]
    assert rows[0]["kind"] == "walk" and rows[0]["recommended"] and rows[0]["schema"]
    assert {x["kind"] for x in rows} == {"walk", "launch", "drop", "mechanism"}
    # the studies agent's context carries the same proposal
    ctx = client.get(f"/studies/context?project_id={build}", headers=AUTH).json()
    assert ctx["assembly_proposals"][0]["kind"] == "walk"


def test_drop_study_runs_as_a_studies_run(client, rt, build):
    bad = client.post("/assembly_studies", headers=AUTH,
                      json={"project_id": build, "kind": "drop", "settings": {"drop_m": -1}})
    assert bad.status_code == 400 and "bad drop settings" in bad.json()["message"]
    r = client.post("/assembly_studies", headers=AUTH,
                    json={"project_id": build, "kind": "drop",
                          "settings": {"drop_m": 1.0, "max_g": 50}})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    rows = _wait_done(client, build)
    assert rows[0]["status"] == "done", (rows[0]["error"], rows[0]["hint"])
    assert rows[0]["verdict"] == "pass"
    assert rows[0]["run_id"] == run_id
    rec = client.get(f"/assembly_studies/{rows[0]['study_id']}", headers=AUTH).json()
    assert rec["playback"]["steps"][0]["joint_id"] == "@root"
    assert rec["fem"]["available"] and rec["fem"]["via"] == "drop"
    assert rt.store.get_run(run_id).agent == "studies"
    # the hand-off refuses an unknown study and a launch-style study with none
    assert client.post("/assembly_studies/as_000000000000/fem", headers=AUTH,
                       json={}).status_code == 404
    # its job id is a job id the Stop button may send (B3's POST /jobs/{id}/cancel)
    r = client.post(f"/jobs/{rows[0]['study_id']}/cancel", headers=AUTH)
    assert r.status_code in (404, 409), r.text
    assert client.post("/jobs/as_000000000000_fem/cancel", headers=AUTH).status_code == 404


def test_unknown_build_and_empty_assembly(client, rt):
    assert client.get("/assembly_studies?project_id=nope", headers=AUTH).status_code == 404
    pid = rt.store.create_project("empty").id
    r = client.post("/assembly_studies", headers=AUTH, json={"project_id": pid, "kind": "drop"})
    assert r.status_code == 404 and "no assembly" in r.json()["message"]


def test_an_older_walk_record_gets_its_named_joint_table(client, rt, build):
    """A record saved before the joint table: GET fills it from the run's saved dyn_torque
    result and names each joint by the part it moves (the legend too)."""
    import json as _json
    from mises.sim import system_study as ss
    rid = "dyn_0123456789ab"
    d = Path(rt.home) / "runs" / "r_old" / "dyn" / build
    d.mkdir(parents=True)
    (d / f"{rid}.json").write_text(_json.dumps({"id": rid, "card": {"rows": [
        {"joint_id": "fl_knee", "peak_nm": 2.07, "use_of_stall": 0.85, "actuator": "s@1.24",
         "status": "fail"}]}}))
    rec = {"study_id": "as_0123456789ab", "project_id": build, "kind": "walk", "name": "Walk",
           "status": "done", "verdict": "fail", "run_id": "r_old", "created_at": 1.0,
           "sources": [{"tool": "dyn_torque", "result_id": rid}], "checks": [], "metrics": [],
           "charts": [{"id": "c", "kind": "line", "title": "Joint torque",
                       "series": [{"name": "fl_knee", "points": [[0, 1]]}]}],
           "notes": [], "accuracy": []}
    ss.save(rt.home, rec)
    out = client.get("/assembly_studies/as_0123456789ab", headers=AUTH).json()
    assert out["joints"] == [{"joint_id": "fl_knee", "label": "fl shank", "actuator": "s@1.24",
                              "peak_nm": 2.07, "use_of_stall": 0.85, "status": "fail"}]
    assert out["charts"][0]["series"][0]["name"] == "fl shank"
