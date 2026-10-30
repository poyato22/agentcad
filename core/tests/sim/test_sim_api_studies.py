"""Studies routes added for full study control (types, validation, templates, materials,
compare, probe / section / charts, cancel), agent-run studies and design_review through the
Gate."""
import functools
import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.sim.ccx import find_ccx
from mises.sim.fields import save_fem
from mises.types import SafetyClass, StepKind

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
CUBE = ('from build123d import *\nVARS={"s":40.0}\ndef build(v):\n'
        '    return Box(v["s"],v["s"],v["s"])\n')
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")


@pytest.fixture
def rt(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    return Runtime(h, provider_resolver=lambda ref: (fake, "fake-model"))


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _record(rt, sim_id, type_="static", part_id="beam", **results):
    d = rt.home / "parts" / part_id / "sims" / sim_id
    d.mkdir(parents=True)
    rec = {"sim_id": sim_id, "part_id": part_id, "type": type_, "status": "done",
           "created_at": time.time(), "run_id": "r1", "geometry_hash": "x",
           "fidelity_class": "fem", "label": "guide only, not a guarantee",
           "results": results, "warnings": []}
    (d / "result.json").write_text(json.dumps(rec))
    return d


def test_new_routes_need_admin_token(client):
    for path in ["/sims/types", "/sims/templates", "/sims/materials",
                 "/sims/sim_0123456789ab/charts", "/sims/sim_0123456789ab/probe?x=0&y=0&z=0"]:
        assert client.get(path).status_code == 401
    assert client.post("/sims/compare", json={"ids": []}).status_code == 401
    assert client.post("/sims/sim_0123456789ab/cancel").status_code == 401


def test_types_route_has_schemas(client, monkeypatch):
    monkeypatch.setattr("mises.sim.cfd.image_status", lambda d: "absent")  # no real docker
    ts = client.get("/sims/types", headers=AUTH).json()
    assert [t["id"] for t in ts] == ["static", "modal", "buckling", "thermal", "drop", "wind"]
    assert all("properties" in t["schema"] for t in ts)
    s = client.get("/sims/solvers", headers=AUTH).json()
    assert {r["id"] for r in s["cfd"]} == {"cfd_builtin", "su2", "openfoam_container", "openfoam"}


def test_post_rejects_bad_settings_before_creating_a_run(client, rt):
    before = len(client.get("/runs", headers=AUTH).json())
    r = client.post("/sims", headers=AUTH, json={"type": "modal", "part_id": "beam",
                                                 "settings": {"n_modes": 500}})
    assert r.status_code == 400 and "n_modes" in r.json()["message"]
    assert r.json()["errors"][0]["loc"] == ["n_modes"]
    r = client.post("/sims", headers=AUTH, json={"type": "warp", "part_id": "beam"})
    assert r.status_code == 400
    r = client.post("/sims", headers=AUTH, json={"type": "wind", "project_id": "p1"})
    assert r.status_code == 400 and "not supported" in r.json()["message"]
    r = client.post("/sims", headers=AUTH, json={"type": "wind", "part_id": "nope"})
    assert r.status_code == 404
    assert len(client.get("/runs", headers=AUTH).json()) == before


def test_templates_crud(client):
    assert client.get("/sims/templates", headers=AUTH).json() == []
    body = {"name": "Desk load", "type": "static", "settings": {"load_cases": [CASE]}}
    r = client.post("/sims/templates", headers=AUTH, json=body)
    assert r.status_code == 201
    tid = r.json()["id"]
    assert client.post("/sims/templates", headers=AUTH, json=body).status_code == 409
    bad = {"name": "x", "type": "modal", "settings": {"n_modes": 0}}
    assert client.post("/sims/templates", headers=AUTH, json=bad).status_code == 400
    assert [t["id"] for t in client.get("/sims/templates", headers=AUTH).json()] == [tid]
    assert client.delete(f"/sims/templates/{tid}", headers=AUTH).status_code == 200
    assert client.delete(f"/sims/templates/{tid}", headers=AUTH).status_code == 404
    assert client.delete("/sims/templates/..%2Fx", headers=AUTH).status_code in (400, 404)


def test_custom_materials(client, rt):
    m = {"name": "Spool 7", "e_mpa": 2000, "nu": 0.36, "yield_mpa": 40, "density_g_cm3": 1.2,
         "k_w_mk": 0.15}
    r = client.post("/sims/materials", headers=AUTH, json=m)
    assert r.status_code == 201 and r.json()["custom"] is True
    got = client.get("/sims/materials", headers=AUTH).json()
    assert [c["name"] for c in got["custom"]] == ["Spool 7"]
    assert {b["name"] for b in got["builtin"]} >= {"PLA", "PETG"}
    assert client.post("/sims/materials", headers=AUTH, json={**m, "name": "pla"}).status_code \
        == 400
    assert client.post("/sims/materials", headers=AUTH, json={**m, "nu": 0.7}).status_code \
        in (400, 422)
    assert (rt.home / "sim_materials.json").is_file()
    assert client.delete("/sims/materials/Spool 7", headers=AUTH).status_code == 200
    assert client.delete("/sims/materials/Spool 7", headers=AUTH).status_code == 404


def test_compare_route(client, rt):
    _record(rt, "sim_00000000000a", max_von_mises_mpa=6.0, safety_factor=5.0)
    _record(rt, "sim_00000000000b", max_von_mises_mpa=3.0, safety_factor=9.0)
    t = client.post("/sims/compare", headers=AUTH,
                    json={"ids": ["sim_00000000000a", "sim_00000000000b"]}).json()
    rows = {r["key"]: r for r in t["rows"]}
    assert rows["max_von_mises_mpa"]["values"] == [6.0, 3.0]
    assert rows["max_von_mises_mpa"]["best"] == 1
    r = client.post("/sims/compare", headers=AUTH,
                    json={"ids": ["sim_00000000000a", "sim_999999999999"]})
    assert r.status_code == 404


def test_probe_section_charts_and_cancel_routes(client, rt):
    d = _record(rt, "sim_00000000000c", allowable_mpa=35.0,
                convergence={"history": [{"nodes": 10, "metric": 5.0},
                                         {"nodes": 80, "metric": 6.0}]})
    coords = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10.0]])
    save_fem(d / "fields.npz", coords, np.array([[0, 1, 2, 3]]),
             {"von_mises": np.array([0.0, 10.0, 20.0, 30.0])}, {"von_mises": "MPa"})
    p = client.get("/sims/sim_00000000000c/probe", headers=AUTH,
                   params={"x": 2, "y": 1, "z": 1}).json()
    assert p["inside"] is True and p["values"]["von_mises"] == pytest.approx(
        10 * 0.2 + 20 * 0.1 + 30 * 0.1)
    assert p["units"]["von_mises"] == "MPa"
    s = client.get("/sims/sim_00000000000c/section", headers=AUTH,
                   params={"axis": "z", "offset": 1, "channel": "von_mises", "n": 6})
    assert s.status_code == 200 and len(s.json()["values"]) == 6
    assert client.get("/sims/sim_00000000000c/section", headers=AUTH, params={
        "axis": "z", "offset": 1, "channel": "nope"}).status_code == 400
    c = client.get("/sims/sim_00000000000c/charts", headers=AUTH).json()
    assert {x["id"] for x in c["charts"]} == {"convergence", "von_mises_histogram"}
    r = client.post("/sims/sim_00000000000c/cancel", headers=AUTH)
    assert r.status_code == 409 and r.json()["error"] == "not_running"
    assert client.get("/sims/sim_00000000000c/slices/evil.png", headers=AUTH).status_code == 404
    assert client.get("/sims/sim_00000000000c/slices/slice_xz.png",
                      headers=AUTH).status_code == 404
    (d / "slice_xz.png").write_bytes(b"\x89PNG\r\n\x1a\nxx")
    g = client.get("/sims/sim_00000000000c/slices/slice_xz.png", headers=AUTH)
    assert g.status_code == 200 and g.headers["content-type"] == "image/png"
    rec = client.get("/sims/sim_00000000000c", headers=AUTH).json()
    assert rec["card"]["urls"]["charts"] == "/sims/sim_00000000000c/charts"


