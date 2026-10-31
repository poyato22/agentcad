"""Engineering-loop tools: interview plan, spec, spec check, load estimates, design studies.

interview_plan suggests the interview; spec_get / spec_set hold the project's spec; spec_check
measures a part against it; study_run searches a parametric part's variables for the best
variant that meets the spec; study_apply applies a chosen variant to the live part;
loads_impact turns a drop into an equivalent static load.

A study never touches the user's live part. Each evaluation builds a scratch copy of the part
under <home>/studies/<study_id>/e<k>/parts/<part_id>/ and runs its stress studies there (coarse
mesh in the loop; the winner is confirmed on the fine mesh). Only study_apply changes the live
part, through mirror_part_set_vars, so user edits are never silently overwritten.

Specs and studies are keyed by project_id (a TEXT key; Task C1 owns the projects table). The
run's project is read defensively so this works before or after that migration; an explicit
project_id must match it once runs carry one.
"""
from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .. import hardware
from .. import edits as _edits
from ..cad import safe_io, skeleton
from ..cad.runner import run_build
from ..cad.tools import PART_ID_RE, SetVarsArgs, _after_build, _existing, mirror_part_set_vars
from ..events import Event
from ..sim.mesh import FACE_ID_RE
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from .interview import build_interview
from .loads import DEFAULT_IMPACT_K, impact_equivalent
from .spec import LOAD_CASE_KINDS, LoadCase, Requirement, Spec, evaluate_spec
from .study import (MAX_EVALS, MAX_MINUTES, Budget, Objective, Variable, Weight, finalize,
                    run_study, weights_from_priorities)

DEFAULT_PROJECT = "default"
STUDY_ID_PREFIX = "study_"
# The winner's fine-mesh confirmation runs after the loop's own budget, capped here.
CONFIRM_MAX_S = 600.0
# Modal / wind targets are not searched per evaluation (too costly): they are checked on the
# study's winners only, at most DYNAMIC_MAX_VARIANTS of them, each study capped at DYNAMIC_MAX_S.
DYNAMIC_MAX_VARIANTS = 3
DYNAMIC_MAX_S = 300.0
DYNAMIC_TYPES = ("modal", "wind")
# The Gate's timeout covers the loop budget, the confirmation, the winners' modal/wind checks
# and some margin.
STUDY_TOOL_TIMEOUT_S = (MAX_MINUTES * 60 + CONFIRM_MAX_S
                        + DYNAMIC_MAX_VARIANTS * len(DYNAMIC_TYPES) * DYNAMIC_MAX_S + 120)
# Rough peak memory of one mesh + solve, for how many evaluations may overlap.
SOLVE_GB = 4
MAX_PARALLEL_EVALS = 3


def _run_project(ctx: ToolContext) -> str | None:
    try:  # C1 adds runs.project_id; read it defensively so C3 stands alone
        pid = getattr(ctx.store.get_run(ctx.run_id), "project_id", None)
        return str(pid) if pid else None
    except Exception:  # noqa: BLE001
        return None


def _resolve_project(ctx: ToolContext, explicit: str | None) -> str:
    """The run's project. An explicit id is allowed only when it is that project (or when runs
    carry no project yet), so an agent cannot read or overwrite another build's spec."""
    own = _run_project(ctx)
    if explicit and own and explicit != own:
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"project {explicit!r} is not this run's build",
                         hint=f"this run works on project {own!r}")
    return explicit or own or DEFAULT_PROJECT


def _check_part(part_id: str) -> None:
    if not PART_ID_RE.match(part_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                         hint=f"part ids match {PART_ID_RE.pattern}")


def _load_spec(ctx: ToolContext, pid: str) -> Spec:
    return Spec.model_validate(ctx.store.get_spec(pid) or Spec().model_dump())


# ---- seams (tests replace these; the real ones need CalculiX)

def _find_ccx() -> str | None:
    from ..sim.ccx import find_ccx
    return find_ccx()


async def _run_sim(ctx: ToolContext, args) -> ToolOutput:
    from ..sim.tools import sim_static_run
    return await sim_static_run.handler(ctx, args)


async def _run_dyn_sim(ctx: ToolContext, args) -> ToolOutput:
    """A modal / wind study of a kept variant (the pluggable evaluator; tests replace it)."""
    from ..sim.tools import sim_run
    return await sim_run.handler(ctx, args)


def _static_args(part_id: str, case: LoadCase, *, fine: bool):
    from ..sim.tools import StaticArgs
    return StaticArgs(part_id=part_id, fix=case.fix, fine=fine, wait=True,
                      load={"face": case.load, "newtons": case.newtons,
                            "direction": case.direction})


# ---- args

class ProjectArg(BaseModel):
    project_id: str | None = Field(default=None, description="the build this spec belongs to; "
                                   "defaults to (and must be) the run's project")


class InterviewArgs(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


class SpecSetArgs(BaseModel):
    project_id: str | None = None
    base_updated_at: float | None = Field(
        default=None, description="the spec's updated_at you read (spec_get); default: this "
        "run's last spec_get. If the spec changed since (e.g. the user edited it), the write is "
        "refused with the current spec, so you re-apply on top of it")
    goal: str = Field(default="", max_length=2000)
    requirements: list[Requirement] = Field(default_factory=list)
    load_cases: list[LoadCase] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)


class SpecCheckArgs(BaseModel):
    part_id: str
    project_id: str | None = None


