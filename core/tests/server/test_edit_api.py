"""C6: live-editing REST routes (user edits ride the Gate on the shared ui run)."""
import json

import pytest
from starlette.testclient import TestClient

import mises.cad.tools as tools_mod
from mises.cad.runner import BuildResult
from mises import edits
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
            "edge_list": [{"id": "e0", "type": "line", "center": [20, 0, 5], "length": 40.0,
                           "direction": [1, 0, 0]}]}


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
    edits.write_version(d, 1)


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


@pytest.fixture
def real_client(rt):
    """The full app with the REAL sandboxed build (no fake_build)."""
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


@pytest.mark.slow
def test_user_source_put_is_sandboxed(real_client, home):
    _seed(home)
    d = home / "parts" / "bracket"
    escape = home / "escape.txt"                # outside the part dir the build may write
    before = (d / "part.py").read_text()
    for body in ("    import socket\n    socket.create_connection(('1.1.1.1', 80), 3)\n",
                 f"    open({str(escape)!r}, 'w').write('x')\n"):
        src = f"from build123d import *\nVARS={{}}\ndef build(v):\n{body}    return Box(1, 1, 1)\n"
        r = real_client.put("/parts/bracket/source", headers=AUTH,
                            json={"source": src, "base_version": 1})
        assert r.status_code == 422, r.text      # the build failed: the escape was denied
        assert (d / "part.py").read_text() == before and edits.read_version(d) == 1
    assert not escape.exists()


@pytest.mark.parametrize("method,path,body", [
    ("post", "/parts/bracket/vars", {"vars": {"length": 50}}),
    ("put", "/parts/bracket/source", {"source": BOX}),
    ("post", "/parts/bracket/ops", {"op": "hole", "target": {"face_id": "f0"},
                                   "params": {"diameter": 5.0}}),
])
def test_base_version_required(client, home, method, path, body):
    _seed(home)
    r = client.request(method.upper(), path, headers=AUTH, json=body)
    assert r.status_code == 400 and "base_version" in r.json()["message"]
    assert edits.read_version(home / "parts" / "bracket") == 1     # nothing changed


@pytest.mark.parametrize("sketch_id", [12, "not-a-sketch", "sk_000000000000"])
def test_bad_sketch_id_is_400(client, home, sketch_id):
    _seed(home)
    r = client.post("/parts/bracket/ops", headers=AUTH,
                    json={"op": "extrude", "target": {"face_id": "f0"}, "base_version": 1,
                          "params": {"sketch_id": sketch_id, "distance": 2.0}})
    assert r.status_code == 400, r.text


def test_get_source_logs_no_step(client, rt, home):
    _seed(home)
    before = len(rt.store.list_runs())
    for _ in range(3):
        assert client.get("/parts/bracket/source", headers=AUTH).status_code == 200
    assert len(rt.store.list_runs()) == before      # no ui run was even needed


def test_source_schema_annotations_and_inferred(client, home):
    _seed(home)
    d = home / "parts" / "bracket"
    (d / "part.py").write_text(
        'VARS = {\n'
        '    "thickness": 4.0,  # mises: min=1 max=10 step=0.5 unit=mm doc="wall thickness"\n'
        '    "count": 3,\n'
        '}\ndef build(v):\n    pass\n')
    (d / "vars.json").write_text(json.dumps({"thickness": 4.0, "count": 3}))
    rows = {r["name"]: r for r in client.get("/parts/bracket/source", headers=AUTH).json()["vars"]}
    assert rows["thickness"] == {"name": "thickness", "value": 4.0, "min": 1.0, "max": 10.0,
                                 "step": 0.5, "unit": "mm", "doc": "wall thickness",
                                 "inferred": False}
    assert rows["count"]["inferred"] is True
    assert rows["count"]["min"] == 0.75 and rows["count"]["max"] == 12.0
    assert "unit" not in rows["count"]
