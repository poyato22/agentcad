"""HTTP routes for generative design, through the Gate on the ui run (fake worker)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.topopt import jobs, verify

from .test_jobs import FakeWorker, _fake_verify, _part

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"part_id": "bracket", "fixtures": [{"face": "x-min"}],
        "load_cases": [{"name": "tip", "loads": [{"face": "x-max", "newtons": 100,
                                                  "direction": "-z"}]}],
        "volume_fraction": 0.3}


@pytest.fixture
def rt(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(h))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    r = Runtime(h, provider_resolver=lambda ref: (fake, "fake-model"))
    _part(h)
    return r


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr(jobs, "run_sandboxed", FakeWorker())
    monkeypatch.setattr(verify, "verify", _fake_verify())
    monkeypatch.setattr(jobs, "POLL_S", 0.05)
    import mises.sim.ccx as ccx_mod
    monkeypatch.setattr(ccx_mod, "find_ccx", lambda: "/usr/bin/true")
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _wait(client, tid, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/topopt/{tid}", headers=AUTH).json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError("never finished")


def test_start_poll_files_and_accept(client, rt, monkeypatch):
    r = client.post("/topopt", headers=AUTH, json=BODY)
    assert r.status_code == 202, r.text
    rec = r.json()
    tid = rec["topopt_id"]
    assert rec["tool"] == "topopt_run" and rec["stream"] == "sim"
    assert rec["variants"][0]["urls"]["preview"].endswith("/variants/v0/preview.glb")
    done = _wait(client, tid)
    assert done["status"] == "done" and done["recommended"] == "v0" and done["summary"]
    assert client.get("/topopt", headers=AUTH, params={"part_id": "bracket"}).json()[0][
        "topopt_id"] == tid
    png = client.get(f"/topopt/{tid}/variants/v0/render.png", headers=AUTH)
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    etag = png.headers["etag"]
    assert client.get(f"/topopt/{tid}/variants/v0/render.png",
                      headers={**AUTH, "If-None-Match": etag}).status_code == 304
    step = client.get(f"/topopt/{tid}/variants/v0/part.step", headers=AUTH)
    assert "bracket_v0.step" in step.headers["content-disposition"]
    assert client.get(f"/topopt/{tid}/variants/v0/secret.txt", headers=AUTH).status_code == 404
    assert client.post(f"/topopt/{tid}/cancel", headers=AUTH).status_code == 409

    from mises.cad.runner import BuildResult
    from mises.topopt import accept as accept_mod

    async def fake_build(d, vars, **kw):
        m = {"volume_mm3": 900.0, "mass_g": 1.1, "bbox": {"size": [24, 16, 8]}, "valid": True,
             "holes": []}
        (d / "metrics.json").write_text(json.dumps(m))
        return BuildResult(True, metrics=m, vars={})
    monkeypatch.setattr(accept_mod, "run_build", fake_build)
    acc = client.post(f"/topopt/{tid}/accept", headers=AUTH, json={"mode": "new"})
    assert acc.status_code == 200, acc.text
    assert acc.json()["part_id"] == "bracket_generative"
    assert (rt.home / "parts" / "bracket_generative" / "topopt_body.step").is_file()


def test_generate_body_and_validation(client):
    r = client.post("/topopt", headers=AUTH,
                    json={**BODY, "sweep": {"volume_fraction": [0.2, 0.3]}})
    assert r.status_code == 202 and r.json()["tool"] == "topopt_generate"
    assert len(r.json()["variants"]) == 2
    _wait(client, r.json()["topopt_id"])
    bad = client.post("/topopt", headers=AUTH, json={**BODY, "volume_fraction": 0.95})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_input"
    missing = client.post("/topopt", headers=AUTH, json={**BODY, "part_id": "nope"})
    assert missing.status_code == 404
    assert client.get("/topopt/to_000000000000", headers=AUTH).status_code == 404
    assert client.get("/topopt/../../etc", headers=AUTH).status_code in (400, 404)