class VariableArg(BaseModel):
    name: str
    lo: float
    hi: float


class WeightArg(BaseModel):
    metric: str = Field(description="mass_g, min_sf, max_disp_mm, or another metric key")
    goal: Literal["min", "max"] = "min"
    weight: float = Field(default=1.0, gt=0, le=100)


class StudyRunArgs(BaseModel):
    part_id: str
    variables: list[VariableArg] = Field(min_length=1, max_length=6)
    objective: str = Field(default="min_mass", description="min_mass, max:min_sf "
                           "(strength-first), min:max_disp_mm (stiffness-first), "
                           "min:<metric> / max:<metric>; ignored when weights or priorities "
                           "are given")
    weights: list[WeightArg] = Field(default_factory=list, max_length=6)
    priorities: list[str] = Field(default_factory=list, max_length=8,
                                  description="the user's ranking, first = most important "
                                  "(weight, impact strength, stiffness, print time); becomes "
                                  "a weighted objective")
    project_id: str | None = None
    max_evals: int = Field(default=MAX_EVALS, ge=1, le=MAX_EVALS)
    max_minutes: float = Field(default=MAX_MINUTES, gt=0, le=MAX_MINUTES)


class StudyGetArgs(BaseModel):
    study_id: str = Field(max_length=64)
    page: int | None = Field(default=None, ge=0, description="page through every evaluation "
                             "(50 per page) instead of the default best + Pareto + lightest "
                             "feasible summary")
    fields: list[str] | None = Field(default=None, max_length=8, description="keep only these "
                                     "keys of each evaluation (n, vars, metrics, feasible, "
                                     "pareto)")


class StudyApplyArgs(BaseModel):
    study_id: str = Field(max_length=64)
    part_id: str = Field(description="the study's part (the live part that changes)")
    iteration: int = Field(ge=0, description="the n of the variant to apply")
    base_version: int | None = Field(default=None, description="the part version you read; "
                                     "a mismatch is refused as stale")
    allow_infeasible: bool = False


class LoadsImpactArgs(BaseModel):
    mass_kg: float = Field(gt=0, le=1e4)
    drop_m: float = Field(ge=0, le=1e4)
    k: float = Field(default=DEFAULT_IMPACT_K, gt=0, le=1e3)


# ---- interview

@tool("interview_plan", safety=SafetyClass.READ,
      description="Suggested requirements interview for a request: at most 6 questions in "
                  "batches of up to 3, each with options. Ask each batch with one ui_ask_user call "
                  "(a one-line `question` intro plus the batch as `questions`, as is), then "
                  "write the spec from the answers.")
async def interview_plan(ctx: ToolContext, args: InterviewArgs) -> ToolOutput:
    iv = build_interview(args.goal)
    batches = [[{"id": q.id, "prompt": q.text, "options": q.options, "multi": q.kind == "multi",
                 "rank": q.kind == "rank", "allow_text": q.allow_free} for q in b]
               for b in iv.batches]
    n = sum(len(b) for b in batches)
    return ToolOutput(summary=f"{iv.domain} interview: {n} question(s) in {len(batches)} "
                              "batch(es)", data={"domain": iv.domain, "batches": batches})


# ---- spec tools

@tool("spec_get", safety=SafetyClass.READ,
      description="Return the project's engineering spec: goal, requirements, load cases and the "
                  "interview answers. An empty spec if none is written yet.")
async def spec_get(ctx: ToolContext, args: ProjectArg) -> ToolOutput:
    pid = _resolve_project(ctx, args.project_id)
    spec = _load_spec(ctx, pid)
    at = _spec_updated_at(ctx, pid)
    _spec_seen[(ctx.run_id, pid)] = at
    s = (f"spec for {pid}: {len(spec.requirements)} requirement(s), "
         f"{len(spec.load_cases)} load case(s)")
    return ToolOutput(summary=s, data={"project_id": pid, "spec": spec.model_dump(),
                                       "updated_at": at})


# (run id, project) -> the spec updated_at that run last read or wrote. spec_set without
# base_updated_at is checked against it, as C6 does for parts: a run cannot overwrite a spec
# edit (by the user) that it never saw. In memory; after a restart a run re-reads.
_spec_seen: dict[tuple[str | None, str], float | None] = {}
_NEVER = object()


def _spec_updated_at(ctx: ToolContext, pid: str) -> float | None:
    getter = getattr(ctx.store, "get_spec_updated_at", None)
    return getter(pid) if getter is not None else None


def _build_scope(args) -> list[str]:
    """Build-wide tools (spec_set) need the whole build in scope: "*" or "project:<id>". A run
    narrowed to some parts cannot rewrite the build's spec."""
    return [f"project:{getattr(args, 'project_id', None) or ''}"]


@tool("spec_set", safety=SafetyClass.LOCAL_MUTATE, scope_keys=_build_scope,
      description="Write the project's engineering spec (goal, requirements, load cases, interview "
                  "answers). Requirement kinds: max_mass_g, min_safety_factor, max_deflection_mm, "
                  "envelope_mm, mount_pattern, printable, custom. Drag and vibration targets are "
                  "custom with the design_review study key as target: {\"max_drag_n\": N}, "
                  "{\"min_natural_freq_hz\": Hz} or {\"avoid_band_hz\": [lo, hi]}. "
                  "A safety-factor or deflection "
                  "requirement names a load_case_id. Load cases use face presets (bottom, top, "
                  "x-min, ...) or tagged faces 'tag:<name>' the part script names in a FACES "
                  "dict (e.g. tag:motor_mount); both hold across variants. Impact/crash load cases must set "
                  "estimate=true; get their newtons from loads_impact.")
