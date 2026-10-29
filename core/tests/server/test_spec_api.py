"""GET/PUT /projects/{id}/spec and GET /projects/{id}/studies (admin token only)."""
import pytest
from starlette.testclient import TestClient

from mises.server.app import create_app

from .conftest import AUTH, TOKEN

PID = "proj_default"

SPEC = {
    "goal": "5 inch drone frame",
    "requirements": [
        {"id": "mass_budget", "kind": "max_mass_g", "target": 120, "priority": 5,
         "source": "user"},
        {"id": "arm_safety", "kind": "min_safety_factor", "target": 2.0,
         "load_case_id": "crash_drop", "priority": 4},
    ],
    "load_cases": [
        {"id": "crash_drop", "name": "crash", "fix": "bottom", "load": "top",
         "newtons": 120, "direction": "-z", "estimate": True},
    ],
    "answers": {"use": "freestyle"},
}


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def test_spec_routes_need_token(client):
    assert client.get(f"/projects/{PID}/spec").status_code == 401
    assert client.put(f"/projects/{PID}/spec", json=SPEC).status_code == 401
    assert client.get(f"/projects/{PID}/studies").status_code == 401


def test_empty_spec(client):
    r = client.get(f"/projects/{PID}/spec", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["spec"]["requirements"] == []


def test_put_and_get_spec(client):
    r = client.put(f"/projects/{PID}/spec", headers=AUTH, json=SPEC)
    assert r.status_code == 200, r.text
    assert len(r.json()["spec"]["requirements"]) == 2
    got = client.get(f"/projects/{PID}/spec", headers=AUTH).json()
    assert len(got["spec"]["load_cases"]) == 1 and got["spec"]["load_cases"][0]["estimate"]


def test_put_invalid_spec_is_422(client):
    bad = {**SPEC, "load_cases": []}  # arm_safety references a missing load case
    r = client.put(f"/projects/{PID}/spec", headers=AUTH, json=bad)
    assert r.status_code == 422
    assert "invalid spec" in r.json()["message"]


def test_studies_empty(client):
    assert client.get(f"/projects/{PID}/studies", headers=AUTH).json() == []
