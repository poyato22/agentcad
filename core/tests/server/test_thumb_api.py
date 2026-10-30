"""Thumbnail routes: GET /parts/{id}/thumb.png and GET /projects/{id}/thumb.png.

Rendering itself is covered (through the real sandbox) by tests/cad/test_thumbs.py. Here we
pre-populate the host cache with real PNGs so the routes resolve without a sandbox spawn, and
check wiring, sizing, ETag/304 and the 404/400 paths.
"""
import json

import pytest
from fastapi.testclient import TestClient

from mises.cad import thumb_render, thumbs
from mises.runtime import Runtime
from mises.server.app import create_app

from .conftest import AUTH, TOKEN

METRICS = {"valid": True, "volume_mm3": 4000.0, "mass_g": 10.8,
           "bbox": {"min": [0, 0, 0], "max": [40, 20, 5], "size": [40, 20, 5]}}


def _png(size: int, shade: int = 180) -> bytes:
    rgba = bytearray()
    for _ in range(size * size):
        rgba += bytes((shade, shade, shade, 255))
    return thumb_render.encode_png(size, size, rgba)


@pytest.fixture
def client(rt: Runtime, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


def make_part(rt: Runtime, part_id: str, *, mesh: bool = True) -> int:
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps({}))
    (d / "metrics.json").write_text(json.dumps(METRICS))
    if mesh:
        (d / "model.stl").write_bytes(b"\x00" * 84)      # a (empty) mesh file need only exist
    pid = rt.active_project_id()
    rt.store.register_part(part_id, pid)
    from pathlib import Path

    from mises import projects
    return projects.part_version(Path(rt.home), rt.store.get_part(part_id))


def cache_part(rt: Runtime, part_id: str, version: int, size: int) -> bytes:
    png = _png(size)
    cdir = rt.home / "parts" / thumbs.THUMBS_DIR / part_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{size}-v{version}.png").write_bytes(png)
    return png


def test_thumb_routes_need_token(client):
    assert client.get("/parts/foo/thumb.png").status_code == 401
    assert client.get("/projects/foo/thumb.png").status_code == 401


def test_part_thumb_serves_cache(client, rt):
    v = make_part(rt, "mount_plate")
    png = cache_part(rt, "mount_plate", v, 256)
    r = client.get("/parts/mount_plate/thumb.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png
    w, h, _ = thumb_render.decode_png(r.content)
    assert (w, h) == (256, 256)


def test_part_thumb_size_param(client, rt):
    v = make_part(rt, "mount_plate")
    cache_part(rt, "mount_plate", v, 128)
    r = client.get("/parts/mount_plate/thumb.png?size=128", headers=AUTH)
    assert r.status_code == 200
    w, h, _ = thumb_render.decode_png(r.content)
    assert (w, h) == (128, 128)


def test_part_thumb_etag_and_304(client, rt):
    v = make_part(rt, "mount_plate")
    cache_part(rt, "mount_plate", v, 256)
    r = client.get("/parts/mount_plate/thumb.png", headers=AUTH)
    etag = r.headers["etag"]
    assert etag
    r2 = client.get("/parts/mount_plate/thumb.png", headers={**AUTH, "If-None-Match": etag})
    assert r2.status_code == 304


def test_part_thumb_404_without_mesh(client, rt):
    make_part(rt, "mount_plate", mesh=False)
    assert client.get("/parts/mount_plate/thumb.png", headers=AUTH).status_code == 404


def test_part_thumb_rejects_bad_id(client):
    assert client.get("/parts/Bad-Id/thumb.png", headers=AUTH).json()["error"] == "invalid_input"


def test_project_thumb_composites_cached_parts(client, rt):
    pid = rt.active_project_id()
    for name in ("alpha", "beta", "gamma"):
        v = make_part(rt, name)
        cache_part(rt, name, v, 256)
    r = client.get(f"/projects/{pid}/thumb.png", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    w, h, _ = thumb_render.decode_png(r.content)
    assert (w, h) == (256, 256)


def test_project_thumb_404_when_no_previews(client, rt):
    pid = rt.active_project_id()
    make_part(rt, "alpha", mesh=False)
    assert client.get(f"/projects/{pid}/thumb.png", headers=AUTH).status_code == 404


def test_project_thumb_404_unknown_build(client):
    assert client.get("/projects/nope123/thumb.png", headers=AUTH).status_code == 404
