"""sim_* tools: studies on local mirror parts (gmsh + CalculiX, and the built-in wind solver).

Every result is a guide, not a guarantee: it names the solver, mesh / lattice, settings and an
error band or convergence history, and carries a fidelity_class. Records live next to the part:
parts/<part_id>/sims/<sim_id>/{result.json, result.glb, fields.npz}; solving happens in
<run workdir>/sim_tmp/<sim_id>/ and is removed. The lifecycle is in studies.py.

- sim_run(type, part_id, settings): any study type (static, modal, buckling, thermal, drop,
  wind) with full settings (schemas: sim_types_list / GET /sims/types).
- sim_static_run: the original one-case static study, a thin alias of sim_run(type=static).
- design_review: picks the studies a spec needs, runs them as child sim_run calls through the
  Gate, and returns a requirement-by-requirement pass / warn / fail table.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..cad import safe_io
from ..cad.tools import _check_part_id, _existing
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import studies
from .ccx import INSTALL_HINT, find_ccx, solver_status  # noqa: F401  (find_ccx: patchable)
from .environments import DIRECTIONS, ENVIRONMENTS, FACE_PRESETS
from .calibration import load_all as load_calibrations
from .materials import FDM_KNOCKDOWN, MATERIALS, load_custom
from .settings import check_face
from .studies import (GUIDE_LABEL, SIM_ID_RE, STUDY_TYPES, find_sim, list_sims,  # noqa: F401
                      read_record)
from .types import SimError

MESH_TIMEOUT_S = 180.0
SOLVE_TIMEOUT_S = 300.0
# One static study is at most two meshes + two solves (+ margin for post-processing). Studies
# queue for a study slot (studies.semaphore); the Gate's timeout covers the queue wait too, so
# the wait is bounded such that a study that gets the slot always has its full time left (else:
# ok=false, "busy").
STUDY_MAX_S = 2 * (MESH_TIMEOUT_S + SOLVE_TIMEOUT_S) + 60
TOOL_TIMEOUT_S = 2 * STUDY_MAX_S + 60
QUEUE_TIMEOUT_S = TOOL_TIMEOUT_S - STUDY_MAX_S - 30
# sim_run takes user timeouts (up to an hour per solve, several levels and cases): its Gate
# timeout is a backstop; every mesher / solver process has its own hard timeout.
SIM_RUN_TIMEOUT_S = 4 * 3600.0
REVIEW_TIMEOUT_S = 8 * 3600.0
CAVEATS = studies.CAVEATS_FEM + [studies.CAVEATS_STATIC_FIXTURE]
FaceSpec = str
Direction = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
_semaphore = studies.semaphore
result_summary = studies.summary


def _check_face(v: str) -> str:
    return check_face(v)


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
    fine: bool = Field(default=True, description="false: coarse mesh only (a fast screening "
                       "solve with no error band, e.g. inside a design study); confirm any "
                       "result you rely on with the default fine+coarse run")
    wait: bool | None = Field(None, description="false (the default for agents): return "
                              "{sim_id, status: running} at once and run the study in the "
                              "background; you get a note when it finishes (sim_wait / "
                              "sim_result_get read it). true: block until the result is in")

    @field_validator("fix")
    @classmethod
    def _fix(cls, v: str) -> str:
        return _check_face(v)


class SimRunArgs(BaseModel):
    type: Literal["static", "modal", "buckling", "thermal", "drop", "wind"]
    part_id: str | None = Field(None, description="the part to study")
    project_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,64}$",
                                   description="an assembly (build) instead of a part: "
                                   "static studies of every instance at its solved position; "
                                   "faces are f<i>@<instance id>, instances interact through "
                                   "settings.nonlinear.contacts (contact or bonded)")
    settings: dict[str, Any] = Field(default_factory=dict, description="the type's settings; "
                                     "see sim_types_list for every field, default and range")
    reason: str | None = Field(None, max_length=200, description="one line: why this study "
                               "(shown on the study card)")
    requirements: list[dict[str, Any]] = Field(default_factory=list, max_length=40,
                                               description="spec requirements to check "
                                               "pass / warn / fail against the result")
    estimate: bool = Field(False, description="the load is an estimate (e.g. F = m*g*k); the "
                           "card is labelled and a pass is reported as warn")
    wait: bool | None = Field(None, description="false (the default for agents): return "
                              "{sim_id, status: running} at once and run the study in the "
                              "background; you get a note when it finishes (sim_wait / "
                              "sim_result_get read it). true: block until the result is in")

    @model_validator(mode="after")
    def _target(self):
        if (self.part_id is None) == (self.project_id is None):
            raise ValueError("give exactly one of part_id or project_id")
        return self


def legacy_settings(fix: str, load: dict, material: str | None,
                    environment: str | None) -> dict:
    """sim_static_run's arguments as static study settings."""
    return {"load_cases": [{"name": "case 1", "supports": [{"kind": "fixed", "face": fix}],
                            "loads": [{"kind": "force", **load}]}],
            "material": {"name": material}, "environment": environment}


