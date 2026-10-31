"""Thumbnails rendered end-to-end in the sandboxed worker, cached by part version."""
from pathlib import Path

import pytest

from mises import platform
from mises.cad import thumb_render, thumbs
from mises.cad.runner import run_build

pytestmark = pytest.mark.slow

BOX = 'from build123d import *\ndef build(v):\n    return Box(30, 20, 10)\n'
PLATE = ('from build123d import *\n'
         'def build(v):\n'
         '    with BuildPart() as p:\n'
         '        Box(40, 30, 6)\n'
         '        with Locations((10, 0, 0), (-10, 0, 0)):\n'
         '            Hole(2.5)\n'
         '    return p.part\n')


async def _build_part(home: Path, part_id: str, script: str) -> None:
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "part.py").write_text(script)
    r = await run_build(d, {})
    assert r.ok, r.error
    assert (d / "model.stl").exists()


async def test_part_thumb_renders_in_sandbox(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_a", BOX)

    png = await thumbs.part_thumb(home, "box_a", 1, size=96)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, rgba = thumb_render.decode_png(png)
    assert (w, h) == (96, 96)
    assert sum(1 for i in range(3, len(rgba), 4) if rgba[i] > 0) > 96 * 96 * 0.1


async def test_part_thumb_is_cached_by_version(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_b", BOX)

    await thumbs.part_thumb(home, "box_b", 1, size=64)
    cache = (home / "parts" / thumbs.THUMBS_DIR / "box_b"
             / f"{thumbs.part_key(home, 'box_b', 1, 64)}.png")
    assert cache.is_file()
    # a second call serves the cache (no leftover temp render dirs)
    again = await thumbs.part_thumb(home, "box_b", 1, size=64)
    assert again == cache.read_bytes()
    assert not list((home / "parts" / thumbs.THUMBS_DIR / "box_b").glob("tmp-*"))


async def test_part_thumb_new_version_prunes_old(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_c", BOX)

    await thumbs.part_thumb(home, "box_c", 1, size=64)
    await thumbs.part_thumb(home, "box_c", 2, size=64)
    cdir = home / "parts" / thumbs.THUMBS_DIR / "box_c"
    assert [p.name for p in cdir.glob("64-v*.png")] == [f"{thumbs.part_key(home, 'box_c', 2, 64)}.png"]


async def test_part_thumb_none_without_mesh(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    (home / "parts" / "nope").mkdir(parents=True)
    assert await thumbs.part_thumb(home, "nope", 1) is None


def _shift(dx: float) -> list[list[float]]:
    return [[1, 0, 0, dx], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def _alpha_box(png: bytes) -> tuple[int, int, int, int, int, int]:
    """(w, h, x0, y0, x1, y1) of the non-transparent pixels."""
    w, h, rgba = thumb_render.decode_png(png)
    xs, ys = [], []
    for i in range(3, len(rgba), 4):
        if rgba[i] > 0:
            px = i // 4
            xs.append(px % w)
            ys.append(px // w)
    return w, h, min(xs), min(ys), max(xs), max(ys)


async def test_project_cover_renders_the_assembly_not_a_grid(tmp_path, monkeypatch):
    # D24: two instances of one part, placed apart, render as ONE assembly picture in one job,
    # 16:10, framed to the content's bbox (a 2x2 grid is square with a tile per quadrant)
    from mises.cad import render_job
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_g", BOX)
    jobs = []
    real = render_job.run_render_job

    async def spy(*a, **kw):
        jobs.append([(m.name, m.transform) for m in a[1]])
        return await real(*a, **kw)

    monkeypatch.setattr(thumbs, "run_render_job", spy)
    placed = [("box_g", 1, _shift(0), None), ("box_g", 1, _shift(80), None)]
    png = await thumbs.project_cover(home, "proj1", placed, size=320)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(jobs) == 1 and [t for _n, t in jobs[0]] == [_shift(0), _shift(80)]
    w, h, x0, y0, x1, y1 = _alpha_box(png)
    assert (w, h) == (320, 200)
    # framed: the content fills most of the frame and is centred (not one quadrant of a grid)
    assert x1 - x0 > w * 0.6 or y1 - y0 > h * 0.6
    assert abs((x0 + x1) / 2 - w / 2) < w * 0.1 and abs((y0 + y1) / 2 - h / 2) < h * 0.1
    assert (home / "parts" / thumbs.THUMBS_DIR / "project-proj1").is_dir()
    # cached by the assembly + parts: the same placement serves the cache, a moved one re-renders
    assert await thumbs.project_cover(home, "proj1", placed, size=320) == png
    assert len(jobs) == 1
    moved = [placed[0], ("box_g", 1, _shift(40), None)]
    assert thumbs.cover_key(home, moved, 320) != thumbs.cover_key(home, placed, 320)
    await thumbs.project_cover(home, "proj1", moved, size=320)
    assert len(jobs) == 2


async def test_project_cover_none_when_no_meshes(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    assert await thumbs.project_cover(home, "empty", [("ghost", 1, None, None)]) is None


async def test_reused_id_after_purge_gets_a_fresh_thumbnail(tmp_path):
    # Empty Trash removes the part, its meta and its thumbnail cache; the same id rebuilt with
    # different geometry restarts at version 1 and must not be served the old picture
    import shutil

    from mises import projects
    from mises.store import Store
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    home.mkdir(parents=True, exist_ok=True)
    store = Store(home / "mises.db")
    pid = store.create_project("Scrap").id
    await _build_part(home, "base_plate", BOX)
    store.register_part("base_plate", pid)
    first = await thumbs.part_thumb(home, "base_plate", 1, size=64)
    projects.trash_project(store, pid)
    projects.empty_trash(store, home)
    assert not (home / "parts" / thumbs.THUMBS_DIR / "base_plate").exists()
    if (home / "parts" / "base_plate").exists():
        shutil.rmtree(home / "parts" / "base_plate")
    await _build_part(home, "base_plate", PLATE)
    second = await thumbs.part_thumb(home, "base_plate", 1, size=64)
    assert first is not None and second is not None and first != second


async def test_cancelled_render_leaves_no_temp_dir(tmp_path):
    import asyncio
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_e", BOX)
    task = asyncio.ensure_future(thumbs.part_thumb(home, "box_e", 1, size=64))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not list((home / "parts" / thumbs.THUMBS_DIR / "box_e").glob("tmp-*"))


async def test_thumb_uses_g1_render_job_and_the_u8_appearance(tmp_path, monkeypatch):
    """int-678: thumbnails go through G1's bounded render job (render_worker under sandbox-exec,
    capped STL, timeout, RSS watchdog) and draw the part in its appearance; a new look is a new
    cache key, and the background is transparent."""
    from mises.cad import render_job
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_f", BOX)
    calls = []
    real = render_job.run_render_job

    async def spy(*a, **kw):
        calls.append(a[1][0].color)
        return await real(*a, **kw)

    monkeypatch.setattr(thumbs, "run_render_job", spy)
    red = {"preset": "matte", "color": "#ff0000", "metalness": 0, "roughness": 0.8, "opacity": 1}
    plain = await thumbs.part_thumb(home, "box_f", 1, size=64)
    reddish = await thumbs.part_thumb(home, "box_f", 1, size=64, look=red)
    assert calls[0] != calls[1] and calls[1] == (1.0, 0.0, 0.0)
    assert thumbs.part_key(home, "box_f", 1, 64) != thumbs.part_key(home, "box_f", 1, 64, red)
    _, _, a = thumb_render.decode_png(plain)
    _, _, b = thumb_render.decode_png(reddish)
    assert a[3] == 0 and b[3] == 0                                  # a corner: keyed out
    reds = sum(1 for i in range(0, len(b), 4) if b[i + 3] and b[i] > 2 * max(b[i + 1], b[i + 2]))
    assert reds > 64 * 64 * 0.05
    await thumbs.part_thumb(home, "box_f", 1, size=64, look=red)    # cached: no new render
    assert len(calls) == 2
