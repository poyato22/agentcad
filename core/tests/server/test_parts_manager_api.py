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


def test_delete_refused_while_in_the_assembly(client, rt):
    _mk_part(rt, "arm")
    pid = rt.active_project_id()
    client.post(f"/projects/{pid}/assembly/instances", json={"part_id": "arm", "name": "arm 1", "fixed": True},
                headers=AUTH)
    r = client.delete("/parts/arm", headers=AUTH)
    assert r.status_code == 400 and "assembly" in r.json()["message"]
    assert (rt.home / "parts" / "arm").exists()


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