def _build(client, rt, pid, script):
    rid = rt.create_external_run("tester", provider="x", model="y")
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "mirror_part_create", {"part_id": pid, "script": script},
        call_id=f"b-{pid}", workdir=rt.workdir(rid)))
    assert out.ok, out.summary


def _wait(client, part_id, run_id, timeout=240.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sims = client.get("/sims", headers=AUTH, params={"part_id": part_id}).json()
        mine = [s for s in sims if s["run_id"] == run_id and s["status"] != "running"]
        if mine:
            return mine[0]
        time.sleep(0.2)
    raise AssertionError("sim never finished")


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_ui_wind_study_through_the_gate(client, rt):
    _build(client, rt, "cube", CUBE)
    r = client.post("/sims", headers=AUTH, json={
        "type": "wind", "part_id": "cube", "reason": "drag of the cube",
        "settings": {"speed_m_s": 5, "refinement": 1, "domain_multiplier": 3,
                     "iterations": 800, "timeout_s": 120}})
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    rec = _wait(client, "cube", rid)
    assert rec["status"] == "done", rec
    full = client.get(f"/sims/{rec['sim_id']}", headers=AUTH).json()
    assert full["type"] == "wind" and full["fidelity_class"] == "cfd_lbm"
    assert full["results"]["cd"] > 0.3 and full["results"]["drag_n"] > 0
    assert full["solver"]["fidelity"] == "low fidelity" and full["guide"] is True
    assert isinstance(full["step_id"], int) and full["reason"] == "drag of the cube"
    assert client.get(f"/sims/{rec['sim_id']}/result.glb", headers=AUTH).content[:4] == b"glTF"
    png = client.get(f"/sims/{rec['sim_id']}/slices/slice_xz.png", headers=AUTH)
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    ch = client.get(f"/sims/{rec['sim_id']}/charts", headers=AUTH).json()
    assert "cd_history" in {c["id"] for c in ch["charts"]}
    pr = client.get(f"/sims/{rec['sim_id']}/probe", headers=AUTH,
                    params={"x": -60, "y": 0, "z": 0}).json()
    assert pr["inside"] and pr["values"]["speed"] > 0
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    assert steps[0]["payload"]["name"] == "sim_run"
    res = next(s for s in steps if s["kind"] == "sim_run")
    card = res["payload"]["output"]["data"]["card"]
    assert card["type"] == "wind" and card["urls"]["glb"].endswith("/result.glb")
    assert card["step_id"] == steps[0]["id"]


@pytest.mark.slow
@pytest.mark.timeout(400)
@needs_ccx
def test_design_review_runs_child_studies_through_the_gate(client, rt):
    _build(client, rt, "beam", BEAM)
    spec = {"goal": "a beam", "requirements": [
        {"id": "r1", "kind": "min_safety_factor", "target": 2, "load_case_id": "tip"},
        {"id": "r2", "kind": "max_deflection_mm", "target": 0.01, "load_case_id": "tip"},
        {"id": "r3", "kind": "max_mass_g", "target": 50},
        {"id": "r4", "kind": "min_natural_freq_hz", "target": 100},
        {"id": "r5", "kind": "printable", "target": True}],
        "load_cases": [{"id": "tip", "name": "tip", "fix": "x-min", "load": "x-max",
                        "newtons": 10, "direction": "-z"}]}
    rid = rt.create_external_run("verifier", provider="x", model="y")
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "design_review", {"part_id": "beam", "spec": spec},
        call_id="rev1", workdir=rt.workdir(rid)))
    assert out.ok, out.summary
    by = {r["requirement_id"]: r for r in out.data["table"]}
    assert by["r1"]["status"] == "pass" and by["r1"]["sim_id"].startswith("sim_")
    assert by["r2"]["status"] == "fail"           # ~1.7 mm, far over 0.01 mm
    assert by["r3"]["status"] == "pass" and by["r3"]["measured"] == pytest.approx(12.4)
    assert by["r4"]["status"] == "pass"           # ~220 Hz
    assert by["r5"]["status"] == "warn"           # not checked by a study
    assert out.data["verdict"] == "fail"
    assert [s["type"] for s in out.data["studies"]] == ["static", "modal"]
    assert "GUIDE" in out.summary.upper() and by["r1"]["sim_id"] in out.summary
    # the child studies are their own Gate calls (local_mutate) in the same run
    calls = [s for s in rt.store.steps(rid, kinds=[StepKind.TOOL_CALL])]
    assert [c.payload["name"] for c in calls] == ["design_review", "sim_run", "sim_run"]
    assert calls[1].payload["call_id"] == "rev1.r1"
    rec = client.get(f"/sims/{by['r1']['sim_id']}", headers=AUTH).json()
    assert rec["run_id"] == rid and rec["step_id"] == calls[1].id
    assert rec["checks"][0]["requirement_id"] == "r1"


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_design_review_children_need_local_mutate(client, rt):
    _build(client, rt, "beam", BEAM)
    spec = {"requirements": [{"id": "r1", "kind": "min_safety_factor", "target": 2}],
            "load_cases": [{"id": "tip", "fix": "x-min", "load": "x-max", "newtons": 10,
                            "direction": "-z"}]}
    # a run without local_mutate: the child sim_run is denied, so the study cannot run
    rid = rt.create_external_run("verifier", provider="x", model="y",
                                 allowed_classes={SafetyClass.READ})
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "design_review", {"part_id": "beam", "spec": spec},
        call_id="rev2", workdir=rt.workdir(rid)))
    assert out.ok
    row = out.data["table"][0]
    # a denied child study is "not checked" (warn), never a fail (H2/K3): no evidence is not a fail
    assert row["status"] == "warn" and row.get("checked") is False and "denied" in row["note"]
    assert out.data["studies"][0]["ok"] is False
    assert out.data["verdict"] != "fail"