async def spec_set(ctx: ToolContext, args: SpecSetArgs) -> ToolOutput:
    pid = _resolve_project(ctx, args.project_id)
    try:
        spec = Spec(goal=args.goal, requirements=args.requirements,
                    load_cases=args.load_cases, answers=args.answers)
    except ValidationError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, "invalid spec: " + "; ".join(
            f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
    cur = _spec_updated_at(ctx, pid)
    base = args.base_updated_at
    if base is None:
        base = _spec_seen.get((ctx.run_id, pid), _NEVER)
    # a first write (no spec yet) needs no read; otherwise the base must match the stored one
    stale = cur is not None and (base is _NEVER or base is None or abs(cur - base) > 1e-6)
    if stale:
        current = _load_spec(ctx, pid)
        _spec_seen[(ctx.run_id, pid)] = cur   # now read: a retry on top of it may proceed
        why = ("this run has not read the spec yet" if base is _NEVER
               else "the spec changed since you read it (possibly the user's edit)")
        return ToolOutput(ok=False, step_kind=StepKind.ERROR,
                          summary=f"spec not saved: {why}; the current spec is in data — merge "
                                  "your change into it and call spec_set again",
                          data={"error": "stale_version", "project_id": pid,
                                "current_updated_at": cur, "spec": current.model_dump()})
    ctx.store.put_spec(pid, spec.model_dump())
    _spec_seen[(ctx.run_id, pid)] = _spec_updated_at(ctx, pid)
    ctx.events.publish(Event(type="spec_changed", run_id=ctx.run_id, data={"project_id": pid}))
    s = (f"spec saved for {pid}: {len(spec.requirements)} requirement(s), "
         f"{len(spec.load_cases)} load case(s)")
    return ToolOutput(summary=s, data={"project_id": pid, "spec": spec.model_dump()})


def _sims_by_case(ctx: ToolContext, part_id: str, spec: Spec) -> tuple[dict, set[str]]:
    """The latest done, non-stale stress study for each load case (matched by fixed face,
    loaded face and direction), plus the cases that only have stale evidence."""
    from ..sim.tools import list_sims  # local import: only pulled in when it is needed
    parts_root = Path(ctx.workdir) / "parts"
    sims = [s for s in list_sims(parts_root, part_id) if s.get("status") == "done"]
    fresh: dict[str, dict] = {}
    stale: set[str] = set()
    for case in spec.load_cases:
        for s in sims:  # list_sims is newest-first
            inp = s.get("inputs") or {}
            load = inp.get("load") or {}
            if (inp.get("fix") == case.fix and load.get("face") == case.load
                    and load.get("direction") == case.direction):
                if s.get("stale"):
                    stale.add(case.id)
                    continue
                fresh[case.id] = s.get("results") or {}
                break
    return fresh, stale - set(fresh)


def spec_report(workdir: Path, spec: Spec, part_id: str) -> dict:
    """The part measured against `spec` (spec_check's data without project): per requirement
    the measured value and pass / warn / fail / not_checked. `workdir` holds `parts/`."""
    from types import SimpleNamespace
    ctx = SimpleNamespace(workdir=workdir)
    d = _existing(ctx, part_id)  # type: ignore[arg-type]
    metrics = _read_part_json(d, "metrics.json")    # safe_io (C6)
    sim_by_case, stale = _sims_by_case(ctx, part_id, spec)  # type: ignore[arg-type]
    report = evaluate_spec(spec, metrics, sim_by_case)
    for r in report["requirements"]:
        if r["status"] == "not_checked" and r["load_case_id"] in stale:
            r["detail"] = "study is stale (the part changed since); rerun it"
    return report


@tool("spec_check", safety=SafetyClass.READ, scope_arg="part_id",
      description="Evaluate a part against the project's spec: one row per requirement with the "
                  "measured value and pass/warn/fail. Geometry requirements (mass, envelope) are "
                  "measured now; safety-factor and deflection requirements use the latest "
                  "matching stress study of the current geometry (a stale study does not count), "
                  "or report 'not checked'. Stress rows are guides; estimate rows used an "
                  "estimated (impact) load.")
async def spec_check(ctx: ToolContext, args: SpecCheckArgs) -> ToolOutput:
    _check_part(args.part_id)
    pid = _resolve_project(ctx, args.project_id)
    report = spec_report(Path(ctx.workdir), _load_spec(ctx, pid), args.part_id)
    counts: dict[str, int] = {}
    for r in report["requirements"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    tally = ", ".join(f"{n} {k}" for k, n in counts.items()) or "no requirements"
    return ToolOutput(summary=f"{args.part_id} vs spec: overall {report['overall']} ({tally})",
                      data={"project_id": pid, "part_id": args.part_id, **report})


# ---- loads

@tool("loads_impact", safety=SafetyClass.READ,
      description="Equivalent static load for an impact/crash: F = m·g·k (k is the dynamic "
                  "amplification factor you choose for the severity). Returns newtons flagged as "
                  "an estimate. Use its newtons for a crash/impact load case and keep estimate=true.")
async def loads_impact(ctx: ToolContext, args: LoadsImpactArgs) -> ToolOutput:
    res = impact_equivalent(args.mass_kg, args.drop_m, args.k)
    return ToolOutput(summary=f"impact (estimate): {res['newtons']:g} N = m·g·k "
                              f"({args.mass_kg:g} kg · g · {args.k:g})", data=res)


# ---- study

def _feasibility(spec: Spec, have_solver: bool):
    """A variant is feasible only when it built and every requirement we can evaluate passes.
    With a solver, a load case a requirement depends on must have produced its number: a failed,
    busy or timed-out stress study makes the variant infeasible, never a silent pass. Without a
    solver, stress requirements stay not_checked (the study is "geometry only")."""
    needs_sf = {r.load_case_id for r in spec.requirements if r.kind == "min_safety_factor"}
    needs_disp = {r.load_case_id for r in spec.requirements if r.kind == "max_deflection_mm"}

    def feasible(m: dict) -> bool:
        if m.get("build_error") or m.get("eval_error"):
            return False
        mass = m.get("mass_g")
        if not isinstance(mass, (int, float)) or isinstance(mass, bool):
            return False
        sf, disp = m.get("sf_by_case") or {}, m.get("disp_by_case") or {}
        if have_solver:
            if any(c not in sf for c in needs_sf) or any(c not in disp for c in needs_disp):
                return False
        sim_by_case = {c.id: {"safety_factor": sf.get(c.id),
                              "max_displacement_mm": disp.get(c.id)} for c in spec.load_cases}
        report = evaluate_spec(spec, m, sim_by_case)
        return all(r["status"] in ("pass", "not_checked") for r in report["requirements"])
    return feasible


def _empty_metrics(variant: str) -> dict:
    # the shared contract names (mass_g, sf_by_case, max_disp_mm, bbox) plus min_sf, always
    # present; disp_by_case / sim_failed_by_case / sim_ids_by_case / build_error are extras
    return {"variant": variant, "mass_g": None, "bbox": None, "sf_by_case": {},
            "max_disp_mm": None, "min_sf": None, "disp_by_case": {}, "sim_failed_by_case": {},
            "sim_ids_by_case": {}}


async def _solve_cases(ctx: ToolContext, vroot: Path, part_id: str, spec: Spec, out: dict,
                       *, fine: bool) -> None:
    sctx = dataclasses.replace(ctx, workdir=vroot)  # the sims resolve parts/ and sim_tmp/ here
    for case in spec.load_cases:
        try:
            sim = await _run_sim(sctx, _static_args(part_id, case, fine=fine))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  recorded: makes the variant infeasible
            out["sim_failed_by_case"][case.id] = f"{type(e).__name__}: {e}"
            continue
        data = sim.data or {}
        r = data.get("results") or {}
        if not sim.ok or r.get("safety_factor") is None:
            out["sim_failed_by_case"][case.id] = (data.get("message") or data.get("error")
                                                  or sim.summary or "no result")
            continue
        out["sf_by_case"][case.id] = r["safety_factor"]
        if r.get("max_displacement_mm") is not None:
            out["disp_by_case"][case.id] = r["max_displacement_mm"]
        if data.get("sim_id"):
            out["sim_ids_by_case"][case.id] = data["sim_id"]
    sfs, disps = list(out["sf_by_case"].values()), list(out["disp_by_case"].values())
    out["min_sf"] = min(sfs) if sfs else None
    out["max_disp_mm"] = max(disps) if disps else None


def _static_evaluator(ctx: ToolContext, part_id: str, part_dir: Path, base_vars: dict,
                      spec: Spec, root: Path, have_solver: bool):
    """Each candidate: a scratch copy of the part source, regenerated (sandboxed) with the
    candidate's vars, measured, then a coarse-mesh stress study per load case on that copy.
    The live part is only read (its part.py), never written."""
    counter = itertools.count()
    source = safe_io.read_text(part_dir, "part.py")   # live part: read, links never followed
    if source is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"{part_id} has no part.py", hint="rebuild it")

    async def evaluate(vars: dict) -> dict:
        variant = f"e{next(counter)}"
        vroot = root / variant
        vpart = vroot / "parts" / part_id
        # the scratch variant is a part dir like any other: built by run_build (the same
        # sandbox, deny process-fork) and handled only through safe_io, then scrubbed
        vpart.parent.mkdir(parents=True, exist_ok=True)
        safe_io.ensure_dir(vpart)
        safe_io.write_text(vpart, "part.py", source)
        out = _empty_metrics(variant)
        # the live part's staged layout skeleton (Plan 8 G3), so skeleton() works in variants
        res = await run_build(vpart, {**base_vars, **vars},
                              skeleton_file=skeleton.staged_file(part_dir))
        bad = _after_build(vpart)
        if bad:
            out["build_error"] = ("the build left links or special files "
                                  f"({', '.join(bad[:5])}); refused")
            return out
        if not res.ok:
            out["build_error"] = res.error or "build failed"
            return out
        safe_io.write_text(vpart, "vars.json", json.dumps(res.vars))
        m = res.metrics
        out["mass_g"], out["bbox"] = m.get("mass_g"), m.get("bbox")
        if have_solver:
            await _solve_cases(ctx, vroot, part_id, spec, out, fine=False)
        return out

    return evaluate


async def _confirm(ctx: ToolContext, root: Path, part_id: str, spec: Spec, best: dict,
                   feasible) -> dict:
    """Re-run the winner's load cases on the fine mesh (coarse + fine, with an error band)."""
    out = _empty_metrics(best["metrics"]["variant"])
    out["mass_g"], out["bbox"] = best["metrics"].get("mass_g"), best["metrics"].get("bbox")
    try:
        await asyncio.wait_for(_solve_cases(ctx, root / out["variant"], part_id, spec, out,
                                            fine=True), CONFIRM_MAX_S)
    except asyncio.TimeoutError:
        return {"n": best["n"], "confirmed": False, "error": "confirmation timed out",
                "metrics": out}
    return {"n": best["n"], "confirmed": bool(feasible(out)), "metrics": out}


def _dynamic_plan(spec: Spec, have_solver: bool) -> list[dict]:
    """The modal / wind studies the spec's targets need (design_review's plan_review), minus
    modal when CalculiX is missing."""
    from ..sim.tools import plan_review
    plan = [p for p in plan_review(spec.model_dump()) if p["type"] in DYNAMIC_TYPES]
    return [p for p in plan if p["type"] != "modal" or have_solver]


async def _dynamic_checks(ctx: ToolContext, root: Path, part_id: str, iterations: list[dict],
                          plan: list[dict], best_id: int) -> dict:
    """Run the spec's modal / wind studies on the winners only: the best variant first, then the
    other feasible Pareto variants by mass, at most DYNAMIC_MAX_VARIANTS. A failed check makes
    that variant infeasible (the caller re-picks the best); a study that could not run is "not
    checked" and never a fail. Rows land in the iteration's metrics.dynamic_checks."""
    from ..sim.tools import SimRunArgs
    by_n = {it["n"]: it for it in iterations}
    others = sorted((it for it in iterations if it.get("pareto") and it.get("feasible")
                     and it["n"] != best_id),
                    key=lambda it: (it.get("metrics") or {}).get("mass_g") or float("inf"))
    cands = [by_n[best_id], *others][:DYNAMIC_MAX_VARIANTS]
    checked: list[int] = []
    failed: list[int] = []
    for it in cands:
        variant = (it.get("metrics") or {}).get("variant")
        if not variant:
            continue
        sctx = dataclasses.replace(ctx, workdir=root / variant)
        rows: list[dict] = []
        for p in plan:
            try:
                out = await asyncio.wait_for(_run_dyn_sim(sctx, SimRunArgs(
                    type=p["type"], part_id=part_id, settings=p["settings"],
                    requirements=p["requirements"],
                    reason=f"design study winner #{it['n']}: {p['type']}", wait=True)),
                    DYNAMIC_MAX_S)
                checks = (out.data or {}).get("checks") if out.ok else None
                note = None if out.ok else (out.summary or "")[:200]
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  - recorded as not checked
                checks, note = None, f"{type(e).__name__}: {e}"[:200]
            if checks:
                rows += [{k: c.get(k) for k in ("requirement_id", "kind", "target", "measured",
                                                "unit", "status")} | {"study": p["type"]}
                         for c in checks]
            else:
                rows += [{"requirement_id": r.get("id"), "kind": r.get("kind"),
                          "target": r.get("target"), "measured": None, "status": "warn",
                          "checked": False, "study": p["type"],
                          "note": note or "not checked: the study produced no result"}
                         for r in p["requirements"]]
        it.setdefault("metrics", {})["dynamic_checks"] = rows
        checked.append(it["n"])
        if any(r["status"] == "fail" for r in rows):
            it["feasible"] = False
            failed.append(it["n"])
    return {"types": [p["type"] for p in plan], "checked": checked, "failed": failed}


REMOVED_SIMS_FILE = "removed_sims.json"


def _cleanup(root: Path, iterations: list[dict], keep_ns: set[int]) -> list[str]:
    """Delete the scratch dirs of the variants that are neither best nor Pareto. Their sims go
    with them: each such iteration is marked `sims_removed`, and the ids are listed in
    <study>/removed_sims.json so GET /sims/{id} answers 410 Gone, not 404 (QA B2-2)."""
    keep = {it["metrics"].get("variant") for it in iterations if it["n"] in keep_ns}
    removed: set[str] = set()
    kept_ids: set[str] = set()
    for it in iterations:
        m = it.get("metrics") or {}
        ids = {s for s in (m.get("sim_ids_by_case") or {}).values() if isinstance(s, str)}
        if m.get("variant") in keep:
            kept_ids |= ids
        elif ids:
            m["sims_removed"] = True
            removed |= ids
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith("e") and d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)
        if removed - kept_ids:
            (root / REMOVED_SIMS_FILE).write_text(json.dumps(sorted(removed - kept_ids)))
    return sorted(v for v in keep if v)


