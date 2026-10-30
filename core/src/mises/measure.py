"""Exact measurements of a built local part (Onshape-style measure, Plan 7 U2a).

`measure_part(part_dir, refs)` measures the picked faces / edges / vertices / points on the exact
B-rep (never the display mesh), in the sandboxed CAD worker, which re-reads the part's model.step
(never the part script). Refs name faces and edges by their metrics ids (`f3`, `e7`); the worker
re-finds each one in the STEP shape by its metrics signature.

Speed: a fresh worker pays the build123d import and the STEP import (~1.5 s). So a part being
measured gets a warm worker (`worker.py <part_dir> --measure-serve`) that keeps the shape loaded
and every resolved pick cached, and answers one JSON line per request (~10-50 ms):
  · one per (part dir, model.step mtime): a rebuilt part gets a new one, the old one is closed;
  · at most MAX_WARM live (the least recently used is closed), each closed after IDLE_S idle;
  · it writes nothing (answers go over stdout, marked with a nonce) and is sandboxed to write only
    its tmp dir; closing it closes stdin, kills its group and reaps any stragglers.
A worker that fails or times out is dropped and the one-shot worker (`--measure`) answers instead.
Answers are also cached per (model.step mtime, refs).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from . import edits as _edits
from .cad import safe_io
from .cad.runner import WORKER, _kill_group, reap
from .cad.tools import _lock
from .platform import sandboxed_command
from .types import MisesError

TIMEOUT_S = 30.0
WARM_START_S = 60.0
WARM_CALL_S = 15.0
IDLE_S = 60.0
MAX_WARM = 2
MAX_REFS = 8
MARK = "MISES_MEASURE"
_TAIL = 2000
_CACHE_MAX = 256
_cache: OrderedDict[tuple, dict] = OrderedDict()


class MeasureError(Exception):
    """`code` is a core error code: not_found, invalid_input, stale_version or tool_failed."""

    def __init__(self, code: str, message: str, *, current_version: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_version = current_version


def check_refs(refs: object) -> list[dict]:
    """The refs as the worker takes them, or MeasureError(invalid_input)."""
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_REFS:
        raise MeasureError("invalid_input", f"refs must list 1 to {MAX_REFS} picks")
    out = []
    for r in refs:
        if not isinstance(r, dict) or len(r) != 1:
            raise MeasureError("invalid_input",
                               "each ref is one of {face_id}, {edge_id}, {vertex_id} or {point}")
        (k, v), = r.items()
        if k in ("face_id", "edge_id", "vertex_id"):
            if not isinstance(v, str) or not 1 <= len(v) <= 16:
                raise MeasureError("invalid_input", f"{k} must be an id like 'f3' / 'e7' / 'v0'")
        elif k == "point":
            if (not isinstance(v, list) or len(v) != 3
                    or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v)):
                raise MeasureError("invalid_input", "point must be [x, y, z] in mm")
        else:
            raise MeasureError("invalid_input",
                               f"unknown ref {k!r}: use face_id, edge_id, vertex_id or point")
        out.append({k: v})
    return out


# ---- the warm workers

@dataclass
class _Warm:
    part_dir: Path
    mtime_ns: int
    proc: asyncio.subprocess.Process
    nonce: str
    used: float = field(default_factory=time.monotonic)
    seq: int = 0
    io: asyncio.Lock = field(default_factory=asyncio.Lock)
    reaper: asyncio.Task | None = None
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)


_warm: OrderedDict[str, _Warm] = OrderedDict()
# workers being started (awaiting "ready"): they hold a MAX_WARM slot already
_starting: list[str] = []


async def _close(w: _Warm) -> None:
    if _warm.get(str(w.part_dir)) is w:
        del _warm[str(w.part_dir)]
    if w.loop is not asyncio.get_running_loop():
        # made under another event loop (a finished test's): its pipes are dead here; kill it
        _kill_group(w.proc)
        return
    if w.reaper is not None and w.reaper is not asyncio.current_task():
        w.reaper.cancel()
    with contextlib.suppress(Exception):
        if w.proc.stdin is not None:
            w.proc.stdin.close()
    _kill_group(w.proc)
    with contextlib.suppress(Exception):
        await reap(w.proc)
        await asyncio.wait_for(w.proc.wait(), 5)


async def close_servers() -> None:
    """Close every warm measure worker (the server's shutdown, tests)."""
    for w in list(_warm.values()):
        await _close(w)


async def _idle_reaper(w: _Warm) -> None:
    while True:
        await asyncio.sleep(max(1.0, IDLE_S - (time.monotonic() - w.used)))
        if time.monotonic() - w.used >= IDLE_S and not w.io.locked():
            await _close(w)
            return


async def _read_answer(w: _Warm, want_id: int, timeout_s: float) -> dict:
    """The next marked line for `want_id` (anything else on stdout is skipped)."""
    assert w.proc.stdout is not None
    prefix = f"{MARK} {w.nonce} ".encode()

    async def read() -> dict:
        while True:
            line = await w.proc.stdout.readline()
            if not line:
                raise MeasureError("tool_failed", "the measure worker exited")
            if line.startswith(prefix):
                msg = json.loads(line[len(prefix):])
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
    return await asyncio.wait_for(read(), timeout_s)


async def _start(part_dir: Path, mtime_ns: int) -> _Warm:
    key = str(part_dir)
    old = _warm.get(key)
    if old is not None:
        await _close(old)                          # another version of the part: never reused
    # the slot is reserved before the (up to WARM_START_S) wait for "ready", so concurrent
    # starts of different parts never run more than MAX_WARM workers together
    while len(_warm) + len(_starting) >= MAX_WARM:
        if _warm:
            await _close(next(iter(_warm.values())))  # least recently used
        else:
            await asyncio.sleep(0.05)              # every slot is a start in flight
    _starting.append(key)
    try:
        return await _spawn(part_dir, mtime_ns)
    finally:
        _starting.remove(key)


async def _spawn(part_dir: Path, mtime_ns: int) -> _Warm:
    key = str(part_dir)
    tmp = part_dir / "tmp"
    safe_io.ensure_dir(tmp)
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir), "--measure-serve"],
                           tmp, read_dirs=(part_dir,)),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True, limit=1 << 22)
    w = _Warm(part_dir, mtime_ns, proc, nonce)
    try:
        await _read_answer(w, 0, WARM_START_S)    # "ready": the shape is loaded
    except BaseException:
        await _close(w)
        raise
    prev = _warm.get(key)
    if prev is not None and prev is not w:         # another start of this part won the race
        if prev.mtime_ns == mtime_ns and prev.proc.returncode is None:
            await _close(w)
            return prev
        await _close(prev)
    _warm[key] = w
    w.reaper = asyncio.create_task(_idle_reaper(w))
    return w


