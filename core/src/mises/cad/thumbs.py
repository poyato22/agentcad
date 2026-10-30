"""Part and project thumbnails, rendered by Plan 8 G1's bounded renderer and cached by mesh
identity and appearance.

The render is G1's render job (cad/render_job.py: render_worker.py under sandbox-exec, its STL
size-capped before it is copied in, a wall-clock timeout and an RSS watchdog), the same one the
agents' views use, so a hostile mesh can only cost the worker. It draws the part's iso view in
its U8 appearance (appearance.render_params: color and opacity) and its job dir lives in a
host-only cache dir (parts/.mises-thumbs/<id>/, a sibling of the part dirs that no build can
write), as does the PNG. The renderer's flat background is keyed out to transparency.

The cache key is the part's version PLUS model.stl's (mtime_ns, size) — the same identity the GLB
route's ETag uses — so a part id reused after Empty Trash (whose version restarts at 1), or a
failed edit restored at the same version, never serves an old picture — plus a digest of the
appearance, so a new color or finish re-renders. The routes build their ETags from the same key.

Project tiles compose up to four part thumbnails (rendered at half size) into a 2x2 grid on the
host (pure code, no sandbox needed: the inputs are our own PNGs).

Renders are capped at _CONCURRENCY at once, a render already in flight for a key is shared, and
a failed render is remembered for _FAIL_TTL_S so a broken part isn't re-rendered on every visit.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import time
from pathlib import Path

from ..types import MisesError
from . import safe_io, thumb_render
from .render import _BG, _DEFAULT_COLOR
from .render_job import MeshIn, run_render_job

THUMBS_DIR = ".mises-thumbs"          # <home>/parts/.mises-thumbs/<id> — never a valid part id
DEFAULT_SIZE = 256
MIN_SIZE = 16
MAX_SIZE = 1024
_TIMEOUT_S = 30.0
_MAX_TILES = 4
_CONCURRENCY = 2
_FAIL_TTL_S = 60.0

_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None
_inflight: dict[tuple, asyncio.Future] = {}
_failed: dict[tuple, float] = {}


def clamp_size(size: int | None) -> int:
    return max(MIN_SIZE, min(MAX_SIZE, int(size or DEFAULT_SIZE)))


def _semaphore() -> asyncio.Semaphore:
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:     # one per event loop (tests run many)
        _sem, _sem_loop = asyncio.Semaphore(_CONCURRENCY), loop
    return _sem


def _cache_dir(home: Path, key: str) -> Path:
    return Path(home) / "parts" / THUMBS_DIR / key


def mesh_stamp(home: Path, part_id: str) -> str | None:
    """`<mtime_ns>-<size>` of the part's model.stl, stat'ed without following links and only
    inside the parts tree; None when the part has no mesh."""
    root = Path(home) / "parts"
    try:
        d = safe_io.real_dir(root / part_id)
        if not d.resolve().is_relative_to(root.resolve()):
            return None
        st = os.lstat(d / "model.stl")
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return f"{st.st_mtime_ns}-{st.st_size}"


def _look_digest(look: dict | None) -> str:
    """A short digest of what the render reads from the appearance (color, opacity)."""
    k = {"color": (look or {}).get("color"), "opacity": float((look or {}).get("opacity") or 1.0)}
    if k["color"] is None and k["opacity"] == 1.0:
        return "plain"                 # the default look: the renderer's grey, opaque
    return hashlib.sha256(json.dumps(k, sort_keys=True).encode()).hexdigest()[:10]


def part_key(home: Path, part_id: str, version: int, size: int,
             look: dict | None = None) -> str | None:
    """The cache/ETag key of a part thumbnail, or None when there is nothing to render. `look`
    is appearance.render_params(row) (U8)."""
    stamp = mesh_stamp(home, part_id)
    return None if stamp is None else f"{clamp_size(size)}-v{version}-{stamp}-{_look_digest(look)}"


def _read_cache(cdir: Path, name: str) -> bytes | None:
    try:
        return safe_io.read_bytes(cdir, name)
    except (OSError, safe_io.UnsafePath):
        return None


def _write_cache(cdir: Path, name: str, png: bytes, *, keep_prefix: str) -> None:
    """Store the PNG and drop any stale siblings sharing keep_prefix (older keys)."""
    try:
        safe_io.ensure_dir(cdir)
        for old in cdir.glob(f"{keep_prefix}*.png"):
            if old.name != name:
                old.unlink(missing_ok=True)
        safe_io.write_bytes(cdir, name, png)
    except (OSError, safe_io.UnsafePath):
        pass                          # a thumbnail is a cache; a failed write just re-renders


def _rgb(hex_color: str | None) -> tuple[float, float, float]:
    try:
        h = str(hex_color).lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))   # type: ignore[return-value]
    except (TypeError, ValueError):
        return _DEFAULT_COLOR


# G1's renderer draws crease lines from per-pixel depth jumps, tuned for view-sized images: a
# thumbnail renders at least this big and is scaled down on the host
_RENDER_MIN = 256


def _keyed_rgba(png: bytes, size: int) -> bytes:
    """The renderer's RGB PNG as one of our RGBA PNGs (thumb_render.decode_png reads it) of
    `size`: its flat background color made transparent, then scaled down. The PNG comes from our
    own worker, size-capped."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as src:
        im = src.convert("RGBA")
    raw = bytearray(im.tobytes())
    bg = bytes(_BG)
    for i in range(0, len(raw), 4):
        if raw[i:i + 3] == bg:
            raw[i + 3] = 0
    im = Image.frombytes("RGBA", im.size, bytes(raw))
    if im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    return thumb_render.encode_png(size, size, im.tobytes())


