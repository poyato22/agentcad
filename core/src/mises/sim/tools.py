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

from ..cad.tools import _check_part_id, _existing
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import studies
from .ccx import INSTALL_HINT, find_ccx, solver_status  # noqa: F401  (find_ccx: patchable)
from .environments import DIRECTIONS, ENVIRONMENTS, FACE_PRESETS
from .materials import FDM_KNOCKDOWN, MATERIALS, load_custom
from .settings import check_face
from .studies import (GUIDE_LABEL, SIM_ID_RE, STUDY_TYPES, find_sim, list_sims,  # noqa: F401
                      read_record)
from .types import SimError

MESH_TIMEOUT_S = 180.0
SOLVE_TIMEOUT_S = 300.0
# One static study is at most two meshes + two solves (+ margin for post-processing). Studies
# run one at a time; the Gate's timeout covers the queue wait too, so the wait is bounded such
# that a study that gets the slot always has its full time left (else: ok=false, "busy").
STUDY_MAX_S = 2 * (MESH_TIMEOUT_S + SOLVE_TIMEOUT_S) + 60
TOOL_TIMEOUT_S = 2 * STUDY_MAX_S + 60
QUEUE_TIMEOUT_S = TOOL_TIMEOUT_S - STUDY_MAX_S - 30
# sim_run takes user timeouts (up to an hour per solve, several levels and cases): its Gate
# timeout is a backstop; every mesher / solver process has its own hard timeout.
SIM_RUN_TIMEOUT_S = 4 * 3600.0
REVIEW_TIMEOUT_S = 8 * 3600.0
STUDY_SLOTS = 1   # studies are memory bound: one at a time (see studies.semaphore)
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

    @field_validator("fix")
    @classmethod
    def _fix(cls, v: str) -> str:
        return _check_face(v)


class SimRunArgs(BaseModel):
    type: Literal["static", "modal", "buckling", "thermal", "drop", "wind"]
    part_id: str | None = Field(None, description="the part to study")
    project_id: str | None = Field(None, description="an assembly (build); not supported yet")
    settings: dict[str, Any] = Field(default_factory=dict, description="the type's settings; "
                                     "see sim_types_list for every field, default and range")
    reason: str | None = Field(None, max_length=200, description="one line: why this study "
                               "(shown on the study card)")
    requirements: list[dict[str, Any]] = Field(default_factory=list, max_length=40,
                                               description="spec requirements to check "
                                               "pass / warn / fail against the result")

    @model_validator(mode="after")
    def _target(self):
        if self.part_id is None and self.project_id is None:
            raise ValueError("give part_id (or project_id)")
        return self


def _run_scope(a) -> list[str]:
    return [a.part_id] if a.part_id else [f"project:{a.project_id}"]


class ResultArgs(BaseModel):
    sim_id: str = Field(pattern=SIM_ID_RE.pattern)


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

def _bad(e: SimError) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint)


def _in_read_scope(ctx: ToolContext, part_id: str) -> bool:
    pol = getattr(ctx, "policy", None)
    if pol is None:
        return True
    allowed = set(pol.edit_scope) | set(pol.read_scope)
    return "*" in allowed or part_id in allowed


def _load_rec(ctx: ToolContext, sim_id: str) -> dict:
    root = Path(ctx.workdir) / "parts"
    d = find_sim(root, sim_id)
    if d is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no study {sim_id}", hint="run one with sim_run")
    rec = read_record(d, root)
    if not _in_read_scope(ctx, rec.get("part_id", "")):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{sim_id} belongs to a part outside this "
                         "run's scope", hint="ask the user to widen the scope")
    return rec


async def _run(ctx: ToolContext, type_id: str, part_id: str, settings: dict, *,
               reason=None, requirements=None, inputs_extra=None, solve_timeout_s=None):
    _check_part_id(part_id)
    part = _existing(ctx, part_id)
    try:
        model = studies.validate_settings(type_id, settings)
    except SimError as e:
        raise _bad(e) from None
    return await studies.run_study(ctx, type_id, part_id, part, model, reason=reason,
                                   requirements=requirements, inputs_extra=inputs_extra,
                                   solve_timeout_s=solve_timeout_s)


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
                  "infill, convergence, solver timeout and threads). Faces are presets or face "
                  "ids from mirror_measure / mirror_faces. Give a one-line `reason`; pass spec "
                  "`requirements` to get pass / warn / fail checks. Results are a GUIDE ONLY: "
                  "always say so, with the solver, mesh and error band.")
async def sim_run(ctx: ToolContext, args: SimRunArgs) -> ToolOutput:
    if args.part_id is None:
        raise MisesError(ErrorCode.INVALID_INPUT, "assembly (project_id) studies are not "
                         "supported yet", hint="study each part with its part_id")
    return await _run(ctx, args.type, args.part_id, args.settings, reason=args.reason,
                      requirements=args.requirements)


@tool("sim_static_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=TOOL_TIMEOUT_S,
      description="Run a static linear-elastic stress study (gmsh + CalculiX) on a local mirror "
                  "part: one face held fixed, a force spread over another face. Faces are "
                  "presets (bottom, top, x-min, ...) or face ids from mirror_measure face_list "
                  "(largest faces) or mirror_faces (all). Returns max von Mises (MPa, excluding "
                  "the fixed face, whose peak is reported separately), max displacement (mm), "
                  "safety factor (yield x FDM knock-down 0.7 / peak) and an error band from a "
                  "coarse vs fine mesh. For more load cases, supports or other study types use "
                  "sim_run. Results are a GUIDE ONLY: always say so when you report them.")