async def _ask_warm(part_dir: Path, mtime_ns: int, refs: list[dict]) -> dict:
    w = _warm.get(str(part_dir))
    if (w is None or w.mtime_ns != mtime_ns or w.proc.returncode is not None
            or w.loop is not asyncio.get_running_loop()):
        w = await _start(part_dir, mtime_ns)
    _warm.move_to_end(str(part_dir))
    async with w.io:
        w.seq += 1
        w.used = time.monotonic()
        assert w.proc.stdin is not None
        try:
            w.proc.stdin.write((json.dumps({"id": w.seq, "refs": refs}) + "\n").encode())
            await w.proc.stdin.drain()
            msg = await _read_answer(w, w.seq, WARM_CALL_S)
        except BaseException:
            await _close(w)
            raise
        w.used = time.monotonic()
    msg.pop("id", None)
    return msg


async def warm_part(part_dir: Path) -> bool:
    """Start (or keep) the part's warm worker ahead of the first pick (Measure mode opened)."""
    try:
        part_dir = safe_io.real_dir(part_dir)
    except MisesError:
        return False
    async with _lock(part_dir):          # the STEP's mtime, never mid-rebuild
        try:
            mtime = os.lstat(part_dir / "model.step").st_mtime_ns
        except OSError:
            return False
        w = _warm.get(str(part_dir))
        if (w is not None and w.mtime_ns == mtime and w.proc.returncode is None
                and w.loop is asyncio.get_running_loop()):
            w.used = time.monotonic()
            return True
        if str(part_dir) in _starting:
            return True                  # already being warmed
    # started outside the part lock: a rebuild is never held up for the worker's start (the
    # mtime is captured; a measure checks it again and restarts a worker of an older STEP)
    try:
        await _start(part_dir, mtime)
    except (MeasureError, OSError, asyncio.TimeoutError, ValueError):
        return False
    return True


