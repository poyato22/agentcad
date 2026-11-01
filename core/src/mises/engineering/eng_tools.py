"""Engineering-check tools (Plan 10 C3, E17/E29): actuator_size, electronics_estimate,
power_budget, thermal_estimate, cable_route and engineering_checks (the "done" gate).

All are reads (electronics_estimate's `reserve` calls internals_pack, itself a read that only
proposes placements). Each writes its verdict into the build's engineering checks record
(engineering.pipeline), so spec_get and the lead's finish check see it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import actuators as act
from . import checks, electronics, pipeline


def _home(ctx: ToolContext) -> Path:
    return Path(ctx.workdir)


def _build(ctx: ToolContext, requested: str | None) -> str:
    from ..dyn.tools import _build as b
    return b(ctx, requested)


def _asm(ctx: ToolContext, pid: str):
    from ..assembly.tools import _store
    return _store(ctx).get(pid)


def _kin(ctx: ToolContext, pid: str, base: str = "auto"):
    from ..dyn import export
    asm = _asm(ctx, pid)
    return asm, export.kinematics(_home(ctx), getattr(ctx, "store", None), asm, base=base)


def _perfs(kin: dict, overrides: dict) -> dict:
    return {j["id"]: act.resolve(overrides.get(j["id"]) or j.get("actuator"))
            for j in kin["tree"] if j["kind"] in ("revolute", "slider")}


def _spec_text(ctx: ToolContext, pid: str) -> str:
    try:
        spec = ctx.store.get_spec(pid) or {}
    except Exception:  # noqa: BLE001
        return ""
    words = [str(spec.get("goal") or "")]
    words += [str(v) for v in (spec.get("answers") or {}).values()]
    try:
        words.append(str(ctx.store.get_project(pid).name))
    except Exception:  # noqa: BLE001
        pass
    return " ".join(words)


# ---- actuator_size

class SizeArgs(BaseModel):
    project_id: str | None = None
    joint_ids: list[str] | None = Field(None, max_length=60)
    actuators: dict[str, str] = Field(default_factory=dict, description="joint id -> actuator "
                                      "(catalog id or name) to check against; default: the "
                                      "joint's own")
    dynamic_factor: float | None = Field(None, ge=1.0, le=5.0, description="static x this "
                                         "(default 1.5; 2.0 for a legged robot)")
    base: Literal["auto", "free", "fixed"] = "auto"


@tool("actuator_size", safety=SafetyClass.READ, scope_arg="project_id",
      description="Static worst-case torque per joint (no simulation, run it at the skeleton "
                  "stage): everything distal to the joint at the worst pose across its range, "
                  "plus, for a legged robot, the moment of its share of the weight on the "
                  "feet; x a dynamic factor (1.5, legs 2.0) vs each joint's actuator (pass at "
                  "<= 60% of stall for servos, 80% for BLDC) with lighter/stronger "
                  "candidates. Confirm with dyn_torque on a real motion before done.")
async def actuator_size(ctx: ToolContext, args: SizeArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, kin = _kin(ctx, pid, args.base)
    static = checks.static_torques(kin["instances"], kin["tree"], root=kin["root"],
                                   free=kin["free"], feet=kin["feet"],
                                   joint_ids=args.joint_ids, pose_fn=kin["pose"])
    if not static:
        raise MisesError(ErrorCode.INVALID_INPUT, "no revolute or slider joints to size",
                         hint="joint the moving parts first (assembly_add_joint)")
    factor = args.dynamic_factor or (2.0 if kin["free"] else 1.5)
    rows = checks.size_rows(static, _perfs(kin, args.actuators), dynamic_factor=factor)
    status = pipeline_status(rows)
    worst = sorted(rows, key=lambda r: -r["design_nm"])[:3]
    summary = f"actuator sizing {status}: " + ", ".join(
        f"{r['joint_id']} {r['design_nm']:.2f} N m" + (f" ({r['use_of_stall']:.0%} of "
                                                       f"{r['actuator']})" if r.get("actuator")
                                                       else " (no actuator)") for r in worst)
    pipeline.record(_home(ctx), pid, "actuator_size", status=status, summary=summary,
                    margins={r["joint_id"]: r.get("use_of_stall") for r in rows},
                    sig=pipeline.signature(asm, _home(ctx)),
                    extra={"design_nm": {r["joint_id"]: r["design_nm"] for r in rows}})
    return ToolOutput(summary=summary, data={"project_id": pid, "status": status,
                                             "dynamic_factor": factor, "free_base": kin["free"],
                                             "total_mass_kg": round(kin["total_mass_kg"], 4),
                                             "joints": rows, "notes": kin["notes"]},
                      warnings=kin["notes"], step_kind=StepKind.MEASURE)


def pipeline_status(rows: list[dict]) -> str:
    from ..dyn.checks import overall
    st = overall(rows)
    return "warn" if st == "not_checked" and rows else st


# ---- electronics + power

class ElecArgs(BaseModel):
    project_id: str | None = None
    vision: bool | None = Field(None, description="camera / SLAM / ROS (default: from the "
                                "spec's words)")
    wifi: bool | None = None
    realtime: bool | None = None
    drone: bool | None = None
    rc: bool | None = None
    outdoor: bool | None = None
    telemetry_km: float = Field(0.0, ge=0, le=100)
    imu: bool = True
    runtime_h: float = Field(0.5, gt=0, le=48)
    enclosure_material: str | None = Field(None, max_length=40)
    actuators: dict[str, str] = Field(default_factory=dict)
    reserve: bool = Field(False, description="also reserve space: internals_pack the parts "
                          "into case_part_id's cavity or cavity_box")
    case_part_id: str | None = None
    cavity_box: dict | None = Field(None, description="{min: [x, y, z], max: [x, y, z]} mm")
    clearance_mm: float = Field(3.0, ge=0, le=50)


def _actuator_loads(ctx, pid: str, kin: dict, perfs: dict) -> tuple[list[dict], str]:
    """Actuator load rows for the power budget: from the latest dyn_torque (RMS torque and
    speed per joint) when it is fresh, else a duty assumption (30% of stall RMS)."""
    rec = pipeline.load(_home(ctx), pid).get("dyn_torque") or {}
    asm = _asm(ctx, pid)
    fresh = rec and rec.get("signature") == pipeline.signature(asm, _home(ctx))
    rows, src = [], "dyn_torque" if fresh else "assumed duty (30% of stall rms)"
    for jid, perf in perfs.items():
        p = perf or act.resolve("mg996r")
        if fresh and jid in (rec.get("rms_nm") or {}):
            rows.append({"name": jid, "perf": p, "tau_rms_nm": rec["rms_nm"][jid],
                         "tau_peak_nm": rec["peak_nm"][jid],
                         "omega_rms_rad_s": (rec.get("speed_rms") or {}).get(jid, 0.5)})
        else:
            rows.append({"name": jid, "perf": p, "tau_rms_nm": 0.3 * p.stall_torque_nm,
                         "tau_peak_nm": 0.8 * p.stall_torque_nm, "omega_rms_rad_s": 0.5})
    if any(v is None for v in perfs.values()):
        src += "; joints with no actuator counted as MG996R servos"
    return rows, src


@tool("electronics_estimate", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=300,
      description="Estimate the electronics an engineering build needs (E17): controller "
                  "(SBC / MCU / flight controller), servo / stepper / BLDC drivers or ESCs, "
                  "radio + antenna clearance, GPS, IMU, BMS, 5 V and servo rails, main "
                  "switch, e-stop, connectors and the battery (from the power budget of the "
                  "build's actuators), each with size, mass, power and the reason. Needs come "
                  "from the build's actuated joints and the spec's words; pass flags to "
                  "override. reserve=true packs them into case_part_id's cavity (or "
                  "cavity_box) with internals_pack and returns the placements to add with "
                  "assembly_add_instance (reserve the space in the layout before skinning).")
async def electronics_estimate(ctx: ToolContext, args: ElecArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    try:
        asm, kin = _kin(ctx, pid)
        perfs = _perfs(kin, args.actuators)
    except MisesError:
        asm, kin, perfs = _asm(ctx, pid), {"free": False, "notes": []}, {}
    words = electronics.needs_from_text(_spec_text(ctx, pid))
    loads, src = _actuator_loads(ctx, pid, kin, perfs) if perfs else ([], "no actuators")
    n = electronics.Needs(
        **electronics.needs_from_actuators(list(perfs.values())),
        **{k: (getattr(args, k) if getattr(args, k) is not None else words[k])
           for k in ("vision", "wifi", "realtime", "drone", "rc", "outdoor")},
        telemetry_km=args.telemetry_km, imu=args.imu, runtime_h=args.runtime_h,
        legs=bool(kin.get("free")) and bool(kin.get("feet")),
        enclosure_material=args.enclosure_material, actuators=loads, notes=[src])
    out = electronics.estimate(n)
    pb = out["power_budget"]
    status = "pass" if pb["status"] == "pass" else "warn"
    picks = ", ".join(f"{c['name']}" + (f" x{c['qty']}" if c["qty"] > 1 else "")
                      for c in out["components"] if c["role"] not in ("connector",
                                                                      "signal_connector"))
    summary = (f"electronics: {len(out['components'])} items, {out['total_mass_g']:.0f} g "
               f"(+{out['wiring_allowance_g']:.0f} g wiring): {picks}")
    data = {"project_id": pid, "status": status, **out, "needs_source": {
        "spec_words": words, "actuators": src}}
    warnings = list(out["warnings"])
    if args.reserve:
        data["reserve"] = await _reserve(ctx, pid, out, args, warnings)
    pipeline.record(_home(ctx), pid, "electronics_estimate", status=status, summary=summary,
                    margins={"mass_g": out["total_mass_g"]},
                    sig=pipeline.signature(asm, _home(ctx)),
                    extra={"components": [{k: c[k] for k in ("role", "item_id", "qty",
                                                             "size_mm", "mass_g", "power_w")}
                                          for c in out["components"]]})
    pipeline.record(_home(ctx), pid, "power_budget", status=pb["status"], summary=pb["reason"],
                    margins={"e_needed_wh": pb["e_needed_wh"], "i_peak_a": pb["i_peak_a"]},
                    sig=pipeline.signature(asm, _home(ctx)))
    return ToolOutput(summary=summary, data=data, warnings=warnings,
                      step_kind=StepKind.MEASURE)


async def _reserve(ctx, pid: str, out: dict, args: ElecArgs, warnings: list[str]) -> dict:
    from ..assembly.internals import PackArgs, internals_pack
    if args.case_part_id is None and args.cavity_box is None:
        warnings.append("reserve: give case_part_id (the enclosure) or cavity_box")
        return {"placements": [], "misfits": []}
    comps = [{"name": b["name"][:60], "size": [round(float(x), 2) for x in b["size"]]}
             for b in out["reserve_boxes"]][:60]
    pa = PackArgs(project_id=pid, components=comps, case_part_id=args.case_part_id,
                  cavity_box=args.cavity_box, clearance_mm=args.clearance_mm)
    res = await internals_pack.handler(ctx, pa)
    if res.data.get("misfits"):
        warnings.append("reserve: " + res.summary)
    return {k: res.data.get(k) for k in ("placements", "misfits", "cavity")}


class PowerArgs(BaseModel):
    project_id: str | None = None
    runtime_h: float = Field(0.5, gt=0, le=48)
    actuators: dict[str, str] = Field(default_factory=dict)
    electronics: list[dict] | None = Field(None, max_length=60, description="[{name, "
                                           "power_w: [typical, peak]}]; default: the latest "
                                           "electronics_estimate")
    battery_cells: int | None = Field(None, ge=1, le=14)
    simultaneity: float = Field(checks.SIMULTANEITY, gt=0, le=1)


@tool("power_budget", safety=SafetyClass.READ, scope_arg="project_id",
      description="Power budget of the build: average power of the actuators (from the latest "
                  "dyn_torque RMS torques and speeds, else a 30% duty assumption) plus the "
                  "electronics, energy for runtime_h (80% depth of discharge, 90% DC-DC), "
                  "peak current, the lightest battery that meets both (C rating x Ah >= "
                  "1.25 x peak), the servo rail and the wire gauges (battery main, actuator "
                  "rail and branch, 5 V logic).")
async def power_budget(ctx: ToolContext, args: PowerArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm, kin = _kin(ctx, pid)
    perfs = _perfs(kin, args.actuators)
    loads, src = _actuator_loads(ctx, pid, kin, perfs)
    el = args.electronics
    if el is None:
        comps = (pipeline.load(_home(ctx), pid).get("electronics_estimate") or {}).get(
            "components") or []
        el = [{"name": c["item_id"], "power_w": [c["power_w"][0] * c["qty"],
                                                 c["power_w"][1] * c["qty"]]}
              for c in comps if c.get("role") != "battery"]
        if not el:
            el = [{"name": "controller (assumed ESP32 + IMU)", "power_w": [0.7, 1.3]}]
    pb = checks.power_budget(loads, el, runtime_h=args.runtime_h,
                             battery_cells=args.battery_cells, simultaneity=args.simultaneity)
    pipeline.record(_home(ctx), pid, "power_budget", status=pb["status"], summary=pb["reason"],
                    margins={"e_needed_wh": pb["e_needed_wh"], "i_peak_a": pb["i_peak_a"]},
                    sig=pipeline.signature(asm, _home(ctx)))
    return ToolOutput(summary=f"power budget {pb['status']}: {pb['reason']}",
                      data={"project_id": pid, **pb, "actuator_source": src},
                      step_kind=StepKind.MEASURE)


# ---- thermal

class ThermalArgs(BaseModel):
    project_id: str | None = None
    power_w: float | None = Field(None, gt=0, le=5000, description="heat inside; default: "
                                  "the latest electronics_estimate's typical power")
    enclosure_part_id: str | None = Field(None, description="its bounding box is the "
                                          "enclosure; or give size_mm")
    size_mm: list[float] | None = Field(None, min_length=3, max_length=3)
    fan: bool = False
    t_amb_c: float = Field(25.0, ge=-40, le=80)
    material: str | None = Field(None, description="enclosure material; default: the part's")
    board_limit_c: float | None = Field(None, ge=0, le=200)

    @model_validator(mode="after")
    def _one(self):
        if self.enclosure_part_id is None and self.size_mm is None:
            raise ValueError("give enclosure_part_id or size_mm")
        return self


@tool("thermal_estimate", safety=SafetyClass.READ, scope_arg="project_id",
      description="Lumped temperature inside an enclosure: T = T_amb + P / (h A), h 5-10 "
                  "W/m2K natural or 25-50 with a fan; flags the enclosure material's service "
                  "temperature (PLA ~55 C) and a board's limit. When it warns, run sim_run "
                  "type thermal on the enclosure.")
async def thermal_estimate(ctx: ToolContext, args: ThermalArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    size = args.size_mm
    mat = args.material
    if args.enclosure_part_id:
        from ..dyn.export import _metrics
        bb = (_metrics(_home(ctx), args.enclosure_part_id).get("bbox") or {}).get("size")
        if not bb:
            raise MisesError(ErrorCode.NOT_FOUND, f"{args.enclosure_part_id} is not built")
        size = bb
        if mat is None:
            from .. import mass as _mass
            mat = _mass.part_material(ctx.store, _home(ctx), args.enclosure_part_id)
    power = args.power_w
    limit_board = args.board_limit_c
    if power is None:
        comps = (pipeline.load(_home(ctx), pid).get("electronics_estimate") or {}).get(
            "components") or []
        power = sum(c["power_w"][0] * c["qty"] for c in comps) or None
        if limit_board is None and any(c.get("item_id") == "rpi5" for c in comps):
            limit_board = 85.0
    if not power:
        raise MisesError(ErrorCode.INVALID_INPUT, "how much heat?",
                         hint="pass power_w, or run electronics_estimate first")
    t = checks.thermal_estimate(power, size_mm=size, fan=args.fan, t_amb_c=args.t_amb_c,
                                max_service_c=_service_temp(mat, _home(ctx)), material=mat,
                                board_limit_c=limit_board)
    return ToolOutput(summary=f"thermal {t['status']}: {t['reason']}",
                      data={"project_id": pid, "material": mat, **t},
                      step_kind=StepKind.MEASURE)


def _service_temp(material: str | None, home: Path) -> float | None:
    if not material:
        return None
    try:
        from ..sim.materials import MATERIALS, load_custom
        for m in [*MATERIALS.values(), *load_custom(home)]:
            if m.name.lower() == material.lower():
                v = getattr(m, "max_service_c", None) or getattr(m, "hdt_c", None)
                return float(v) if v else None
    except Exception:  # noqa: BLE001
        return None
    return None


# ---- cable routing

class Net(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    from_: str = Field(alias="from", description="instance id (or 'x,y,z' mm)")
    to: str = Field(description="instance id (or 'x,y,z' mm)")
    amps: float = Field(1.0, gt=0, le=500)

    model_config = {"populate_by_name": True}


class CableArgs(BaseModel):
    project_id: str | None = None
    nets: list[Net] = Field(min_length=1, max_length=40)
    clearance_mm: float = Field(3.0, ge=0, le=50)
    voxel_mm: float = Field(5.0, ge=1, le=50)


@tool("cable_route", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=180,
      description="Route each net (battery -> switch -> driver -> motor, signal lines) "
                  "between two instances through the assembly's free space (A* on a voxel "
                  "grid around the parts' boxes, with clearance): path, length, wire gauge "
                  "from the current, mass, and a service loop + bend-radius check at every "
                  "moving joint it crosses (the path should pass within 15 mm of the joint "
                  "axis). Build the cable with the cable_path recipe from the returned path "
                  "and add the lengths to the parts list.")
async def cable_route(ctx: ToolContext, args: CableArgs) -> ToolOutput:
    import numpy as np
    pid = _build(ctx, args.project_id)
    asm, kin = _kin(ctx, pid)
    rows = {r["id"]: r for r in kin["instances"]}

    def point(ref: str):
        if ref in rows:
            lo, hi = (np.array(x) * 1000.0 for x in rows[ref]["aabb"])
            return ((lo + hi) / 2).tolist(), ref
        try:
            xyz = [float(x) for x in ref.split(",")]
            if len(xyz) == 3:
                return xyz, None
        except ValueError:
            pass
        raise MisesError(ErrorCode.NOT_FOUND, f"no instance {ref!r}",
                         hint="nets name instance ids from assembly_get, or 'x,y,z' in mm")
    tree = kin["tree"]
    out, statuses = [], []
    for net in args.nets:
        (a, ia), (b, ib) = point(net.from_), point(net.to)
        obstacles = [tuple(np.array(r["aabb"][k]) * 1000.0 for k in (0, 1))
                     for r in kin["instances"] if r["id"] not in (ia, ib)]
        r = checks.route(a, b, obstacles, voxel=args.voxel_mm, clearance=args.clearance_mm)
        w = checks.wire_gauge(net.amps)
        row = {"net": net.name, "from": net.from_, "to": net.to, "amps": net.amps,
               "awg": w["awg"], "od_mm": w["od_mm"], "path": r["path"]}
        if r["path"] is None:
            row.update(status="fail", reason="no free path between them (boxed in)")
            out.append(row)
            statuses.append("fail")
            continue
        length = r["length_mm"]
        loops, warn = [], []
        crossed = _joints_between(tree, ia, ib) if ia and ib else []
        for j in crossed:
            lim = j.get("limits") or [-90, 90]
            rom = float(lim[1]) - float(lim[0]) if j["kind"] == "revolute" else 0.0
            extra = checks.service_loop_mm(w["od_mm"], rom) if j["kind"] == "revolute" else \
                abs(float(lim[1]) - float(lim[0])) + 20.0
            p = np.array(j["anchor"]) * 1000.0
            ax = np.array(j["axis"])
            d = min(float(np.linalg.norm(np.cross(np.array(q) - p, ax))) for q in r["path"])
            loops.append({"joint_id": j["id"], "service_loop_mm": round(extra, 1),
                          "path_to_axis_mm": round(d, 1)})
            length += extra
            if d > 15.0:
                warn.append(f"passes {d:.0f} mm from joint {j['id']}'s axis (route it within "
                            "15 mm so it bends, not stretches)")
        bend = 8.0 * w["od_mm"]
        row.update(length_mm=round(length, 1), mass_g=round(length / 1000.0 * w["g_per_m"], 2),
                   service_loops=loops, min_bend_radius_mm=round(bend, 1),
                   status="warn" if warn else "pass", reason="; ".join(warn) or
                   f"{length:.0f} mm of {w['awg']} AWG")
        statuses.append(row["status"])
        out.append(row)
    from ..dyn.checks import worst
    st = worst(statuses)
    summary = f"cable routes {st}: " + ", ".join(f"{r['net']} {r.get('length_mm') or '-'} mm "
                                                 f"{r['awg']} AWG" for r in out)
    return ToolOutput(summary=summary, data={"project_id": pid, "status": st, "nets": out},
                      step_kind=StepKind.MEASURE)


def _joints_between(tree: list[dict], a: str, b: str) -> list[dict]:
    """The moving joints on the tree path from instance a to instance b."""
    adj: dict[str, list] = {}
    for j in tree:
        adj.setdefault(j["a"], []).append((j["b"], j))
        adj.setdefault(j["b"], []).append((j["a"], j))
    prev: dict[str, tuple] = {a: (None, None)}
    q = [a]
    while q:
        x = q.pop(0)
        if x == b:
            break
        for y, j in adj.get(x, []):
            if y not in prev:
                prev[y] = (x, j)
                q.append(y)
    if b not in prev:
        return []
    out, x = [], b
    while prev[x][0] is not None:
        x, j = prev[x]
        if j["kind"] != "fixed":
            out.append(j)
    return out


# ---- the done gate

class ChecksArgs(BaseModel):
    project_id: str | None = None


@tool("engineering_checks", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=180,
      description="The build's engineering checks before you report done: the skeleton "
                  "checks (actuator_size, electronics_estimate, power_budget) and, for a build "
                  "with moving joints, dyn_torque, dyn_stability, dyn_rom_sweep, dyn_to_fem "
                  "and assembly_health, each pass / warn / fail / missing / stale (the "
                  "assembly changed since) with its margins. Do not report done while one is "
                  "missing, failing or stale; state the margins in your final message.")
async def engineering_checks(ctx: ToolContext, args: ChecksArgs) -> ToolOutput:
    pid = _build(ctx, args.project_id)
    asm = _asm(ctx, pid)
    health = await _health(ctx, pid, asm)
    rep = pipeline.report(_home(ctx), pid, asm, health_available=health is not None)
    text = pipeline.done_text(rep)
    summary = (f"engineering checks {rep['overall']}" +
               (f": {text}" if text else ": clear (state the margins in your report)"))
    return ToolOutput(summary=summary, data={"project_id": pid, **rep, "done_ok": text is None},
                      step_kind=StepKind.VERIFY)


async def _health(ctx: ToolContext, pid: str, asm) -> dict | None:
    """C1's assembly_health (E33), when this Mises has it: called through the Gate (it is a
    read) and recorded as the pipeline's assembly_health row. None when it does not exist."""
    from ..sim.tools import _gate
    gate = _gate(ctx)
    if gate is None or not gate.registry.list(["assembly_health"]) or not ctx.run_id:
        return None
    try:
        out = await gate.call(ctx.run_id, "assembly_health", {"project_id": pid},
                              call_id=f"{ctx.call_id}.health", workdir=Path(ctx.workdir))
    except MisesError as e:
        pipeline.record(_home(ctx), pid, "assembly_health", status="fail",
                        summary=f"assembly_health failed: {e.message}",
                        sig=pipeline.signature(asm, _home(ctx)))
        return {"error": e.message}
    d = out.data or {}
    errors = d.get("errors")
    if errors is None:
        errors = [x for k in ("overlaps", "stray", "strays", "floating") for x in (d.get(k) or [])]
    status = "fail" if (not out.ok or errors) else "pass"
    pipeline.record(_home(ctx), pid, "assembly_health", status=status, summary=out.summary,
                    margins={"issues": len(errors) if isinstance(errors, list) else errors},
                    sig=pipeline.signature(asm, _home(ctx)))
    return d


TOOLS = (actuator_size, electronics_estimate, power_budget, thermal_estimate, cable_route,
         engineering_checks)


def register_eng_check_tools(registry: ToolRegistry) -> None:
    for t in TOOLS:
        registry.register(t)
