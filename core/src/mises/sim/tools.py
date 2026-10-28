"""sim_* tools: static stress studies on local mirror parts (gmsh + CalculiX).

Every result is a guide, not a guarantee: it names the solver, mesh sizes, material model and a
measured error band (coarse vs fine mesh), applies the FDM knock-down, and carries
fidelity_class "fem". Records live next to the part: parts/<part_id>/sims/<sim_id>/
{result.json, result.glb}; solving happens in <run workdir>/sim_tmp/<sim_id>/ and is removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import weakref
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..cad.tools import _check_part_id, _existing, _lock
from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import (ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput, canonical_json,
                     sha256_hex)
from .ccx import INSTALL_HINT, ccx_version, find_ccx, run_ccx, solver_status, write_inp
from .environments import DIRECTIONS, ENVIRONMENTS, FACE_PRESETS, direction_vector, get_environment
from .materials import FDM_KNOCKDOWN, MATERIALS, get_material
from .mesh import FACE_ID_RE, consistent_loads, mesh_sizes, mesh_step, select_faces
from .results import error_band, parse_frd, peaks, write_result_glb
from .types import SimError

SIM_ID_RE = re.compile(r"^sim_[0-9a-f]{12}$")
MESH_TIMEOUT_S = 180.0
SOLVE_TIMEOUT_S = 300.0
TOOL_TIMEOUT_S = 2 * (MESH_TIMEOUT_S + SOLVE_TIMEOUT_S) + 60
GUIDE_LABEL = "guide only, not a guarantee"
CAVEATS = [
    "Guide only: a linear-elastic FEM estimate, not a certification or a guarantee.",
    "Isotropic material model; printed parts are weaker across layers, so strength is "
    f"multiplied by an FDM knock-down of {FDM_KNOCKDOWN}.",
    "Small deflections assumed; contact, creep, fatigue, temperature and vibration are not "
    "simulated.",
    "Peak stress excludes nodes on the fixed face (an ideal clamp gives a constraint "
    "singularity there); that value is reported separately.",
]
FaceSpec = str
Direction = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


def _check_face(v: str) -> str:
    if v not in FACE_PRESETS and not FACE_ID_RE.match(v):
        raise ValueError(f"face must be one of {list(FACE_PRESETS)} or a face id like 'f3'")
    return v


class LoadSpec(BaseModel):
    face: FaceSpec = Field(description="preset (bottom, top, x-min, x-max, y-min, y-max, z-min, "
                                       "z-max) or a face id from mirror_measure face_list")
    newtons: float = Field(gt=0, le=1e6, description="total force spread over the face, N")
    direction: Direction

    @field_validator("face")
    @classmethod
    def _face(cls, v: str) -> str:
        return _check_face(v)


class StaticArgs(BaseModel):
    part_id: str
    fix: FaceSpec = Field(description="face held fixed: a preset or a face id (e.g. 'f3')")
    load: LoadSpec
    material: str | None = Field(default=None, description=f"one of {list(MATERIALS)}; "
                                 "default: the profile's default_material")
    environment: str | None = Field(default=None, description="optional preset from "
                                    "sim_env_list; its temperature is checked against the "
                                    "material")

    @field_validator("fix")
    @classmethod
    def _fix(cls, v: str) -> str:
        return _check_face(v)


class ResultArgs(BaseModel):
    sim_id: str = Field(pattern=SIM_ID_RE.pattern)


class EnvListArgs(BaseModel):
    pass


# ---- records

def _sims_dir(parts_root: Path, part_id: str) -> Path:
    return Path(parts_root) / part_id / "sims"


def _write_record(sim_dir: Path, rec: dict) -> None:
    sim_dir.mkdir(parents=True, exist_ok=True)
    tmp = sim_dir / "result.json.tmp"
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, sim_dir / "result.json")


def _file_hash(p: Path) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def find_sim(parts_root: Path, sim_id: str) -> Path | None:
    """The record dir of sim_id, or None. sim_id must already match SIM_ID_RE."""
    if not SIM_ID_RE.fullmatch(sim_id) or not Path(parts_root).is_dir():
        return None
    root = Path(parts_root).resolve()
    for d in Path(parts_root).glob(f"*/sims/{sim_id}"):
        try:
            real = d.resolve(strict=True)
        except OSError:
            continue
        if real.is_relative_to(root) and (real / "result.json").is_file():
            return real
    return None


def read_record(sim_dir: Path, parts_root: Path) -> dict:
    rec = json.loads((sim_dir / "result.json").read_text())
    cur = _file_hash(Path(parts_root) / rec.get("part_id", "") / "model.step")
    rec["stale"] = cur is None or cur != rec.get("geometry_hash")
    return rec


SUMMARY_KEYS = ("sim_id", "part_id", "run_id", "status", "stage", "created_at", "finished_at",
                "error", "hint", "inputs", "results", "guide", "label", "fidelity_class")


def list_sims(parts_root: Path, part_id: str | None = None,
              run_id: str | None = None) -> list[dict]:
    out = []
    pattern = f"{part_id}/sims/*/result.json" if part_id else "*/sims/*/result.json"
    if not Path(parts_root).is_dir():
        return out
    for f in Path(parts_root).glob(pattern):
        if not SIM_ID_RE.fullmatch(f.parent.name):
            continue
        try:
            rec = read_record(f.parent, parts_root)
        except (OSError, ValueError):
            continue
        if run_id is not None and rec.get("run_id") != run_id:
            continue
        out.append({k: rec.get(k) for k in SUMMARY_KEYS} | {"stale": rec["stale"]})
    return sorted(out, key=lambda r: r.get("created_at") or 0, reverse=True)


# ---- concurrency: one study at a time per process (meshing + solving are memory hungry)

_sems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = \
    weakref.WeakKeyDictionary()


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    s = _sems.get(loop)
    if s is None:
        s = _sems[loop] = asyncio.Semaphore(1)
    return s


def _default_material(ctx: ToolContext) -> str:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    try:
        return rt.profile().default_material if rt is not None else "PLA"
    except Exception:  # noqa: BLE001
        return "PLA"


def _bad(e: SimError) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint)


async def _solve(ctx: ToolContext, job: Path, step: Path, metrics: dict, size: float,
                 args: StaticArgs, material, ccx: str, progress) -> dict:
    stage = job.name
    progress(f"mesh_{stage}", size_mm=size)
    mesh = await mesh_step(step, job, size, timeout_s=MESH_TIMEOUT_S)
    bbox, faces = metrics["bbox"], metrics.get("face_list")
    fix_tags = select_faces(args.fix, mesh.surfaces, bbox, faces)
    load_tags = select_faces(args.load.face, mesh.surfaces, bbox, faces)
    if set(fix_tags) & set(load_tags):
        raise SimError("bad_setup", "the fixed face and the load face overlap",
                       hint="load a different face than the one held fixed")
    fixed = mesh.nodes_of(fix_tags)
    d = direction_vector(args.load.direction)
    loads = consistent_loads(mesh, load_tags, tuple(args.load.newtons * x for x in d))
    for n in fixed:  # a load on a clamped node goes straight into the support
        loads.pop(n, None)
    # big meshes make these slow: keep them off the server's event loop
    await asyncio.to_thread(write_inp, job / "job.inp", mesh, fixed, loads, material)
    progress(f"solve_{stage}", nodes=len(mesh.nodes), elements=len(mesh.tets))
    frd = await run_ccx(ccx, job, timeout_s=SOLVE_TIMEOUT_S)
    pk = await asyncio.to_thread(lambda: peaks(mesh, parse_frd(frd), exclude=fixed))
    return {"mesh": mesh, "peaks": pk,
            "info": {"size_mm": round(size, 4), "nodes": len(mesh.nodes),
                     "elements": len(mesh.tets), "fixed_nodes": len(fixed),
                     "loaded_nodes": len(loads)}}


@tool("sim_static_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=TOOL_TIMEOUT_S,
      description="Run a static linear-elastic stress study (gmsh + CalculiX) on a local mirror "
                  "part: one face held fixed, a force spread over another face. Faces are "
                  "presets (bottom, top, x-min, ...) or face ids from mirror_measure face_list. "
                  "Returns max von Mises (MPa), max displacement (mm), safety factor (yield x "
                  "FDM knock-down 0.7 / peak) and an error band from a coarse vs fine mesh. "
                  "Results are a GUIDE ONLY: always say so when you report them.")
async def sim_static_run(ctx: ToolContext, args: StaticArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    part = _existing(ctx, args.part_id)
    if args.fix == args.load.face:
        raise MisesError(ErrorCode.INVALID_INPUT, "fix and load are the same face",
                         hint="hold one face and load another")
    try:
        material = get_material(args.material or _default_material(ctx))
        env = get_environment(args.environment) if args.environment else None
    except SimError as e:
        raise _bad(e) from None

    parts_root = Path(ctx.workdir) / "parts"
    sim_id = "sim_" + secrets.token_hex(6)
    sim_dir = _sims_dir(parts_root, args.part_id) / sim_id
    job_root = Path(ctx.workdir) / "sim_tmp" / sim_id
    job_root.mkdir(parents=True, exist_ok=True)
    async with _lock(part):  # a consistent snapshot of the geometry this study solves
        shutil.copyfile(part / "model.step", job_root / "model.step")
        metrics = json.loads((part / "metrics.json").read_text())
    geometry_hash = _file_hash(job_root / "model.step")
    inputs = {"fix": args.fix, "load": args.load.model_dump(), "material": material.name,
              "environment": env["name"] if env else None}
    rec: dict = {
        "sim_id": sim_id, "part_id": args.part_id, "run_id": getattr(ctx, "run_id", None),
        "status": "running", "stage": "queued", "created_at": time.time(), "finished_at": None,
        "analysis": "static_linear_elastic", "guide": True, "label": GUIDE_LABEL,
        "fidelity_class": "fem", "validated_against": None, "inputs": inputs,
        "geometry_hash": geometry_hash, "material": material.to_dict(),
        "environment": env, "error": None, "hint": None, "warnings": [], "caveats": CAVEATS}
    _write_record(sim_dir, rec)

    def progress(stage: str, **data) -> None:
        rec["stage"] = stage
        _write_record(sim_dir, rec)
        events = getattr(ctx, "events", None)
        if events is not None:
            events.publish(Event(type="sim_progress", run_id=getattr(ctx, "run_id", None),
                                 data={"sim_id": sim_id, "part_id": args.part_id,
                                       "stage": stage, **data}))

    def fail(code: str, message: str, hint: str | None, extra: dict | None = None) -> ToolOutput:
        rec.update(status="failed", stage="failed", error=message, hint=hint,
                   finished_at=time.time())
        _write_record(sim_dir, rec)
        summary = f"study failed: {message}" + (f" ({hint})" if hint else "")
        return ToolOutput(ok=False, summary=summary, step_kind=StepKind.ERROR,
                          data={"sim_id": sim_id, "error": code, "message": message,
                                "hint": hint, **(extra or {})})

    try:
        ccx = find_ccx()
        if ccx is None:
            msg = "CalculiX (ccx) is not installed"
            return fail("solver_missing", msg, f"install it with: {INSTALL_HINT}",
                        {"missing": ["ccx"], "install": INSTALL_HINT})
        version = await asyncio.to_thread(ccx_version, ccx) or "unknown"
        coarse_h, fine_h = mesh_sizes(metrics["bbox"]["size"], metrics.get("volume_mm3", 0))
        step = job_root / "model.step"
        try:
            async with _semaphore():
                coarse = await _solve(ctx, job_root / "coarse", step, metrics, coarse_h, args,
                                      material, ccx, progress)
                fine = await _solve(ctx, job_root / "fine", step, metrics, fine_h, args,
                                    material, ccx, progress)
        except SimError as e:
            return fail(e.code, e.message, e.hint)
        progress("post")
        pf, pc = fine["peaks"], coarse["peaks"]
        peak = pf["max_von_mises_mpa"]
        band = error_band(coarse=pc["max_von_mises_mpa"], fine=peak)
        sf = material.allowable_mpa / peak if peak > 0 else None
        warnings = []
        if sf is not None and sf < 1:
            warnings.append(f"predicted to yield: safety factor {sf:.2f} < 1")
        if band is not None and band > 0.1:
            warnings.append(f"mesh not converged: peak stress changed {band:.0%} between the "
                            "coarse and fine mesh; treat the peak as uncertain")
        fx = pf["max_von_mises_at_fixture_mpa"]
        if fx is not None and fx > 1.5 * peak:
            warnings.append(f"stress at the fixed face reaches {fx:.3g} MPa (an idealised "
                            "clamp exaggerates it); real fixtures spread load")
        if env and env["temperature_c"] >= material.hdt_c - 5:
            warnings.append(f"{env['name']}: {env['temperature_c']} C is at or above "
                            f"{material.name}'s heat deflection temperature ({material.hdt_c:g} "
                            "C): the part may soften or creep; this result does not cover it")
        solver = {"name": "CalculiX", "version": version, "path": ccx,
                  "element": "C3D10 (quadratic tetrahedra)"}
        mesh_info = {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                     "coarse": coarse["info"], "fine": fine["info"]}
        inputs_hash = sha256_hex(canonical_json({"inputs": inputs, "geometry": geometry_hash,
                                                 "material": material.to_dict(),
                                                 "mesh": [coarse_h, fine_h], "solver": version}))
        results = {
            "max_von_mises_mpa": peak, "max_von_mises_at": pf["max_von_mises_at"],
            "max_von_mises_at_fixture_mpa": fx,
            "max_displacement_mm": pf["max_displacement_mm"],
            "max_displacement_at": pf["max_displacement_at"],
            "safety_factor": sf, "allowable_mpa": material.allowable_mpa,
            "coarse_max_von_mises_mpa": pc["max_von_mises_mpa"],
            "coarse_max_displacement_mm": pc["max_displacement_mm"],
            "error_band": band, "error_band_pct": None if band is None else round(100 * band, 1),
            "peak_excludes": "nodes on the fixed face"}
        await asyncio.to_thread(lambda: write_result_glb(
            job_root / "result.glb", fine["mesh"], pf["vm"], vmax=peak,
            extras={"sim_id": sim_id, "part_id": args.part_id}))
        sim_dir.mkdir(parents=True, exist_ok=True)
        os.replace(job_root / "result.glb", sim_dir / "result.glb")
        rec.update(status="done", stage="done", finished_at=time.time(), solver=solver,
                   mesh=mesh_info, results=results, warnings=warnings,
                   inputs_hash=inputs_hash, glb="result.glb",
                   color_range_mpa=[0.0, peak])
        _write_record(sim_dir, rec)
    except asyncio.CancelledError:
        rec.update(status="cancelled", stage="cancelled", error="cancelled",
                   finished_at=time.time())
        _write_record(sim_dir, rec)
        raise
    except Exception as e:
        fail("internal", f"{type(e).__name__}: {e}", None)
        raise
    finally:
        shutil.rmtree(job_root, ignore_errors=True)

    band_s = "n/a" if band is None else f"±{band:.1%}"
    sf_s = "n/a" if sf is None else f"{sf:.2f}"
    summary = (f"GUIDE ONLY ({solver['name']} {version}, fine mesh {fine_h:.2g} mm, "
               f"{material.name} x{FDM_KNOCKDOWN} knock-down): {args.part_id} max von Mises "
               f"{peak:.3g} MPa (error band {band_s}), max displacement "
               f"{pf['max_displacement_mm']:.3g} mm, safety factor {sf_s}")
    if warnings:
        summary += "; warnings: " + "; ".join(warnings)
    return ToolOutput(summary=summary, data=rec, step_kind=StepKind.SIM_RUN,
                      warnings=warnings)


def _in_read_scope(ctx: ToolContext, part_id: str) -> bool:
    pol = getattr(ctx, "policy", None)
    if pol is None:
        return True
    allowed = set(pol.edit_scope) | set(pol.read_scope)
    return "*" in allowed or part_id in allowed


@tool("sim_result_get", safety=SafetyClass.READ,
      description="Return a stored study result by sim_id (inputs, solver, mesh, material, "
                  "peaks, error band, warnings; stale=true if the part changed since).")
async def sim_result_get(ctx: ToolContext, args: ResultArgs) -> ToolOutput:
    root = Path(ctx.workdir) / "parts"
    d = find_sim(root, args.sim_id)
    if d is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no study {args.sim_id}",
                         hint="run one with sim_static_run")
    rec = read_record(d, root)
    if not _in_read_scope(ctx, rec.get("part_id", "")):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{args.sim_id} belongs to a part outside "
                         "this run's scope", hint="ask the user to widen the scope")
    r = rec.get("results") or {}
    summary = (f"{args.sim_id} ({rec['status']}, {GUIDE_LABEL})"
               + (f": max von Mises {r['max_von_mises_mpa']:.3g} MPa, safety factor "
                  f"{r['safety_factor']:.2f}" if r.get("safety_factor") else "")
               + ("; STALE: the part changed since this study" if rec["stale"] else ""))
    return ToolOutput(summary=summary, data=rec, step_kind=StepKind.SIM_RUN)


@tool("sim_env_list", safety=SafetyClass.READ,
      description="List study presets: environments, fixture/load face presets, load "
                  "directions, materials (with FDM knock-down) and whether the solver is "
                  "installed.")
async def sim_env_list(ctx: ToolContext, args: EnvListArgs) -> ToolOutput:
    st = solver_status()
    data = {"environments": ENVIRONMENTS, "face_presets": list(FACE_PRESETS),
            "face_ids": "or a face id (\"f3\") from mirror_measure face_list",
            "directions": list(DIRECTIONS), "knockdown": FDM_KNOCKDOWN,
            "materials": [m.to_dict() for m in MATERIALS.values()], "solvers": st}
    s = (f"{len(ENVIRONMENTS)} environments, {len(MATERIALS)} materials; CalculiX "
         + ("found" if st["ccx"]["found"] else f"MISSING (install: {INSTALL_HINT})"))
    return ToolOutput(summary=s, data=data)


def register_sim_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (sim_static_run, sim_result_get, sim_env_list):
        if not registry.list([t.name]):
            registry.register(t)
