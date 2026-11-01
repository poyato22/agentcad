"""Internals packing (Plan 8 G3): the cavity of a case, greedy component placement with
clearances and keep-outs, and per-section interference/clearance checks of the assembly.

The geometry runs in the sandboxed worker (`worker.py --internals`, code in
cad/layout_worker.py); the host only hands it host-made STEP copies, like assembly/geometry.py.
All three tools are reads: pack proposes placements (with a ready transform for each part) and
the agent applies them with assembly_add_instance.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..cad import safe_io
from ..cad.runner import WORKER
from ..platform import kill_process_tree, sandboxed_command
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from .geometry import part_dir
from .model import check_part_id
from .tools import _check_part_in_build, _parts_root, _resolve_build, _solve

MAX_PLANES = 12
MAX_COMPONENTS = 60


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_internals(job: dict, steps: dict[str, Path], *, timeout_s: float = 120) -> dict:
    """Run one internals job in the sandboxed worker. `steps` maps a job-local name to the part
    dir holding the model.step to copy in (read via safe_io, links never followed); the job's
    `step` / `instances[].step` fields name those copies. Returns the worker's result."""
    out = Path(tempfile.mkdtemp(prefix="mises-internals-"))
    try:
        inputs = out / "inputs"
        copies: dict[str, str] = {}
        for key, d in steps.items():
            try:
                f = safe_io.copy_out(d, "model.step", inputs, f"{key}.step")
            except (OSError, safe_io.UnsafePath):
                f = None
            if f is None:
                raise MisesError(ErrorCode.NOT_FOUND, f"part {Path(d).name!r} is not built",
                                 hint="build it first")
            copies[key] = str(f)
        job = dict(job)
        if "step" in job and job["step"] is not None:
            job["step"] = copies[job["step"]]
        if "instances" in job:
            job["instances"] = [{**it, "step": copies[it["step"]]} for it in job["instances"]]
        job["out_dir"] = str(out.resolve())
        tmp = out / "tmp"
        tmp.mkdir()
        job_path = out / "job.json"
        job_path.write_text(json.dumps(job))
        nonce = secrets.token_hex(16)
        env = {"MISES_NONCE": nonce, "PATH": "/usr/bin:/bin", "TMPDIR": str(tmp),
               "HOME": str(tmp), "PYTHONDONTWRITEBYTECODE": "1"}
        proc = await asyncio.create_subprocess_exec(
            *sandboxed_command([sys.executable, "-I", str(WORKER), "--internals", str(job_path)],
                               out),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            raise MisesError(ErrorCode.TOOL_FAILED,
                             f"internals check timed out after {timeout_s:g}s")
        except BaseException:
            _kill_group(proc)
            await proc.wait()
            raise
        _kill_group(proc)
        await asyncio.to_thread(kill_process_tree, proc.pid)
        if proc.returncode != 0:
            msg = err.decode(errors="replace")[-2000:].strip()
            last = msg.splitlines()[-1] if msg else f"worker exited {proc.returncode}"
            raise MisesError(ErrorCode.TOOL_FAILED, f"internals check failed: {last}")
        try:
            data = safe_io.read_json(out, "result.json")    # a planted link is refused
            if not isinstance(data, dict) or data.get("nonce") != nonce:
                raise ValueError("nonce mismatch")
        except Exception as e:  # noqa: BLE001
            raise MisesError(ErrorCode.TOOL_FAILED, f"internals check produced no result: {e}")
        data.pop("nonce", None)
        return data
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _metrics(parts_root: Path, pid: str) -> dict:
    try:
        m = safe_io.read_json(part_dir(parts_root, pid), "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        m = None
    if not isinstance(m, dict) or not isinstance(m.get("bbox"), dict):
        raise MisesError(ErrorCode.NOT_FOUND, f"part {pid!r} is not built", hint="build it first")
    return m


# ---- inner_volume

class InnerVolumeArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    part_id: str

    @field_validator("part_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        return check_part_id(v)


@tool("inner_volume", safety=SafetyClass.READ, scope_arg="part_id",
      description="The cavity of a built case or shell part: its volume, bounding box and open "
                  "sides (an open-top case reports z-max); smaller voids such as outer pockets "
                  "are listed in other_voids. The part must be in this build. Part scripts can "
                  "get the cavity solid itself with inner_volume(\"case_id\").")
async def inner_volume(ctx: ToolContext, args: InnerVolumeArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    _check_part_in_build(ctx, pid, args.part_id)
    root = _parts_root(ctx)
    data = await run_internals({"action": "inner_volume", "step": "case"},
                               {"case": part_dir(root, args.part_id)})
    cav = data["cavity"]
    s = cav["bbox"]["size"]
    opens = ", ".join(cav["open_sides"]) or "closed"
    return ToolOutput(summary=f"{args.part_id}: cavity {cav['volume_mm3']:.0f} mm3, "
                              f"{s[0]:.1f} x {s[1]:.1f} x {s[2]:.1f} mm ({opens})",
                      data={"part_id": args.part_id, **cav}, step_kind=StepKind.MEASURE)


# ---- pack

class Box(BaseModel):
    min: list[float] = Field(min_length=3, max_length=3)
    max: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _order(self) -> "Box":
        if any(a >= b for a, b in zip(self.min, self.max)):
            raise ValueError("a box's min must be below its max on every axis")
        return self


class Component(BaseModel):
    name: str
    part_id: str | None = Field(default=None, description="A built part: its bounding box is "
                                                          "the size, and the result carries a "
                                                          "transform for assembly_add_instance.")
    size: list[float] | None = Field(default=None, min_length=3, max_length=3,
                                     description="Or an explicit box size [x, y, z] in mm.")

    @model_validator(mode="after")
    def _one(self) -> "Component":
        if (self.part_id is None) == (self.size is None):
            raise ValueError("give a component exactly one of part_id or size")
        if self.part_id is not None:
            check_part_id(self.part_id)
        if self.size is not None and any(not (x > 0) for x in self.size):
            raise ValueError("component sizes must be positive")
        return self


class PackArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    components: list[Component] = Field(min_length=1, max_length=MAX_COMPONENTS)
    case_part_id: str | None = Field(default=None, description="The case whose cavity "
                                                               "(inner_volume) is packed.")
    cavity_box: Box | None = Field(default=None, description="Or the cavity as a box.")
    clearance_mm: float = Field(2.0, ge=0, le=100)
    strategy: Literal["layered", "grid"] = "layered"
    keep_outs: list[Box] = Field(default_factory=list, max_length=40,
                                 description="Boxes nothing may enter (a fan's airflow, a "
                                             "connector's cable bend, a screw boss).")
    allow_rotate: bool = True

    @model_validator(mode="after")
    def _cavity(self) -> "PackArgs":
        if (self.case_part_id is None) == (self.cavity_box is None):
            raise ValueError("give exactly one of case_part_id or cavity_box")
        if self.case_part_id is not None:
            check_part_id(self.case_part_id)
        return self


def _transform(p: dict) -> list[list[float]]:
    """4x4 row-major: moves the part's bounding box (turned 90 deg about z when `rotated`)
    onto the placement."""
    lo, hi, at = p["bbox_min"], p["bbox_max"], p["min"]
    if p.get("rotated"):
        # (x, y, z) -> (-y, x, z): the turned box's min corner is (-max.y, min.x, min.z)
        t = [at[0] + hi[1], at[1] - lo[0], at[2] - lo[2]]
        return [[0.0, -1.0, 0.0, t[0]], [1.0, 0.0, 0.0, t[1]], [0.0, 0.0, 1.0, t[2]],
                [0.0, 0.0, 0.0, 1.0]]
    t = [at[i] - lo[i] for i in range(3)]
    return [[1.0, 0.0, 0.0, t[0]], [0.0, 1.0, 0.0, t[1]], [0.0, 0.0, 1.0, t[2]],
            [0.0, 0.0, 0.0, 1.0]]


@tool("internals_pack", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=300,
      description="Propose placements for components inside a case's cavity (or a cavity box), "
                  "greedy and simple: each keeps clearance_mm to the walls, to the others and to "
                  "keep-out boxes. strategy layered (biggest first, floor then layers) or grid. "
                  "Components are built parts (their bounding boxes; each placement carries a "
                  "transform for assembly_add_instance) or plain sizes. Reports misfits (too "
                  "big, or no room left). Nothing is changed.")
async def internals_pack(ctx: ToolContext, args: PackArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    root = _parts_root(ctx)
    comps = []
    for c in args.components:
        item: dict = {"name": c.name}
        if c.part_id is not None:
            _check_part_in_build(ctx, pid, c.part_id)
            bb = _metrics(root, c.part_id)["bbox"]
            item.update(part_id=c.part_id, size=list(bb["size"]), bbox_min=list(bb["min"]),
                        bbox_max=list(bb["max"]))
        else:
            item["size"] = list(c.size)
        comps.append(item)
    job = {"action": "pack", "components": comps, "clearance_mm": args.clearance_mm,
           "strategy": args.strategy, "allow_rotate": args.allow_rotate,
           "keep_outs": [k.model_dump() for k in args.keep_outs]}
    if args.case_part_id is not None:
        _check_part_in_build(ctx, pid, args.case_part_id)
        job["step"] = "case"
        data = await run_internals(job, {"case": part_dir(root, args.case_part_id)})
    else:
        from ..cad.layout_worker import pack_boxes  # pure python: a box needs no worker
        b = args.cavity_box
        data = pack_boxes(b.min, b.max, comps, args.clearance_mm, args.strategy,
                          job["keep_outs"], None, args.allow_rotate)
        data["cavity"] = {"bbox": {"min": b.min, "max": b.max,
                                   "size": [b.max[i] - b.min[i] for i in range(3)]}}
    for p in data.get("placements", []):
        if "bbox_min" in p and "bbox_max" in p:
            p["transform"] = _transform(p)
        p.pop("bbox_min", None)
        p.pop("bbox_max", None)
    placed, miss = len(data.get("placements", [])), data.get("misfits", [])
    summary = f"placed {placed} of {placed + len(miss)} component(s) with " \
              f"{args.clearance_mm:g} mm clearance ({args.strategy})"
    if miss:
        summary += "; misfit: " + "; ".join(f"{m['name']} ({m['reason']})" for m in miss[:5])
    return ToolOutput(summary=summary, data={"project_id": pid, **data},
                      step_kind=StepKind.MEASURE)


# ---- section_check

class SectionPlane(BaseModel):
    axis: Literal["x", "y", "z"] | None = None
    offset: float = 0.0
    origin: list[float] | None = Field(default=None, min_length=3, max_length=3)
    normal: list[float] | None = Field(default=None, min_length=3, max_length=3)
    label: str | None = None

    @model_validator(mode="after")
    def _form(self) -> "SectionPlane":
        if (self.axis is None) == (self.normal is None):
            raise ValueError("a plane is {axis, offset} or {origin, normal}")
        if self.normal is not None and sum(x * x for x in self.normal) < 1e-12:
            raise ValueError("a plane normal must not be zero")
        return self

    def resolved(self) -> dict:
        if self.axis is not None:
            i = "xyz".index(self.axis)
            n = [0.0, 0.0, 0.0]
            n[i] = 1.0
            o = [0.0, 0.0, 0.0]
            o[i] = float(self.offset)
            return {"origin": o, "normal": n, "label": self.label or f"{self.axis}={self.offset:g}"}
        return {"origin": list(self.origin or [0.0, 0.0, 0.0]), "normal": list(self.normal),
                "label": self.label or "plane"}


class SectionArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    planes: list[SectionPlane] = Field(default_factory=list, max_length=MAX_PLANES,
                                       description="Section planes; default: the three "
                                                   "mid-planes of the assembly.")
    min_clearance_mm: float = Field(1.0, ge=0, le=100,
                                    description="Report pairs closer than this in a section.")


def _world_bbox(parts_root: Path, asm, res) -> tuple[list[float], list[float]] | None:
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for inst in asm.instances:
        try:
            bb = _metrics(parts_root, inst.part_id)["bbox"]
        except MisesError:
            continue
        m = res.transforms[inst.id]
        for x in (bb["min"][0], bb["max"][0]):
            for y in (bb["min"][1], bb["max"][1]):
                for z in (bb["min"][2], bb["max"][2]):
                    p = [m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3] for r in range(3)]
                    lo = [min(a, b) for a, b in zip(lo, p)]
                    hi = [max(a, b) for a, b in zip(hi, p)]
    return (lo, hi) if lo[0] != float("inf") else None


@tool("section_check", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=300,
      description="Cut the build's assembly with section planes and report, per plane, each "
                  "instance's section area, the pairs that overlap in the section "
                  "(interference) and the pairs closer than min_clearance_mm (clearance). Use "
                  "it after packing internals, beside assembly_check.")
async def section_check(ctx: ToolContext, args: SectionArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    root = _parts_root(ctx)
    asm, res = _solve(ctx, pid)
    steps: dict[str, Path] = {}
    instances = []
    for inst in asm.instances:
        if not safe_io.exists(part_dir(root, inst.part_id), "model.step"):
            continue
        steps.setdefault(inst.part_id, part_dir(root, inst.part_id))
        instances.append({"name": inst.name or inst.id, "step": inst.part_id,
                          "transform": res.transforms[inst.id]})
    if not instances:
        raise MisesError(ErrorCode.NOT_FOUND, "no built parts in this assembly",
                         hint="add built parts with assembly_add_instance first")
    planes = [p.resolved() for p in args.planes]
    if not planes:
        box = _world_bbox(root, asm, res)
        mid = [(a + b) / 2 for a, b in zip(*box)] if box else [0.0, 0.0, 0.0]
        planes = [SectionPlane(axis=a, offset=round(mid[i], 4)).resolved()
                  for i, a in enumerate("xyz")]
    data = await run_internals({"action": "section", "instances": instances, "planes": planes,
                                "min_clearance_mm": args.min_clearance_mm}, steps)
    results = data.get("planes", [])
    n_int = sum(len(p["interferences"]) for p in results)
    n_clr = sum(len(p["clearances"]) for p in results)
    worst = [f"{p['plane']['label']}: {i['a']}/{i['b']} {i['overlap_mm2']:.1f} mm2"
             for p in results for i in p["interferences"]][:4]
    summary = (f"{len(results)} section(s): {n_int} interference(s), {n_clr} tight "
               f"clearance(s) under {args.min_clearance_mm:g} mm")
    if worst:
        summary += "; " + "; ".join(worst)
    return ToolOutput(summary=summary, data={"project_id": pid, "planes": results,
                                             "min_clearance_mm": args.min_clearance_mm},
                      step_kind=StepKind.MEASURE)


INTERNALS_TOOLS = (inner_volume, internals_pack, section_check)


def register_layout_tools(registry: ToolRegistry) -> None:
    """Plan 8 G3: the layout skeleton tools and the internals tools."""
    from ..cad.skeleton_tools import SKELETON_TOOLS
    for t in (*SKELETON_TOOLS, *INTERNALS_TOOLS):
        registry.register(t)