def _progress(sub, run_id):
    out = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "sim_progress" and ev.run_id == run_id:
            out.append(ev.data)
    return out


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_sim_progress_names_its_call_and_ends_with_a_terminal_phase(client, rt):
    """Each sim_progress carries the tool call's call_id and step_id (design_review runs
    several same-type studies of one part at once), and the last one is phase done / failed."""
    _build(client, rt, "cube", CUBE)
    sub = rt.events.subscribe()
    rid = rt.create_external_run("tester", provider="x", model="y")
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "sim_run", {"type": "wind", "part_id": "cube",
                                       "settings": {"solver": "empirical"}},
        call_id="w1", workdir=rt.workdir(rid)))
    assert out.ok, out.summary
    prog = _progress(sub, rid)
    call = next(s for s in rt.store.steps(rid, kinds=[StepKind.TOOL_CALL])
                if s.payload["call_id"] == "w1")
    assert len(prog) >= 2
    assert all(p["call_id"] == "w1" and p["step_id"] == call.id for p in prog)
    assert prog[-1]["phase"] == "done" and prog[-1]["stage"] == "done"
    assert prog[-1]["pct"] == 100
    assert all(p["phase"] != "done" for p in prog[:-1])
    # a failure ends with phase failed (a static study without CalculiX)
    import mises.sim.tools as sim_tools
    real = sim_tools.find_ccx
    sim_tools.find_ccx = lambda: None
    try:
        out = client.portal.call(functools.partial(
            rt.gate.call, rid, "sim_run", {"type": "static", "part_id": "cube", "settings": {
                "load_cases": [{"name": "c", "supports": [{"face": "bottom"}],
                                "loads": [{"kind": "force", "face": "top", "newtons": 5,
                                           "direction": "-z"}]}]}},
            call_id="s1", workdir=rt.workdir(rid)))
    finally:
        sim_tools.find_ccx = real
    assert not out.ok
    prog = [p for p in _progress(sub, rid) if p["call_id"] == "s1"]
    assert prog[-1]["phase"] == "failed" and prog[-1]["stage"] == "failed"
    sub.close()