def _study_root(ctx: ToolContext) -> Path:
    rt = (ctx.services or {}).get("runtime")
    home = getattr(rt, "home", None)
    return (Path(home) if home else Path(ctx.workdir)) / "studies"


def _max_parallel() -> int:
    try:
        ram = int(hardware.machine().get("ram_gb") or 0)
    except Exception:  # noqa: BLE001
        ram = 0
    return max(1, min(MAX_PARALLEL_EVALS, (ram // 2) // SOLVE_GB))


def _objective(args: StudyRunArgs) -> Objective:
    try:
        if args.weights:
            return Objective("weighted", tuple(Weight(w.metric, w.goal, w.weight)
                                               for w in args.weights))
        if args.priorities:
            ws = weights_from_priorities(args.priorities)
            if ws:
                return Objective("weighted", ws)
        return Objective(args.objective)
    except ValueError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, str(e),
                         hint="objective: min_mass, max:min_sf, min:max_disp_mm") from None


@tool("study_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=STUDY_TOOL_TIMEOUT_S,
      description="Run a design study on one parametric part: search its variables for the best "
                  "variant that meets the project spec (Latin-hypercube sampling, then "
                  "coordinate refinement). Each variant is built as a scratch copy and, if "
                  "CalculiX is installed, gets a coarse-mesh stress study per load case; the "
                  "winner is confirmed on the fine mesh. Modal and wind targets (custom "
                  "avoid_band_hz / min_natural_freq_hz / max_drag_n) are too costly per variant: "
                  f"they are checked on the winners only (best first, at most "
                  f"{DYNAMIC_MAX_VARIANTS}), and a winner that fails one is dropped for the next. "
                  "The live part is NOT changed: apply a "
                  "variant with study_apply. Objective: min_mass (default), max:min_sf, "
                  "min:max_disp_mm, or weights / the user's priorities. Budget capped at "
                  f"{MAX_EVALS} evaluations / {MAX_MINUTES:g} minutes; a variant whose build or "
                  "stress study fails is infeasible. Emits study_progress; cancellable (partial "
                  "results are kept). Runs its stress studies internally under this call's "
                  "approval.")
async def study_run(ctx: ToolContext, args: StudyRunArgs) -> ToolOutput:
    _check_part(args.part_id)
    pid = _resolve_project(ctx, args.project_id)
    d = _existing(ctx, args.part_id)
    base_vars = _read_part_json(d, "vars.json")
    unknown = sorted({v.name for v in args.variables} - set(base_vars))
    if unknown:
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown variables {unknown}",
                         hint=f"known: {sorted(base_vars)}")
    try:
        variables = [Variable(v.name, v.lo, v.hi) for v in args.variables]
    except ValueError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None
    spec = _load_spec(ctx, pid)
    # face ids are per-geometry: f3 on one variant is a different face on the next
    by_id = [c.id for c in spec.load_cases if FACE_ID_RE.match(c.fix) or FACE_ID_RE.match(c.load)]
    if by_id:
        raise MisesError(ErrorCode.INVALID_INPUT, f"load case(s) {by_id} use face ids, which "
                         "change between variants", hint="use tagged faces (tag:<name>, from a "
                         "FACES dict in part.py) or presets (bottom, top, x-min, ...) in the "
                         "spec's load cases, then run the study")
    obj = _objective(args)
    have_solver = _find_ccx() is not None
    budget = Budget(evals=args.max_evals, minutes=args.max_minutes).clamped()
    max_parallel = _max_parallel()

    study_id = STUDY_ID_PREFIX + secrets.token_hex(6)
    root = _study_root(ctx) / study_id
    root.mkdir(parents=True, exist_ok=True)
    iterations: list[dict] = []
    record: dict = {
        "id": study_id, "project_id": pid, "part_id": args.part_id, "run_id": ctx.run_id,
        "created_at": time.time(), "variables": [v.model_dump() for v in args.variables],
        "objective": obj.objective, "weights": [dataclasses.asdict(w) for w in obj.weights],
        "status": "running", "iterations": iterations, "best_id": None,
        "solver": "CalculiX" if have_solver else None, "geometry_only": not have_solver,
        "max_parallel": max_parallel, "confirmation": None, "kept_variants": []}
    ctx.store.create_study(study_id, pid, record, "running")
    feasible = _feasibility(spec, have_solver)

    def on_iteration(_it: dict, its: list[dict]) -> None:
        ctx.store.update_study(study_id, record, "running")  # live table for the UI
        ctx.events.publish(Event(type="study_progress", run_id=ctx.run_id,
                                 data={"study_id": study_id, "n": len(its),
                                       "total": budget.evals}))
        rt = (ctx.services or {}).get("runtime")     # the status line: "design study · 6/24"
        if rt is not None and hasattr(rt, "activity_tracker"):
            rt.activity_tracker(ctx.run_id).set("tool", f"design study · {len(its)}/{budget.evals}")

    def save(status: str, best_id: int | None) -> None:
        keep = {it["n"] for it in iterations if it.get("pareto")}
        if best_id is not None:
            keep.add(best_id)
        record.update(status=status, best_id=best_id,
                      kept_variants=_cleanup(root, iterations, keep))
        ctx.store.update_study(study_id, record, status)
        # QA B2-1: the last progress event fired before the fine-mesh confirmation, so the UI
        # stayed on "running 4/4"; the terminal one carries the final status
        ctx.events.publish(Event(type="study_progress", run_id=ctx.run_id,
                                 data={"study_id": study_id, "n": len(iterations),
                                       "total": budget.evals, "status": status,
                                       "best_id": best_id,
                                       "kept_variants": record["kept_variants"]}))

    try:
        result = await run_study(variables, _static_evaluator(ctx, args.part_id, d, base_vars,
                                                              spec, root, have_solver),
                                 objective=obj, feasible=feasible, budget=budget,
                                 on_iteration=on_iteration, iterations=iterations,
                                 max_parallel=max_parallel, seed=0)
        best_id = result["best_id"]
        dyn_plan = _dynamic_plan(spec, have_solver) if best_id is not None else []
        if dyn_plan:
            record["dynamic"] = await _dynamic_checks(ctx, root, args.part_id, iterations,
                                                      dyn_plan, best_id)
            best_id = finalize(iterations, obj)   # a winner that failed modal/wind is out
        if best_id is not None and have_solver and spec.load_cases:
            best = next(it for it in iterations if it["n"] == best_id)
            record["confirmation"] = await _confirm(ctx, root, args.part_id, spec, best,
                                                    feasible)
    except asyncio.CancelledError:
        save("cancelled", finalize(iterations, obj))  # keep what finished
        raise
    except BaseException:
        save("failed", finalize(iterations, obj))
        raise
    save(result["status"], best_id)

    warnings = []
    if not have_solver:
        warnings.append("CalculiX is not installed: safety-factor and deflection requirements "
                        "were not evaluated; the result is on mass and geometry only")
    conf = record["confirmation"]
    if conf is not None and not conf["confirmed"]:
        warnings.append("the winner did not hold up on the fine mesh: treat it as unconfirmed "
                        "and check it with sim_static_run")
    if result["status"] == "budget_exhausted":
        warnings.append("the study hit its time budget; results are partial")
    dyn = record.get("dynamic")
    if dyn and best_id is not None and best_id not in dyn["checked"]:
        warnings.append(f"winner(s) {dyn['failed']} failed the {'/'.join(dyn['types'])} "
                        f"target; the new best #{best_id} was NOT checked for it — run "
                        "design_review on it before applying")
    return ToolOutput(summary=_study_summary(record), data=compact_study(record),
                      warnings=warnings, step_kind=StepKind.SIM_RUN)


def mark_interrupted(store, rec: dict) -> dict:
    """A study still "running" whose run has ended was interrupted (Mises restarted or the run
    died mid-study): report it as such. Its iterations so far are kept."""
    if rec.get("status") == "running" and rec.get("run_id"):
        try:
            if store.get_run(rec["run_id"]).status not in ("running", "queued"):
                rec = {**rec, "status": "interrupted"}
        except KeyError:
            rec = {**rec, "status": "interrupted"}
    return rec


def _get_study(ctx: ToolContext, study_id: str) -> dict:
    if not study_id.startswith(STUDY_ID_PREFIX):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad study id: {study_id!r}")
    rec = ctx.store.get_study(study_id)
    if rec is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no study {study_id}",
                         hint="run one with study_run")
    _resolve_project(ctx, rec.get("project_id"))  # another build's study is out of scope
    return mark_interrupted(ctx.store, rec)


