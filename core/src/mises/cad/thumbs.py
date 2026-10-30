"""Part and project thumbnails, rendered in the sandboxed worker and cached by part version.

The render runs through platform.sandboxed_command (thumb_worker.py), so it has no network, no new
processes and cannot read the Mises home except the parts tree; it writes only a throwaway temp
dir. The host then moves the PNG into a host-only cache dir (parts/.mises-thumbs/<id>/, a sibling
of the part dirs that no build can write), keyed by the part's version so a rebuild invalidates it.

Project tiles compose up to four part thumbnails into a 2x2 grid on the host (pure code, no
sandbox needed: the inputs are our own PNGs).
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import shutil
import sys
from pathlib import Path

from ..platform import sandboxed_command
from . import safe_io, thumb_render
from .runner import reap

WORKER = Path(__file__).resolve().parent / "thumb_worker.py"
THUMBS_DIR = ".mises-thumbs"          # <home>/parts/.mises-thumbs/<id> — never a valid part id
DEFAULT_SIZE = 256
MIN_SIZE = 16
MAX_SIZE = 1024
_TIMEOUT_S = 30.0
_MAX_TILES = 4


def clamp_size(size: int | None) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size or DEFAULT_SIZE)))


def _cache_dir(home: Path, key: str) -> Path:
    return Path(home) / "parts" / THUMBS_DIR / key


def _read_cache(cdir: Path, name: str) -> bytes | None:
    try:
        return safe_io.read_bytes(cdir, name)
    except (OSError, safe_io.UnsafePath):
        return None


def _write_cache(cdir: Path, name: str, png: bytes, *, keep_prefix: str) -> None:
    """Store the PNG and drop any stale siblings sharing keep_prefix (older versions / keys)."""
    try:
        safe_io.ensure_dir(cdir)
        for old in cdir.glob(f"{keep_prefix}*.png"):
            if old.name != name:
                old.unlink(missing_ok=True)
        safe_io.write_bytes(cdir, name, png)
    except (OSError, safe_io.UnsafePath):
        pass                          # a thumbnail is a cache; a failed write just re-renders


async def _render(home: Path, part_dir: Path, size: int) -> bytes | None:
    """Run the sandboxed worker on part_dir/model.stl; return the PNG bytes or None on failure."""
    base = _cache_dir(home, part_dir.name)
    try:
        safe_io.ensure_dir(base)
    except (OSError, safe_io.UnsafePath):
        return None
    tmp = base / f"tmp-{secrets.token_hex(8)}"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    argv = sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir), str(tmp),
                              str(size)], tmp, home=Path(home))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
        try:
            await asyncio.wait_for(proc.communicate(), _TIMEOUT_S)
        except asyncio.TimeoutError:
            await reap(proc)
            await proc.wait()
            return None
        if proc.returncode != 0:
            return None
        return _read_cache(tmp, "thumb.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def part_thumb(home: Path, part_id: str, version: int, size: int = DEFAULT_SIZE) \
        -> bytes | None:
    """The part's thumbnail PNG at this version, from cache or a fresh sandboxed render. None
    when the part has no built mesh (model.stl) yet."""
    size = clamp_size(size)
    cdir = _cache_dir(home, part_id)
    name = f"{size}-v{version}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit
    stl = Path(home) / "parts" / part_id / "model.stl"
    if not stl.is_file():
        return None
    png = await _render(home, Path(home) / "parts" / part_id, size)
    if png is not None:
        _write_cache(cdir, name, png, keep_prefix=f"{size}-v")
    return png


async def project_thumb(home: Path, project_id: str, parts: list[tuple[str, int]],
                        size: int = DEFAULT_SIZE) -> bytes | None:
    """A 2x2 composite of up to four of the project's parts. Cached by the parts and versions it
    was built from, so a rebuild or a new part refreshes it. None when no part has a mesh."""
    size = clamp_size(size)
    chosen = parts[:_MAX_TILES]
    key = "-".join(f"{pid}:{ver}" for pid, ver in chosen) or "empty"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    cdir = _cache_dir(home, f"project-{project_id}")
    name = f"{size}-{digest}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit
    tiles = []
    for pid, ver in chosen:
        png = await part_thumb(home, pid, ver, size)
        if png is not None:
            tiles.append(thumb_render.decode_png(png))
    if not tiles:
        return None
    w, h, rgba = thumb_render.compose_rgba(tiles, size)
    png = thumb_render.encode_png(w, h, rgba)
    _write_cache(cdir, name, png, keep_prefix=f"{size}-")
    return png