def _run_scope(a) -> list[str]:
    # an assembly study is in scope by its build id (like the assembly_* tools) or the
    # "project:<id>" form
    return [a.part_id] if a.part_id else [a.project_id, f"project:{a.project_id}"]


class ResultArgs(BaseModel):
    sim_id: str = Field(pattern=SIM_ID_RE.pattern)


SIM_WAIT_MAX_S = 600.0


class WaitArgs(BaseModel):
    sim_id: str = Field(pattern=SIM_ID_RE.pattern)
    timeout_s: float = Field(120, gt=0, le=SIM_WAIT_MAX_S, description="how long to wait, s "
                             f"(at most {SIM_WAIT_MAX_S:g}); a study still running then comes "
                             "back as status running")


class CompareArgs(BaseModel):
    sim_ids: list[str] = Field(min_length=2, max_length=8)

    @field_validator("sim_ids")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        for s in v:
            if not SIM_ID_RE.fullmatch(s):
                raise ValueError(f"bad sim id {s!r}")
        return v


class EnvListArgs(BaseModel):
    pass


class TypesArgs(BaseModel):
    pass


class ReviewArgs(BaseModel):
    part_id: str | None = None
    project_id: str | None = Field(None, description="the build whose spec to read (spec_get)")
    spec: dict[str, Any] | None = Field(None, description="the spec (goal, requirements, "
                                        "load_cases); default: spec_get of project_id")
    material: str | None = None
    budget_minutes: float = Field(60, ge=1, le=480)
    wind: dict[str, Any] | None = Field(None, description="wind settings overrides")

    @model_validator(mode="after")
    def _t(self):
        if self.part_id is None:
            raise ValueError("give part_id (assembly reviews are not supported yet)")
        return self


# ---- helpers

def _wait(ctx: ToolContext, wait: bool | None) -> bool:
    """A study blocks its call only when asked to; an agent's call defaults to the background
    (Plan 9 D4), the app's own calls and in-core callers (design_review, design studies) wait."""
    if wait is not None:
        return wait
    return not getattr(ctx, "agent_call", False)


def _bad(e: SimError) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint)


def _rec_scope(rec: dict) -> list[str]:
    """The scope keys a stored study answers to: its part, or its build."""
    if rec.get("part_id"):
        return [rec["part_id"]]
    pid = rec.get("project_id") or ""
    return [pid, f"project:{pid}"]


def _in_read_scope(ctx: ToolContext, part_id: str | list[str]) -> bool:
    pol = getattr(ctx, "policy", None)
    if pol is None:
        return True
    allowed = set(pol.edit_scope) | set(pol.read_scope)
    keys = [part_id] if isinstance(part_id, str) else part_id
    return "*" in allowed or any(k in allowed for k in keys)


def _load_rec(ctx: ToolContext, sim_id: str) -> dict:
    root = Path(ctx.workdir) / "parts"
    d = find_sim(root, sim_id)
    if d is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no study {sim_id}", hint="run one with sim_run")
    rec = read_record(d, d.parent.parent.parent)
    if not _in_read_scope(ctx, _rec_scope(rec)):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{sim_id} belongs to a part outside this "
                         "run's scope", hint="ask the user to widen the scope")
    return rec


async def _run(ctx: ToolContext, type_id: str, part_id: str, settings: dict, *,
               reason=None, requirements=None, inputs_extra=None, solve_timeout_s=None,
               coarse_only: bool = False, wait: bool = True):
    _check_part_id(part_id)
    part = _existing(ctx, part_id)
    try:
        model = studies.validate_settings(type_id, settings)
    except SimError as e:
        raise _bad(e) from None
    return await studies.run_study(ctx, type_id, part_id, part, model, reason=reason,
                                   requirements=requirements, inputs_extra=inputs_extra,
                                   solve_timeout_s=solve_timeout_s, coarse_only=coarse_only,
                                   wait=wait)


# ---- tools

