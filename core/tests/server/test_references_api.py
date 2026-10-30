"""POST/GET /references, and the shape_complexity routing hint on a started run."""
import base64
import io
import time

import pytest
from starlette.testclient import TestClient

from mises.server.app import create_app

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def _png_b64() -> str:
    from PIL import Image
    im = Image.new("RGB", (32, 32), (12, 34, 56))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_post_and_get_reference(client, rt):
    r = client.post("/references", headers=AUTH, json={
        "image": {"data_b64": _png_b64(), "name": "ref.png"},
        "project_id": "pA", "kind": "three_view", "note": "front/top/side"})
    assert r.status_code == 404                     # final review M7: a real build only
    r = client.post("/references", headers=AUTH, json={
        "image": {"data_b64": _png_b64(), "name": "ref.png"},
        "project_id": rt.active_project_id(), "kind": "three_view", "note": "front/top/side"})
    assert r.status_code == 200, r.text
    rid = r.json()["reference_id"]
    assert rid.startswith("ref_") and r.json()["kind"] == "three_view"
    got = client.get(f"/references/{rid}", headers=AUTH)
    assert got.status_code == 200 and got.json()["reference_id"] == rid


def test_get_unknown_reference_404(client):
    assert client.get("/references/ref_000000000000", headers=AUTH).status_code == 404


def test_post_reference_requires_auth(client):
    assert client.post("/references", json={"image": {"data_b64": _png_b64()}}).status_code == 401


def test_started_run_records_shape_complexity(client):
    rid = client.post("/runs", headers=AUTH, json={
        "agent": "designer", "prompt": "model a B-2 bomber, ~1:100 scale"}).json()["run_id"]
    deadline = time.time() + 5
    hint = None
    while time.time() < deadline:
        hint = client.get(f"/runs/{rid}", headers=AUTH).json().get("shape_complexity")
        if hint is not None:
            break
        time.sleep(0.02)
    assert hint is not None and hint["level"] == "high"
    assert hint["vision_recommended"] is True
