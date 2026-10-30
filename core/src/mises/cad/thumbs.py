"""Part and project thumbnails, rendered in the sandboxed worker and cached by mesh identity.

The render runs through platform.sandboxed_command (thumb_worker.py), so it has no network, no new
processes and cannot read the Mises home except the parts tree; it writes only a throwaway temp
dir. The host then moves the PNG into a host-only cache dir (parts/.mises-thumbs/<id>/, a sibling
of the part dirs that no build can write).

The cache key is the part's version PLUS model.stl's (mtime_ns, size) — the same identity the GLB
route's ETag uses — so a part id reused after Empty Trash (whose version restarts at 1), or a
failed edit restored at the same version, never serves an old picture. The routes build their
ETags from the same key.

Project tiles compose up to four part thumbnails (rendered at half size) into a 2x2 grid on the
host (pure code, no sandbox needed: the inputs are our own PNGs).

Renders are capped at _CONCURRENCY at once, a render already in flight for a key is shared, and
a failed render is remembered for _FAIL_TTL_S so a broken part isn't re-rendered on every visit.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
import stat
import sys
import time
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


def part_key(home: Path, part_id: str, version: int, size: int) -> str | None:
    """The cache/ETag key of a part thumbnail, or None when there is nothing to render."""
    stamp = mesh_stamp(home, part_id)
    return None if stamp is None else f"{clamp_size(size)}-v{version}-{stamp}"


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
        async with _semaphore():
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
            try:
                await asyncio.wait_for(proc.communicate(), _TIMEOUT_S)
            except asyncio.TimeoutError:
                await reap(proc)
                await proc.wait()
                return None
            except BaseException:     # cancelled (shutdown, dropped request): never leak it
                await asyncio.shield(reap(proc))
                raise
            await reap(proc)
            if proc.returncode != 0:
                return None
            return _read_cache(tmp, "thumb.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


async def part_thumb(home: Path, part_id: str, version: int, size: int = DEFAULT_SIZE) \
        -> bytes | None:
    """The part's thumbnail PNG for its current mesh, from cache or a fresh sandboxed render.
    None when the part has no built mesh (model.stl) or the render failed."""
    size = clamp_size(size)
    key = part_key(home, part_id, version, size)
    if key is None:
        return None
    cdir = _cache_dir(home, part_id)
    name = f"{key}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit

    async def make():
        png = await _render(home, Path(home) / "parts" / part_id, size)
        if png is not None:
            _write_cache(cdir, name, png, keep_prefix=f"{size}-v")
        return png

    return await _once((str(home), part_id, key), make)


def project_key(home: Path, parts: list[tuple[str, int]], size: int) -> str:
    """The cache/ETag key of a project tile: its size and the keys of the tiles it composes."""
    cell = max(MIN_SIZE, clamp_size(size) // 2)
    keys = [f"{pid}:{part_key(home, pid, ver, cell)}" for pid, ver in parts[:_MAX_TILES]]
    digest = hashlib.sha256(",".join(keys).encode()).hexdigest()[:16]
    return f"{clamp_size(size)}-{digest}"


async def project_thumb(home: Path, project_id: str, parts: list[tuple[str, int]],
                        size: int = DEFAULT_SIZE) -> bytes | None:
    """A 2x2 composite of up to four of the project's parts, each rendered at half size so the
    grid is a straight copy. Cached by the tiles' own keys, so a rebuild or a new part refreshes
    it. None when no part has a mesh."""
    size = clamp_size(size)
    chosen = parts[:_MAX_TILES]
    cell = max(MIN_SIZE, size // 2)
    key = project_key(home, chosen, size)
    cdir = _cache_dir(home, f"project-{project_id}")
    name = f"{key}.png"
    hit = _read_cache(cdir, name)
    if hit is not None:
        return hit
    tiles = []
    for pid, ver in chosen:
        png = await part_thumb(home, pid, ver, cell)
        if png is not None:
            tiles.append(thumb_render.decode_png(png))
    if not tiles:
        return None
    w, h, rgba = thumb_render.compose_rgba(tiles, size)
    png = thumb_render.encode_png(w, h, rgba)
    _write_cache(cdir, name, png, keep_prefix=f"{size}-")
    return png
