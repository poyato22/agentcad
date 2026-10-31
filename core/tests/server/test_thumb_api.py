"""Thumbnail routes: GET /parts/{id}/thumb.png and GET /projects/{id}/thumb.png.

Rendering itself is covered (through the real sandbox) by tests/cad/test_thumbs.py. Here we
pre-populate the host cache with real PNGs so the routes resolve without a sandbox spawn, and
check wiring, sizing, ETag/304 and the 404/400 paths.
"""
import io
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
    (cdir / f"{thumbs.part_key(rt.home, part_id, version, size)}.png").write_bytes(png)
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


def test_part_thumb_etag_follows_the_mesh_not_only_the_version(client, rt):
    # same id, same version, different model.stl (a reused id after Empty Trash, or a failed
    # edit restored at the same version): the ETag must change so no stale image is revalidated
    v = make_part(rt, "mount_plate")
    cache_part(rt, "mount_plate", v, 256)
    etag = client.get("/parts/mount_plate/thumb.png", headers=AUTH).headers["etag"]
    (rt.home / "parts" / "mount_plate" / "model.stl").write_bytes(b"\x00" * 134)
    cache_part(rt, "mount_plate", v, 256)
    r = client.get("/parts/mount_plate/thumb.png", headers={**AUTH, "If-None-Match": etag})
    assert r.status_code == 200
    assert r.headers["etag"] != etag


def test_part_thumb_404_without_mesh(client, rt):
    make_part(rt, "mount_plate", mesh=False)
    assert client.get("/parts/mount_plate/thumb.png", headers=AUTH).status_code == 404


def test_part_thumb_rejects_bad_id(client):
    assert client.get("/parts/Bad-Id/thumb.png", headers=AUTH).json()["error"] == "invalid_input"


def _fake_render(jobs: list):
    """A stand-in for the sandboxed render job: records the meshes, answers the renderer's flat
    background with a grey block in the middle (what the cover keys out and frames)."""
    from PIL import Image

    from mises.cad.render import _BG
    from mises.cad.render_job import RenderOut

    async def fake(_root, meshes, *, renders=(), **_kw):
        jobs.append([(m.name, m.transform, m.color) for m in meshes])
        size = renders[0]["size"]
        im = Image.new("RGB", (size, size), _BG)
        im.paste((90, 90, 90), (size // 4, size // 3, 3 * size // 4, 2 * size // 3))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return RenderOut(pngs={renders[0]["key"]: {"iso": buf.getvalue()}})
    return fake


def _shift(dx: float) -> list[list[float]]:
    return [[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def test_project_thumb_renders_the_assembly(client, rt, monkeypatch):
    # D24: the cover is one render of the build's assembly (every instance at its pose), 16:10,
    # never a 2x2 grid of parts
    from mises.assembly.model import Instance
    pid = rt.active_project_id()
    for name in ("alpha", "beta"):
        make_part(rt, name)
    rt.assembly_store.add_instance(pid, Instance(part_id="alpha", name="a1", transform=_shift(0)))
    rt.assembly_store.add_instance(pid, Instance(part_id="alpha", name="a2", transform=_shift(60)))
    jobs: list = []
    monkeypatch.setattr(thumbs, "run_render_job", _fake_render(jobs))
    r = client.get(f"/projects/{pid}/thumb.png", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert len(jobs) == 1
    assert [(n, t) for n, t, _c in jobs[0]] == [("alpha", _shift(0)), ("alpha", _shift(60))]
    w, h, rgba = thumb_render.decode_png(r.content)
    assert (w, h) == (thumbs.COVER_W, thumbs.COVER_W * 10 // 16)
    assert rgba[3] == 0                                     # background keyed out (themed)
    etag = r.headers["etag"]
    again = client.get(f"/projects/{pid}/thumb.png", headers={**AUTH, "If-None-Match": etag})
    assert again.status_code == 304
    # moving an instance changes the cover's key
    inst = rt.assembly_store.get(pid).instances[1]
    rt.assembly_store.update_instance(pid, inst.id, transform=_shift(90))
    r2 = client.get(f"/projects/{pid}/thumb.png", headers={**AUTH, "If-None-Match": etag})
    assert r2.status_code == 200 and r2.headers["etag"] != etag


def test_project_thumb_falls_back_to_the_largest_part(client, rt, monkeypatch):
    pid = rt.active_project_id()
    make_part(rt, "small")
    make_part(rt, "large")
    d = rt.home / "parts" / "large"
    (d / "metrics.json").write_text(json.dumps({**METRICS, "volume_mm3": 90000.0}))
    jobs: list = []
    monkeypatch.setattr(thumbs, "run_render_job", _fake_render(jobs))
    r = client.get(f"/projects/{pid}/thumb.png", headers=AUTH)
    assert r.status_code == 200
    assert [(n, t) for n, t, _c in jobs[0]] == [("large", None)]


def test_project_thumb_404_when_no_previews(client, rt):
    pid = rt.active_project_id()
    make_part(rt, "alpha", mesh=False)
    assert client.get(f"/projects/{pid}/thumb.png", headers=AUTH).status_code == 404


def test_project_thumb_404_unknown_build(client):
    assert client.get("/projects/nope123/thumb.png", headers=AUTH).status_code == 404


def test_projects_last_edited_follows_part_and_run_activity(client, rt):
    import os
    pid = rt.active_project_id()
    row = next(p for p in client.get("/projects", headers=AUTH).json() if p["id"] == pid)
    base = row["last_edited_at"]
    assert base >= row["updated_at"]
    make_part(rt, "alpha")
    later = base + 5000
    os.utime(rt.home / "parts" / "alpha" / "model.stl", (later, later))
    row = next(p for p in client.get("/projects", headers=AUTH).json() if p["id"] == pid)
    assert row["last_edited_at"] == pytest.approx(later)
    assert row["updated_at"] < later            # the row's own meaning is unchanged


def test_empty_trash_purges_thumbnail_caches(rt):
    from mises import projects
    pid = rt.store.create_project("Scrap").id
    d = rt.home / "parts" / "scrap_part"
    d.mkdir(parents=True)
    (d / "vars.json").write_text("{}")
    rt.store.register_part("scrap_part", pid)
    for key in ("scrap_part", f"project-{pid}"):
        c = rt.home / "parts" / thumbs.THUMBS_DIR / key
        c.mkdir(parents=True)
        (c / "256-v1-1-1.png").write_bytes(b"x")
    projects.trash_project(rt.store, pid)
    projects.empty_trash(rt.store, rt.home)
    assert not (rt.home / "parts" / thumbs.THUMBS_DIR / "scrap_part").exists()
    assert not (rt.home / "parts" / thumbs.THUMBS_DIR / f"project-{pid}").exists()
