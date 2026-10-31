"""Assembly REST routes against a real Runtime (fake provider)."""
import json
import struct
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.server.app import create_app

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _frame(iid):
    return {"instance_id": iid, "frame": {"origin": [10, 0, 0], "axis": [0, 0, 1]}}


def test_routes_need_token(client):
    assert client.get("/projects/p/assembly").status_code == 401


def test_remove_parts_and_undo_put_instances_and_their_joints_back_exactly(client, rt):
    """Plan 9 D29 (re-review N1): the tree's Remove from assembly, then Undo."""
    add = lambda part, name, fixed=False: client.post(  # noqa: E731
        "/projects/p/assembly/instances", headers=AUTH, json={"part_id": part, "name": name, "fixed": fixed}).json()
    base, lid, pin = add("plate", "base", True), add("lid", "lid"), add("pin", "pin")
    j1 = client.post("/projects/p/assembly/joints", headers=AUTH,
                     json={"kind": "revolute", "a": _frame(base["id"]), "b": _frame(lid["id"])}).json()
    j2 = client.post("/projects/p/assembly/joints", headers=AUTH,
                     json={"kind": "fixed", "a": _frame(lid["id"]), "b": _frame(pin["id"])}).json()
    rt.assembly_store.mark_auto("p", pin["id"])
    before = rt.assembly_store.get("p")
    r = client.post("/projects/p/assembly/remove_parts", headers=AUTH, json={"part_ids": ["lid"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["removed_instances"] == [lid["id"]] and sorted(out["removed_joints"]) == sorted([j1["id"], j2["id"]])
    assert [i.id for i in rt.assembly_store.get("p").instances] == [base["id"], pin["id"]]
    back = client.post("/projects/p/assembly/restore", headers=AUTH, json=out["undo"]).json()
    assert back == {"restored_instances": 1, "restored_joints": 2}
    after = rt.assembly_store.get("p")
    assert after.model_dump() == before.model_dump()          # ids, order, transforms, joints
    assert rt.assembly_store.auto_ids("p") == {pin["id"]}


def test_instance_crud_and_solve(client):
    base = client.post("/projects/p/assembly/instances", headers=AUTH,
                        json={"part_id": "plate", "name": "base", "fixed": True})
    assert base.status_code == 201, base.text
    flap = client.post("/projects/p/assembly/instances", headers=AUTH,
                       json={"part_id": "plate", "name": "flap"}).json()
    aid, bid = base.json()["id"], flap["id"]

    j = client.post("/projects/p/assembly/joints", headers=AUTH,
                    json={"kind": "revolute", "a": _frame(aid), "b": _frame(bid)})
    assert j.status_code == 201, j.text
    jid = j.json()["id"]

    asm = client.get("/projects/p/assembly", headers=AUTH).json()
    assert len(asm["instances"]) == 2 and len(asm["joints"]) == 1
    assert asm["dof_remaining"] == 1

    drive = client.post("/projects/p/assembly/drive", headers=AUTH,
                        json={"joint_id": jid, "value": 90.0}).json()
    t = drive["transforms"][bid]
    # pivot (10,0,0) stays put under a 90-deg revolve about +Z
    pivot = [sum(t[i][k] * ([10, 0, 0, 1][k]) for k in range(4)) for i in range(3)]
    assert pivot == pytest.approx([10, 0, 0], abs=1e-9)


def test_drive_clamps_to_limits(client):
    g = client.post("/projects/p/assembly/instances", headers=AUTH,
                    json={"part_id": "rail", "name": "g", "fixed": True}).json()
    c = client.post("/projects/p/assembly/instances", headers=AUTH,
                   json={"part_id": "car", "name": "c"}).json()
    j = client.post("/projects/p/assembly/joints", headers=AUTH, json={
        "kind": "slider",
        "a": {"instance_id": g["id"], "frame": {"origin": [0, 0, 0], "axis": [1, 0, 0]}},
        "b": {"instance_id": c["id"], "frame": {"origin": [0, 0, 0], "axis": [1, 0, 0]}},
        "limits": [0.0, 20.0]}).json()
    drive = client.post("/projects/p/assembly/drive", headers=AUTH,
                        json={"joint_id": j["id"], "value": 999.0}).json()
    assert drive["applied"] == pytest.approx(20.0)


def test_loop_joint_rejected(client):
    ids = []
    for name in ("a", "b", "c"):
        r = client.post("/projects/p/assembly/instances", headers=AUTH,
                        json={"part_id": "plate", "name": name, "fixed": name == "a"})
        ids.append(r.json()["id"])
    client.post("/projects/p/assembly/joints", headers=AUTH,
                json={"kind": "revolute", "a": _frame(ids[0]), "b": _frame(ids[1])})
    client.post("/projects/p/assembly/joints", headers=AUTH,
                json={"kind": "revolute", "a": _frame(ids[1]), "b": _frame(ids[2])})
    loop = client.post("/projects/p/assembly/joints", headers=AUTH,
                       json={"kind": "revolute", "a": _frame(ids[2]), "b": _frame(ids[0])})
    assert loop.status_code == 400
    assert "loop" in loop.json()["message"]
    assert len(client.get("/projects/p/assembly", headers=AUTH).json()["joints"]) == 2


def test_delete_instance_cascades(client):
    a = client.post("/projects/p/assembly/instances", headers=AUTH,
                    json={"part_id": "plate", "name": "a", "fixed": True}).json()
    b = client.post("/projects/p/assembly/instances", headers=AUTH,
                   json={"part_id": "plate", "name": "b"}).json()
    j = client.post("/projects/p/assembly/joints", headers=AUTH,
                    json={"kind": "revolute", "a": _frame(a["id"]), "b": _frame(b["id"])}).json()
    d = client.delete(f"/projects/p/assembly/instances/{a['id']}", headers=AUTH).json()
    assert d["removed_joints"] == [j["id"]]
    asm = client.get("/projects/p/assembly", headers=AUTH).json()
    assert len(asm["instances"]) == 1 and asm["joints"] == []


def test_scoped_by_project(client):
    client.post("/projects/one/assembly/instances", headers=AUTH,
                json={"part_id": "plate", "name": "only"})
    assert client.get("/projects/two/assembly", headers=AUTH).json()["instances"] == []


BOX = ('from build123d import *\nVARS={"s":10.0}\n'
       'def build(v):\n    return Box(v["s"], v["s"], v["s"])\n')


@pytest.mark.slow
async def test_interference_and_glb(rt):
    # build a cube into the runtime's shared parts dir
    await mirror_part_create.handler(SimpleNamespace(workdir=rt.home),
                                     CreateArgs(part_id="cube", script=BOX))
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as client:
        client.post("/projects/p/assembly/instances", headers=AUTH,
                    json={"part_id": "cube", "name": "a", "fixed": True})
        client.post("/projects/p/assembly/instances", headers=AUTH, json={
            "part_id": "cube", "name": "b",
            "transform": [[1, 0, 0, 5], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]})
        hits = client.get("/projects/p/assembly/interference", headers=AUTH).json()
        assert len(hits) == 1 and hits[0]["volume_mm3"] == pytest.approx(500, rel=0.02)

        glb = client.get("/projects/p/assembly.glb", headers=AUTH)
        assert glb.status_code == 200
        data = glb.content
        assert data[:4] == b"glTF"
        jlen = struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20:20 + jlen])
        assert len(gltf["nodes"]) == 2


def test_traversing_part_id_rejected_by_route(client):
    r = client.post("/projects/p/assembly/instances", headers=AUTH,
                    json={"part_id": "../x", "name": "evil"})
    assert r.status_code in (400, 422), r.text
    ok = client.post("/projects/p/assembly/instances", headers=AUTH,
                     json={"part_id": "plate", "name": "a"}).json()
    r = client.patch(f"/projects/p/assembly/instances/{ok['id']}", headers=AUTH,
                     json={"part_id": "../../etc"})
    assert r.status_code in (400, 422), r.text
    asm = client.get("/projects/p/assembly", headers=AUTH).json()
    assert [i["part_id"] for i in asm["instances"]] == ["plate"]


def test_delete_missing_is_404(client):
    assert client.delete("/projects/p/assembly/instances/ghost", headers=AUTH).status_code == 404
    assert client.delete("/projects/p/assembly/joints/ghost", headers=AUTH).status_code == 404


@pytest.mark.slow
async def test_glb_cache_keyed_on_inputs(rt):
    await mirror_part_create.handler(SimpleNamespace(workdir=rt.home),
                                     CreateArgs(part_id="cube", script=BOX))
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as client:
        inst = client.post("/projects/p/assembly/instances", headers=AUTH,
                           json={"part_id": "cube", "name": "a", "fixed": True}).json()
        first = client.get("/projects/p/assembly.glb", headers=AUTH)
        etag = first.headers["etag"]
        again = client.get("/projects/p/assembly.glb", headers={**AUTH, "If-None-Match": etag})
        assert again.status_code == 304
        client.patch(f"/projects/p/assembly/instances/{inst['id']}", headers=AUTH, json={
            "transform": [[1, 0, 0, 9], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]})
        moved = client.get("/projects/p/assembly.glb", headers={**AUTH, "If-None-Match": etag})
        assert moved.status_code == 200 and moved.headers["etag"] != etag
        assert len(list((rt.home / "assembly_cache").iterdir())) == 2
