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
    cache = home / "parts" / thumbs.THUMBS_DIR / "box_b" / "64-v1.png"
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
    assert (cdir / "64-v2.png").is_file()
    assert not (cdir / "64-v1.png").exists()


async def test_part_thumb_none_without_mesh(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    (home / "parts" / "nope").mkdir(parents=True)
    assert await thumbs.part_thumb(home, "nope", 1) is None


async def test_project_thumb_composites_parts(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    await _build_part(home, "box_d", BOX)
    await _build_part(home, "plate_d", PLATE)

    png = await thumbs.project_thumb(home, "proj1", [("box_d", 1), ("plate_d", 1)], size=128)
    assert png is not None
    w, h, _ = thumb_render.decode_png(png)
    assert (w, h) == (128, 128)
    assert (home / "parts" / thumbs.THUMBS_DIR / "project-proj1").is_dir()


async def test_project_thumb_none_when_no_meshes(tmp_path):
    home = tmp_path / "home"
    platform.register_sandbox_home(home)
    assert await thumbs.project_thumb(home, "empty", [("ghost", 1)]) is None
