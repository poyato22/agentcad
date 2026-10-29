"""engineering_* tools: the requirements-driven loop.

spec_get / spec_set hold the project's spec; spec_check measures a part against it; study_run
searches a parametric part's variables for the lightest variant that meets the spec, running a
coarse stress study per load case; loads_impact turns a drop into an equivalent static load.

Specs and studies are keyed by project_id (a TEXT key; Task C1 owns the projects table). We read
the project from the run defensively so this works before or after that migration, and fall back
to the "default" project when none is set.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..cad.runner import run_build
from ..cad.tools import PART_ID_RE, _existing, _lock
from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput
from .loads import DEFAULT_IMPACT_K, impact_equivalent
from .spec import LoadCase, Requirement, Spec, evaluate_spec
from .study import MAX_EVALS, MAX_MINUTES, Budget, Variable, run_study

DEFAULT_PROJECT = "default"
STUDY_ID_RE = "study_"
# A whole study is many regenerate+solve evaluations; the Gate's timeout covers it.
STUDY_TOOL_TIMEOUT_S = MAX_MINUTES * 60 + 120


def _resolve_project(ctx: ToolContext, explicit: str | None) -> str:
    if explicit:
        return explicit
    try:  # C1 adds runs.project_id; read it defensively so C3 stands alone
        run = ctx.store.get_run(ctx.run_id)
        pid = getattr(run, "project_id", None)
        if pid:
            return str(pid)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_PROJECT


def _check_part(part_id: str) -> None:
    if not PART_ID_RE.match(part_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                         hint=f"part ids match {PART_ID_RE.pattern}")


# ---- args

class ProjectArg(BaseModel):
    project_id: str | None = Field(default=None, description="the build this spec belongs to; "
                                   "defaults to the run's project")


class SpecSetArgs(BaseModel):
    project_id: str | None = None
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


class StudyRunArgs(BaseModel):
    part_id: str
    variables: list[VariableArg] = Field(min_length=1, max_length=6)
    objective: str = "min_mass"
    project_id: str | None = None
    max_evals: int = Field(default=MAX_EVALS, ge=1, le=MAX_EVALS)
    max_minutes: float = Field(default=MAX_MINUTES, gt=0, le=MAX_MINUTES)


class StudyGetArgs(BaseModel):
    study_id: str


class LoadsImpactArgs(BaseModel):
    mass_kg: float = Field(gt=0, le=1e4)
    drop_m: float = Field(ge=0, le=1e4)
    k: float = Field(default=DEFAULT_IMPACT_K, gt=0, le=1e3)


# ---- spec tools

@tool("spec_get", safety=SafetyClass.READ,
      description="Return the project's engineering spec: goal, requirements, load cases and the "
                  "interview answers. An empty spec if none is written yet.")
async def spec_get(ctx: ToolContext, args: ProjectArg) -> ToolOutput:
    pid = _resolve_project(ctx, args.project_id)
    data = ctx.store.get_spec(pid) or Spec().model_dump()
    spec = Spec.model_validate(data)
    s = (f"spec for {pid}: {len(spec.requirements)} requirement(s), "
         f"{len(spec.load_cases)} load case(s)")
    return ToolOutput(summary=s, data={"project_id": pid, "spec": spec.model_dump()})


@tool("spec_set", safety=SafetyClass.LOCAL_MUTATE,
      description="Write the project's engineering spec (goal, requirements, load cases, interview "
                  "answers). Requirement kinds: max_mass_g, min_safety_factor, max_deflection_mm, "
                  "envelope_mm, mount_pattern, printable, custom. A safety-factor or deflection "
                  "requirement names a load_case_id. Impact/crash load cases must set "
                  "estimate=true; get their newtons from loads_impact.")
async def spec_set(ctx: ToolContext, args: SpecSetArgs) -> ToolOutput:
    pid = _resolve_project(ctx, args.project_id)
    try:
        spec = Spec(goal=args.goal, requirements=args.requirements,
                    load_cases=args.load_cases, answers=args.answers)
    except ValidationError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, "invalid spec: " + "; ".join(
            f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
    ctx.store.put_spec(pid, spec.model_dump())
    ctx.events.publish(Event(type="spec_changed", run_id=ctx.run_id, data={"project_id": pid}))
    s = (f"spec saved for {pid}: {len(spec.requirements)} requirement(s), "
         f"{len(spec.load_cases)} load case(s)")
    return ToolOutput(summary=s, data={"project_id": pid, "spec": spec.model_dump()})


def _sims_by_case(ctx: ToolContext, part_id: str, spec: Spec) -> dict:
    """Match the latest done stress study to each load case (by fixed face, loaded face and
    direction). Imported lazily so the sim package is only pulled in when it is needed."""
    from ..sim.tools import list_sims  # local import: avoid a hard sim dependency at load time
    parts_root = Path(ctx.workdir) / "parts"
    sims = [s for s in list_sims(parts_root, part_id) if s.get("status") == "done"]
    out: dict[str, dict] = {}
    for case in spec.load_cases:
        for s in sims:  # list_sims is newest-first
            inp = s.get("inputs") or {}
            load = inp.get("load") or {}
            if (inp.get("fix") == case.fix and load.get("face") == case.load
                    and load.get("direction") == case.direction):
                out[case.id] = s.get("results") or {}
                break
    return out


@tool("spec_check", safety=SafetyClass.READ, scope_arg="part_id",
      description="Evaluate a part against the project's spec: one row per requirement with the "
                  "measured value and pass/warn/fail. Geometry requirements (mass, envelope) are "
                  "measured now; safety-factor and deflection requirements use the latest matching "
                  "stress study, or report 'not checked' if none exists yet.")
async def spec_check(ctx: ToolContext, args: SpecCheckArgs) -> ToolOutput:
    _check_part(args.part_id)
    pid = _resolve_project(ctx, args.project_id)
    d = _existing(ctx, args.part_id)
    metrics = json.loads((d / "metrics.json").read_text())
    spec = Spec.model_validate(ctx.store.get_spec(pid) or Spec().model_dump())
    sim_by_case = _sims_by_case(ctx, args.part_id, spec)
    report = evaluate_spec(spec, metrics, sim_by_case)
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

def _feasibility(spec: Spec):
    """A candidate is feasible when every requirement we can evaluate passes; requirements with
    no evidence (e.g. a load case with no sf yet) do not block, and warnings (near-misses) do."""
    def feasible(metrics: dict) -> bool:
        sim_by_case = {cid: {"safety_factor": metrics.get("sf_by_case", {}).get(cid),
                             "max_displacement_mm": metrics.get("disp_by_case", {}).get(cid)}
                       for cid in metrics.get("sf_by_case", {}) | metrics.get("disp_by_case", {})}
        report = evaluate_spec(spec, metrics, sim_by_case)
        return all(r["status"] in ("pass", "not_checked") for r in report["requirements"])
    return feasible


def _static_evaluator(ctx: ToolContext, part_id: str, part_dir: Path, base_vars: dict,
                      spec: Spec):
    """Regenerate the part with the candidate vars (sandboxed) and measure mass and bbox, then run
    a coarse static study per load case for its safety factor and deflection.

    Evaluations mutate the one shared part dir, so the study runs them sequentially; the solver
    serialises internally anyway. The caller restores the original vars when the study ends.
    """
    from ..sim.ccx import find_ccx
    from ..sim.tools import StaticArgs, sim_static_run
    have_solver = find_ccx() is not None

    async def evaluate(vars: dict) -> dict:
        merged = {**base_vars, **vars}
        async with _lock(part_dir):
            res = await run_build(part_dir, merged)
            if not res.ok:
                return {"mass_g": None, "build_error": res.error}
            (part_dir / "vars.json").write_text(json.dumps(res.vars))
        m = res.metrics
        out: dict = {"mass_g": m.get("mass_g"), "bbox": m.get("bbox"),
                     "sf_by_case": {}, "disp_by_case": {}}
        if have_solver:
            for case in spec.load_cases:
                try:
                    sim = await sim_static_run(ctx, StaticArgs(
                        part_id=part_id, fix=case.fix,
                        load={"face": case.load, "newtons": case.newtons,
                              "direction": case.direction}))
                except Exception:  # noqa: BLE001  a bad load case must not kill the study
                    continue
                r = (sim.data or {}).get("results") or {}
                if r.get("safety_factor") is not None:
                    out["sf_by_case"][case.id] = r["safety_factor"]
                if r.get("max_displacement_mm") is not None:
                    out["disp_by_case"][case.id] = r["max_displacement_mm"]
        return out

    return evaluate, have_solver


@tool("study_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=STUDY_TOOL_TIMEOUT_S,
      description="Run a design study: search a parametric part's variables for the lightest "
                  "variant that still meets the project spec. Coarse Latin-hypercube sampling then "
                  "coordinate refinement; each variant regenerates the part and (if CalculiX is "
                  "installed) runs a coarse stress study per load case. Budget capped at "
                  f"{MAX_EVALS} evaluations / {MAX_MINUTES:g} minutes. Emits study_progress; "
                  "cancellable. Returns a Pareto table (mass vs safety factor) and the best "
                  "feasible variant.")
async def study_run(ctx: ToolContext, args: StudyRunArgs) -> ToolOutput:
    _check_part(args.part_id)
    pid = _resolve_project(ctx, args.project_id)
    d = _existing(ctx, args.part_id)
    base_vars = json.loads((d / "vars.json").read_text())
    unknown = sorted({v.name for v in args.variables} - set(base_vars))
    if unknown:
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown variables {unknown}",
                         hint=f"known: {sorted(base_vars)}")
    spec = Spec.model_validate(ctx.store.get_spec(pid) or Spec().model_dump())
    variables = [Variable(v.name, v.lo, v.hi) for v in args.variables]
    budget = Budget(evals=args.max_evals, minutes=args.max_minutes)
    evaluate, have_solver = _static_evaluator(ctx, args.part_id, d, base_vars, spec)

    study_id = "study_" + secrets.token_hex(6)
    created = time.time()
    record = {"id": study_id, "project_id": pid, "part_id": args.part_id, "created_at": created,
              "variables": [v.model_dump() for v in args.variables], "objective": args.objective,
              "status": "running", "iterations": [], "best_id": None,
              "solver": "CalculiX" if have_solver else None}
    ctx.store.create_study(study_id, pid, record, "running")

    def on_progress(n: int, total: int) -> None:
        ctx.events.publish(Event(type="study_progress", run_id=ctx.run_id,
                                 data={"study_id": study_id, "n": n, "total": total}))

    try:
        result = await run_study(variables, evaluate, objective=args.objective,
                                 feasible=_feasibility(spec), budget=budget,
                                 on_progress=on_progress, max_parallel=1, seed=0)
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        ctx.store.update_study(study_id, record, "cancelled")
        raise
    finally:
        async with _lock(d):  # leave the part as the user had it; the agent applies the winner
            restore = await run_build(d, base_vars)
            if restore.ok:
                (d / "vars.json").write_text(json.dumps(restore.vars))

    record.update(status=result["status"], iterations=result["iterations"],
                  best_id=result["best_id"])
    ctx.store.update_study(study_id, record, result["status"])
    warnings = [] if have_solver else [
        "CalculiX is not installed: safety-factor and deflection load cases were not evaluated; "
        "the study optimised mass and geometry only"]
    return ToolOutput(summary=_study_summary(record), data=record, warnings=warnings)


@tool("study_get", safety=SafetyClass.READ,
      description="Return a stored design study by id: its variables, objective, status, every "
                  "evaluation (vars, metrics, feasible, pareto) and the best feasible variant.")
async def study_get(ctx: ToolContext, args: StudyGetArgs) -> ToolOutput:
    if not args.study_id.startswith(STUDY_ID_RE):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad study id: {args.study_id!r}")
    rec = ctx.store.get_study(args.study_id)
    if rec is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no study {args.study_id}",
                         hint="run one with study_run")
    return ToolOutput(summary=_study_summary(rec), data=rec)


def _study_summary(rec: dict) -> str:
    iters = rec.get("iterations") or []
    feas = sum(1 for it in iters if it.get("feasible"))
    best = rec.get("best_id")
    s = (f"{rec['id']} {rec.get('status')}: {len(iters)} evaluation(s), {feas} feasible")
    if best is not None:
        bi = next((it for it in iters if it.get("n") == best), None)
        if bi:
            mass = (bi.get("metrics") or {}).get("mass_g")
            if isinstance(mass, (int, float)):
                s += f"; best feasible {mass:.2f} g at {bi.get('vars')}"
    else:
        s += "; no feasible variant found"
    return s


def register_engineering_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (spec_get, spec_set, spec_check, loads_impact, study_run, study_get):
        if not registry.list([t.name]):
            registry.register(t)
