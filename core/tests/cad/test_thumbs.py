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


def _png_rgba(w: int, h: int, pixels: bytes) -> bytes:
    from PIL import Image
    import io as _io
    im = Image.frombytes("RGBA", (w, h), pixels)
    buf = _io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _solid_box(png: bytes, solid: int = 128):
    """(w, h, x0, y0, x1, y1) of the silhouette (alpha >= solid), x1/y1 exclusive."""
    w, h, rgba = thumb_render.decode_png(png)
    xs, ys = [], []
    for i in range(3, len(rgba), 4):
        if rgba[i] >= solid:
            px = i // 4
            xs.append(px % w)
            ys.append(px // w)
    return w, h, min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _assert_framed(png: bytes) -> None:
    """The cover's silhouette is centred (+-5% of each side) and fills >= 80% of the tighter
    axis, and the frame's edge rows/columns are fully transparent (the shadow stays inside)."""
    w, h, x0, y0, x1, y1 = _solid_box(png)
    assert abs((x0 + x1) / 2 - w / 2) <= 0.05 * w, (x0, x1, w)
    assert abs((y0 + y1) / 2 - h / 2) <= 0.05 * h, (y0, y1, h)
    assert max((x1 - x0) / w, (y1 - y0) / h) >= 0.80, (x0, y0, x1, y1)
    _w, _h, rgba = thumb_render.decode_png(png)
    edge = [rgba[(y * w + x) * 4 + 3] for x in range(w) for y in (0, h - 1)] + \
           [rgba[(y * w + x) * 4 + 3] for y in range(h) for x in (0, w - 1)]
    assert max(edge) == 0                               # nothing cut at the frame edge


def test_framed_centres_the_silhouette_not_the_shadow():
    # coordinator fix: a long faint shadow to one side and a noise speck far away must not push
    # the model off-centre; the silhouette is centred and fills the tighter axis
    side = 120
    px = bytearray(side * side * 4)
    for y in range(side):
        for x in range(side):
            i = (y * side + x) * 4
            if 10 <= x < 50 and 8 <= y < 40:                 # the model, up and to the left
                px[i:i + 4] = bytes((120, 120, 120, 255))
            elif 30 <= x < 115 and 40 <= y < 110:            # a long, faint shadow down-right
                px[i:i + 4] = bytes((20, 20, 20, 40))
    px[(118 * side + 2) * 4 + 3] = 2                         # a sub-visible speck in a corner
    _assert_framed(thumbs._framed(_png_rgba(side, side, bytes(px)), 320, 200))


def test_framed_keeps_the_shadow_in_alpha_no_opaque_slab():
    # FW-core I-4: the cover render is already transparent; _framed must NOT key by colour or
    # force the soft shadow opaque. A grey, semi-transparent shadow pixel stays semi-transparent.
    side = 40
    px = bytearray(side * side * 4)
    for y in range(side):
        for x in range(side):
            i = (y * side + x) * 4
            if 12 <= x < 28 and 8 <= y < 24:                 # the model: opaque mid-grey
                px[i:i + 4] = bytes((120, 120, 120, 255))
            elif 10 <= x < 30 and 24 <= y < 32:              # a soft ground shadow: part alpha
                px[i:i + 4] = bytes((90, 90, 90, 80))
    framed = thumbs._framed(_png_rgba(side, side, bytes(px)), 320, 200)
    _w, _h, rgba = thumb_render.decode_png(framed)
    alphas = [rgba[i] for i in range(3, len(rgba), 4)]
    partial = sum(1 for a in alphas if 0 < a < 255)
    opaque = sum(1 for a in alphas if a == 255)
    assert partial > 0 and opaque > 0                        # shadow kept as alpha, model solid
    # the shadow never becomes an opaque slab: no fully-opaque pixel is the shadow's grey (90)
    slab = sum(1 for i in range(0, len(rgba), 4)
               if rgba[i + 3] == 255 and 80 <= rgba[i] <= 100
               and abs(rgba[i] - rgba[i + 1]) < 6 and abs(rgba[i] - rgba[i + 2]) < 6)
    assert slab == 0


async def test_project_cover_renders_the_assembly_not_a_grid(tmp_path, monkeypatch):
    # D24: two instances of one part, placed apart, render as ONE assembly picture in one job,
    # 16:10, framed to the content's bbox (a 2x2 grid is square with a tile per quadrant)
    from mises.cad import render_job
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_g", BOX)
    jobs = []
    specs = []
    real = render_job.run_render_job

    async def spy(*a, **kw):
        jobs.append([(m.name, m.transform) for m in a[1]])
        specs.append(kw.get("renders") or (a[2] if len(a) > 2 else None))
        return await real(*a, **kw)

    monkeypatch.setattr(thumbs, "run_render_job", spy)
    placed = [("box_g", 1, _shift(0), None), ("box_g", 1, _shift(80), None)]
    png = await thumbs.project_cover(home, "proj1", placed, size=320)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(jobs) == 1 and [t for _n, t in jobs[0]] == [_shift(0), _shift(80)]
    # FW-core I-4: the cover is the hero product shot, shaded, on a transparent background
    r = specs[0][0]
    assert r["views"] == ["hero"] and r["style"] == "shaded"
    # opaque parts: the shaded path (x-ray flag off) on a transparent background -> shadow in alpha
    assert r["transparent"] is False and r["background"] == "transparent"
    # the soft ground shadow lives in alpha, not an opaque grey slab
    _ww, _hh, rgba = thumb_render.decode_png(png)
    assert any(0 < rgba[i] < 255 for i in range(3, len(rgba), 4))
    w, h, x0, y0, x1, y1 = _alpha_box(png)
    assert (w, h) == (320, 200)
    # framed on the silhouette: centred (+-5%) and filling >= 80% of the tighter axis
    _assert_framed(png)
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
