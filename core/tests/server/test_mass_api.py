"""Plan 9 B2 (D8) API: PATCH /parts/{id} {material}, mass fields on GET /parts, the assembly's
total mass, and part_set_material in the tool list."""
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


def _mk_part(rt, part_id, volume):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text("{}")
    (d / "part.py").write_text("VARS = {}\n")
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": volume, "mass_g": 99.0}))
    run = rt.store.create_run("designer", "fake", "m", "build", project_id=rt.active_project_id())
    projects.register_built_part(rt.store, run.id, part_id)


def test_patch_material_and_mass_in_the_listing(client, rt):
    _mk_part(rt, "arm", 1000.0)
    listed = {p["id"]: p for p in client.get("/parts", headers=AUTH).json()}
    assert listed["arm"]["mass_g"] == pytest.approx(1.24)
    assert listed["arm"]["mass_source"] == "built" and listed["arm"]["material"] is None
    r = client.patch("/parts/arm", json={"material": "PETG"}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["material"] == "PETG" and r.json()["mass_g"] == pytest.approx(1.27)
    bad = client.patch("/parts/arm", json={"material": "cheese"}, headers=AUTH)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_input"
    assert client.patch("/parts/arm", json={"material": None},
                        headers=AUTH).json()["material"] is None


def test_assembly_reports_its_total_mass(client, rt):
    _mk_part(rt, "wheel", 1000.0)
    pid = rt.active_project_id()
    for n in range(2):
        r = client.post(f"/projects/{pid}/assembly/instances",
                        json={"part_id": "wheel", "name": f"w{n}"}, headers=AUTH)
        assert r.status_code == 201, r.text
    a = client.get(f"/projects/{pid}/assembly", headers=AUTH).json()
    assert a["total_mass_g"] == pytest.approx(2.48) and a["mass_unknown"] == []


def test_part_set_material_is_a_tool(client):
    names = [t["name"] for t in client.get("/tools", headers=AUTH).json()]
    assert "part_set_material" in names


def test_the_profile_default_material_must_be_a_table_material(client):
    bad = client.put("/profile", json={"default_material": "PLA+"}, headers=AUTH)
    assert bad.status_code == 422 and "PLA+" in bad.json()["message"]
    assert "PETG" in bad.json()["message"]
    ok = client.put("/profile", json={"default_material": "petg"}, headers=AUTH)
    assert ok.status_code == 200 and ok.json()["default_material"] == "PETG"