# ---- one measure

async def measure_part(part_dir: Path, refs: object, *, base_version: int | None = None,
                       timeout_s: float = TIMEOUT_S, warm: bool = True) -> dict:
    """{version, items: [...], values: [{key, label, value, unit}]} for the picks; MeasureError
    otherwise. `base_version` (the version the picks were made on) is checked under the part
    lock, against the same STEP the measure reads: stale → MeasureError("stale_version")."""
    clean = check_refs(refs)
    try:
        part_dir = safe_io.real_dir(part_dir)
    except MisesError as e:
        raise MeasureError("not_found", f"part is not built: {e.message}") from None
    async with _lock(part_dir):          # never while the part is being rebuilt
        version = _edits.read_version(part_dir)
        if base_version is not None and base_version != version:
            raise MeasureError("stale_version", f"the part changed since version {base_version}; "
                               "pick again", current_version=version)
        return {"version": version, **await _measure(part_dir, clean, timeout_s, warm)}


async def _measure(part_dir: Path, refs: list[dict], timeout_s: float, warm: bool) -> dict:
    try:
        os.close(safe_io.open_dir(part_dir))
        safe_io.scrub(part_dir)
    except MisesError as e:
        raise MeasureError("tool_failed", f"measure refused: {e.message}") from None
    try:
        step = os.lstat(part_dir / "model.step")
        os.lstat(part_dir / "metrics.json")
    except OSError:
        raise MeasureError("not_found", "part is not built (no model.step)") from None
    key = (str(part_dir), step.st_mtime_ns, json.dumps(refs, sort_keys=True))
    if key in _cache:
        _cache.move_to_end(key)
        return json.loads(json.dumps(_cache[key]))
    out: dict | None = None
    if warm:
        try:
            out = await _ask_warm(part_dir, step.st_mtime_ns, refs)
        except (MeasureError, OSError, asyncio.TimeoutError, ValueError):
            out = None                   # the one-shot worker answers instead
    if out is None:
        out = await _one_shot(part_dir, refs, timeout_s)
    error = out.get("error")
    if isinstance(error, dict):
        code = error.get("code") if error.get("code") in ("not_found", "invalid_input") else "tool_failed"
        raise MeasureError(code, str(error.get("message") or "measure failed"))
    res = {"items": out.get("items") or [], "values": out.get("values") or []}
    _cache[key] = res
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return json.loads(json.dumps(res))


async def _one_shot(part_dir: Path, refs: list[dict], timeout_s: float) -> dict:
    tmp = part_dir / "tmp"
    safe_io.ensure_dir(tmp)
    safe_io.write_text(tmp, "measure_args.json", json.dumps({"refs": refs}))
    safe_io.unlink(tmp, "measure.json")
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir),
                            str(tmp / "measure_args.json"), "--measure"], tmp,
                           read_dirs=(part_dir,)),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        await reap(proc)
        await proc.wait()
        raise MeasureError("tool_failed", f"measure timed out after {timeout_s:g}s") from None
    except BaseException:
        _kill_group(proc)
        await proc.wait()
        raise
    await reap(proc)
    bad = safe_io.scrub(part_dir)
    if bad:
        raise MeasureError("tool_failed",
                           f"measure refused: links or special files appeared ({', '.join(bad[:5])})")
    if proc.returncode != 0:
        msg = err.decode(errors="replace")[-_TAIL:].strip() or f"worker exited {proc.returncode}"
        raise MeasureError("tool_failed", f"measure failed: {msg}")
    try:
        out = safe_io.read_json(tmp, "measure.json")
        if not isinstance(out, dict) or out.get("nonce") != nonce:
            raise ValueError("nonce mismatch")
    except (OSError, ValueError, MisesError) as e:
        raise MeasureError("tool_failed", f"measure did not complete: {e}") from None
    return out


__all__ = ["MeasureError", "check_refs", "close_servers", "measure_part", "warm_part"]
