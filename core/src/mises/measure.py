"""Exact measurements of a built local part (Onshape-style measure, Plan 7 U2a).

`measure_part(part_dir, refs)` runs the sandboxed CAD worker in --measure mode: it re-reads the
part's model.step (never the part script) and measures the picked faces / edges / vertices /
points on the exact B-rep, not the display mesh. Refs name faces and edges by their metrics ids
(`f3`, `e7`); the worker re-finds each one in the STEP shape by its metrics signature.

Answers are cached per (model.step mtime, refs): the worker costs a build123d import, and a user
measuring tends to click the same few things.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from collections import OrderedDict
from pathlib import Path

from .cad import safe_io
from .cad.runner import WORKER, _kill_group, reap
from .cad.tools import _lock
from .platform import sandboxed_command
from .types import MisesError

TIMEOUT_S = 30.0
MAX_REFS = 8
_TAIL = 2000
_CACHE_MAX = 256
_cache: OrderedDict[tuple, dict] = OrderedDict()


class MeasureError(Exception):
    """`code` is a core error code: not_found, invalid_input or tool_failed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


async def measure_part(part_dir: Path, refs: object, *, timeout_s: float = TIMEOUT_S) -> dict:
    """{items: [...], values: [{key, label, value, unit}]} for the picks; MeasureError otherwise."""
    clean = check_refs(refs)
    try:
        part_dir = safe_io.real_dir(part_dir)
    except MisesError as e:
        raise MeasureError("not_found", f"part is not built: {e.message}") from None
    async with _lock(part_dir):          # never while the part is being rebuilt
        return await _measure(part_dir, clean, timeout_s)


async def _measure(part_dir: Path, refs: list[dict], timeout_s: float) -> dict:
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
    tmp = part_dir / "tmp"
    safe_io.ensure_dir(tmp)
    safe_io.write_text(tmp, "measure_args.json", json.dumps({"refs": refs}))
    safe_io.unlink(tmp, "measure.json")
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir),
                            str(tmp / "measure_args.json"), "--measure"], part_dir),
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
    error = out.get("error")
    if isinstance(error, dict):
        code = error.get("code") if error.get("code") in ("not_found", "invalid_input") else "tool_failed"
        raise MeasureError(code, str(error.get("message") or "measure failed"))
    res = {"items": out.get("items") or [], "values": out.get("values") or []}
    _cache[key] = res
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return json.loads(json.dumps(res))


__all__ = ["MeasureError", "check_refs", "measure_part"]