async def _render(home: Path, part_dir: Path, size: int, look: dict | None = None) -> bytes | None:
    """The part's iso view from G1's sandboxed render job; the PNG bytes or None on failure."""
    base = _cache_dir(home, part_dir.name)
    try:
        safe_io.ensure_dir(base)
    except (OSError, safe_io.UnsafePath):
        return None
    look = look or {}
    mesh = MeshIn(part_dir, part_dir.name, _rgb(look.get("color")),
                  float(look.get("opacity") or 1.0))
    try:
        async with _semaphore():
            out = await run_render_job(base / "jobs", [mesh], renders=[{
                "key": "t", "views": ["iso"], "size": max(size, _RENDER_MIN),
                "transparent": mesh.opacity < 1.0}])
    except (MisesError, OSError, safe_io.UnsafePath):
        return None
    png = (out.pngs.get("t") or {}).get("iso")
    if png is None:
        return None
    try:
        return await asyncio.to_thread(_keyed_rgba, png, size)
    except (OSError, ValueError):
        return None


async def _once(key: tuple, make):
    """Share one in-flight computation per key; remember failures for _FAIL_TTL_S."""
    when = _failed.get(key)
    if when is not None and time.monotonic() - when < _FAIL_TTL_S:
        return None
    fut = _inflight.get(key)
    if fut is not None and fut.get_loop() is asyncio.get_running_loop():
        return await asyncio.shield(fut)
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        out = await make()
    except BaseException as e:
        fut.set_exception(e)
        fut.exception()               # mark retrieved: waiters re-raise, nobody logs it twice
        raise
    finally:
        if _inflight.get(key) is fut:
            del _inflight[key]
    if out is None:
        _failed[key] = time.monotonic()
    else:
        _failed.pop(key, None)
    fut.set_result(out)
    return out


async def part_thumb(home: Path, part_id: str, version: int, size: int = DEFAULT_SIZE,
                     look: dict | None = None) -> bytes | None:
    """The part's thumbnail PNG for its current mesh and appearance (`look`, from
    appearance.render_params), from cache or a fresh sandboxed render. None when the part has
    no built mesh (model.stl) or the render failed."""
    size = clamp_size(size)
    key = part_key(home, part_id, version, size, look)
    if key is None:
        return None
    cdir = _cache_dir(home, part_id)
    name = f"{key}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit

    async def make():
        png = await _render(home, Path(home) / "parts" / part_id, size, look)
        if png is not None:
            _write_cache(cdir, name, png, keep_prefix=f"{size}-v")
        return png

    return await _once((str(home), part_id, key), make)


def project_key(home: Path, parts: list[tuple[str, int]], size: int,
                looks: dict[str, dict] | None = None) -> str:
    """The cache/ETag key of a project tile: its size and the keys of the tiles it composes."""
    cell = max(MIN_SIZE, clamp_size(size) // 2)
    looks = looks or {}
    keys = [f"{pid}:{part_key(home, pid, ver, cell, looks.get(pid))}"
            for pid, ver in parts[:_MAX_TILES]]
    digest = hashlib.sha256(",".join(keys).encode()).hexdigest()[:16]
    return f"{clamp_size(size)}-{digest}"


async def project_thumb(home: Path, project_id: str, parts: list[tuple[str, int]],
                        size: int = DEFAULT_SIZE,
                        looks: dict[str, dict] | None = None) -> bytes | None:
    """A 2x2 composite of up to four of the project's parts, each rendered at half size so the
    grid is a straight copy. Cached by the tiles' own keys, so a rebuild or a new part refreshes
    it. None when no part has a mesh."""
    size = clamp_size(size)
    chosen = parts[:_MAX_TILES]
    cell = max(MIN_SIZE, size // 2)
    looks = looks or {}
    key = project_key(home, chosen, size, looks)
    cdir = _cache_dir(home, f"project-{project_id}")
    name = f"{key}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit
    tiles = []
    for pid, ver in chosen:
        png = await part_thumb(home, pid, ver, cell, looks.get(pid))
        if png is not None:
            tiles.append(thumb_render.decode_png(png))
    if not tiles:
        return None
    w, h, rgba = thumb_render.compose_rgba(tiles, size)
    png = thumb_render.encode_png(w, h, rgba)
    _write_cache(cdir, name, png, keep_prefix=f"{size}-")
    return png
