"""POST /sims with project_id (an assembly study) and listing / reading its records."""
import json
import time

from mises.assembly.model import Instance
from mises.types import ToolOutput

from .test_sim_api_studies import AUTH, _build, client, rt  # noqa: F401  (fixtures)

BLOCK = ('from build123d import *\nVARS={"s":20.0}\ndef build(v):\n'
         '    return Box(v["s"],v["s"],10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
SETTINGS = {"load_cases": [{"name": "press", "supports": [{"kind": "fixed",
                                                           "face": "bottom"}],
                            "loads": [{"kind": "force", "face": "top", "newtons": 10,
                                       "direction": "-z"}]}],
            "nonlinear": {"contacts": [{"kind": "bonded", "a": {"instance": "lid"},
                                        "b": {"instance": "base"}}]}}


def _runs(client):
    return len(client.get("/runs", headers=AUTH).json())


def test_assembly_post_is_refused_before_a_run_exists(client, rt):
    before = _runs(client)
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": "nope",
                                                 "settings": SETTINGS})
    assert r.status_code == 404
    pid = rt.store.create_project("Stack").id
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": pid,
                                                 "settings": SETTINGS})
    assert r.status_code == 404 and "no assembly instances" in r.json()["message"]
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": pid,
                                                 "part_id": "blk", "settings": SETTINGS})
    assert r.status_code == 400 and "exactly one" in r.json()["message"]
    _build(client, rt, "blk", BLOCK)
    rt.assembly_store.add_instance(pid, Instance(id="base", part_id="blk", name="base",
                                                 fixed=True))
    bad = json.loads(json.dumps(SETTINGS))
    bad["load_cases"][0]["loads"][0]["face"] = "f2"
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": pid,
                                                 "settings": bad})
    assert r.status_code == 400 and "ambiguous" in r.json()["message"]
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": pid,
                                                 "settings": SETTINGS})
    assert r.status_code == 400 and "no instance lid" in r.json()["message"]
    assert _runs(client) == before + 1           # only _build's run


def test_assembly_post_runs_sim_run_scoped_to_the_build(client, rt, monkeypatch):
    pid = rt.store.create_project("Stack").id
    _build(client, rt, "blk", BLOCK)
    rt.assembly_store.add_instance(pid, Instance(id="base", part_id="blk", name="base",
                                                 fixed=True))
    rt.assembly_store.add_instance(pid, Instance(
        id="lid", part_id="blk", name="lid", fixed=True,
        transform=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 10], [0, 0, 0, 1]]))
    calls = []

    async def fake_call(run_id, tool, payload, **kw):
        calls.append((run_id, tool, payload))
        return ToolOutput(summary="ok", data={})
    monkeypatch.setattr(rt.gate, "call", fake_call)
    r = client.post("/sims", headers=AUTH, json={"type": "static", "project_id": pid,
                                                 "settings": SETTINGS})
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    assert calls[0][1] == "sim_run" and calls[0][2]["project_id"] == pid
    assert calls[0][2]["part_id"] is None
    pol = rt.store.current_policy(rid)
    assert set(pol.edit_scope) == {pid, f"project:{pid}", "blk"}


def test_assembly_records_are_listed_and_read(client, rt):
    d = rt.home / "assemblies" / "p9" / "sims" / "sim_0000000000a1"
    d.mkdir(parents=True)
    rec = {"sim_id": "sim_0000000000a1", "part_id": None, "project_id": "p9",
           "type": "static", "status": "done", "created_at": time.time(), "run_id": "r1",
           "part_hashes": {"blk": "x"}, "fidelity_class": "fem_nonlinear",
           "results": {"max_von_mises_mpa": 1.0, "model_error_band": 0.15,
                       "nonlinear": {"description": "contact"}}, "warnings": []}
    (d / "result.json").write_text(json.dumps(rec))
    listed = client.get("/sims", headers=AUTH, params={"project_id": "p9"}).json()
    assert [s["sim_id"] for s in listed] == ["sim_0000000000a1"]
    assert listed[0]["project_id"] == "p9" and listed[0]["stale"] is True
    assert client.get("/sims", headers=AUTH, params={"project_id": "zz"}).json() == []
    assert client.get("/sims", headers=AUTH, params={"project_id": "../x"}).status_code == 400
    got = client.get("/sims/sim_0000000000a1", headers=AUTH).json()
    assert got["project_id"] == "p9" and got["card"]["fidelity_note"].startswith("nonlinear")
    assert got["card"]["part_id"] is None
    assert any(s["sim_id"] == "sim_0000000000a1"
               for s in client.get("/sims", headers=AUTH).json())


def test_list_sims_judges_staleness_against_the_right_root(tmp_path):
    import hashlib

    from mises.sim.studies import list_sims
    part = tmp_path / "parts" / "blk"
    part.mkdir(parents=True)
    (part / "model.step").write_text("x")
    asm = tmp_path / "assemblies" / "p1" / "sims"
    h = hashlib.sha256(b"x").hexdigest()
    for d, rec in ((part / "sims" / "sim_0000000000b1", {"part_id": "blk", "geometry_hash": h}),
                   (asm / "sim_0000000000b2", {"part_id": None, "project_id": "p1",
                                               "part_hashes": {"blk": h}})):
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({"sim_id": d.name, "status": "done",
                                                   "created_at": time.time(), **rec}))
    got = {s["sim_id"]: s["stale"] for s in list_sims(tmp_path / "parts")}
    assert got == {"sim_0000000000b1": False, "sim_0000000000b2": False}
