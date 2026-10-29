"""Plan 5 C1 API: builds, part tree, run management and custom agents."""
import json

import pytest
from starlette.testclient import TestClient

from mises.server.app import create_app
from mises.store import Approval

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _mk_part(rt, part_id, vars_=None):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps(vars_ or {"length": 40.0}))
    (d / "metrics.json").write_text(json.dumps({"mass_g": 7.0}))


# ---- builds

def test_project_lifecycle(client):
    created = client.post("/projects", json={"name": "Drone"}, headers=AUTH).json()
    assert created["name"] == "Drone"
    assert created["part_count"] == 0 and created["run_count"] == 0 and created["onshape"] is None
    pid = created["id"]
    client.patch(f"/projects/{pid}", json={"name": "Drone v2"}, headers=AUTH)
    assert client.get("/projects", headers=AUTH).json()[0]["name"] == "Drone v2"
    client.delete(f"/projects/{pid}", headers=AUTH)
    assert client.get("/projects", headers=AUTH).json()[0]["trashed_at"] is not None
    client.post(f"/projects/{pid}/restore", headers=AUTH)
    assert client.get("/projects", headers=AUTH).json()[0]["trashed_at"] is None
    client.delete(f"/projects/{pid}", headers=AUTH)
    assert client.post("/projects/trash/empty", headers=AUTH).json() == {"purged": [pid],
                                                                          "count": 1}
    assert client.get("/projects", headers=AUTH).json() == []


# ---- part tree

def test_parts_list_patch_reorder(client, rt):
    _mk_part(rt, "base_plate")
    _mk_part(rt, "left_arm")
    parts = client.get("/parts", headers=AUTH).json()
    assert [p["id"] for p in parts] == ["base_plate", "left_arm"]
    assert parts[0]["part_id"] == "base_plate" and parts[0]["vars"] == {"length": 40.0}
    pid = parts[0]["project_id"]
    # recolor + rename
    r = client.patch("/parts/base_plate", json={"label": "Base", "color": "#00ff88"}, headers=AUTH)
    assert r.json()["color"] == "#00ff88" and r.json()["label"] == "Base"
    # bad color rejected
    assert client.patch("/parts/base_plate", json={"color": "red"}, headers=AUTH).status_code == 400
    # re-parent, then reorder
    client.patch("/parts/left_arm", json={"parent_id": "base_plate"}, headers=AUTH)
    assert client.get("/parts", headers=AUTH).json()  # still lists
    out = client.post("/parts/reorder",
                      json={"project_id": pid, "parent_id": None, "ids": ["left_arm", "base_plate"]},
                      headers=AUTH).json()
    assert [p["id"] for p in out] == ["left_arm", "base_plate"]


