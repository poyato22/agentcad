"""C6: live-editing REST routes (user edits ride the Gate on the shared ui run)."""
import json

import pytest
from starlette.testclient import TestClient

import mises.cad.tools as tools_mod
from mises.cad.runner import BuildResult
from mises.edits import OP_MARK
from mises.server.app import create_app

from .conftest import AUTH, TOKEN

BOX = ('from build123d import *\nVARS={"length":40.0}\ndef build(v):\n'
       '    return Box(v["length"],20,10)\n')


def _metrics(vol=8000.0):
    return {"valid": True, "volume_mm3": vol, "area_mm2": 100.0, "mass_g": vol / 1000 * 1.24,
            "bbox": {"min": [0, 0, 0], "max": [40, 20, 10], "size": [40, 20, 10]},
            "center_of_mass": [0, 0, 0], "faces": 6, "edges": 12, "holes": [], "solids": 1,
            "face_list": [{"id": "f0", "type": "plane", "center": [0, 0, 5], "normal": [0, 0, 1],
                           "area": 800.0}],
            "edge_list": [{"id": "e0", "type": "line", "center": [20, 0, 5], "length": 40.0}]}


@pytest.fixture
def fake_build(monkeypatch):
    """A fast fake build: writes metrics.json and echoes merged vars, no sandbox subprocess."""
    async def _build(part_dir, vars, **kw):
        (part_dir / "metrics.json").write_text(json.dumps(_metrics()))
        return BuildResult(True, metrics=_metrics(), vars=dict(vars))
    monkeypatch.setattr(tools_mod, "run_build", _build)


@pytest.fixture
def client(rt, fake_build):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _seed(home, pid="bracket"):
    d = home / "parts" / pid
    d.mkdir(parents=True)
    (d / "part.py").write_text(BOX)
    (d / "vars.json").write_text(json.dumps({"length": 40.0}))
    (d / "metrics.json").write_text(json.dumps(_metrics()))
    (d / "version.json").write_text(json.dumps({"version": 1}))


def test_vars_edit_bumps_version(client, home):
    _seed(home)
    r = client.post("/parts/bracket/vars", headers=AUTH,
                    json={"vars": {"length": 50}, "base_version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2


def test_vars_edit_stale_is_409(client, home):
    _seed(home)
    assert client.post("/parts/bracket/vars", headers=AUTH,
                       json={"vars": {"length": 50}, "base_version": 1}).status_code == 200
    r = client.post("/parts/bracket/vars", headers=AUTH,
                    json={"vars": {"length": 60}, "base_version": 1})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "stale_version" and body["current_version"] == 2


def test_get_and_put_source(client, home):
    _seed(home)
    g = client.get("/parts/bracket/source", headers=AUTH)
    assert g.status_code == 200 and "def build" in g.json()["source"]
    assert g.json()["version"] == 1
    r = client.put("/parts/bracket/source", headers=AUTH,
                   json={"source": BOX, "base_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2


def test_op_appends_mises_op_block(client, home):
    _seed(home)
    r = client.post("/parts/bracket/ops", headers=AUTH,
                    json={"op": "hole", "target": {"face_id": "f0"},
                          "params": {"diameter": 5.0, "through": True}, "base_version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    src = (home / "parts" / "bracket" / "part.py").read_text()
    assert OP_MARK in src and "mises-op hole" in src


def test_op_bad_face_is_422_or_400(client, home):
    _seed(home)
    r = client.post("/parts/bracket/ops", headers=AUTH,
                    json={"op": "hole", "target": {"face_id": "fZZ"},
                          "params": {"diameter": 5.0}, "base_version": 1})
    assert r.status_code in (400, 422)


def test_unknown_part_is_404(client, home):
    assert client.get("/parts/ghost/source", headers=AUTH).status_code == 404


def test_part_changed_event_marks_user(client, home):
    _seed(home)
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        client.post("/parts/bracket/vars", headers=AUTH,
                    json={"vars": {"length": 55}, "base_version": 1})
        seen = None
        for _ in range(40):
            msg = ws.receive_json()
            if msg.get("type") == "part_changed":
                seen = msg
                break
        assert seen is not None
        assert seen["data"]["by"] == "user" and seen["data"]["version"] == 2