@tool("study_get", safety=SafetyClass.READ,
      description="Return a stored design study by id. The default shows the best feasible "
                  "variant, the Pareto set and the lightest feasible variants, each with its "
                  "vars and key metrics, plus the fine-mesh confirmation and iterations_total. "
                  "Pass page=0,1,... to read every evaluation 50 at a time, and fields to keep "
                  "only some keys.")
async def study_get(ctx: ToolContext, args: StudyGetArgs) -> ToolOutput:
    rec = _get_study(ctx, args.study_id)
    return ToolOutput(summary=_study_summary(rec),
                      data=compact_study(rec, page=args.page, fields=args.fields))


def _read_part_json(d: Path, name: str) -> dict:
    """A JSON file of a live part, read via safe_io (never through a link the sandbox left)."""
    try:
        v = safe_io.read_json(d, name)
    except (OSError, ValueError):
        v = None
    if not isinstance(v, dict):
        raise MisesError(ErrorCode.NOT_FOUND, f"{Path(d).name}: {name} is missing",
                         hint="rebuild the part")
    return v


def _part_version(part_dir: Path) -> int:
    """The live part's version: C6's host-only meta (parts/.mises-meta/<id>/version.json),
    which the build sandbox cannot write."""
    return _edits.read_version(part_dir)