def test_part_reparent_cycle_rejected(client, rt):
    _mk_part(rt, "arm_outer")
    _mk_part(rt, "arm_inner")
    client.get("/parts", headers=AUTH)
    client.patch("/parts/arm_inner", json={"parent_id": "arm_outer"}, headers=AUTH)
    r = client.patch("/parts/arm_outer", json={"parent_id": "arm_inner"}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"] == "invalid_input"


# ---- runs management

def test_run_patch_and_delete_rules(client, rt):
    p = rt.store.create_project("B")
    running = rt.store.create_run("designer", "fake", "m", "x", project_id=p.id)
    # a running run cannot be deleted
    r = client.delete(f"/runs/{running.id}", headers=AUTH)
    assert r.status_code == 409 and r.json() == {"reason": "running"}
    # a finished run can be renamed, pinned and deleted
    done = rt.store.create_run("designer", "fake", "m", "y", project_id=p.id)
    rt.store.update_run(done.id, status="done")
    patched = client.patch(f"/runs/{done.id}", json={"title": "arm", "pinned": True},
                           headers=AUTH).json()
    assert patched["title"] == "arm" and patched["pinned"] is True
    assert patched["cost"] == {"usd": 0.0, "tokens": 0}
    assert client.delete(f"/runs/{done.id}", headers=AUTH).json() == {"ok": True}
    assert client.get(f"/runs/{done.id}", headers=AUTH).status_code == 404


def test_delete_refused_with_pending_approval(client, rt):
    p = rt.store.create_project("B")
    run = rt.store.create_run("designer", "fake", "m", "y", project_id=p.id)
    rt.store.update_run(run.id, status="done")
    rt.store.insert_approval(Approval(id="ap", run_id=run.id, action="x", payload={},
                                      payload_hash="h", status="pending", expires_at=1e12))
    r = client.delete(f"/runs/{run.id}", headers=AUTH)
    assert r.status_code == 409 and r.json() == {"reason": "pending_approval"}


def test_cleanup_dry_run_then_delete(client, rt):
    p = rt.store.create_project("B")
    done = rt.store.create_run("designer", "fake", "m", "y", project_id=p.id)
    rt.store.update_run(done.id, status="done")
    rt.store.create_run("designer", "fake", "m", "live", project_id=p.id)  # running: skipped
    dry = client.post("/runs/cleanup", json={"project_id": p.id, "dry_run": True},
                      headers=AUTH).json()
    assert dry == {"ids": [done.id], "count": 1}
    assert rt.store.get_run(done.id).status == "done"  # dry run did not delete
    wet = client.post("/runs/cleanup", json={"project_id": p.id, "dry_run": False},
                      headers=AUTH).json()
    assert wet["ids"] == [done.id]
    assert client.get(f"/runs/{done.id}", headers=AUTH).status_code == 404


def test_runs_tree(client, rt):
    p = rt.store.create_project("B")
    root = rt.store.create_run("planner", "fake", "m", "orch", project_id=p.id)
    child = rt.store.create_run("designer", "fake", "m", "w", project_id=p.id,
                                parent_run_id=root.id)
    tree = client.get("/runs/tree", params={"project_id": p.id}, headers=AUTH).json()
    assert len(tree) == 1 and tree[0]["id"] == root.id
    assert [c["id"] for c in tree[0]["children"]] == [child.id]


# ---- custom agents

def test_custom_agent_crud_and_builtins(client, rt):
    agents = {a["name"]: a for a in client.get("/agents", headers=AUTH).json()}
    assert agents["designer"]["builtin"] is True and agents["designer"]["hidden"] is False
    # create a custom agent
    r = client.post("/agents", json={"name": "framer", "description": "frames",
                                     "model": "inherit", "body": "Build frames."}, headers=AUTH)
    assert r.status_code == 200 and r.json()["builtin"] is False
    assert (rt.home / "agents" / "framer.md").exists()
    # it shows up, editable, not builtin
    agents = {a["name"]: a for a in client.get("/agents", headers=AUTH).json()}
    assert agents["framer"]["builtin"] is False
    client.put("/agents/framer", json={"description": "d2", "body": "Updated."}, headers=AUTH)
    assert client.get("/agents", headers=AUTH)
    # a built-in cannot be edited or deleted
    assert client.put("/agents/designer", json={"body": "x"}, headers=AUTH).status_code == 403
    assert client.delete("/agents/designer", headers=AUTH).status_code == 403
    # delete the custom one
    assert client.delete("/agents/framer", headers=AUTH).json() == {"ok": True}
    assert not (rt.home / "agents" / "framer.md").exists()


def test_custom_agent_bad_name_rejected(client, rt):
    r = client.post("/agents", json={"name": "../evil", "description": "", "body": "x"},
                    headers=AUTH)
    assert r.status_code == 400
    assert not (rt.home / "agents").exists() or not list((rt.home / "agents").glob("*evil*"))


def test_hide_builtin_via_profile(client, rt):
    client.put("/profile", json={"hidden_agents": ["sim"]}, headers=AUTH)
    agents = {a["name"]: a for a in client.get("/agents", headers=AUTH).json()}
    assert agents["sim"]["hidden"] is True and agents["designer"]["hidden"] is False
