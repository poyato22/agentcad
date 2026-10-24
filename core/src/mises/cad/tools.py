"""mirror_* tools: iterate on parts locally with build123d instead of spending Onshape calls."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..naming import check_name
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from .runner import run_build

PART_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_locks: dict[str, asyncio.Lock] = {}


def _lock(part_dir: Path) -> asyncio.Lock:
    # keyed on the real path: every run reaches the shared parts dir through its own
    # <workdir>/parts symlink, so the unresolved path differs per run
    return _locks.setdefault(str(Path(part_dir).resolve()), asyncio.Lock())


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _check_part_id(part_id: str) -> None:
    if not PART_ID_RE.match(part_id):
        raise _bad(f"part_id {part_id!r} must match {PART_ID_RE.pattern}",
                   "lowercase letters, digits, underscores; start with a letter")
    check_name(part_id)


def _check_vars(vars: dict[str, float]) -> None:
    for name, value in vars.items():
        check_name(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _bad(f"var {name!r} must be a number")


def _part_dir(ctx: ToolContext, part_id: str) -> Path:
    return Path(ctx.workdir) / "parts" / part_id


def _existing(ctx: ToolContext, part_id: str) -> Path:
    _check_part_id(part_id)
    d = _part_dir(ctx, part_id)
    if not (d / "metrics.json").exists():
        raise MisesError(ErrorCode.NOT_FOUND, f"no built part {part_id!r}",
                         hint="create it with mirror_part_create")
    return d


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


_OUTPUTS = ("part.py", "vars.json", "metrics.json", "model.step", "model.stl", "model.glb")


def _snapshot(d: Path) -> dict[str, bytes]:
    return {n: (d / n).read_bytes() for n in _OUTPUTS if (d / n).exists()}


def _restore(d: Path, snap: dict[str, bytes]) -> None:
    """Undo anything a failed (possibly hostile) build left behind in the part dir."""
    for n in _OUTPUTS:
        if n in snap:
            (d / n).write_bytes(snap[n])
        else:
            (d / n).unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class CreateArgs(BaseModel):
    part_id: str
    script: str = Field(description="Python source defining VARS: dict[str, float] and "
                        "build(v: dict) returning a build123d Part or Compound")
    vars: dict[str, float] = Field(default_factory=dict)


class SetVarsArgs(BaseModel):
    part_id: str
    vars: dict[str, float]


class PartArgs(BaseModel):
    part_id: str


class ExportArgs(BaseModel):
    part_id: str
    format: Literal["step", "stl", "glb"]


class ListArgs(BaseModel):
    pass


def _fail(summary: str, stdout: str = "") -> ToolOutput:
    return ToolOutput(ok=False, summary=summary, data={"stdout_tail": stdout},
                      step_kind=StepKind.ERROR)


@tool("mirror_part_create", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      description="Create (or replace) a local build123d part from a script and build it. "
                  "Returns metrics (volume, mass, bbox, holes). A failed build returns ok=false "
                  "with the error so you can fix the script.")
async def mirror_part_create(ctx: ToolContext, args: CreateArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    _check_vars(args.vars)
    d = _part_dir(ctx, args.part_id)
    async with _lock(d):
        d.mkdir(parents=True, exist_ok=True)
        snap = _snapshot(d)
        (d / "part.py").write_text(args.script)
        res = await run_build(d, args.vars)
        if not res.ok:
            if not snap:
                shutil.rmtree(d, ignore_errors=True)
            else:
                _restore(d, snap)
            return _fail(res.error or "build failed", res.stdout_tail)
        _atomic_write(d / "vars.json", json.dumps(res.vars))
    return ToolOutput(summary=_summary(args.part_id, res.metrics),
                      data={"part_id": args.part_id, "vars": res.vars, **res.metrics})


@tool("mirror_part_set_vars", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      description="Change some variables of a local part and rebuild. Returns metrics plus a "
                  "delta against the previous build. A failed rebuild keeps the previous state.")
async def mirror_part_set_vars(ctx: ToolContext, args: SetVarsArgs) -> ToolOutput:
    d = _existing(ctx, args.part_id)
    _check_vars(args.vars)
    async with _lock(d):
        current = _read_json(d / "vars.json")
        unknown = sorted(set(args.vars) - set(current))
        if unknown:
            raise _bad(f"unknown variables {unknown}; known: {sorted(current)}")
        new_vars = {**current, **args.vars}
        prev = _read_json(d / "metrics.json")
        snap = _snapshot(d)
        res = await run_build(d, new_vars)
        if not res.ok:
            _restore(d, snap)
            return _fail(res.error or "build failed", res.stdout_tail)
        _atomic_write(d / "vars.json", json.dumps(res.vars))
        new_vars = res.vars
    m = res.metrics
    delta = {"volume_mm3": m["volume_mm3"] - prev["volume_mm3"],
             "mass_g": m["mass_g"] - prev["mass_g"],
             "bbox_size": [a - b for a, b in zip(m["bbox"]["size"], prev["bbox"]["size"])]}
    return ToolOutput(summary=f"{_summary(args.part_id, m)}; volume delta {delta['volume_mm3']:+.1f} mm3",
                      data={"part_id": args.part_id, "vars": new_vars, "delta": delta, **m},
                      step_kind=StepKind.DIFF)


@tool("mirror_measure", safety=SafetyClass.READ, scope_arg="part_id",
      description="Return the stored metrics of a built local part.")
async def mirror_measure(ctx: ToolContext, args: PartArgs) -> ToolOutput:
    d = _existing(ctx, args.part_id)
    m = _read_json(d / "metrics.json")
    return ToolOutput(summary=_summary(args.part_id, m), data={"part_id": args.part_id, **m},
                      step_kind=StepKind.MEASURE)


@tool("mirror_export", safety=SafetyClass.READ, scope_arg="part_id",
      description="Return a file handle for the part's STEP, STL or GLB export.")
async def mirror_export(ctx: ToolContext, args: ExportArgs) -> ToolOutput:
    d = _existing(ctx, args.part_id)
    f = d / f"model.{args.format}"
    if not f.exists():
        raise MisesError(ErrorCode.NOT_FOUND, f"{f.name} missing; rebuild the part")
    return ToolOutput(summary=f"{args.part_id}: {args.format} export", handles=[str(f)],
                      data={"part_id": args.part_id, "format": args.format})


@tool("mirror_part_list", safety=SafetyClass.READ,
      description="List local parts and their variables.")
async def mirror_part_list(ctx: ToolContext, args: ListArgs) -> ToolOutput:
    root = Path(ctx.workdir) / "parts"
    parts = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if (d / "vars.json").exists():
                parts.append({"part_id": d.name, "vars": _read_json(d / "vars.json")})
    return ToolOutput(summary=f"{len(parts)} part(s)", data={"parts": parts})


def _summary(part_id: str, m: dict) -> str:
    s = m["bbox"]["size"]
    return (f"{part_id}: volume {m['volume_mm3']:.1f} mm3, mass {m['mass_g']:.2f} g, "
            f"bbox {s[0]:.1f}x{s[1]:.1f}x{s[2]:.1f} mm, {len(m['holes'])} hole(s), "
            f"valid={m['valid']}")


def register_mirror_tools(registry: ToolRegistry) -> None:
    for t in (mirror_part_create, mirror_part_set_vars, mirror_measure, mirror_export,
              mirror_part_list):
        registry.register(t)
