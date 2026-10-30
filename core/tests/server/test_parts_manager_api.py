"""Plan 7 U8 API: what the parts manager's menu needs — appearance on PATCH /parts/{id}, the
viewer background in the profile, and duplicate / delete / export of a part."""
import json

import pytest
from starlette.testclient import TestClient

from mises import projects
from mises.server.app import create_app

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _mk_part(rt, part_id, project_id=None):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps({"length": 40.0}))
    (d / "metrics.json").write_text(json.dumps({"mass_g": 7.0}))
    (d / "part.py").write_text("VARS = {}\n")
    (d / "model.step").write_text("ISO-10303-21;\n")
    (d / "model.stl").write_bytes(b"solid x\nendsolid x\n")
    run = rt.store.create_run("designer", "fake", "m", "build",
                              project_id=project_id or rt.active_project_id())
    projects.register_built_part(rt.store, run.id, part_id)
    rt.store.update_run(run.id, status="done")          # the build that made it has finished


def test_patch_appearance_and_listing(client, rt):
    _mk_part(rt, "arm")
    r = client.patch("/parts/arm", json={"appearance": {"preset": "polished_metal"}}, headers=AUTH)
    assert r.status_code == 200, r.text
    a = r.json()["appearance"]
    assert a["preset"] == "polished_metal" and a["metalness"] == 1.0
    listed = client.get("/parts", headers=AUTH).json()
    assert listed[0]["appearance"]["preset"] == "polished_metal"
    bad = client.patch("/parts/arm", json={"appearance": {"preset": "gold"}}, headers=AUTH)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_input"
    cleared = client.patch("/parts/arm", json={"appearance": None}, headers=AUTH).json()
    assert cleared["appearance"] is None


