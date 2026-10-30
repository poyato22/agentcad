"""POST /parts/{id}/measure: exact B-rep values over HTTP, versioned like the edit routes."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.server.app import create_app

from .conftest import AUTH, TOKEN

pytestmark = pytest.mark.slow

PLATE = ('from build123d import *\nVARS={}\ndef build(v):\n'
         '    with BuildPart() as p:\n'
         '        Box(60, 40, 5)\n'
         '        with Locations((-20, 0, 0)):\n'
         '            Hole(2.1)\n'
         '    return p.part\n')


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


async def _build(rt):
    r = await mirror_part_create.handler(SimpleNamespace(workdir=rt.home),
                                         CreateArgs(part_id="plate", script=PLATE))
    assert r.ok, r.summary


def test_measure_needs_the_token(client):
    assert client.post("/parts/plate/measure", json={"refs": [{"face_id": "f0"}]}).status_code == 401


async def test_measure_a_hole_and_two_faces(client, rt):
    await _build(rt)
    m = json.loads((rt.home / "parts" / "plate" / "metrics.json").read_text())
    circle = next(e for e in m["edge_list"] if e["type"] == "circle")
    r = client.post("/parts/plate/measure", headers=AUTH, json={"refs": [{"edge_id": circle["id"]}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["part_id"] == "plate" and isinstance(body["version"], int)
    vals = {v["key"]: v for v in body["values"]}
    assert vals["diameter"]["value"] == pytest.approx(4.2, abs=1e-9)
    assert vals["diameter"]["unit"] == "mm" and vals["diameter"]["label"] == "Diameter"
    top = next(f for f in m["face_list"] if f["normal"] == [0.0, 0.0, 1.0])
    bottom = next(f for f in m["face_list"] if f["normal"] == [0.0, 0.0, -1.0])
    two = client.post("/parts/plate/measure", headers=AUTH, json={
        "refs": [{"face_id": top["id"]}, {"face_id": bottom["id"]}], "base_version": body["version"]})
    v2 = {v["key"]: v["value"] for v in two.json()["values"]}
    assert v2["normal_distance"] == pytest.approx(5.0, abs=1e-9) and v2["angle"] == pytest.approx(0.0)


async def test_measure_errors(client, rt):
    assert client.post("/parts/ghost/measure", headers=AUTH,
                       json={"refs": [{"face_id": "f0"}]}).status_code == 404
    assert client.post("/parts/Bad!/measure", headers=AUTH,
                       json={"refs": [{"face_id": "f0"}]}).status_code == 404
    await _build(rt)
    bad = client.post("/parts/plate/measure", headers=AUTH, json={"refs": []})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_input"
    gone = client.post("/parts/plate/measure", headers=AUTH, json={"refs": [{"face_id": "f999"}]})
    assert gone.status_code == 404 and "pick again" in gone.json()["message"]
    stale = client.post("/parts/plate/measure", headers=AUTH,
                        json={"refs": [{"face_id": "f0"}], "base_version": 99})
    assert stale.status_code == 409
    assert stale.json()["error"] == "stale_version" and stale.json()["current_version"] == 1


async def test_warm_loads_the_part_ahead_and_shutdown_closes_it(rt, monkeypatch):
    from mises import measure as M
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    await _build(rt)
    with TestClient(create_app(rt, TOKEN)) as client:
        r = client.post("/parts/plate/measure", headers=AUTH, json={"warm": True})
        assert r.status_code == 200 and r.json() == {"part_id": "plate", "warm": True}
        w = next(iter(M._warm.values()))
        assert w.proc.returncode is None
        assert client.post("/parts/ghost/measure", headers=AUTH, json={"warm": True}).status_code == 404
    assert not M._warm and w.proc.returncode is not None      # the server's shutdown closed it