@tool("study_apply", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      description="Apply one variant of a finished design study to the live part (sets its "
                  "variables through mirror_part_set_vars and rebuilds). Pass base_version from "
                  "your last read: if the user changed the part since, the call is refused as "
                  "stale. Refuses an infeasible variant unless allow_infeasible is set.")
async def study_apply(ctx: ToolContext, args: StudyApplyArgs) -> ToolOutput:
    _check_part(args.part_id)
    rec = _get_study(ctx, args.study_id)
    if rec.get("part_id") != args.part_id:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{args.study_id} studied "
                         f"{rec.get('part_id')!r}, not {args.part_id!r}")
    if rec.get("status") == "running":
        raise MisesError(ErrorCode.INVALID_INPUT, f"{args.study_id} is still running",
                         hint="wait for it to finish, or cancel it")
    it = next((i for i in rec.get("iterations") or [] if i.get("n") == args.iteration), None)
    if it is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"{args.study_id} has no iteration {args.iteration}")
    # feasibility against the spec as it is NOW (it may have changed since the study ran)
    spec_now = _load_spec(ctx, str(rec.get("project_id") or _resolve_project(ctx, None)))
    feasible_now = _feasibility(spec_now, not rec.get("geometry_only"))(it.get("metrics") or {})
    if not feasible_now and not args.allow_infeasible:
        changed = " against the current spec" if it.get("feasible") else ""
        raise MisesError(ErrorCode.INVALID_INPUT, f"iteration {args.iteration} is infeasible"
                         f"{changed}", hint=f"apply a variant that meets the spec or set "
                         "allow_infeasible")
    d = _existing(ctx, args.part_id)
    current = _part_version(d)
    if args.base_version is not None and current != args.base_version:
        raise MisesError(ErrorCode.STALE_VERSION, f"{args.part_id} is at version {current}, "
                         f"not {args.base_version}", hint="re-read the part, then decide again")
    # pass base_version through as-is (C6): None means the run's last-read version (never read =
    # conflict); forcing `current` here would let an apply overwrite an edit the run never saw.
    out = await mirror_part_set_vars.handler(ctx, SetVarsArgs(
        part_id=args.part_id, vars=it["vars"], base_version=args.base_version))
    if out.ok:
        out.summary = f"applied {args.study_id} #{args.iteration}: {out.summary}"
        out.data = {**out.data, "study_id": args.study_id, "iteration": args.iteration}
    return out