@tool("sim_run", safety=SafetyClass.LOCAL_MUTATE, scope_keys=_run_scope,
      timeout_s=SIM_RUN_TIMEOUT_S,
      description="Run a study on a local mirror part. type: static (stress, several load "
                  "cases: force, pressure, moment, gravity, bolt bearing, remote force; "
                  "supports fixed / pinned / roller / symmetry), modal (natural frequencies, "
                  "flags modes in an excitation band), buckling (load factors), thermal (steady "
                  "state), drop (equivalent static impact load, an ESTIMATE) or wind (CFD: Cd, "
                  "Cl, drag and lift in N, surface Cp, yaw sweep; LOW FIDELITY). settings follow "
                  "the type's schema from sim_types_list (mesh, material incl. custom and FDM "
                  "infill, convergence, solver timeout and threads). Static settings.nonlinear "
                  "adds large deflection, plasticity, contact / bonded pairs and rigid "
                  "supports (slower; reports the load fraction reached, plastic strain and "
                  "contact pressure with a validated error band). project_id instead of "
                  "part_id runs a static study of the build's assembly (instance faces "
                  "f3@<instance id>; instances interact only through settings.nonlinear."
                  "contacts). Faces are presets or face "
                  "ids from mirror_measure / mirror_faces. Give a one-line `reason`; pass spec "
                  "`requirements` to get pass / warn / fail checks. By default the study runs in "
                  "the background: the call returns {sim_id, status: running} at once, keep "
                  "talking and working, and a note tells you when it finishes (wait: true "
                  "blocks instead). Results are a GUIDE ONLY: "
                  "always say so, with the solver, mesh and error band.")
async def sim_run(ctx: ToolContext, args: SimRunArgs) -> ToolOutput:
    extra = {"estimate": True} if args.estimate else None
    if args.project_id is not None:
        return await _run_assembly(ctx, args, extra)
    return await _run(ctx, args.type, args.part_id, args.settings, reason=args.reason,
                      requirements=args.requirements, inputs_extra=extra,
                      wait=_wait(ctx, args.wait))


async def _run_assembly(ctx: ToolContext, args: SimRunArgs, extra: dict | None):
    from . import assembly_study
    if args.type != "static":
        raise MisesError(ErrorCode.INVALID_INPUT, f"assembly studies are static only (not "
                         f"{args.type})", hint="study each part with its part_id")
    try:
        model = studies.validate_settings("static", args.settings)
    except SimError as e:
        raise _bad(e) from None
    target = assembly_study.resolve(ctx, args.project_id)
    return await studies.run_study(ctx, "static", None, None, model, reason=args.reason,
                                   requirements=args.requirements,
                                   inputs_extra={"project_id": target["project_id"],
                                                 **(extra or {})},
                                   assembly=target, wait=_wait(ctx, args.wait))


@tool("sim_static_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=TOOL_TIMEOUT_S,
      description="Run a static linear-elastic stress study (gmsh + CalculiX) on a local mirror "
                  "part: one face held fixed, a force spread over another face. Faces are "
                  "presets (bottom, top, x-min, ...) or face ids from mirror_measure face_list "
                  "(largest faces) or mirror_faces (all). Returns max von Mises (MPa, excluding "
                  "the fixed face, whose peak is reported separately), max displacement (mm), "
                  "safety factor (yield x FDM knock-down 0.7 / peak) and an error band from a "
                  "coarse vs fine mesh. For more load cases, supports or other study types use "
                  "sim_run. Runs in the background by default, like sim_run (wait: true blocks). "
                  "Results are a GUIDE ONLY: always say so when you report them.")
