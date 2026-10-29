"""Study-type registry and the shared study lifecycle.

Every study (static, modal, buckling, thermal, drop, wind) goes through run_study(): validated
settings, a geometry snapshot, a record at parts/<part_id>/sims/<sim_id>/result.json that is
updated as it runs, live `sim_progress {sim_id, phase: mesh|solve|post, pct, detail}` events,
one study at a time (meshing, solving and the lattice are memory hungry), cancel by sim_id
(kills the solver's process group), and outputs moved next to the record: result.glb (colored
surface + channel attributes), fields.npz (probe / section data) and, for wind, slice PNGs.

Every record names its solver, mesh / lattice, settings, error band or convergence history,
`guide: true` with the "guide only" label, and a `fidelity_class`.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ..events import Event
from ..types import (ErrorCode, MisesError, StepKind, ToolOutput, canonical_json, sha256_hex)
from .ccx import INSTALL_HINT, ccx_version, find_ccx
from .environments import get_environment
from .materials import Material, custom_material, get_material, with_fdm
from .settings import SETTINGS_MODELS, unit_vec
from .types import SimError

SIM_ID_RE = re.compile(r"^sim_[0-9a-f]{12}$")
GUIDE_LABEL = "guide only, not a guarantee"
QUEUE_TIMEOUT_S = 1500.0
TEMPLATES_FILE = "sim_templates.json"
TEMPLATE_ID_RE = re.compile(r"^tpl_[0-9a-f]{10}$")
MAX_TEMPLATES = 200

CAVEATS_FEM = [
    "Guide only: a linear-elastic FEM estimate, not a certification or a guarantee.",
    "Isotropic material model; printed parts are weaker across layers, so strength is "
    "multiplied by an FDM knock-down.",
    "Small deflections assumed; contact, creep, fatigue, temperature and vibration are not "
    "simulated unless this study type says so.",
]
CAVEATS_STATIC_FIXTURE = (
    "Peak stress excludes nodes on the fixed face (an ideal clamp gives a constraint "
    "singularity there); that value and a safety factor including it are reported separately.")
CAVEATS_WIND = [
    "Guide only, LOW FIDELITY: a coarse lattice-Boltzmann (LES) solution on a voxelized part in "
    "a periodic tunnel, at a reduced effective Reynolds number.",
    "The boundary layer is not resolved: drag is the resolved pressure drag plus a flat-plate "
    "skin-friction estimate; the raw lattice total is reported as cd_lattice_total.",
    "Validated on a sphere and a flat plate (see the Mises C7 report); expect 10-30% error on "
    "other shapes, more on streamlined bodies whose drag is mostly friction.",
]


@dataclass(frozen=True)
class StudyType:
    id: str
    name: str
    solver: str
    fidelity_class: str
    doc: str
    channels: tuple[str, ...]
    needs_ccx: bool = True


STUDY_TYPES: dict[str, StudyType] = {t.id: t for t in [
    StudyType("static", "Static stress", "CalculiX *STATIC", "fem",
              "stress, displacement and safety factor under one or more load cases",
              ("von_mises", "displacement", "displacement_magnitude", "s1", "s3",
               "safety_factor")),
    StudyType("modal", "Modal (vibration)", "CalculiX *FREQUENCY", "fem",
              "natural frequencies and mode shapes; flags modes inside an excitation band",
              ("mode_1", "mode_2", "mode_3")),
    StudyType("buckling", "Buckling", "CalculiX *BUCKLE", "fem",
              "load multipliers at which the part buckles (linear eigenvalue buckling)",
              ("mode_1", "mode_2", "mode_3")),
    StudyType("thermal", "Thermal (steady state)", "CalculiX *HEAT TRANSFER", "fem",
              "temperature field from fixed temperatures, heat input and convection",
              ("temperature",)),
    StudyType("drop", "Drop / impact (estimate)", "CalculiX *STATIC (equivalent load)",
              "fem_estimate", "an equivalent static load F = m g k toward the impact face",
              ("von_mises", "displacement", "displacement_magnitude", "safety_factor")),
    StudyType("wind", "Wind / aero (CFD)", "built-in lattice-Boltzmann D3Q19 (numpy)",
              "cfd_lbm", "drag and lift (Cd, Cl), moments, surface Cp, velocity slices and a "
              "yaw sweep", ("cp", "velocity", "speed"), needs_ccx=False),
]}


def types_list() -> list[dict]:
    st = find_ccx()
    out = []
    for t in STUDY_TYPES.values():
        available = (st is not None) if t.needs_ccx else True
        out.append({"id": t.id, "name": t.name, "solver": t.solver,
                    "fidelity_class": t.fidelity_class, "doc": t.doc, "channels": list(t.channels),
                    "available": available,
                    "detail": None if available else f"CalculiX missing: {INSTALL_HINT}",
                    "schema": SETTINGS_MODELS[t.id].model_json_schema()})
    return out


def validate_settings(type_id: str, settings: dict | None) -> BaseModel:
    if type_id not in STUDY_TYPES:
        raise SimError("bad_setup", f"unknown study type {type_id!r}",
                       hint=f"use one of: {', '.join(STUDY_TYPES)}")
    try:
        return SETTINGS_MODELS[type_id].model_validate(settings or {})
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, er['loc'])) or 'settings'}: {er['msg']}"
                         for er in e.errors()[:8])
        raise SimError("bad_setup", f"invalid {type_id} settings: {msgs}",
                       data={"errors": json.loads(e.json())[:20]}) from None


def resolve_material(ms, default: str, home: Path | None) -> Material:
    if ms is None:
        m = get_material(default, home)
    elif ms.custom is not None:
        m = custom_material(ms.custom.model_dump())
    else:
        m = get_material(ms.name or default, home)
    if ms is not None and ms.fdm is not None:
        m = with_fdm(m, ms.fdm.infill_pct, ms.fdm.walls, ms.fdm.layer_orientation)
    return m


# ---- records

def sims_dir(parts_root: Path, part_id: str) -> Path:
    return Path(parts_root) / part_id / "sims"


def write_record(sim_dir: Path, rec: dict) -> None:
    sim_dir.mkdir(parents=True, exist_ok=True)
    tmp = sim_dir / "result.json.tmp"
    tmp.write_text(json.dumps(rec, default=float))
    os.replace(tmp, sim_dir / "result.json")


def file_hash(p: Path) -> str | None:
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
    cur = file_hash(Path(parts_root) / rec.get("part_id", "") / "model.step")
    rec["stale"] = cur is None or cur != rec.get("geometry_hash")
    rec.setdefault("type", "static")
    return rec


SUMMARY_KEYS = ("sim_id", "part_id", "run_id", "step_id", "type", "status", "stage", "phase",
                "pct", "created_at", "finished_at", "error", "hint", "inputs", "results",
                "guide", "label", "fidelity_class", "reason")


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


# ---- concurrency and cancel

_sems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = \
    weakref.WeakKeyDictionary()
_active: dict[str, asyncio.Task] = {}
_cancel_requested: set[str] = set()


def semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    s = _sems.get(loop)
    if s is None:
        s = _sems[loop] = asyncio.Semaphore(1)
    return s


def active_ids() -> list[str]:
    return [k for k, t in _active.items() if not t.done()]


def cancel(sim_id: str) -> bool:
    """Cancel a running study: its task is cancelled, which kills the mesher / solver process
    group. False if no such study is running in this process."""
    t = _active.get(sim_id)
    if t is None or t.done():
        return False
    _cancel_requested.add(sim_id)
    t.cancel()
    return True


# ---- one study

def _home(ctx) -> Path | None:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    return Path(rt.home) if rt is not None and getattr(rt, "home", None) else None


def default_material(ctx) -> str:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    try:
        return rt.profile().default_material if rt is not None else "PLA"
    except Exception:  # noqa: BLE001
        return "PLA"


def _step_id(ctx) -> int | None:
    store, run_id, call_id = (getattr(ctx, "store", None), getattr(ctx, "run_id", None),
                              getattr(ctx, "call_id", None))
    if store is None or not run_id or not call_id:
        return None
    try:
        steps = store.steps(run_id, kinds=[StepKind.TOOL_CALL])
    except Exception:  # noqa: BLE001
        return None
    for s in reversed(steps):
        if (s.payload or {}).get("call_id") == call_id:
            return s.id
    return None


def urls(sim_id: str) -> dict:
    return {"record": f"/sims/{sim_id}", "glb": f"/sims/{sim_id}/result.glb",
            "charts": f"/sims/{sim_id}/charts", "probe": f"/sims/{sim_id}/probe",
            "section": f"/sims/{sim_id}/section"}


Runner = Callable[..., Any]


async def run_study(ctx, type_id: str, part_id: str, part_dir: Path, settings: BaseModel, *,
                    reason: str | None = None, requirements: list[dict] | None = None,
                    inputs_extra: dict | None = None,
                    solve_timeout_s: float | None = None) -> ToolOutput:
    t = STUDY_TYPES[type_id]
    home = _home(ctx)
    try:
        material = (resolve_material(getattr(settings, "material", None), default_material(ctx),
                                     home) if type_id != "wind" else None)
        env = (get_environment(settings.environment)
               if getattr(settings, "environment", None) else None)
    except SimError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint) from None

    parts_root = Path(ctx.workdir) / "parts"
    sim_id = "sim_" + secrets.token_hex(6)
    sim_dir = sims_dir(parts_root, part_id) / sim_id
    job_root = Path(ctx.workdir) / "sim_tmp" / sim_id
    job_root.mkdir(parents=True, exist_ok=True)
    from ..cad.tools import _lock
    async with _lock(part_dir):  # a consistent snapshot of the geometry this study solves
        shutil.copyfile(part_dir / "model.step", job_root / "model.step")
        if (part_dir / "model.stl").is_file():
            shutil.copyfile(part_dir / "model.stl", job_root / "model.stl")
        metrics = json.loads((part_dir / "metrics.json").read_text())
    geometry_hash = file_hash(job_root / "model.step")
    sdump = settings.model_dump(mode="json")
    inputs = {"type": type_id, "settings": sdump, **(inputs_extra or {}),
              "material": material.name if material else None,
              "environment": env["name"] if env else None}
    rec: dict = {
        "sim_id": sim_id, "part_id": part_id, "type": type_id,
        "run_id": getattr(ctx, "run_id", None), "call_id": getattr(ctx, "call_id", None),
        "step_id": _step_id(ctx), "reason": (reason or None),
        "status": "running", "stage": "queued", "phase": "queued", "pct": 0.0,
        "created_at": time.time(), "finished_at": None,
        "analysis": type_id, "guide": True, "label": GUIDE_LABEL,
        "fidelity_class": t.fidelity_class, "validated_against": None, "inputs": inputs,
        "settings": sdump, "geometry_hash": geometry_hash,
        "material": material.to_dict() if material else None, "environment": env,
        "error": None, "hint": None, "warnings": [],
        "caveats": (CAVEATS_WIND if type_id == "wind" else
                    CAVEATS_FEM + ([CAVEATS_STATIC_FIXTURE] if type_id in ("static", "drop")
                                   else [])),
        "urls": urls(sim_id)}
    write_record(sim_dir, rec)

    def progress(stage: str, phase: str | None = None, pct: float | None = None,
                 detail: str | None = None, **data) -> None:
        rec["stage"] = stage
        if phase is not None:
            rec["phase"] = phase
        if pct is not None:
            rec["pct"] = round(float(pct), 1)
        write_record(sim_dir, rec)
        events = getattr(ctx, "events", None)
        if events is not None:
            events.publish(Event(type="sim_progress", run_id=getattr(ctx, "run_id", None),
                                 data={"sim_id": sim_id, "part_id": part_id, "type": type_id,
                                       "stage": stage, "phase": rec["phase"], "pct": rec["pct"],
                                       "detail": detail or stage, **data}))

    def fail(code: str, message: str, hint: str | None, extra: dict | None = None,
             status: str = "failed") -> ToolOutput:
        rec.update(status=status, stage=status, error=message, hint=hint,
                   finished_at=time.time())
        write_record(sim_dir, rec)
        summary = f"study {status}: {message}" + (f" ({hint})" if hint else "")
        return ToolOutput(ok=False, summary=summary, step_kind=StepKind.ERROR,
                          data={"sim_id": sim_id, "error": code, "message": message,
                                "hint": hint, "card": card(rec), **(extra or {})})

    async def body() -> ToolOutput:
        ccx = None
        version = None
        if t.needs_ccx:
            ccx = _tools_attr("find_ccx", find_ccx)()
            if ccx is None:
                return fail("solver_missing", "CalculiX (ccx) is not installed",
                            f"install it with: {INSTALL_HINT}",
                            {"missing": ["ccx"], "install": INSTALL_HINT})
            version = await asyncio.to_thread(ccx_version, ccx) or "unknown"
        sem = semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), queue_timeout())
        except asyncio.TimeoutError:
            return fail("busy", f"another study kept the solver busy for "
                                f"{queue_timeout():g}s", "retry when the running study has "
                                                         "finished")
        try:
            out = await _dispatch(type_id, job_root, metrics, material, ccx, settings, env,
                                  progress, sim_id, part_id, solve_timeout_s)
        except SimError as e:
            return fail(e.code, e.message, e.hint)
        finally:
            sem.release()
        solver = ({"name": "CalculiX", "version": version, "path": ccx,
                   "element": "C3D10 (quadratic tetrahedra)" if getattr(
                       settings.mesh, "second_order", True) else "C3D4 (linear tetrahedra)",
                   "procedure": t.solver} if t.needs_ccx else out.pop("solver"))
        sim_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for name in ("result.glb", "fields.npz", "slice_xz.png", "slice_xy.png"):
            if (job_root / name).is_file():
                os.replace(job_root / name, sim_dir / name)
                outputs.append(name)
        inputs_hash = sha256_hex(canonical_json({"inputs": inputs, "geometry": geometry_hash,
                                                 "material": rec["material"],
                                                 "solver": solver.get("version")}))
        rec.update(status="done", stage="done", phase="done", pct=100.0,
                   finished_at=time.time(), solver=solver, mesh=out.get("mesh"),
                   results=out["results"], warnings=out["warnings"], inputs_hash=inputs_hash,
                   glb="result.glb" if "result.glb" in outputs else None, outputs=outputs,
                   **{k: v for k, v in out.items()
                      if k.startswith("color_range") or k in ("fidelity_class", "lattice")})
        if requirements:
            rec["checks"] = check_requirements(rec, requirements)
        write_record(sim_dir, rec)
        return ToolOutput(summary=summary(rec), data={**rec, "card": card(rec)},
                          step_kind=StepKind.SIM_RUN, warnings=rec["warnings"])

    task = asyncio.ensure_future(body())
    _active[sim_id] = task
    try:
        return await task
    except asyncio.CancelledError:
        rec.update(status="cancelled", stage="cancelled", error="cancelled",
                   finished_at=time.time())
        write_record(sim_dir, rec)
        cur = asyncio.current_task()
        if sim_id in _cancel_requested and not (cur is not None and cur.cancelling()):
            return ToolOutput(ok=False, summary=f"study {sim_id} cancelled by the user",
                              step_kind=StepKind.ERROR,
                              data={"sim_id": sim_id, "error": "cancelled",
                                    "message": "cancelled", "card": card(rec)})
        task.cancel()
        raise
    except Exception as e:
        fail("internal", f"{type(e).__name__}: {e}", None)
        raise
    finally:
        _active.pop(sim_id, None)
        _cancel_requested.discard(sim_id)
        shutil.rmtree(job_root, ignore_errors=True)


def _tools_attr(name: str, default):
    from . import tools as _tools  # the tool module owns the (patchable) solver lookup/timeouts
    return getattr(_tools, name, default)


def queue_timeout() -> float:
    return _tools_attr("QUEUE_TIMEOUT_S", QUEUE_TIMEOUT_S)


async def _dispatch(type_id, job_root, metrics, material, ccx, settings, env, progress,
                    sim_id, part_id, solve_timeout_s=None) -> dict:
    if type_id == "wind":
        return await _run_wind(job_root, metrics, settings, progress)
    from .fem import FemJob
    job = FemJob(root=job_root, step=job_root / "model.step", metrics=metrics,
                 material=material, ccx=ccx, mesh_settings=settings.mesh,
                 solver_settings=settings.solver, progress=progress,
                 convergence=getattr(settings, "convergence", None),
                 extra={"solve_timeout_s": solve_timeout_s} if solve_timeout_s else {})
    if type_id == "static":
        from .static import run_static
        return await run_static(job, settings.load_cases, env=env, sim_id=sim_id,
                                part_id=part_id)
    if type_id == "modal":
        from .modal import run_modal
        return await run_modal(job, settings, sim_id=sim_id, part_id=part_id)
    if type_id == "buckling":
        from .buckling import run_buckling
        return await run_buckling(job, settings, sim_id=sim_id, part_id=part_id)
    if type_id == "thermal":
        from .thermal import run_thermal
        return await run_thermal(job, settings, sim_id=sim_id, part_id=part_id)
    from .drop import run_drop
    return await run_drop(job, settings, sim_id=sim_id, part_id=part_id)


def wind_payload(s) -> dict:
    d = s.model_dump(mode="json")
    d["direction"] = list(unit_vec(s.direction))
    d["up"] = list(unit_vec(s.up))
    return d


async def _run_wind(job_root: Path, metrics: dict, s, progress) -> dict:
    from .cfd import run_wind_job
    from .cfd.setup import read_stl
    stl = job_root / "model.stl"
    if not stl.is_file():
        raise SimError("bad_setup", "this part has no STL to voxelize",
                       hint="rebuild it (mirror_part_set_vars with its current vars)")
    tris = await asyncio.to_thread(read_stl, stl)
    progress("setup", phase="mesh", pct=2.0, detail="voxelizing the part")

    def prog(d: dict) -> None:
        n = d.get("angles", 1)
        ang = f" · angle {d.get('angle', 0) + 1}/{n}" if n > 1 else ""
        progress("solve", phase="solve", pct=min(95.0, 5 + 0.9 * float(d.get("pct", 0))),
                 detail=f"lattice step {d.get('step')}/{d.get('steps')}{ang}")
    r = await run_wind_job(tris, wind_payload(s), job_root, progress=prog)
    progress("post", phase="post", pct=96.0, detail="post-processing")
    lbm = r.get("method") == "lbm"
    warnings = []
    if lbm:
        warnings.append(f"LOW FIDELITY: lattice of {r['lattice']['cells_across']} cells across "
                        f"the body at an effective Reynolds number of "
                        f"{r['lattice']['reynolds_effective']:.3g} (real {r['reynolds']:.3g})")
        if not r.get("converged"):
            warnings.append(f"the drag did not settle within the run ({r.get('stopped')}); "
                            "raise iterations or timeout_s")
        if r["lattice"]["blockage_pct"] > 6:
            warnings.append(f"tunnel blockage {r['lattice']['blockage_pct']:.1f}% inflates drag; "
                            "raise domain_multiplier")
    else:
        warnings.append(f"ESTIMATE: empirical Cd {r['cd']:.2f} for a {r['shape_class']} "
                        f"({r.get('reason')}); +-{100 * r['error_band']:.0f}%")
    results = {k: r.get(k) for k in (
        "cd", "cl", "cs", "drag_n", "lift_n", "side_n", "moment_nm", "cd_pressure",
        "cd_friction_estimate", "cd_lattice_total", "reynolds", "ref_area_m2",
        "frontal_area_m2", "dynamic_pressure_pa", "error_band", "converged", "stopped",
        "steps", "method", "shape_class", "reason", "yaw_sweep", "cp_range")}
    results["error_band_pct"] = (None if results["error_band"] is None
                                 else round(100 * results["error_band"], 1))
    results["convergence"] = {"history": r.get("history", []), "converged": r.get("converged")}
    solver = ({"name": "Mises lattice-Boltzmann", "version": "D3Q19 1.0",
               "method": "BGK + Smagorinsky LES, half-way bounce-back, momentum exchange",
               "fidelity": "low fidelity"} if lbm else
              {"name": "empirical shape-class table", "version": "1.0",
               "method": "Cd from Hoerner / White for the shape class x frontal area",
               "fidelity": "estimate"})
    return {"results": results, "warnings": warnings, "solver": solver,
            "mesh": {"lattice": r.get("lattice"), "frame": r.get("frame")},
            "fidelity_class": "cfd_lbm" if lbm else "empirical",
            "color_range_cp": r.get("cp_range")}


# ---- summaries and cards

def _num(v, fmt: str) -> str:
    return "n/a" if v is None else format(v, fmt)


def summary(rec: dict) -> str:
    """One line that always carries what a reader needs: guide only, type, solver, mesh, error
    band or convergence, and the key numbers."""
    if rec.get("status") != "done":
        s = f"{rec.get('sim_id')} {rec.get('status')} ({GUIDE_LABEL})"
        if rec.get("error"):
            s += f": {rec['error']}" + (f" ({rec['hint']})" if rec.get("hint") else "")
        return s
    typ = rec.get("type", "static")
    if typ in ("static", "drop"):
        s = static_summary(rec)
        if typ == "drop":
            imp = rec["results"].get("impact") or {}
            s = (f"ESTIMATE (drop {imp.get('drop_m')} m, F = m g k, k = {imp.get('k')}: "
                 f"{_num(imp.get('newtons'), '.3g')} N) - " + s)
        return s
    r, sv = rec["results"], rec["solver"]
    band = r.get("error_band")
    bands = "n/a" if band is None else format(band, ".1%")
    if typ == "wind":
        lat = (rec.get("mesh") or {}).get("lattice") or {}
        mesh = (f"lattice {lat.get('cells_across')} cells across, "
                f"Re_eff {_num(lat.get('reynolds_effective'), '.3g')}" if lat else "no lattice")
        s = (f"GUIDE ONLY - {rec.get('fidelity_class')} ({sv.get('fidelity')}), {sv['name']}, "
             f"{mesh}: {rec['part_id']} Cd {_num(r.get('cd'), '.3f')}, Cl "
             f"{_num(r.get('cl'), '.3f')}, drag {_num(r.get('drag_n'), '.3g')} N, lift "
             f"{_num(r.get('lift_n'), '.3g')} N at Re {_num(r.get('reynolds'), '.3g')} "
             f"(unsteadiness band {bands})")
    else:
        mesh = rec.get("mesh") or {}
        m = rec.get("material") or {}
        fine = (mesh.get("fine") or {}).get("size_mm")
        pre = (f"GUIDE ONLY - {sv['name']} {sv.get('version')} {sv.get('procedure', '')}, fine "
               f"mesh {_num(fine, '.2g')} mm, material {m.get('name')}: {rec['part_id']} ")
        if typ == "modal":
            fr = ", ".join(f"{f:.4g}" for f in r["frequencies_hz"][:6])
            s = (pre + f"first natural frequency {_num(r.get('first_freq_hz'), '.4g')} Hz "
                 f"(modes: {fr} Hz), error band {bands}")
            if r.get("band_hz"):
                s += (f"; {r['modes_in_band']} mode(s) inside {r['band_hz'][0]:.0f}-"
                      f"{r['band_hz'][1]:.0f} Hz")
        elif typ == "buckling":
            s = (pre + f"first buckling factor {_num(r.get('first_factor'), '.3g')} "
                 f"(critical load {_num(r.get('critical_load_n'), '.3g')} N), error band "
                 f"{bands}")
        else:
            s = (pre + f"max temperature {_num(r.get('max_temp_c'), '.4g')} C, min "
                 f"{_num(r.get('min_temp_c'), '.4g')} C, error band {bands}")
    if rec.get("warnings"):
        s += "; warnings: " + "; ".join(rec["warnings"])
    return s


def static_summary(rec: dict) -> str:
    r, m, sv, mesh = rec["results"], rec["material"], rec["solver"], rec["mesh"]
    band = r.get("error_band")
    if r.get("peak_excludes"):
        fixture = (f"peak excludes the fixed face; at fixture: "
                   f"{_num(r.get('max_von_mises_at_fixture_mpa'), '.3g')} MPa (safety factor "
                   f"incl. fixture {_num(r.get('safety_factor_incl_fixture'), '.2f')})")
    else:
        fixture = "peak includes the fixed face (every stressed node is on it)"
    s = (f"GUIDE ONLY - {sv['name']} {sv['version']}, fine mesh {mesh['fine']['size_mm']:.2g} mm "
         f"(coarse {mesh['coarse']['size_mm']:.2g} mm), material {m['name']} isotropic linear "
         f"elastic, strength x{m['knockdown']:.3g} FDM knock-down: {rec['part_id']} max von "
         f"Mises {r['max_von_mises_mpa']:.3g} MPa (error band "
         f"{'n/a' if band is None else format(band, '.1%')}), max displacement "
         f"{r['max_displacement_mm']:.3g} mm, safety factor {_num(r.get('safety_factor'), '.2f')}"
         f"; {fixture}")
    cases = r.get("cases") or []
    if len(cases) > 1:
        s += f"; governing case {r.get('governing_case')} of {len(cases)}"
    if rec.get("warnings"):
        s += "; warnings: " + "; ".join(rec["warnings"])
    return s


KEY_METRICS = {
    "static": [("max_von_mises_mpa", "max von Mises", "MPa"),
               ("safety_factor", "safety factor", ""),
               ("max_displacement_mm", "max displacement", "mm"),
               ("error_band_pct", "error band", "%")],
    "drop": [("max_von_mises_mpa", "max von Mises", "MPa"),
             ("safety_factor", "safety factor", ""),
             ("max_displacement_mm", "max displacement", "mm"),
             ("error_band_pct", "error band", "%")],
    "modal": [("first_freq_hz", "first frequency", "Hz"), ("modes_in_band", "modes in band", ""),
              ("error_band_pct", "error band", "%")],
    "buckling": [("first_factor", "buckling factor", ""),
                 ("critical_load_n", "critical load", "N"),
                 ("error_band_pct", "error band", "%")],
    "thermal": [("max_temp_c", "max temperature", "degC"),
                ("min_temp_c", "min temperature", "degC"),
                ("error_band_pct", "error band", "%")],
    "wind": [("cd", "Cd", ""), ("cl", "Cl", ""), ("drag_n", "drag", "N"),
             ("lift_n", "lift", "N"), ("error_band_pct", "unsteadiness", "%")],
}


def card(rec: dict) -> dict:
    """What a thread / Studies card renders: type, status, progress, key metrics with
    pass / warn / fail, and the URLs of the result GLB and chart data."""
    typ = rec.get("type", "static")
    r = rec.get("results") or {}
    metrics = [{"key": k, "label": lab, "unit": u, "value": r.get(k)}
               for k, lab, u in KEY_METRICS.get(typ, [])]
    status = None
    if rec.get("status") == "done":
        status = "warn" if rec.get("warnings") else "pass"
        checks = rec.get("checks") or []
        if any(c["status"] == "fail" for c in checks):
            status = "fail"
        elif typ in ("static", "drop") and (r.get("safety_factor") or 99) < 1:
            status = "fail"
    elif rec.get("status") in ("failed", "cancelled", "interrupted"):
        status = "fail"
    return {"sim_id": rec.get("sim_id"), "type": typ, "status": rec.get("status"),
            "verdict": status, "phase": rec.get("phase"), "pct": rec.get("pct"),
            "part_id": rec.get("part_id"), "label": GUIDE_LABEL,
            "fidelity_class": rec.get("fidelity_class"), "metrics": metrics,
            "checks": rec.get("checks") or [], "warnings": (rec.get("warnings") or [])[:4],
            "reason": rec.get("reason"), "run_id": rec.get("run_id"),
            "step_id": rec.get("step_id"), "urls": urls(rec.get("sim_id") or "")}


# ---- requirement checks (spec requirements; see design_review)

WARN_MARGIN = 0.1
# a pass from these is reported as warn: the number is not good enough to sign off on
LOW_FIDELITY = {"fem_estimate": "the drop load is an estimate (F = m g k)",
                "empirical": "the drag is an empirical shape-class estimate",
                "cfd_lbm": "the wind solver is low fidelity"}


def _verdict(measured: float | None, target: float, kind: str, band: float | None) -> str:
    if measured is None:
        return "warn"
    if kind == "min":
        ok, close = measured >= target, measured < target * (1 + WARN_MARGIN)
    else:
        ok, close = measured <= target, measured > target * (1 - WARN_MARGIN)
    if not ok:
        return "fail"
    if close or (band is not None and band > 0.1):
        return "warn"
    return "pass"


def check_requirements(rec: dict, reqs: list[dict]) -> list[dict]:
    """Requirements this study can answer (see design_review for the kinds)."""
    r = rec.get("results") or {}
    typ = rec.get("type")
    band = r.get("error_band")
    out = []
    for q in reqs:
        kind, target = q.get("kind"), q.get("target")
        meas, sense, unit = None, None, ""
        if typ in ("static", "drop") and kind == "min_safety_factor":
            case = q.get("load_case_id")
            sfs = r.get("sf_by_case") or {}
            meas = sfs.get(case) if case in sfs else r.get("safety_factor")
            sense = "min"
        elif typ in ("static", "drop") and kind == "max_deflection_mm":
            meas, sense, unit = r.get("max_displacement_mm"), "max", "mm"
        elif typ == "modal" and kind == "min_natural_freq_hz":
            meas, sense, unit = r.get("first_freq_hz"), "min", "Hz"
        elif typ == "modal" and kind == "avoid_band_hz":
            n = r.get("modes_in_band") or 0
            out.append({"requirement_id": q.get("id"), "kind": kind, "target": target,
                        "measured": n, "unit": "modes",
                        "status": "fail" if n else "pass", "sim_id": rec.get("sim_id")})
            continue
        elif typ == "wind" and kind == "max_drag_n":
            meas, sense, unit = r.get("drag_n"), "max", "N"
        elif typ == "wind" and kind == "max_cd":
            meas, sense = r.get("cd"), "max"
        elif typ == "buckling" and kind == "min_buckling_factor":
            meas, sense = r.get("first_factor"), "min"
        elif typ == "thermal" and kind == "max_temp_c":
            meas, sense, unit = r.get("max_temp_c"), "max", "degC"
        if sense is None or not isinstance(target, (int, float)):
            continue
        st = _verdict(meas, float(target), sense, band)
        note = None
        if rec.get("fidelity_class") in LOW_FIDELITY and st == "pass":
            st, note = "warn", f"passes, but {LOW_FIDELITY[rec['fidelity_class']]}"
        out.append({"requirement_id": q.get("id"), "kind": kind, "target": target,
                    "measured": meas, "unit": unit, "status": st, "sim_id": rec.get("sim_id"),
                    "note": note})
    return out


# ---- compare

COMPARE_ROWS = [
    ("mass_g", "mass", "g", "min"), ("max_von_mises_mpa", "max von Mises", "MPa", "min"),
    ("safety_factor", "safety factor", "", "max"),
    ("max_displacement_mm", "max displacement", "mm", "min"),
    ("first_freq_hz", "first frequency", "Hz", None),
    ("first_factor", "buckling factor", "", "max"),
    ("max_temp_c", "max temperature", "degC", "min"), ("cd", "Cd", "", "min"),
    ("cl", "Cl", "", None), ("drag_n", "drag", "N", "min"), ("lift_n", "lift", "N", None),
    ("error_band_pct", "error band", "%", "min")]


def compare(records: list[dict]) -> dict:
    cols = [{"sim_id": r["sim_id"], "type": r.get("type", "static"), "part_id": r.get("part_id"),
             "status": r.get("status"), "stale": r.get("stale"),
             "fidelity_class": r.get("fidelity_class"),
             "material": (r.get("material") or {}).get("name"),
             "created_at": r.get("created_at")} for r in records]
    rows = []
    for key, label, unit, better in COMPARE_ROWS:
        vals = [(r.get("results") or {}).get(key) for r in records]
        if all(v is None for v in vals):
            continue
        best = None
        nums = [(i, v) for i, v in enumerate(vals) if isinstance(v, (int, float))]
        if better and len(nums) > 1:
            best = (min if better == "min" else max)(nums, key=lambda x: x[1])[0]
        rows.append({"key": key, "label": label, "unit": unit, "values": vals, "best": best})
    return {"columns": cols, "rows": rows, "label": GUIDE_LABEL}


# ---- templates

def _tpl_path(home: Path) -> Path:
    return Path(home) / TEMPLATES_FILE


def load_templates(home: Path) -> list[dict]:
    try:
        d = json.loads(_tpl_path(home).read_text())
    except (OSError, ValueError):
        return []
    return [t for t in d if isinstance(t, dict) and TEMPLATE_ID_RE.fullmatch(t.get("id", ""))] \
        if isinstance(d, list) else []


def _save_templates(home: Path, tpls: list[dict]) -> None:
    p = _tpl_path(home)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(tpls, indent=1))
    os.replace(tmp, p)


def add_template(home: Path, name: str, type_id: str, settings: dict) -> dict:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise SimError("bad_setup", "a template needs a name of 1-60 characters")
    model = validate_settings(type_id, settings)
    tpls = load_templates(home)
    if any(t["name"].lower() == name.lower() for t in tpls):
        raise SimError("conflict", f"a template named {name!r} already exists",
                       hint="pick another name or delete the old one")
    if len(tpls) >= MAX_TEMPLATES:
        raise SimError("bad_setup", f"at most {MAX_TEMPLATES} templates")
    t = {"id": "tpl_" + secrets.token_hex(5), "name": name, "type": type_id,
         "settings": model.model_dump(mode="json"), "created_at": time.time()}
    _save_templates(home, [*tpls, t])
    return t


def delete_template(home: Path, tpl_id: str) -> bool:
    tpls = load_templates(home)
    left = [t for t in tpls if t["id"] != tpl_id]
    if len(left) == len(tpls):
        return False
    _save_templates(home, left)
    return True
