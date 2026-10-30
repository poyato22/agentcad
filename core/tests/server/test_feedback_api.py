"""POST /projects/{id}/feedback (U2a's minimal stand-in for U3c's lead routing): a pinned comment
goes to the build's agent as a message, its location inside untrusted-data delimiters."""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from mises.server.app import create_app

from .conftest import AUTH, TOKEN

METRICS = {
    "valid": True, "volume_mm3": 1.0, "area_mm2": 1.0, "mass_g": 1.0,
    "bbox": {"min": [-30, -20, -2.5], "max": [30, 20, 2.5], "size": [60, 40, 5]},
    "center_of_mass": [0, 0, 0], "faces": 2, "edges": 0, "solids": 1, "edge_list": [],
    "face_list": [{"id": "f0", "type": "plane", "center": [0, 0, 2.5], "normal": [0, 0, 1], "area": 2386.1},
                  {"id": "f1", "type": "cylinder", "center": [-20, 0, 0], "normal": None, "area": 65.97, "radius": 2.1}],
    "holes": [{"diameter_mm": 4.2, "axis": [0, 0, 1], "center": [-20, 0, 0], "depth_mm": 5}],
}


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


def _part(rt, project_id: str) -> None:
    d = rt.home / "parts" / "plate"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps(METRICS))
    rt.store.register_part("plate", project_id, label="Base plate")


def _settle(client, run_id: str) -> dict:
    for _ in range(200):
        r = client.get(f"/runs/{run_id}", headers=AUTH).json()
        if r["status"] not in ("running", "queued"):
            return r
        time.sleep(0.02)
    raise AssertionError("run never settled")


def _project(client) -> str:
    return client.get("/projects", headers=AUTH).json()[0]["id"]


def test_feedback_needs_the_token(client):
    assert client.post("/projects/x/feedback", json={}).status_code == 401


def test_feedback_starts_a_run_then_reaches_the_same_one(client, rt):
    pid = _project(client)
    _part(rt, pid)
    r = client.post(f"/projects/{pid}/feedback", headers=AUTH, json={
        "part_id": "plate", "point": [-16.0, 0.0, 2.5], "face_id": "f0", "text": "make this hole bigger"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert r.json()["started"] is True
    run = _settle(client, run_id)
    assert run["project_id"] == pid
    steps = client.get(f"/runs/{run_id}/steps", headers=AUTH).json()
    text = next(s["payload"]["text"] for s in steps if s["payload"].get("role") == "user")
    assert text.startswith("make this hole bigger")
    m = re.search(r'<untrusted_user_data kind="feedback_pin" nonce="([0-9a-f]+)">\n(.*)\n'
                  r'</untrusted_user_data nonce="\1">', text, re.S)
    assert m, text
    loc = json.loads(m.group(2))
    assert loc["part_id"] == "plate" and loc["part"] == "Base plate"
    assert loc["point"] == [-16.0, 0.0, 2.5]
    assert loc["face"] == {"id": "f0", "type": "plane", "normal": [0, 0, 1], "area": 2386.1}
    assert loc["nearby"] == "Ø4.2 hole, 1.9 mm away"
    # the next pin goes to that same agent (the build's newest top-level run)
    r2 = client.post(f"/projects/{pid}/feedback", headers=AUTH, json={
        "part_id": "plate", "point": [0, 0, 2.5], "text": "and round this edge"})
    assert r2.status_code == 200 and r2.json() == {"run_id": run_id, "step_id": None, "started": False}


def test_feedback_refuses_bad_input(client, rt):
    pid = _project(client)
    _part(rt, pid)
    url = f"/projects/{pid}/feedback"
    ok = {"part_id": "plate", "point": [0, 0, 0], "text": "x"}
    assert client.post("/projects/nope/feedback", headers=AUTH, json=ok).status_code == 404
    assert client.post(url, headers=AUTH, json={**ok, "text": "  "}).status_code == 400
    assert client.post(url, headers=AUTH, json={**ok, "text": "x" * 4001}).status_code == 400
    assert client.post(url, headers=AUTH, json={**ok, "point": [0, 0]}).status_code == 400
    assert client.post(url, headers=AUTH, json={**ok, "point": ["a", 0, 0]}).status_code == 400
    assert client.post(url, headers=AUTH, json={**ok, "part_id": "ghost"}).status_code == 404
    assert client.post(url, headers=AUTH, json={**ok, "face_id": "f99"}).status_code == 400