async def sim_static_run(ctx: ToolContext, args: StaticArgs) -> ToolOutput:
    _check_part_id(args.part_id)
    _existing(ctx, args.part_id)
    if args.fix == args.load.face:
        raise MisesError(ErrorCode.INVALID_INPUT, "fix and load are the same face",
                         hint="hold one face and load another")
    settings = {"load_cases": [{"name": "case 1", "supports": [{"kind": "fixed",
                                                                "face": args.fix}],
                                "loads": [{"kind": "force", **args.load.model_dump()}]}],
                "material": {"name": args.material}, "environment": args.environment}
    return await _run(ctx, "static", args.part_id, settings,
                      inputs_extra={"fix": args.fix, "load": args.load.model_dump()},
                      solve_timeout_s=SOLVE_TIMEOUT_S)


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
    rec = _load_rec(ctx, args.sim_id)
    summary = (f"{args.sim_id}: " + studies.summary(rec)
               + ("; STALE: the part changed since this study" if rec["stale"] else ""))
    return ToolOutput(summary=summary, data={**rec, "card": studies.card(rec)},
                      step_kind=StepKind.SIM_RUN)


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
    if pol is not None and "*" not in pol.edit_scope and rec.get("part_id") not in pol.edit_scope:
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
            "solvers": st, "study_types": list(STUDY_TYPES)}
    s = (f"{len(ENVIRONMENTS)} environments, {len(data['materials'])} materials, study types "
         f"{', '.join(STUDY_TYPES)}; CalculiX "
         + ("found" if st["ccx"]["found"] else f"MISSING (install: {INSTALL_HINT})"))
    return ToolOutput(summary=s, data=data)


# ---- design review

STRESS_KINDS = {"min_safety_factor", "max_deflection_mm"}
MODAL_KINDS = {"min_natural_freq_hz", "avoid_band_hz"}
WIND_KINDS = {"max_drag_n", "max_cd"}
DROP_KINDS = {"survive_drop_m"}


def plan_review(spec: dict, wind_overrides: dict | None = None,
                material: str | None = None) -> list[dict]:
    """The studies a spec needs: [{type, case, settings, requirements}]. Static: one study per
    load case; modal when an excitation band (or a frequency requirement) is given; wind when a
    drag requirement or spec.wind is present; drop when spec.drop or a drop requirement is."""
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
    if not args.project_id:
        return None
    gate = _gate(ctx)
    try:
        if gate is None or not gate.registry.list(["spec_get"]):
            return None
    except Exception:  # noqa: BLE001
        return None
    out = await gate.call(ctx.run_id, "spec_get", {"project_id": args.project_id},
                          call_id=f"{ctx.call_id}.spec", workdir=Path(ctx.workdir))
    if not out.ok:
        return None
    d = out.data
    return d.get("spec") if isinstance(d.get("spec"), dict) else d


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
    return max(1, min(lim, STUDY_SLOTS))


@tool("design_review", safety=SafetyClass.READ, scope_arg="part_id", timeout_s=REVIEW_TIMEOUT_S,
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
    metrics = json.loads((part / "metrics.json").read_text())
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
            if p["type"] == "wind":
                left = max(30.0, deadline - time.monotonic())
                settings["timeout_s"] = min(float(settings.get("timeout_s", 600)), left)
            payload = {"type": p["type"], "part_id": args.part_id, "settings": settings,
                       "requirements": p["requirements"],
                       "reason": f"design review: {p['type']} {p['case']}"[:200]}
            try:
                out = await gate.call(ctx.run_id, "sim_run", payload,
                                      call_id=f"{ctx.call_id}.r{i + 1}",
                                      workdir=Path(ctx.workdir))
                results[i] = {"plan": p, "out": out}
            except MisesError as e:
                results[i] = {"plan": p, "error": f"{e.code}: {e.message}"}

    await asyncio.gather(*(one(i, p) for i, p in enumerate(plan)))
    table = _metric_rows(spec, metrics, material)
    studies_run = []
    for res in results:
        p = res["plan"]
        out = res.get("out")
        data = out.data if out is not None else {}
        sid = data.get("sim_id")
        studies_run.append({"type": p["type"], "case": p["case"], "sim_id": sid,
                            "ok": bool(out and out.ok), "error": res.get("error") or (
                                None if out is None or out.ok else out.summary),
                            "url": f"/sims/{sid}" if sid else None,
                            "card": data.get("card")})
        checks = data.get("checks") if out is not None and out.ok else None
        if checks:
            for c in checks:
                table.append({**c, "study": p["type"], "case": p["case"],
                              "url": f"/sims/{sid}"})
        else:
            for r in p["requirements"]:
                table.append({"requirement_id": r.get("id"), "kind": r.get("kind"),
                              "target": r.get("target"), "measured": None, "unit": "",
                              "status": "warn" if res.get("error") is None and out is not None
                              and out.ok else "fail", "sim_id": sid, "study": p["type"],
                              "case": p["case"], "url": f"/sims/{sid}" if sid else None,
                              "note": res.get("error") or (out.summary[:200] if out else None)})
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
    return ToolOutput(summary=head + (": " + rows if rows else ""),
                      data={"verdict": verdict, "table": table, "studies": studies_run,
                            "label": GUIDE_LABEL, "part_id": args.part_id},
                      warnings=[s["error"] for s in studies_run if s.get("error")])


def register_sim_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (sim_static_run, sim_run, sim_types_list, sim_result_get, sim_compare, sim_cancel,
              sim_env_list, design_review):
        if not registry.list([t.name]):
            registry.register(t)