def test_design_study_variant_sims_are_served(client, rt):
    # C3 keeps the best / Pareto variants' stress studies under <home>/studies/<study>/<variant>/;
    # the spec panel's scatter opens them in the immersive view through the normal sim routes
    d = rt.home / "studies" / "study_abcdefabcdef" / "e3" / "parts" / "frame_x" / "sims" / "sim_00000000beef"
    d.mkdir(parents=True)
    rec = {"sim_id": "sim_00000000beef", "part_id": "frame_x", "type": "static", "status": "done",
           "created_at": time.time(), "run_id": "r1", "geometry_hash": "x", "fidelity_class": "fem",
           "label": "guide only, not a guarantee", "results": {}, "warnings": []}
    (d / "result.json").write_text(json.dumps(rec))
    r = client.get("/sims/sim_00000000beef", headers=AUTH)
    assert r.status_code == 200 and r.json()["part_id"] == "frame_x"
    assert client.get("/sims/sim_00000000dead", headers=AUTH).status_code == 404


def test_a_removed_variant_sim_is_gone_not_missing(client, rt):
    # QA B2-2: "View result" on a variant the study did not keep opened a bare 404
    root = rt.home / "studies" / "study_abcdefabcdef"
    root.mkdir(parents=True)
    (root / "removed_sims.json").write_text(json.dumps(["sim_00000000cafe"]))
    r = client.get("/sims/sim_00000000cafe", headers=AUTH)
    assert r.status_code == 410, r.text
    body = r.json()
    assert body["error"] == "gone" and body["study_id"] == "study_abcdefabcdef"
    assert "not kept" in body["message"] and body["hint"]
    assert client.get("/sims/sim_00000000cafe/result.glb", headers=AUTH).status_code == 410
    assert client.get("/sims/sim_00000000dead", headers=AUTH).status_code == 404
