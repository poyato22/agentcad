import functools
import os
import json
import time

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.sim import tools as sim_tools
from mises.sim.ccx import INSTALL_HINT, find_ccx

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
BODY = {"part_id": "beam", "fix": "x-min",
        "load": {"face": "x-max", "newtons": 10, "direction": "-z"}, "material": "PETG"}


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


def write_record(rt, part_id="beam", sim_id="sim_0123456789ab", status="done", glb=b"glTF"):
    d = rt.home / "parts" / part_id / "sims" / sim_id
    d.mkdir(parents=True)
    rec = {"sim_id": sim_id, "part_id": part_id, "status": status, "created_at": 1.0,
           "run_id": "r1", "geometry_hash": "x", "results": {"max_von_mises_mpa": 6.0}}
    (d / "result.json").write_text(json.dumps(rec))
    if glb is not None:
        (d / "result.glb").write_bytes(glb)
    return d


def wait_sims(client, part_id, run_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sims = client.get("/sims", headers=AUTH, params={"part_id": part_id}).json()
        mine = [s for s in sims if s["run_id"] == run_id and s["status"] != "running"]
        if mine:
            return mine[0]
        time.sleep(0.1)
    raise AssertionError("sim never finished")


def test_routes_need_admin_token(client):
    for path in ["/sims", "/sims/solvers", "/sims/sim_0123456789ab"]:
        assert client.get(path).status_code == 401
    assert client.post("/sims", json=BODY).status_code == 401


def test_list_get_glb(client, rt):
    assert client.get("/sims", headers=AUTH).json() == []
    write_record(rt)
    write_record(rt, part_id="other", sim_id="sim_ffffffffffff")
    sims = client.get("/sims", headers=AUTH, params={"part_id": "beam"}).json()
    assert [s["sim_id"] for s in sims] == ["sim_0123456789ab"]
    assert len(client.get("/sims", headers=AUTH).json()) == 2
    r = client.get("/sims/sim_0123456789ab", headers=AUTH)
    assert r.status_code == 200 and r.json()["results"]["max_von_mises_mpa"] == 6.0
    assert r.json()["stale"] is True  # the part's model.step is not the one solved
    g = client.get("/sims/sim_0123456789ab/result.glb", headers=AUTH)
    assert g.status_code == 200 and g.content == b"glTF"
    assert g.headers["content-type"] == "model/gltf-binary" and g.headers["etag"]
    again = client.get("/sims/sim_0123456789ab/result.glb",
                       headers={**AUTH, "If-None-Match": g.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/sims/sim_999999999999", headers=AUTH).status_code == 404
    assert client.get("/sims/..%2F..%2Fx", headers=AUTH).status_code in (400, 404)
    assert client.get("/sims", headers=AUTH, params={"part_id": "Bad/../x"}).status_code == 400


def test_solvers_route(client, monkeypatch):
    monkeypatch.setattr("mises.sim.ccx.find_ccx", lambda: None)
    s = client.get("/sims/solvers", headers=AUTH).json()
    assert s["ccx"] == {"found": False, "path": None, "version": None, "install": INSTALL_HINT,
                        "name": "CalculiX"}
    assert s["gmsh"]["found"] is True


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_post_without_ccx_goes_through_gate_and_fails_clearly(client, rt, monkeypatch):
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: None)
    bad = client.post("/sims", headers=AUTH, json={**BODY, "part_id": "nope"})
    assert bad.status_code == 404  # no such part: refused before a run is created
    _build(client, rt)
    r = client.post("/sims", headers=AUTH, json=BODY)
    assert r.status_code == 202, r.text
    rid = r.json()["run_id"]
    rec = wait_sims(client, "beam", rid)
    assert rec["status"] == "failed" and INSTALL_HINT in rec["hint"]
    run = client.get(f"/runs/{rid}", headers=AUTH).json()
    assert run["agent"] == "studies" and run["status"] == "failed"
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    assert [s["kind"] for s in steps][:1] == ["tool_call"]
    assert steps[0]["payload"]["name"] == "sim_static_run"


def _build(client, rt):
    # build the part the way an agent would: through the gate in a run
    rid = rt.create_external_run("tester", provider="x", model="y")
    out = client.portal.call(functools.partial(
        rt.gate.call, rid, "mirror_part_create", {"part_id": "beam", "script": BEAM},
        call_id="b1", workdir=rt.workdir(rid)))
    assert out.ok, out.summary


@pytest.mark.slow
@pytest.mark.timeout(180)
@pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")
def test_post_runs_a_study(client, rt):
    _build(client, rt)
    r = client.post("/sims", headers=AUTH, json=BODY)
    assert r.status_code == 202
    rid = r.json()["run_id"]
    rec = wait_sims(client, "beam", rid)
    assert rec["status"] == "done", rec
    full = client.get(f"/sims/{rec['sim_id']}", headers=AUTH).json()
    assert full["material"]["name"] == "PETG" and full["fidelity_class"] == "fem"
    assert full["stale"] is False and full["guide"] is True
    assert client.get(f"/sims/{rec['sim_id']}/result.glb", headers=AUTH).content[:4] == b"glTF"
    assert client.get(f"/runs/{rid}", headers=AUTH).json()["status"] == "done"
    kinds = [s["kind"] for s in client.get(f"/runs/{rid}/steps", headers=AUTH).json()]
    assert "sim_run" in kinds


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_cancel_kills_the_solver(client, rt, monkeypatch, tmp_path):
    fake = tmp_path / "bin" / "ccx"
    fake.parent.mkdir()
    # the solver's job dir is the only writable place: it writes its pid there and hangs
    # `exec sleep`: the sandbox denies process-fork
    fake.write_text("#!/bin/sh\n[ \"$1\" = -v ] && echo Version 2.23 && exit 0\necho $$ > job.pid\n"
                    "exec sleep 60\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: str(fake))
    _build(client, rt)
    rid = client.post("/sims", headers=AUTH, json=BODY).json()["run_id"]
    deadline = time.time() + 60
    pid = None
    while time.time() < deadline and pid is None:
        for p in (rt.home / "runs" / rid / "sim_tmp").glob("*/coarse/job.pid"):
            if p.read_text().strip():
                pid = int(p.read_text())
        time.sleep(0.05)
    assert pid is not None, "solver never started"
    assert client.post(f"/runs/{rid}/cancel", headers=AUTH).status_code == 200
    rec = wait_sims(client, "beam", rid, timeout=30)
    assert rec["status"] == "cancelled"
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert client.get(f"/runs/{rid}", headers=AUTH).json()["status"] == "cancelled"