async def sim_static_run(ctx: ToolContext, args: StaticArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    _existing(ctx, args.part_id)
    if args.fix == args.load.face:
        raise MisesError(ErrorCode.INVALID_INPUT, "fix and load are the same face",
                         hint="hold one face and load another")
    settings = legacy_settings(args.fix, args.load.model_dump(), args.material, args.environment)
    extra = {"fix": args.fix, "load": args.load.model_dump()}
    if not args.fine:  # only screening runs record it, so default records are unchanged
        extra["fine"] = False
    return await _run(ctx, "static", args.part_id, settings, inputs_extra=extra,
                      solve_timeout_s=SOLVE_TIMEOUT_S, coarse_only=not args.fine,
                      wait=_wait(ctx, args.wait))


@tool("sim_types_list", safety=SafetyClass.READ,
      description="List the study types (static, modal, buckling, thermal, drop, wind) with "
                  "their solver, fidelity class, availability and the JSON schema of their "
                  "settings (defaults, ranges, units, docs).")
async def sim_types_list(ctx: ToolContext, args: TypesArgs) -> ToolOutput:
    ts = studies.types_list()
    s = "; ".join(f"{t['id']} ({t['fidelity_class']}{'' if t['available'] else ', unavailable'})"
                  for t in ts)
    return ToolOutput(summary=f"{len(ts)} study types: {s}", data={"types": ts})


@tool("sim_result_get", safety=SafetyClass.READ,
      description="Return a stored study result by sim_id (inputs, solver, mesh, material, "
                  "results, error band, warnings, checks; stale=true if the part changed "
                  "since).")
async def sim_result_get(ctx: ToolContext, args: ResultArgs) -> ToolOutput:
    return _result(_load_rec(ctx, args.sim_id))


def _result(rec: dict) -> ToolOutput:
    sim_id = rec["sim_id"]
    if rec.get("status") == "running":
        pct = rec.get("pct")
        return ToolOutput(summary=f"{sim_id} is still running ({rec.get('stage')}"
                          + (f", {pct:g}%" if isinstance(pct, (int, float)) else "") + ")",
                          data={**studies.compact(rec), "stream": "sim"})
    summary = (f"{sim_id}: " + studies.summary(rec)
               + ("; STALE: the part changed since this study" if rec["stale"] else ""))
    return ToolOutput(summary=summary, data=studies.compact(rec), step_kind=StepKind.SIM_RUN)


@tool("sim_wait", safety=SafetyClass.READ, timeout_s=SIM_WAIT_MAX_S + 30,
      description="Wait for a background study (sim_run / sim_static_run) to finish, up to "
                  f"timeout_s (at most {SIM_WAIT_MAX_S:g} s), and return its result like "
                  "sim_result_get; still running then: status running. You need not wait: a "
                  "note arrives when it finishes.")
async def sim_wait(ctx: ToolContext, args: WaitArgs) -> ToolOutput:
    _load_rec(ctx, args.sim_id)                     # exists and is in scope
    await studies.wait_for(args.sim_id, args.timeout_s)
    return _result(_load_rec(ctx, args.sim_id))


@tool("sim_compare", safety=SafetyClass.READ,
      description="Compare 2-8 studies side by side (mass, stress, safety factor, displacement, "
                  "frequency, buckling factor, temperature, Cd, drag, error band).")
async def sim_compare(ctx: ToolContext, args: CompareArgs) -> ToolOutput:
    recs = [_load_rec(ctx, s) for s in args.sim_ids]
    table = studies.compare(recs)
    lines = [f"{r['label']}: " + ", ".join("n/a" if v is None else f"{v:.4g}"
                                           if isinstance(v, (int, float)) else str(v)
                                           for v in r["values"]) for r in table["rows"]]
    return ToolOutput(summary=f"compare {', '.join(args.sim_ids)} ({GUIDE_LABEL}): "
                      + "; ".join(lines), data=table)


@tool("sim_cancel", safety=SafetyClass.LOCAL_MUTATE,
      description="Cancel a running study by sim_id (kills its mesher / solver).")
async def sim_cancel(ctx: ToolContext, args: ResultArgs) -> ToolOutput:
    rec = _load_rec(ctx, args.sim_id)
    pol = getattr(ctx, "policy", None)
    if pol is not None and "*" not in pol.edit_scope and not any(
            k in pol.edit_scope for k in _rec_scope(rec)):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{args.sim_id} is outside this run's scope")
    if not studies.cancel(args.sim_id):
        return ToolOutput(ok=False, summary=f"{args.sim_id} is not running "
                          f"(status {rec.get('status')})", data={"status": rec.get("status")})
    return ToolOutput(summary=f"cancelling {args.sim_id}", data={"sim_id": args.sim_id})


@tool("sim_env_list", safety=SafetyClass.READ,
      description="List study presets: environments, fixture/load face presets, load "
                  "directions, materials (with FDM knock-down), study types and whether the "
                  "solver is installed.")
async def sim_env_list(ctx: ToolContext, args: EnvListArgs) -> ToolOutput:
    st = solver_status()
    home = studies._home(ctx)
    data = {"environments": ENVIRONMENTS, "face_presets": list(FACE_PRESETS),
            "face_ids": "or a face id (\"f3\") from mirror_measure face_list / mirror_faces",
            "directions": list(DIRECTIONS), "knockdown": FDM_KNOCKDOWN,
            "materials": [m.to_dict() for m in [*MATERIALS.values(), *load_custom(home)]],
            "solvers": st, "study_types": list(STUDY_TYPES),
            "calibrations": [{k: r.get(k) for k in ("id", "material", "printer", "basis")}
                             | {"confidence": r["fit"].get("confidence")}
                             for r in load_calibrations(home)]}
    from . import cfd as cfd_mod     # S2: the optional OpenFOAM wind solver (read-only check)
    docker, why = await asyncio.to_thread(cfd_mod.openfoam_available)
    data["solvers"] = {**st, "openfoam": {"available": docker is not None, "why_not": why}}
    s = (f"{len(ENVIRONMENTS)} environments, {len(data['materials'])} materials, study types "
         f"{', '.join(STUDY_TYPES)}; CalculiX "
         + ("found" if st["ccx"]["found"] else f"MISSING (install: {INSTALL_HINT})")
         + "; OpenFOAM wind solver " + ("ok" if docker else f"unavailable ({why})"))
    return ToolOutput(summary=s, data=data)


# ---- design review

STRESS_KINDS = {"min_safety_factor", "max_deflection_mm"}
MODAL_KINDS = {"min_natural_freq_hz", "avoid_band_hz"}
WIND_KINDS = {"max_drag_n", "max_cd"}
DROP_KINDS = {"survive_drop_m"}


STUDY_KINDS = (STRESS_KINDS | MODAL_KINDS | WIND_KINDS | DROP_KINDS
               | {"min_buckling_factor", "max_temp_c"})
SPEC_EXTRAS = ("excitation", "excitation_band_hz", "wind", "drop")


def normalize_spec(spec: dict) -> dict:
    """The contract's requirement kinds stop at `custom`; a custom requirement whose target is
    {<study kind>: value} (e.g. {"max_drag_n": 2}, {"avoid_band_hz": [380, 420]}) becomes
    that kind, and {"excitation": {...}} / {"wind": {...}} / {"drop": {...}} targets fill the
    spec's study inputs."""
    spec = dict(spec)
    reqs = []
    for r in spec.get("requirements") or []:
        if isinstance(r, dict) and r.get("kind") == "custom" and isinstance(r.get("target"), dict):
            t = r["target"]
            for k in SPEC_EXTRAS:
                if k in t and k not in spec:
                    spec[k] = t[k]
            key = next((k for k in t if k in STUDY_KINDS), None)
            if key is not None:
                r = {**r, "kind": key, "target": t[key]}
            elif any(k in t for k in SPEC_EXTRAS):
                continue  # only carried study inputs
        reqs.append(r)
    spec["requirements"] = reqs
    return spec


def plan_review(spec: dict, wind_overrides: dict | None = None,
                material: str | None = None) -> list[dict]:
    """The studies a spec needs: [{type, case, settings, requirements}]. Static: one study per
    load case; modal when an excitation band (or a frequency requirement) is given; wind when a
    drag requirement or spec.wind is present; drop when spec.drop or a drop requirement is."""
    spec = normalize_spec(spec)
    reqs = [r for r in spec.get("requirements") or [] if isinstance(r, dict)]
    mat = {"name": material} if material else {}
    plan = []
    for lc in spec.get("load_cases") or []:
        if not isinstance(lc, dict) or not lc.get("fix") or not lc.get("load"):
            continue
        cid = str(lc.get("id") or lc.get("name") or f"case{len(plan) + 1}")
        load = {"kind": "force", "face": lc["load"], "newtons": lc.get("newtons"),
                "direction": lc.get("direction") or "-z"}
        rq = [r for r in reqs if r.get("kind") in STRESS_KINDS
              and r.get("load_case_id") in (None, cid)]
        rq = [{**r, "load_case_id": cid} for r in rq]
        plan.append({"type": "static", "case": cid, "requirements": rq,
                     "settings": {"load_cases": [{"name": cid[:60], "supports": [
                         {"kind": "fixed", "face": lc["fix"]}], "loads": [load],
                         "note": str(lc.get("note") or "")[:300]}], "material": mat},
                     "estimate": bool(lc.get("estimate"))})
    band = spec.get("excitation_band_hz")
    exc = spec.get("excitation")
    mreq = [r for r in reqs if r.get("kind") in MODAL_KINDS]
    if band or exc or mreq:
        ms: dict = {"material": mat, "supports": []}
        fix = next((lc.get("fix") for lc in spec.get("load_cases") or []
                    if isinstance(lc, dict) and lc.get("fix")), None)
        if fix:
            ms["supports"] = [{"kind": "fixed", "face": fix}]
        if band:
            ms["band_hz"] = band
        elif exc:
            ms["excitation"] = exc
        for r in mreq:
            if r.get("kind") == "avoid_band_hz" and isinstance(r.get("target"), list):
                ms["band_hz"] = r["target"]
        plan.append({"type": "modal", "case": "modal", "settings": ms, "requirements": mreq})
    wreq = [r for r in reqs if r.get("kind") in WIND_KINDS]
    if wreq or spec.get("wind"):
        ws = {**(spec.get("wind") or {}), **(wind_overrides or {})}
        plan.append({"type": "wind", "case": "wind", "settings": ws, "requirements": wreq})
    dreq = [r for r in reqs if r.get("kind") in DROP_KINDS]
    if dreq or spec.get("drop"):
        d = dict(spec.get("drop") or {})
        if dreq and "drop_m" not in d and isinstance(dreq[0].get("target"), (int, float)):
            d["drop_m"] = dreq[0]["target"]
        d.setdefault("impact_face", "bottom")
        d["material"] = mat
        sf = [r for r in reqs if r.get("kind") == "min_safety_factor"
              and r.get("load_case_id") in (None, "drop")]
        plan.append({"type": "drop", "case": "drop", "settings": d,
                     "requirements": [{**r, "load_case_id": "impact"} for r in sf]})
    return plan


def _metric_rows(spec: dict, metrics: dict, material) -> list[dict]:
    rows = []
    for r in spec.get("requirements") or []:
        if not isinstance(r, dict):
            continue
        kind, target = r.get("kind"), r.get("target")
        if kind == "max_mass_g" and isinstance(target, (int, float)):
            mass = metrics.get("volume_mm3", 0) * material.density_g_cm3 / 1000.0
            st = studies._verdict(mass, float(target), "max", None)
            rows.append({"requirement_id": r.get("id"), "kind": kind, "target": target,
                         "measured": round(mass, 2), "unit": "g", "status": st, "sim_id": None,
                         "note": f"from the volume x {material.name} density (solid)"})
        elif kind == "envelope_mm" and isinstance(target, list) and len(target) == 3:
            size = sorted(metrics.get("bbox", {}).get("size", [0, 0, 0]))
            ok = all(a <= b + 1e-6 for a, b in zip(size, sorted(float(t) for t in target)))
            rows.append({"requirement_id": r.get("id"), "kind": kind, "target": target,
                         "measured": metrics.get("bbox", {}).get("size"), "unit": "mm",
                         "status": "pass" if ok else "fail", "sim_id": None,
                         "note": "bounding box, any orientation"})
        elif kind in ("printable", "mount_pattern", "custom"):
            rows.append({"requirement_id": r.get("id"), "kind": kind, "target": target,
                         "measured": None, "unit": "", "status": "warn", "sim_id": None,
                         "note": "not checked by a study; review by hand"})
    return rows


async def _get_spec(ctx: ToolContext, args: ReviewArgs) -> dict | None:
    if args.spec is not None:
        return args.spec
    gate = _gate(ctx)
    try:
        if gate is None or not gate.registry.list(["spec_get"]):
            return None
    except Exception:  # noqa: BLE001
        return None
    # no project_id: spec_get({}) resolves to the run's own build, so the verifier (which has no
    # spec_get of its own, and may not know the id) still finds its spec. An out-of-scope id is
    # surfaced as itself, not swallowed into "no spec to review against".
    payload = {"project_id": args.project_id} if args.project_id else {}
    out = await gate.call(ctx.run_id, "spec_get", payload,
                          call_id=f"{ctx.call_id}.spec", workdir=Path(ctx.workdir))
    if not out.ok:
        return None
    d = out.data
    return d.get("spec") if isinstance(d.get("spec"), dict) else d


MIN_CHILD_S = 30.0
CHILD_GRACE_S = 60.0   # post-processing after the last solve


def _rec_by_call(parts_root: Path, part_id: str, call_id: str) -> dict | None:
    for f in (Path(parts_root) / part_id / "sims").glob("sim_*/result.json"):
        try:
            rec = safe_io.read_json(f.parent, f.name)    # never follows a link (C6)
        except (OSError, ValueError, safe_io.UnsafePath):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("call_id") == call_id:
            return rec
    return None


def _gate(ctx: ToolContext):
    sv = getattr(ctx, "services", None) or {}
    if sv.get("gate") is not None:
        return sv["gate"]
    rt = sv.get("runtime")
    return getattr(rt, "gate", None)


def _parallel(ctx: ToolContext) -> int:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    try:
        lim = int(rt.parallel_limit()) if rt is not None else 1
    except Exception:  # noqa: BLE001
        lim = 1
    return max(1, min(lim, studies.study_limit()))


@tool("design_review", safety=SafetyClass.READ, scope_arg="part_id", timeout_s=REVIEW_TIMEOUT_S,
      implied_classes={SafetyClass.LOCAL_MUTATE},  # runs its child studies as sim_run calls
      description="Review a part against its spec: picks the studies the spec needs (static "
                  "per load case, modal for an excitation band, wind for a drag requirement, "
                  "drop for an impact), runs them as sim_run calls (each one a local_mutate "
                  "tool call through the Gate), and returns a requirement-by-requirement "
                  "pass / warn / fail table with the study ids. The spec comes from `spec` or "
                  "spec_get of project_id. Results are a GUIDE ONLY; cite the study ids.")
async def design_review(ctx: ToolContext, args: ReviewArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    part = _existing(ctx, args.part_id)
    spec = await _get_spec(ctx, args)
    if not spec:
        raise MisesError(ErrorCode.INVALID_INPUT, "no spec to review against",
                         hint="pass spec, or a project_id whose spec is set (spec_set)")
    spec = normalize_spec(spec)
    metrics = safe_io.read_json(part, "metrics.json") or {}
    try:
        material = studies.resolve_material(None, args.material or studies.default_material(ctx),
                                             studies._home(ctx))
    except SimError as e:
        raise _bad(e) from None
    plan = plan_review(spec, args.wind, args.material)
    gate = _gate(ctx)
    if plan and gate is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "design_review needs the Gate to run studies")
    deadline = time.monotonic() + args.budget_minutes * 60
    lim = asyncio.Semaphore(_parallel(ctx))
    results: list[dict | None] = [None] * len(plan)

    async def one(i: int, p: dict) -> None:
        async with lim:
            if time.monotonic() > deadline:
                results[i] = {"plan": p, "error": "not run: the review's time budget ran out"}
                return
            settings = dict(p["settings"])
            left = max(MIN_CHILD_S, deadline - time.monotonic())
            if p["type"] == "wind":
                settings["timeout_s"] = min(float(settings.get("timeout_s", 600)), left)
            else:  # each solve gets at most the time left (a study solves >= 2 meshes)
                solver = dict(settings.get("solver") or {})
                solver["timeout_s"] = max(5.0, min(float(solver.get("timeout_s", 300)),
                                                   left / 2))
                settings["solver"] = solver
            call_id = f"{ctx.call_id}.r{i + 1}"
            payload = {"type": p["type"], "part_id": args.part_id, "settings": settings,
                       "wait": True,      # the review needs every child's result
                       "requirements": p["requirements"], "estimate": bool(p.get("estimate")),
                       "reason": f"design review: {p['type']} {p['case']}"[:200]}
            try:  # the budget is a hard cap on every child study too
                out = await asyncio.wait_for(
                    gate.call(ctx.run_id, "sim_run", payload, call_id=call_id,
                              workdir=Path(ctx.workdir)), left + CHILD_GRACE_S)
                results[i] = {"plan": p, "out": out, "call_id": call_id}
            except asyncio.TimeoutError:
                results[i] = {"plan": p, "call_id": call_id,
                              "error": "stopped: the review's time budget ran out"}
            except MisesError as e:
                results[i] = {"plan": p, "call_id": call_id, "error": f"{e.code}: {e.message}"}

    await asyncio.gather(*(one(i, p) for i, p in enumerate(plan)))
    table = _metric_rows(spec, metrics, material)
    studies_run = []
    for res in results:
        p = res["plan"]
        out = res.get("out")
        data = out.data if out is not None else {}
        if out is not None and not data.get("sim_id") and res.get("call_id"):
            # output spilled or trimmed: the record on disk is the source of truth
            rec = _rec_by_call(Path(ctx.workdir) / "parts", args.part_id, res["call_id"])
            if rec is not None:
                data = {"sim_id": rec["sim_id"], "checks": rec.get("checks"),
                        "card": studies.card(rec)}
        sid = data.get("sim_id")
        studies_run.append({"type": p["type"], "case": p["case"], "sim_id": sid,
                            "ok": bool(out and out.ok), "error": res.get("error") or (
                                None if out is None or out.ok else out.summary),
                            "url": f"/sims/{sid}" if sid else None,
                            "card": data.get("card")})
        checks = data.get("checks") if out is not None and out.ok else None
        if checks:
            for c in checks:
                row = {**c, "study": p["type"], "case": p["case"], "url": f"/sims/{sid}"}
                if p.get("estimate") and row.get("status") == "pass":
                    # physics honesty: an estimated load (F = m*g*k) never comes back as a clean
                    # pass; it passes the estimate only
                    row["status"] = "warn"
                    row["estimate"] = True
                    row["note"] = ((row.get("note") + "; ") if row.get("note") else "") + \
                        "passes, but the load is an estimate (F = m*g*k)"
                table.append(row)
        else:
            # the study produced no checks: it was denied, errored or timed out, or ran but gave
            # no requirement verdict. Either way we have no evidence, so the row is "not checked"
            # (warn), never "fail" — a fail must mean a study ran and a requirement failed.
            note = (res.get("error") or (out.summary[:200] if out else None)
                    or "not checked: the study produced no result")
            for r in p["requirements"]:
                table.append({"requirement_id": r.get("id"), "kind": r.get("kind"),
                              "target": r.get("target"), "measured": None, "unit": "",
                              "status": "warn", "checked": False, "sim_id": sid,
                              "study": p["type"], "case": p["case"],
                              "url": f"/sims/{sid}" if sid else None, "note": note})
    # requirements that no study or metric answered
    answered = {(r.get("requirement_id"), r.get("case")) for r in table}
    ids_answered = {a for a, _ in answered}
    for r in spec.get("requirements") or []:
        if isinstance(r, dict) and r.get("id") not in ids_answered:
            table.append({"requirement_id": r.get("id"), "kind": r.get("kind"),
                          "target": r.get("target"), "measured": None, "unit": "",
                          "status": "warn", "sim_id": None,
                          "note": "no study answers this requirement (missing load case?)"})
    order = {"fail": 0, "warn": 1, "pass": 2}
    verdict = min((r["status"] for r in table), key=lambda s: order[s], default="warn")
    if any(not s["ok"] for s in studies_run):
        verdict = "fail" if verdict == "fail" else "warn"
    head = (f"design review of {args.part_id} ({GUIDE_LABEL}): {verdict.upper()}; "
            f"{sum(r['status'] == 'pass' for r in table)} pass, "
            f"{sum(r['status'] == 'warn' for r in table)} warn, "
            f"{sum(r['status'] == 'fail' for r in table)} fail")
    rows = "; ".join(f"{r.get('requirement_id')} {r['kind']}: {r['status']}"
                     + (f" ({r['measured']:.4g}{r.get('unit') or ''} vs {r['target']})"
                        if isinstance(r.get("measured"), (int, float)) else "")
                     + (f" [{r['sim_id']}]" if r.get("sim_id") else "") for r in table)
    data = {"verdict": verdict, "table": table, "studies": studies_run,
            "label": GUIDE_LABEL, "part_id": args.part_id}
    _fit_review(data)
    return ToolOutput(summary=head + (": " + rows if rows else ""), data=data,
                      warnings=[s["error"] for s in studies_run if s.get("error")])


REVIEW_SOFT_MAX_BYTES = 14 * 1024


def _fit_review(data: dict) -> None:
    """Keep design_review under the Gate's 16 KB spill limit (many load cases + modal + a wind
    sweep can approach it): drop the cards' charts first, then trim their warnings. The verdict,
    the table and every study id always stay; the full cards are at each study's url."""
    import json

    def size() -> int:
        return len(json.dumps(data, default=float).encode())
    if size() <= REVIEW_SOFT_MAX_BYTES:
        return
    for s in data["studies"]:
        if isinstance(s.get("card"), dict):
            s["card"] = {k: v for k, v in s["card"].items() if k != "charts"}
    if size() > REVIEW_SOFT_MAX_BYTES:
        for s in data["studies"]:
            if isinstance(s.get("card"), dict):
                s["card"]["warnings"] = (s["card"].get("warnings") or [])[:1]
                s["card"]["checks"] = []
    if size() > REVIEW_SOFT_MAX_BYTES:
        for s in data["studies"]:
            s["card"] = None
    data["trimmed"] = True


def register_sim_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (sim_static_run, sim_run, sim_types_list, sim_result_get, sim_wait, sim_compare,
              sim_cancel, sim_env_list, design_review):
        if not registry.list([t.name]):
            registry.register(t)
    from .calibration_tools import register_calibration_tools  # S3
    register_calibration_tools(registry)