def test_profile_viewer_background(client):
    assert client.get("/profile", headers=AUTH).json()["viewer_background"] == {"mode": "theme", "colors": []}
    r = client.put("/profile", json={"viewer_background": {"mode": "gradient", "colors": ["#FFFFFF", "#c9d3de"]}},
                   headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["viewer_background"] == {"mode": "gradient", "colors": ["#ffffff", "#c9d3de"]}
    for bad in ({"mode": "solid", "colors": []}, {"mode": "gradient", "colors": ["#fff000"]},
                {"mode": "plaid", "colors": []}, {"mode": "solid", "colors": ["red"]}):
        # PUT /profile answers an invalid profile with 422 (profile_api._invalid)
        assert client.put("/profile", json={"viewer_background": bad}, headers=AUTH).status_code == 422, bad


def test_duplicate_copies_the_build_and_the_look(client, rt):
    _mk_part(rt, "arm")
    client.patch("/parts/arm", json={"label": "Arm", "appearance": {"preset": "anodized"}}, headers=AUTH)
    r = client.post("/parts/arm/duplicate", headers=AUTH)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["part_id"] == "arm_copy" and row["label"] == "Arm copy"
    assert row["appearance"]["preset"] == "anodized" and row["vars"] == {"length": 40.0}
    assert (rt.home / "parts" / "arm_copy" / "part.py").read_text() == "VARS = {}\n"
    # listed right after the original
    ids = [p["part_id"] for p in client.get("/parts", headers=AUTH).json()]
    assert ids == ["arm", "arm_copy"]
    assert client.post("/parts/arm/duplicate", headers=AUTH).json()["part_id"] == "arm_copy2"
    assert client.post("/parts/nope/duplicate", headers=AUTH).status_code == 404
    # an unlabeled part's copy is named as the app shows it ("top_plate" reads "top plate")
    _mk_part(rt, "top_plate")
    assert client.post("/parts/top_plate/duplicate", headers=AUTH).json()["label"] == "top plate copy"


def test_delete_moves_the_part_to_the_trash_and_lifts_its_children(client, rt):
    _mk_part(rt, "arm")
    _mk_part(rt, "pin")
    client.patch("/parts/pin", json={"parent_id": "arm"}, headers=AUTH)
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": "arm"}
    assert not (rt.home / "parts" / "arm").exists()
    trashed = list((rt.home / "trash" / "parts").iterdir())
    assert len(trashed) == 1 and (trashed[0] / "part.py").exists()
    listed = client.get("/parts", headers=AUTH).json()
    assert [(p["part_id"], p["parent_id"]) for p in listed] == [("pin", None)]
    assert client.delete("/parts/arm", headers=AUTH).status_code == 404


def test_duplicate_keeps_the_skeleton_dependency(client, rt):
    """Final review I4: the copy was built against the same skeleton, so skeleton_set treats
    it like the original (regenerate or mark stale), never as an independent part."""
    from mises import edits
    from mises.cad import skeleton
    _mk_part(rt, "arm")
    md = edits.meta_dir(rt.home / "parts" / "arm")
    md.mkdir(parents=True)
    refs = {"params": ["wing_span"], "skeleton_id": "sk1", "skeleton_version": 3}
    (md / skeleton.REFS_NAME).write_text(json.dumps(refs))
    assert client.post("/parts/arm/duplicate", headers=AUTH).status_code == 200
    copied = skeleton.read_refs(rt.home / "parts" / "arm_copy")
    assert copied == refs and skeleton.uses_skeleton(copied)


def test_delete_refused_while_another_builds_assembly_places_the_part(client, rt):
    """Final review I5: the Empty Trash guard, for a single part."""
    from mises.assembly.model import Instance
    _mk_part(rt, "arm")
    other = rt.store.create_project("Other")
    rt.assembly_store.add_instance(other.id, Instance(part_id="arm", name="borrowed"))
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code == 400 and "another build's assembly" in r.json()["message"]
    assert (rt.home / "parts" / "arm").exists()


def test_delete_moves_the_thumbnails_with_the_part(client, rt):
    """Final review M6: no thumbnails left behind for a deleted part."""
    _mk_part(rt, "arm")
    thumbs = rt.home / "parts" / ".mises-thumbs" / "arm"
    thumbs.mkdir(parents=True)
    (thumbs / "t.png").write_bytes(b"x")
    assert client.delete("/parts/arm", headers=AUTH).status_code == 200
    assert not thumbs.exists()
    assert list((rt.home / "trash" / "parts" / ".mises-thumbs").iterdir())


def test_delete_refused_while_in_the_assembly(client, rt):
    _mk_part(rt, "arm")
    pid = rt.active_project_id()
    client.post(f"/projects/{pid}/assembly/instances", json={"part_id": "arm", "name": "arm 1", "fixed": True},
                headers=AUTH)
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code == 400 and "assembly" in r.json()["message"]
    assert (rt.home / "parts" / "arm").exists()


def test_delete_refused_while_an_agent_may_be_editing_the_part(client, rt):
    from mises.types import Caps
    _mk_part(rt, "arm")
    _mk_part(rt, "pin")
    pid = rt.active_project_id()
    # a live run scoped to another part does not block; one scoped to this part (or "*") does
    other = rt.store.create_run("designer", "fake", "m", "edit pin", project_id=pid)
    rt.store.update_run(other.id, status="running")
    rt.store.create_policy(other.id, ["pin"], ["*"], Caps(), [])
    mine = rt.store.create_run("designer", "fake", "m", "edit arm", project_id=pid)
    rt.store.update_run(mine.id, status="running")
    rt.store.create_policy(mine.id, ["arm"], ["*"], Caps(), [])
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["run_ids"] == [mine.id] and r.json()["reason"] == "running"
    assert (rt.home / "parts" / "arm").exists()
    rt.store.update_run(mine.id, status="done")
    assert client.delete("/parts/arm", headers=AUTH).status_code == 200


def test_delete_refuses_a_link_planted_at_the_trash(client, rt, tmp_path):
    _mk_part(rt, "arm")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (rt.home / "trash").symlink_to(elsewhere)
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code >= 400
    assert list(elsewhere.iterdir()) == []
    assert (rt.home / "parts" / "arm").exists()


def test_agents_get_the_appearance_tool_but_not_the_menus_file_actions(client):
    names = {t["name"] for t in client.get("/tools", headers=AUTH).json()}
    assert "part_set_appearance" in names
    assert not [n for n in names if "duplicate" in n or "delete_part" in n or n.startswith("part_export")]


def test_export_writes_to_the_export_folder(client, rt, tmp_path, monkeypatch):
    out = tmp_path / "Downloads"
    monkeypatch.setenv("MISES_EXPORT_DIR", str(out))
    _mk_part(rt, "arm")
    client.patch("/parts/arm", json={"label": "Arm / v2"}, headers=AUTH)
    r = client.post("/parts/arm/export", json={"format": "step"}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Arm v2.step" and (out / "Arm v2.step").read_text() == "ISO-10303-21;\n"
    # never overwrites: the second export gets a new name
    assert client.post("/parts/arm/export", json={"format": "step"}, headers=AUTH).json()["name"] == "Arm v2 (2).step"
    assert client.post("/parts/arm/export", json={"format": "stl"}, headers=AUTH).json()["name"] == "Arm v2.stl"
    assert client.post("/parts/arm/export", json={"format": "exe"}, headers=AUTH).status_code == 400
    (rt.home / "parts" / "arm" / "model.stl").unlink()
    assert client.post("/parts/arm/export", json={"format": "stl"}, headers=AUTH).status_code == 404