_ITER_METRIC_KEYS = ("mass_g", "min_sf", "max_disp_mm", "sf_by_case", "dynamic_checks")
_ITER_KEYS = ("n", "vars", "feasible", "pareto", "metrics")
STUDY_PAGE_SIZE = 50
_TOP_FEASIBLE = 8


def _trim_iteration(it: dict, fields: list[str] | None = None) -> dict:
    m = it.get("metrics") or {}
    row = {"n": it.get("n"), "vars": it.get("vars"), "feasible": it.get("feasible"),
           "pareto": it.get("pareto"),
           "metrics": {k: m.get(k) for k in _ITER_METRIC_KEYS if k in m}}
    if it.get("build_error") or (m.get("build_error")):
        row["build_error"] = (it.get("build_error") or m.get("build_error"))
    if fields:
        return {k: row[k] for k in fields if k in row}
    return row


def compact_study(rec: dict, *, page: int | None = None,
                  fields: list[str] | None = None) -> dict:
    """The agent-facing `data` of a study: always under the Gate's 16 KB cap. The default view is
    the best variant, the Pareto set and the lightest feasible iterations, each with trimmed
    metrics, plus iterations_total. The full record stays on disk (GET /projects/<id>/studies);
    study_get(study_id, page=n) pages every evaluation, and `fields` narrows each row."""
    iters = rec.get("iterations") or []
    out = {k: rec.get(k) for k in ("id", "project_id", "part_id", "run_id", "status", "objective",
                                   "weights", "variables", "best_id", "geometry_only", "solver",
                                   "max_parallel", "created_at", "kept_variants", "dynamic")}
    conf = rec.get("confirmation")
    if isinstance(conf, dict):
        cm = conf.get("metrics") or {}
        out["confirmation"] = {"n": conf.get("n"), "confirmed": conf.get("confirmed"),
                               "error": conf.get("error"),
                               "metrics": {k: cm.get(k) for k in ("mass_g", "min_sf",
                                                                  "max_disp_mm")}}
    else:
        out["confirmation"] = conf
    out["iterations_total"] = len(iters)
    out["study_url"] = f"/projects/{rec.get('project_id')}/studies"
    if page is not None:
        start = page * STUDY_PAGE_SIZE
        chunk = iters[start:start + STUDY_PAGE_SIZE]
        out.update(page=page, page_size=STUDY_PAGE_SIZE,
                   iterations=[_trim_iteration(it, fields) for it in chunk])
        return out
    best = rec.get("best_id")
    chosen: dict = {it["n"]: it for it in iters if it.get("n") == best or it.get("pareto")}
    feas = sorted((it for it in iters if it.get("feasible")),
                  key=lambda it: (it.get("metrics") or {}).get("mass_g") or float("inf"))
    for it in feas[:_TOP_FEASIBLE]:
        chosen.setdefault(it["n"], it)
    sel = [chosen[n] for n in sorted(chosen)]
    out["iterations"] = [_trim_iteration(it, fields) for it in sel]
    out["iterations_shown"] = len(sel)
    out["note"] = ("best, Pareto set and lightest feasible variants; "
                   f"study_get(study_id, page=0..) pages all {len(iters)} evaluations")
    return out


def _study_summary(rec: dict) -> str:
    iters = rec.get("iterations") or []
    feas = sum(1 for it in iters if it.get("feasible"))
    s = f"{rec['id']} {rec.get('status')}: {len(iters)} evaluation(s), {feas} feasible"
    best = rec.get("best_id")
    label = ("best on mass/geometry only (no solver)" if rec.get("geometry_only")
             else "best feasible")
    if best is not None:
        bi = next((it for it in iters if it.get("n") == best), None)
        m = (bi or {}).get("metrics") or {}
        if isinstance(m.get("mass_g"), (int, float)):
            s += f"; {label} #{best}: {m['mass_g']:.2f} g"
            if isinstance(m.get("min_sf"), (int, float)):
                s += f", SF {m['min_sf']:.2f} (coarse mesh)"
            s += f" at {bi.get('vars')}"
        conf = rec.get("confirmation")
        if conf is not None:
            s += "; fine-mesh check " + ("held" if conf.get("confirmed") else "FAILED")
    else:
        s += "; no feasible variant found"
    return s


def register_engineering_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (interview_plan, spec_get, spec_set, spec_check, loads_impact, study_run,
              study_get, study_apply):
        if not registry.list([t.name]):
            registry.register(t)
