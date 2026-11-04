"""Dynamics tools (Plan 10 C3, research §4.3): dyn_build, dyn_torque, dyn_stability,
dyn_reactions, dyn_rom_sweep and dyn_to_fem.

Each exports the build's assembly (mises.dyn.export), runs the job in the sandboxed child
process (mises.dyn.run), judges the result (mises.dyn.checks), keeps it per build, writes the
verdict into the build's engineering checks record (engineering.pipeline: what the lead's
"done" gate reads) and returns a card with the accuracy statements. dyn_to_fem turns the peak
joint reactions of the most loaded links into CalculiX static studies (sim_run, one
LOCAL_MUTATE call per link through the Gate) and writes each link's safety factor back.

Joint values in and out are Mises units (deg, mm); trajectories name Mises joint ids.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from ..assembly.tools import _resolve_build, _store
from ..engineering import pipeline
from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import checks, export, fem_bridge, run

FEM_TOOL_TIMEOUT_S = 3600.0
SAMPLE_CHART_JOINTS = 8
MAX_MOTION_S = 60.0       # a dynamics motion is capped on the host (the worker refuses > 120 s)


# ---- arguments

class Key(BaseModel):
    t: float = Field(ge=0, le=120, description="s from the start of the motion")
    joints: dict[str, float] = Field(description="joint id -> value (deg for revolute, mm for "
                                                 "slider)")


class Trajectory(BaseModel):
    kind: Literal["hold", "keyframes", "squat", "stand_up", "trot", "walk", "reach", "sweep",
                  "animation"] = Field(
        "hold", description="hold: the current pose; keyframes: joint values over time; "
        "squat / stand_up / trot / walk (a 4-beat crawl, one foot up at a time): a legged "
        "robot's named motions (feet held by IK); reach: "
        "an arm's tip to target_mm; sweep: each joint through its range in turn; animation: "
        "a saved assembly_animate motion (animation_id)")
    duration_s: float | None = Field(None, gt=0, le=60)
    period_s: float | None = Field(None, gt=0.05, le=30, description="squat / trot / walk / sweep")
    cycles: int | None = Field(None, ge=1, le=10)
    depth_mm: float | None = Field(None, gt=0, le=1000, description="squat depth")
    step_mm: float | None = Field(None, gt=0, le=500, description="trot / walk step length")
    lift_mm: float | None = Field(None, gt=0, le=300, description="trot / walk foot lift")
    target_mm: list[float] | None = Field(None, min_length=3, max_length=3,
                                          description="reach: world point")
    joints: list[str] | None = Field(None, max_length=40, description="sweep: these joints")
    keys: list[Key] | None = Field(None, max_length=60)
    animation_id: str | None = Field(None, max_length=64)
    settle_s: float | None = Field(None, ge=0, le=5)

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "keyframes" and not self.keys:
            raise ValueError("keyframes needs keys")
        if self.kind == "reach" and self.target_mm is None:
            raise ValueError("reach needs target_mm")
        if self.kind == "animation" and not self.animation_id:
            raise ValueError("animation needs animation_id")
        return self


Base = Literal["auto", "free", "fixed"]


class DynArgs(BaseModel):
    project_id: str | None = Field(None, description="Default: this run's build.")
    base: Base = Field("auto", description="auto: free-floating on a floor when the robot "
                       "stands on its legs/wheels, else fixed to the ground")
    actuators: dict[str, str] = Field(default_factory=dict, description="joint id -> actuator "
                                      "(catalog id or name, e.g. 'mg996r', 'xm430', "
                                      "'ak80_9'); default: each joint's own actuator, else "
                                      "an ideal servo")
    slider_force_n: dict[str, float] = Field(default_factory=dict, description="slider joint "
                                             "id -> its linear actuator's force limit, N "
                                             "(default: an ideal servo)")


class TorqueArgs(DynArgs):
    trajectory: Trajectory | None = Field(None, description="default: squat for a legged "
                                          "robot, sweep for a fixed base")


class StabilityArgs(DynArgs):
    trajectory: Trajectory | None = Field(None, description="also check the CoP margin "
                                          "along this motion")
    tilt_test: bool = Field(True, description="tilt the floor until it tips (about 1-2 s)")


class RomArgs(DynArgs):
    joints: list[str] | None = Field(None, max_length=40, description="joint ids; default: "
                                     "every moving joint")
    n: int = Field(2000, ge=10, le=20000, description="poses (Latin hypercube)")


class FemArgs(TorqueArgs):
    links: list[str] | None = Field(None, max_length=8, description="instance ids to study; "
                                    "default: the n_links most loaded")
    n_links: int = Field(2, ge=1, le=6)
    min_safety_factor: float = Field(2.0, gt=0, le=20, description="printed parts 2.0, "
                                     "metals 1.5 [J]")
    wait: bool | None = Field(None, description="false (the default for agents): return at "
                              "once and run the studies in the background (a note arrives); "
                              "true: block until every SF is in")


# ---- helpers

def _home(ctx: ToolContext) -> Path:
    return Path(ctx.workdir)


def _build(ctx: ToolContext, requested: str | None) -> str:
    if requested:
        return _resolve_build(ctx, requested)
    from ..catalog.tools import resolve_project
    pid = resolve_project(ctx, None)
    if not pid:
        raise MisesError(ErrorCode.INVALID_INPUT, "which build?", hint="pass project_id")
    return _resolve_build(ctx, pid)


def _export(ctx: ToolContext, pid: str, args: DynArgs):
    pipeline.note_actuators(_home(ctx), pid, args.actuators)
    asm = _store(ctx).get(pid)
    ex = export.export(_home(ctx), getattr(ctx, "store", None), asm, base=args.base,
                       actuators=args.actuators, slider_force_n=args.slider_force_n)
    return asm, ex


def _names(ex: export.Export, asm) -> dict[str, str]:
    """MuJoCo body name -> the instance names it holds."""
    inst = {i.id: i for i in asm.instances}
    return {b: " + ".join(inst[i].name or i for i in ids) for b, ids in ex.map["bodies"].items()}


def _traj(ctx: ToolContext, pid: str, t: Trajectory | None, ex: export.Export) -> dict:
    free = ex.base == "free"
    if t is None:
        t = Trajectory(kind="squat" if free else "sweep")
    if t.kind in ("squat", "stand_up", "trot", "walk") and not free:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{t.kind} needs a free-standing robot; "
                         "this build is fixed to the ground",
                         hint="use sweep, reach or keyframes, or base='free'")
    out: dict = {"kind": t.kind}
    if t.settle_s is not None:
        out["settle_s"] = t.settle_s
    for k in ("duration_s", "period_s", "cycles"):
        v = getattr(t, k)
        if v is not None:
            out[k] = v
    if t.depth_mm is not None:
        out["depth_m"] = t.depth_mm / 1000.0
    if t.step_mm is not None:
        out["step_m"] = t.step_mm / 1000.0
    if t.lift_mm is not None:
        out["lift_m"] = t.lift_mm / 1000.0
    if t.target_mm is not None:
        out["target_m"] = [x / 1000.0 for x in t.target_mm]
    if t.joints:
        out["joints"] = [export.to_mj(ex.map, j, 0.0)[0] for j in t.joints]
    if t.kind == "keyframes":
        out["keys"] = [{"t": k.t, "q": dict(export.to_mj(ex.map, j, v) for j, v in
                                            k.joints.items())} for k in t.keys]
    if t.kind == "animation":
        try:
            anim = _store(ctx).get_animation(pid, t.animation_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"no animation {t.animation_id!r} in this "
                             "build") from None
        keys, t0 = [], 0.0
        for st in anim.get("steps") or []:
            f, to = st.get("from"), st.get("to")
            if isinstance(f, (list, tuple)) or isinstance(to, (list, tuple)):
                continue
            name, v0 = export.to_mj(ex.map, st["joint_id"], float(f))
            _n, v1 = export.to_mj(ex.map, st["joint_id"], float(to))
            keys.append({"t": t0 + 1e-3, "q": {name: v0}})
            t0 += float(st.get("duration_s") or 1.0)
            keys.append({"t": t0, "q": {name: v1}})
        if not keys:
            raise MisesError(ErrorCode.INVALID_INPUT, "that animation moves no revolute or "
                             "slider joint")
        out = {"kind": "keyframes", "keys": keys}
    dur = _motion_seconds(out, ex)
    if dur > MAX_MOTION_S:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"this {t.kind} motion lasts {dur:.0f} s; dynamics runs at most "
                         f"{MAX_MOTION_S:g} s", hint="shorten period_s / cycles / duration_s, "
                         "sweep fewer joints, or split the motion")
    return out


def _motion_seconds(t: dict, ex) -> float:
    """How long the worker would simulate this trajectory (s), before it runs."""
    k = t.get("kind")
    period = float(t.get("period_s") or {"trot": 0.6, "walk": 1.2}.get(k, 2.0))
    if k == "sweep":
        n = len(t.get("joints") or [v for v in ex.map["actuators"].values()])
        return period * max(n, 1)
    if k in ("squat", "trot", "walk"):
        return period * int(t.get("cycles") or (1 if k == "squat" else 3))
    if k == "stand_up":
        return period
    if k == "keyframes":
        return max((float(x["t"]) for x in t.get("keys") or []), default=0.0)
    return float(t.get("duration_s") or 2.0)


def _label(t: dict) -> str:
    k = t.get("kind", "hold")
    bits = [k]
    if t.get("period_s"):
        bits.append(f"{t['period_s']:g} s")
    if t.get("depth_m"):
        bits.append(f"{t['depth_m'] * 1000:.0f} mm")
    return " ".join(bits)


def _publish(ctx: ToolContext, pid: str, kind: str, rid: str, status: str, summary: str,
             card: dict) -> None:
    events = getattr(ctx, "events", None)
    if events is not None:
        events.publish(Event(type="dyn_result", run_id=getattr(ctx, "run_id", None), data={
            "project_id": pid, "kind": kind, "result_id": rid, "status": status,
            "summary": summary, "card": card, "stream": "sim"}))


def _chart(title: str, unit: str, series: list[tuple[str, list, list]], x="time") -> dict:
    return {"id": title.lower().replace(" ", "_")[:40], "kind": "line", "title": title,
            "x": {"label": x, "unit": "s"}, "y": {"label": title, "unit": unit},
            "series": [{"name": n, "points": [[a, b] for a, b in zip(xs, ys)]}
                       for n, xs, ys in series]}


PLAYBACK_POINTS = 97       # samples per joint of a motion's playback (the app takes <= 129)


def _playback(res: dict, ex) -> dict:
    """Each joint's commanded motion in Mises units, thinned: {joint id: {t, values}} (H4:
    the assembly player replays it)."""
    out = {}
    for j in res.get("joints") or []:
        s = j.get("series") or {}
        t, v = s.get("t") or [], s.get("target") or []
        n = min(len(t), len(v))
        if n < 2:
            continue
        idx = sorted({int(round(x)) for x in np.linspace(0, n - 1, min(n, PLAYBACK_POINTS))})
        out[_jid(ex, j["joint"])] = {
            "t": [round(float(t[i]), 4) for i in idx],
            "values": [round(export.from_mj(ex.map, j["joint"], float(v[i])), 3) for i in idx]}
    return out


def _jid(ex, name: str) -> str:
    return (ex.map["joints"].get(name) or {}).get("joint_id", name)


def _sig(ctx, asm, pid: str) -> str:
    return pipeline.signature(asm, _home(ctx), store=getattr(ctx, "store", None),
                              project_id=pid)


# ---- dyn_build

@tool("dyn_build", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=240,
      description="Build the dynamics (MuJoCo) model of the build's assembly and check it: "
                  "bodies (rigid clusters of fixed-joined parts), joints, actuators, "
                  "couplings and loops, total mass vs the parts' masses, every moving joint "
                  "mapped, and parts overlapping at rest (> 1 mm). Run it first when a "
                  "dyn_* check fails to understand the model. Mass and inertia come from "
                  "the B-rep (rebuild old parts for exact inertia).")
async def dyn_build(ctx: ToolContext, args: DynArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, ex = _export(ctx, pid, args)
    res = await run.run_job(_home(ctx), {"task": "build", "model": ex.model})
    from .. import mass as _mass
    try:
        tot = _mass.assembly_total(ctx.store, _home(ctx), pid,
                                   [i for i in asm.instances if i.id not in ex.excluded])
        parts_kg = tot["total_mass_g"] / 1000.0
    except Exception:  # noqa: BLE001 - a bare store
        parts_kg = ex.total_mass_kg
    names = _names(ex, asm)
    pen = [p for p in res["initial_penetrations"] if p["depth_mm"] > 1.0]
    moving = [j.id for j in asm.joints if j.kind != "fixed"]
    mapped = {v["joint_id"] for v in ex.map["joints"].values()}
    unmapped = [j for j in moving if j not in mapped]
    warnings = list(ex.notes)
    if abs(res["total_mass_kg"] - parts_kg) > max(0.01 * parts_kg, 1e-3):
        warnings.append(f"model mass {res['total_mass_kg']:.3f} kg vs parts "
                        f"{parts_kg:.3f} kg (left-out or unweighed parts)")
    if unmapped:
        warnings.append(f"joints not in the model (loops closed as constraints): "
                        f"{', '.join(unmapped[:8])}")
    for p in pen[:6]:
        a, b = (names.get(x, x) for x in p["bodies"])
        warnings.append(f"{a} and {b} overlap {p['depth_mm']:.1f} mm at rest (bounding boxes)")
    data = {"project_id": pid, "base": ex.base, "bodies": res["nbody"], "joints": res["njnt"],
            "actuators": [{"name": k, **{x: v[x] for x in ("joint_id", "actuator")}}
                          for k, v in ex.map["actuators"].items()],
            "constraints": res["neq"], "total_mass_kg": res["total_mass_kg"],
            "parts_mass_kg": round(parts_kg, 4), "loop_residual_mm": res["loop_residual_m"] * 1e3,
            "overlaps_at_rest": pen, "excluded": ex.excluded, "notes": ex.notes,
            "body_map": {names.get(b, b): ids for b, ids in ex.map["bodies"].items()},
            "accuracy": checks.ACCURACY["dynamics"]}
    s = (f"dynamics model: {res['nbody']} bodies, {len(ex.map['actuators'])} actuated "
         f"joints, {res['neq']} constraints, {res['total_mass_kg']:.3f} kg, base {ex.base}")
    return ToolOutput(summary=s, data=data, warnings=warnings, step_kind=StepKind.SIM_RUN)


# ---- dyn_torque

async def _torque(ctx, pid, args, *, reactions: bool = False):
    asm, ex = _export(ctx, pid, args)
    traj = _traj(ctx, pid, args.trajectory, ex)
    res = await run.run_job(_home(ctx), {"task": "torque", "model": ex.model, "trajectory": traj,
                                          "reactions": reactions})
    return asm, ex, traj, res


@tool("dyn_torque", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=240,
      description="Joint torques of the build moving through a motion (MuJoCo rigid-body "
                  "dynamics): the actuators track the trajectory in a forward simulation "
                  "(servo forces, speeds, power) and inverse dynamics gives the ideal torque. "
                  "Per joint: peak and RMS torque, peak speed and power vs its actuator's "
                  "stall/rated torque and torque-speed line (pass at peak <= 60% of stall for "
                  "servos, 80% for BLDC; RMS <= rated), with lighter/stronger candidates. "
                  "Trajectory: squat / stand_up / trot for a legged robot (feet held by IK), "
                  "reach / sweep / keyframes for an arm, or a saved animation. A guide: state "
                  "the margins and the accuracy notes.")
async def dyn_torque(ctx: ToolContext, args: TorqueArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, ex, traj, res = await _torque(ctx, pid, args)
    rows = checks.torque_rows(res, ex.map)
    status = checks.overall(rows)
    bare = [r["joint_id"] for r in rows if r["status"] == "not_checked"]
    if bare and status in ("pass", "not_checked"):
        # an unjudged joint never clears the gate (Plan 10 C3 fix round 1)
        status = "not_checked"
    label = _label(traj)
    worst = sorted(rows, key=lambda r: -(r.get("use_of_stall") or r["peak_nm"]))
    parts = [f"{r['joint_id']} {r['peak_nm']:.2f} N m" +
             (f" ({r['use_of_stall']:.0%} of {r['actuator']})" if r.get("use_of_stall")
              is not None else "") for r in worst[:3]]
    summary = f"{label}: {status}; peaks " + ", ".join(parts)
    if bare:
        summary = (f"no actuator assigned to moving joint(s) {', '.join(bare[:8])}: assign "
                   "actuators (the actuators argument or the joint's actuator) and rerun "
                   "dyn_torque; " + summary)
    charts = [_chart("Joint torque", "N m",
                     [(f"{_jid(ex, j['joint'])}", j["series"]["t"], j["series"]["servo"])
                      for j in res["joints"][:SAMPLE_CHART_JOINTS]])]
    playback = _playback(res, ex)
    for j in res["joints"]:
        j.pop("series", None)
        j.pop("points", None)
    if res.get("stability", {}).get("series"):
        st = res["stability"]
        charts.append(_chart("CoP margin", "m", [("margin", st["series"]["t"],
                                                  st["series"]["margin"])]))
        st.pop("series", None)
    margins = {r["joint_id"]: r.get("use_of_stall") for r in rows}
    card = {"kind": "dyn_torque", "title": f"Joint torque: {label}", "status": status,
            "rows": rows, "accuracy": checks.ACCURACY["dynamics"], "charts": charts}
    rid = run.save_result(_home(ctx), pid, "dyn_torque", {"summary": summary, "card": card,
                                                           "result": res, "trajectory": traj,
                                                           "playback": playback})
    pipeline.record(_home(ctx), pid, "dyn_torque", status=status, summary=summary,
                    margins=margins, result_id=rid, sig=_sig(ctx, asm, pid),
                    extra={"rms_nm": {r["joint_id"]: r["rms_nm"] for r in rows},
                           "peak_nm": {r["joint_id"]: r["peak_nm"] for r in rows},
                           "speed_rms": {_jid(ex, j["joint"]): j["rms_speed"]
                                         for j in res["joints"]},
                           "actuators": {r["joint_id"]: r.get("actuator") for r in rows}})
    _publish(ctx, pid, "dyn_torque", rid, status, summary, card)
    data = {"project_id": pid, "result_id": rid, "status": status, "trajectory": traj,
            "joints": rows, "duration_s": res["duration_s"], "realtime_x": res["realtime_x"],
            "stability": res.get("stability"), "notes": res["notes"] + ex.notes,
            "card": card, "accuracy": card["accuracy"], "stream": "sim"}
    if res.get("com_travel_m") is not None:
        data["com_travel_mm"] = round(res["com_travel_m"] * 1000, 1)
    if res.get("com_speed_mps") is not None:
        data["com_speed_mps"] = res["com_speed_mps"]
    return ToolOutput(summary=summary, data=data, warnings=ex.notes, step_kind=StepKind.SIM_RUN)


# ---- dyn_stability

@tool("dyn_stability", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=240,
      description="Stability of a free-standing build: centre of mass vs the support polygon "
                  "of its floor contacts after it settles (margin and static tip angle), a "
                  "floor-tilt test (the slope at which it tips or slides) and, with a "
                  "trajectory, the CoP margin along the motion. Pass at >= 20 mm margin and "
                  ">= 15 deg tip angle. A fixed base reports not applicable.")
async def dyn_stability(ctx: ToolContext, args: StabilityArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, ex = _export(ctx, pid, args)
    st = await run.run_job(_home(ctx), {"task": "stability", "model": ex.model,
                                        "tilt": args.tilt_test})
    traj_st = None
    traj = None
    if args.trajectory is not None and ex.base == "free":
        traj = _traj(ctx, pid, args.trajectory, ex)
        res = await run.run_job(_home(ctx), {"task": "torque", "model": ex.model,
                                             "trajectory": traj})
        traj_st = res.get("stability")
        if traj_st:
            traj_st.pop("series", None)
    rows = checks.stability_rows(st, traj_st)
    status = checks.overall(rows) if st.get("applicable", True) else "n/a"
    summary = "stability " + status + ": " + "; ".join(r["reason"] for r in rows if r.get("reason"))
    card = {"kind": "dyn_stability", "title": "Stability", "status": status, "rows": rows,
            "accuracy": checks.ACCURACY["stability"]}
    rid = run.save_result(_home(ctx), pid, "dyn_stability", {"summary": summary, "card": card,
                                                              "result": st, "trajectory": traj})
    margins = {r["check"]: r.get("margin_mm", r.get("tips_at_deg")) for r in rows}
    pipeline.record(_home(ctx), pid, "dyn_stability",
                    status=status, summary=summary if status != "n/a" else
                    "not applicable: " + (st.get("note") or "fixed base"),
                    margins=margins, result_id=rid, sig=_sig(ctx, asm, pid))
    _publish(ctx, pid, "dyn_stability", rid, status, summary, card)
    com = st.get("com_m")
    data = {"project_id": pid, "result_id": rid, "status": status, "rows": rows,
            "com_mm": [round(c * 1000, 1) for c in com] if com else None,
            "support_polygon_mm": [[round(x * 1000, 1) for x in p]
                                   for p in st.get("support_polygon_m") or []],
            "tilt_test": st.get("tilt_test"), "trajectory": traj_st, "card": card,
            "accuracy": card["accuracy"], "stream": "sim", "notes": ex.notes}
    return ToolOutput(summary=summary, data=data, warnings=ex.notes, step_kind=StepKind.SIM_RUN)


# ---- dyn_reactions

def _reaction_rows(res: dict, ex, asm) -> list[dict]:
    names = _names(ex, asm)
    out = []
    for r in res.get("reactions") or []:
        out.append({"link": names.get(r["body"], r["body"]),
                    "instances": ex.map["bodies"].get(r["body"]),
                    "joint_id": _jid(ex, r["joint"]), "peak_force_n": r["peak_force_n"],
                    "rms_force_n": r["rms_force_n"], "at_s": r["at_s"],
                    "bending_moment_nm": r["moment_perp_nm"],
                    "force_n": r["force_n"], "moment_nm": r["moment_nm"]})
    return out


@tool("dyn_reactions", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=240,
      description="Joint reaction loads through a motion: for every link, the force and "
                  "moment its parent joint transmits (at the joint anchor, world axes), peak "
                  "and RMS with the time of the peak. These are the loads dyn_to_fem puts on "
                  "the links' bores; use them to size bearings, pins and shafts.")
async def dyn_reactions(ctx: ToolContext, args: TorqueArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, ex, traj, res = await _torque(ctx, pid, args, reactions=True)
    rows = _reaction_rows(res, ex, asm)
    rows.sort(key=lambda r: -r["peak_force_n"])
    charts = [_chart("Joint reaction force", "N",
                     [(r["body"], r["series"]["t"], r["series"]["force_n"])
                      for r in (res.get("reactions") or [])[:SAMPLE_CHART_JOINTS]])]
    summary = (f"{_label(traj)}: " + ", ".join(f"{r['link']} {r['peak_force_n']:.1f} N at "
                                              f"{r['at_s']:.2f} s" for r in rows[:3]))
    card = {"kind": "dyn_reactions", "title": f"Joint reactions: {_label(traj)}",
            "status": "pass", "rows": rows, "charts": charts,
            "accuracy": checks.ACCURACY["dynamics"]}
    rid = run.save_result(_home(ctx), pid, "dyn_reactions", {"summary": summary, "card": card,
                                                              "trajectory": traj})
    _publish(ctx, pid, "dyn_reactions", rid, "pass", summary, card)
    return ToolOutput(summary=summary, data={"project_id": pid, "result_id": rid,
                                             "links": rows, "card": card, "stream": "sim",
                                             "accuracy": card["accuracy"]},
                      step_kind=StepKind.SIM_RUN)


# ---- dyn_rom_sweep

ROM_CONFIRM = 6           # the worst box hits re-checked on the exact geometry
ROM_CONFIRM_TIMEOUT_S = 60.0


async def _confirm_exact(ctx: ToolContext, asm, ex, res: dict, rows: list[dict]) -> None:
    """Re-check the worst box hits on the exact B-reps at the pose that collided: only the two
    bodies' parts, posed by the assembly solved at those joint values. A hit the exact check
    clears becomes a pass (a box near-miss); a confirmed one keeps failing with its real
    overlap. A part not built (no STEP) or a slow check leaves the box verdict as it was."""
    from ..assembly import geometry, motion
    from ..assembly.solve import solve
    parts = Path(ctx.workdir) / "parts"
    for c, r in list(zip(res.get("collisions") or [], rows))[:ROM_CONFIRM]:
        if not r.get("pair"):
            continue
        a_ids = set(ex.map["bodies"].get(c["bodies"][0]) or [])
        b_ids = set(ex.map["bodies"].get(c["bodies"][1]) or [])
        if not a_ids or not b_ids:
            continue
        values = {_jid(ex, k): export.from_mj(ex.map, k, v) for k, v in c["pose"].items()}
        try:
            posed = solve(motion.with_values(asm, values),
                          resolve_face=geometry.face_resolver(parts))
            sub = asm.model_copy(update={"instances": [i for i in asm.instances
                                                       if i.id in a_ids | b_ids]})
            geo = await geometry.run_geometry(parts, sub, posed, want="interference",
                                              timeout_s=ROM_CONFIRM_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - the box verdict stands
            continue
        vol = sum(float(h.get("volume_mm3") or 0.0) for h in geo.interferences
                  if {h.get("a_id", h.get("a")), h.get("b_id", h.get("b"))} & a_ids
                  and {h.get("a_id", h.get("a")), h.get("b_id", h.get("b"))} & b_ids)
        r["exact_mm3"] = round(vol, 3)
        if vol <= 0.0:
            r["status"] = "pass"
            r["reason"] += ("; the exact geometry at that pose is clear (a bounding-box "
                            "near-miss)")
        else:
            r["reason"] += f"; the exact geometry confirms it: {vol:.1f} mm3 of overlap"


@tool("dyn_rom_sweep", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=420,
      description="Range-of-motion collision sweep: thousands of poses across the joints' "
                  "limits (Latin hypercube, MuJoCo), every pair of parts that collides (not a "
                  "part and the part it is jointed to), how deep and at which joint values. "
                  "Fast and conservative (bounding boxes); the worst hits are then re-checked "
                  "on the exact geometry at their pose (exact_mm3: 0 = a box near-miss, which "
                  "passes). Pairs that already touch at rest are reported apart.")
async def dyn_rom_sweep(ctx: ToolContext, args: RomArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, ex = _export(ctx, pid, args)
    joints = None
    if args.joints:
        joints = [export.to_mj(ex.map, j, 0.0)[0] for j in args.joints]
    res = await run.run_job(_home(ctx), {"task": "rom", "model": ex.model, "joints": joints,
                                         "n": args.n})
    names = _names(ex, asm)
    rows = checks.rom_rows(res, names)
    await _confirm_exact(ctx, asm, ex, res, rows)
    for r in rows:
        if r.get("pose"):
            r["pose"] = {_jid(ex, k): round(export.from_mj(ex.map, k, v), 2)
                         for k, v in r["pose"].items()}
    status = checks.overall(rows)
    rest = [" + ".join(names.get(b, b) for b in p) for p in res["rest_contacts"]]
    summary = (f"ROM sweep {status}: {res['poses']} poses over {len(res['joints'])} joints, "
               f"{res['colliding_pairs']} colliding pair(s)")
    lead = next((r for r in rows if r.get("pair") and r["status"] == "fail"), None) or \
        next((r for r in rows if r.get("pair")), None)
    if lead is not None:
        cleared = sum(1 for r in rows if r.get("exact_mm3") == 0)
        summary += ("; worst " + lead["reason"] +
                    (f" ({cleared} box hit(s) cleared on the exact geometry)" if cleared and
                     lead["status"] == "fail" else ""))
    card = {"kind": "dyn_rom_sweep", "title": "Range of motion", "status": status,
            "rows": rows, "accuracy": checks.ACCURACY["rom"]}
    rid = run.save_result(_home(ctx), pid, "dyn_rom_sweep", {"summary": summary, "card": card,
                                                              "result": res})
    pipeline.record(_home(ctx), pid, "dyn_rom_sweep", status=status, summary=summary,
                    margins={"colliding_pairs": res["colliding_pairs"]}, result_id=rid,
                    sig=_sig(ctx, asm, pid))
    _publish(ctx, pid, "dyn_rom_sweep", rid, status, summary, card)
    return ToolOutput(summary=summary, data={"project_id": pid, "result_id": rid,
                                             "status": status, "collisions": rows,
                                             "touching_at_rest": rest, "card": card,
                                             "accuracy": card["accuracy"], "stream": "sim"},
                      step_kind=StepKind.SIM_RUN)


# ---- dyn_to_fem

def _sim_gate(ctx):
    from ..sim.tools import _gate
    return _gate(ctx)


async def _sim_run(ctx: ToolContext, payload: dict, k: int) -> ToolOutput:
    """One link study as a sim_run call: through the Gate when there is one (approval,
    scope, budget, the Simulations stream), else the handler (tests, in-core)."""
    gate = _sim_gate(ctx)
    if gate is not None and getattr(ctx, "run_id", None):
        return await gate.call(ctx.run_id, "sim_run", payload,
                               call_id=f"{ctx.call_id}.fem{k}", workdir=Path(ctx.workdir))
    from ..sim.tools import SimRunArgs, sim_run
    return await sim_run.handler(ctx, SimRunArgs(**payload))


def _part_material(ctx, part_id: str) -> str | None:
    try:
        row = ctx.store.get_part(part_id)
        return getattr(row, "material", None)
    except Exception:  # noqa: BLE001
        return None


async def _fem(ctx: ToolContext, pid: str, args: FemArgs) -> ToolOutput:
    asm, ex, traj, res = await _torque(ctx, pid, args, reactions=True)
    reactions = res.get("reactions") or []
    if not reactions:
        raise MisesError(ErrorCode.INVALID_INPUT, "no jointed links to load",
                         hint="the assembly needs moving joints between its parts")
    origins = fem_bridge.body_origins(ex.model)
    inst = {i.id: i for i in asm.instances}
    sizes = {}
    for b in ex.model["bodies"]:
        sizes[b["name"]] = max((2 * float(np.linalg.norm(g["size"])) for g in b["geoms"]),
                               default=0.05)
    if args.links:
        want = set(args.links)
        chosen = [r for r in reactions if want & set(ex.map["bodies"].get(r["body"], []))]
        if not chosen:
            raise MisesError(ErrorCode.NOT_FOUND, "none of those instances is a jointed link",
                             hint="name instance ids from assembly_get")
    else:
        chosen = fem_bridge.rank(reactions, sizes, args.n_links)
    estimate = traj.get("kind") in ("trot", "walk")
    rows, k = [], 0
    from .export import _metrics
    for r in chosen:
        members = ex.map["bodies"].get(r["body"], [])
        # the link's main part: the heaviest instance of the body
        body = next(b for b in ex.model["bodies"] if b["name"] == r["body"])
        iid = max(members, key=lambda i: _part_mass(ctx, inst[i].part_id))
        part_id = inst[iid].part_id
        metrics = _metrics(_home(ctx), part_id)
        frames = fem_bridge.Frames(r["xmat"], r["xpos_m"], origins[body["name"]],
                                   ex.transforms[iid])
        label = f"{inst[iid].name or iid} @ {_label(traj)} t={r['at_s']:.2f}s"
        try:
            lc = fem_bridge.load_case(r, frames, metrics, label=label)
        except ValueError as e:
            rows.append({"link": inst[iid].name or iid, "instance_id": iid, "part_id": part_id,
                         "status": "not_checked", "reason": str(e)})
            continue
        payload = {"type": "static", "part_id": part_id,
                   "settings": {"load_cases": [lc["case"]],
                                **({"material": {"name": m}} if (m := _part_material(
                                    ctx, part_id)) else {})},
                   "reason": f"dyn_to_fem: {label}"[:200],
                   "requirements": [{"id": f"sf_{iid}"[:48], "kind": "min_safety_factor",
                                     "target": args.min_safety_factor}],
                   "estimate": estimate, "wait": True}
        k += 1
        try:
            out = await _sim_run(ctx, payload, k)
        except MisesError as e:
            rows.append({"link": inst[iid].name or iid, "instance_id": iid, "part_id": part_id,
                         "status": "not_checked", "reason": e.message,
                         "load_case": lc["case"]})
            continue
        d = out.data or {}
        results = d.get("results") or {}
        sf = results.get("safety_factor")
        sf = sf if isinstance(sf, (int, float)) else None
        st = ("not_checked" if sf is None or not out.ok else
              "pass" if sf >= args.min_safety_factor else
              "warn" if sf >= 0.9 * args.min_safety_factor else "fail")
        if st == "pass" and estimate:
            st = "warn"
        rows.append({"link": inst[iid].name or iid, "instance_id": iid, "part_id": part_id,
                     "sim_id": d.get("sim_id"), "status": st, "safety_factor": sf,
                     "max_von_mises_mpa": results.get("max_von_mises_mpa"),
                     "max_displacement_mm": results.get("max_displacement_mm"),
                     "peak_force_n": r["peak_force_n"], "at_s": r["at_s"],
                     "load_index_nm": r.get("load_index_nm"), "support": lc["support"],
                     "loads": lc["entries"], "notes": lc["notes"],
                     "reason": (f"SF {sf:.2f} vs {args.min_safety_factor:g}" if sf is not None
                                else out.summary)})
    status = checks.worst(r["status"] for r in rows) if rows else "not_checked"
    if any(r["status"] == "not_checked" for r in rows) and status in ("pass", "not_checked"):
        status = "not_checked"      # a link study that failed never clears the gate
    summary = (f"dyn_to_fem {status} ({_label(traj)}): " +
               ", ".join(f"{r['link']} SF {r['safety_factor']:.2f}" if r.get("safety_factor")
                         is not None else f"{r['link']} not evaluated: {r.get('reason')}"
                         for r in rows))
    card = {"kind": "dyn_to_fem", "title": f"Link stress: {_label(traj)}", "status": status,
            "rows": rows, "accuracy": checks.ACCURACY["fem"] + checks.ACCURACY["dynamics"][:1]}
    rid = run.save_result(_home(ctx), pid, "dyn_to_fem", {"summary": summary, "card": card,
                                                           "trajectory": traj})
    pipeline.record(_home(ctx), pid, "dyn_to_fem", status=status, summary=summary,
                    margins={r["link"]: r.get("safety_factor") for r in rows}, result_id=rid,
                    sig=_sig(ctx, asm, pid), extra={"sim_ids": [r.get("sim_id") for r in rows]})
    _publish(ctx, pid, "dyn_to_fem", rid, status, summary, card)
    return ToolOutput(summary=summary, data={"project_id": pid, "result_id": rid,
                                             "status": status, "links": rows, "card": card,
                                             "accuracy": card["accuracy"], "stream": "sim"},
                      ok=True, step_kind=StepKind.SIM_RUN)


def _part_mass(ctx, part_id: str) -> float:
    from .. import mass as _mass
    try:
        return float(_mass.part_mass(ctx.store, _home(ctx), part_id)["mass_g"] or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


@tool("dyn_to_fem", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      timeout_s=FEM_TOOL_TIMEOUT_S, implied_classes=frozenset({SafetyClass.LOCAL_MUTATE}),
      description="Dynamics -> stress: run the motion, take the peak joint reactions of the "
                  "most loaded links (or `links`), and study each link's part in CalculiX "
                  "(one sim_run static study per link: held at its parent joint's bore, the "
                  "child joints' forces and moments and the floor contacts on their faces). "
                  "Returns each link's safety factor vs min_safety_factor and writes it into "
                  "the build's engineering checks. Runs in the background by default (a note "
                  "arrives; job_wait reads it). Results are a GUIDE ONLY: the FEM error band "
                  "and the dynamics accuracy notes apply.")
async def dyn_to_fem(ctx: ToolContext, args: FemArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    wait = args.wait if args.wait is not None else not getattr(ctx, "agent_call", False)
    if wait:
        return await _fem(ctx, pid, args)
    from .. import jobs
    jobs.check_room(ctx)
    job_id = "dyn_" + secrets.token_hex(6)
    job = jobs.start(ctx, "dyn_fem", job_id, "dynamics -> FEM link studies",
                     lambda: _fem(ctx, pid, args), timeout_s=FEM_TOOL_TIMEOUT_S)
    job.scope = [pid, f"project:{pid}"]
    job.build_id = pid
    return ToolOutput(summary=f"dyn_to_fem started in the background ({job_id}); a note "
                              "arrives when every link study is done",
                      data={"job_id": job_id, "status": "running", "project_id": pid,
                            "stream": "sim"}, step_kind=StepKind.SIM_RUN)


TOOLS = (dyn_build, dyn_torque, dyn_stability, dyn_reactions, dyn_rom_sweep, dyn_to_fem)


def register_dyn_tools(registry: ToolRegistry) -> None:
    for t in TOOLS:
        registry.register(t)
