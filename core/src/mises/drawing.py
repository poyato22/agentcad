"""2D engineering drawings of the local build123d parts.

`mirror_drawing(part_dir, ...)` runs the sandboxed CAD worker in --drawing mode (it re-reads the
part's model.step, never the part script) and leaves drawing.svg, drawing.dxf and drawing.json
(sheet facts) in the part directory. The sheet is A4 landscape, third-angle: front, top, right and
an isometric view, overall dimensions from the bbox, hole callouts, a title block.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from datetime import date as _date

from pydantic import BaseModel, Field

from .heavy import heavy_slot
from .cad import safe_io
from .cad.runner import WORKER, _kill_group, reap
from .cad.tools import _check_part_id, _lock
from .platform import sandboxed_command
from .profile import load_profile
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, ToolOutput

TIMEOUT_S = 60.0
MATERIAL_RE = re.compile(r"^[A-Za-z0-9 ._+-]{1,24}$")
_TAIL = 2000


class DrawingError(Exception):
    pass


@dataclass
class Drawing:
    svg_path: Path
    dxf_path: Path
    info: dict


def _plain(part_dir: Path, name: str) -> os.stat_result | None:
    """lstat of a plain, singly-linked file in the part dir; None for anything else."""
    try:
        st = os.lstat(part_dir / name)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else None


def _cached(part_dir: Path, want: dict) -> dict | None:
    try:
        info = safe_io.read_json(part_dir, "drawing.json")
    except (OSError, ValueError, MisesError):
        return None
    step = _plain(part_dir, "model.step")
    if not isinstance(info, dict) or step is None:
        return None
    if _plain(part_dir, "drawing.svg") is None or _plain(part_dir, "drawing.dxf") is None:
        return None
    if info.get("step_mtime_ns") != step.st_mtime_ns or any(info.get(k) != v
                                                            for k, v in want.items()):
        return None
    info.pop("nonce", None)
    return info


async def mirror_drawing(part_dir: Path, *, label: str, material: str, date: str,
                         timeout_s: float = TIMEOUT_S) -> Drawing:
    # The part dir's own last component is never resolved: a link there is refused (safe_io).
    part_dir = safe_io.real_dir(part_dir)
    async with _lock(part_dir):  # not while the part is being rebuilt
        return await _mirror_drawing(part_dir, label=label, material=material, date=date,
                                     timeout_s=timeout_s)


async def _mirror_drawing(part_dir: Path, *, label: str, material: str, date: str,
                          timeout_s: float) -> Drawing:
    try:
        os.close(safe_io.open_dir(part_dir))
        safe_io.scrub(part_dir)              # nothing a build left may redirect the writes below
    except MisesError as e:
        raise DrawingError(f"drawing refused: {e.message}") from None
    if _plain(part_dir, "model.step") is None or _plain(part_dir, "metrics.json") is None:
        raise DrawingError("part is not built (no model.step); build it with mirror_part_create")
    want = {"label": label, "material": material, "date": date}
    info = _cached(part_dir, want)
    if info is not None:
        return Drawing(part_dir / "drawing.svg", part_dir / "drawing.dxf", info)
    tmp = part_dir / "tmp"
    safe_io.ensure_dir(tmp)
    args_path = tmp / "drawing_args.json"
    safe_io.write_text(tmp, args_path.name, json.dumps(want))      # replaces a link, never follows
    safe_io.unlink(part_dir, "drawing.json")
    nonce = secrets.token_hex(16)
    env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1"}
    async with heavy_slot("drawing"):   # one of the global heavy-worker slots (M9)
        proc = await asyncio.create_subprocess_exec(
            *sandboxed_command([sys.executable, "-I", str(WORKER), str(part_dir), str(args_path),
                                "--drawing"], part_dir),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError:
            await reap(proc)
            await proc.wait()
            raise DrawingError(f"drawing timed out after {timeout_s:g}s; the part is too complex") from None
        except BaseException:
            _kill_group(proc)
            await proc.wait()
            raise
        await reap(proc)
    bad = safe_io.scrub(part_dir)
    if bad:
        raise DrawingError(f"drawing refused: links or special files appeared ({', '.join(bad[:5])})")
    if proc.returncode != 0:
        msg = err.decode(errors="replace")[-_TAIL:].strip() or f"worker exited {proc.returncode}"
        raise DrawingError(f"drawing failed: {msg}")
    try:
        info = safe_io.read_json(part_dir, "drawing.json")
        if not isinstance(info, dict) or info.get("nonce") != nonce:
            raise ValueError("nonce mismatch")
    except (OSError, ValueError, MisesError) as e:
        raise DrawingError(f"drawing did not complete: {e}") from None
    info.pop("nonce", None)
    return Drawing(part_dir / "drawing.svg", part_dir / "drawing.dxf", info)


# ---- the agent tool

class DrawingArgs(BaseModel):
    part_id: str
    material: str | None = Field(default=None, pattern=MATERIAL_RE.pattern)


@tool("mirror_drawing", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id", timeout_s=120,
      description="Make a 2D engineering drawing (SVG + DXF) of a built local part: front, "
                  "top, right and isometric views, overall dimensions, hole callouts and a "
                  "title block. Returns file handles and the sheet's facts.")
async def mirror_drawing_tool(ctx: ToolContext, args: DrawingArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    rt = (ctx.services or {}).get("runtime")
    material = args.material or (load_profile(rt.home).default_material if rt else "PLA")
    try:
        d = await mirror_drawing(Path(ctx.workdir) / "parts" / args.part_id,
                                 label=args.part_id, material=material,
                                 date=_date.today().isoformat())
    except DrawingError as e:
        raise MisesError(ErrorCode.TOOL_FAILED, str(e)) from None
    return ToolOutput(summary=f"{args.part_id}: drawing at {d.info['scale_text']} "
                              f"({len(d.info['views'])} views)",
                      handles=[str(d.svg_path), str(d.dxf_path)],
                      data={"part_id": args.part_id, **d.info})


def register_drawing_tools(registry: ToolRegistry) -> None:
    registry.register(mirror_drawing_tool)
