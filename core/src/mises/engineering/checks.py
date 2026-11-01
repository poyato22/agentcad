"""Practical engineering checks (Plan 10 C3, research §3): static actuator sizing, power
budget + battery pick + wire gauges, lumped electronics thermal, and cable routing.

Pure functions (no MuJoCo, no sandbox): each returns plain data with a pass / warn / fail /
not_checked status and the reason, so the tools can write it into the build's checks record
and the agent can state the margins. Numbers marked [J] in the research are defaults the agent
may override.
"""
from __future__ import annotations

import heapq
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import actuators as act

G = 9.81
DATA = Path(__file__).resolve().parent / "data" / "electronics.json"
DOD = 0.8                    # usable depth of discharge
ETA_DCDC = 0.9
PEAK_HEADROOM = 1.25         # battery C x Ah >= 1.25 x peak current
SIMULTANEITY = 0.6           # share of actuators at peak current at once [J]


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads(DATA.read_text())


def item(item_id: str) -> dict | None:
    return next((i for i in data()["items"] if i["id"] == item_id), None)


# ---------------------------------------------------------------- wires

def wire_gauge(amps: float) -> dict:
    """The thinnest silicone wire (bundled chassis wiring) that carries `amps` continuously."""
    for w in data()["wire_awg"]:
        if amps <= w["amps"]:
            return dict(w)
    return {**data()["wire_awg"][-1], "note": f"{amps:.0f} A is over 10 AWG: parallel runs"}


# ---------------------------------------------------------------- battery

def pick_battery(e_wh: float, i_peak: float, *, cells: int | None = None,
                 chem: str | None = None) -> dict | None:
    """The lightest pack with energy >= e_wh and C x Ah >= PEAK_HEADROOM x i_peak (optionally
    a cell count and chemistry). None when no pack in the table is big enough."""
    fits = []
    for b in data()["batteries"]:
        if cells is not None and b["cells"] != cells:
            continue
        if chem is not None and b["chem"] != chem:
            continue
        i_max = b["c"] * b["mah"] / 1000.0
        if b["wh"] >= e_wh and i_max >= PEAK_HEADROOM * i_peak:
            fits.append({**b, "i_max_a": round(i_max, 1)})
    return min(fits, key=lambda b: b["mass_g"]) if fits else None


def cells_for(volts: float) -> int:
    """The LiPo series count whose nominal voltage (3.7 V/cell) reaches `volts` (2S for a 6 V
    servo rail through a BEC)."""
    return max(1, math.ceil((volts - 0.05) / 3.7))


def power_budget(actuators: list[dict], electronics: list[dict], *, runtime_h: float = 0.5,
                 battery_cells: int | None = None, simultaneity: float = SIMULTANEITY) -> dict:
    """Research §3.2. actuators: [{name, perf: Perf | id, tau_rms_nm, omega_rms_rad_s,
    tau_peak_nm}] (from dyn_torque, or a duty estimate); electronics: [{name, power_w: [typ,
    peak], volts?}]. Returns P_avg, E_needed, I_peak, the battery pick and wire gauges."""
    rows, p_act, i_peak_act, v_max = [], 0.0, 0.0, 0.0
    branch_peak: list[float] = []
    for a in actuators:
        perf = a["perf"] if isinstance(a.get("perf"), act.Perf) else act.resolve(a.get("perf"))
        if perf is None:
            rows.append({"name": a.get("name"), "status": "not_checked",
                         "reason": "no actuator data: not in the budget"})
            continue
        tau_rms, w_rms = float(a.get("tau_rms_nm", 0.0)), float(a.get("omega_rms_rad_s", 0.0))
        tau_peak = float(a.get("tau_peak_nm", tau_rms))
        if perf.type.startswith("bldc"):
            p = tau_rms * w_rms / perf.efficiency + perf.idle_current_a * perf.voltage_v
            i_pk = max(perf.current_at(tau_peak), p / max(perf.voltage_v, 1e-9))
        else:
            # servos and geared DC draw current to HOLD a torque, even at zero speed
            i_rms = perf.current_at(tau_rms)
            p = perf.voltage_v * i_rms + (tau_rms * w_rms) * 0.0
            i_pk = perf.current_at(tau_peak)
        p_act += p
        i_peak_act += i_pk
        branch_peak.append(i_pk)
        v_max = max(v_max, perf.voltage_v)
        rows.append({"name": a.get("name"), "actuator": perf.id, "power_w": round(p, 3),
                     "current_peak_a": round(i_pk, 3), "volts": perf.voltage_v})
    p_el = sum(float(e["power_w"][0]) for e in electronics)
    p_el_peak = sum(float(e["power_w"][1]) for e in electronics)
    p_avg = p_act + p_el
    e_needed = p_avg * runtime_h / DOD / ETA_DCDC
    cells = battery_cells or cells_for(max(v_max, 7.0) if v_max else 7.4)
    v_bat = 3.7 * cells
    # actuators on their own rail (at their voltage), electronics on 5 V: back to the battery
    i_peak = (simultaneity * i_peak_act * (v_max or v_bat) / v_bat / ETA_DCDC
              + p_el_peak / v_bat / ETA_DCDC)
    bat = pick_battery(e_needed, i_peak, cells=cells) or pick_battery(e_needed, i_peak)
    wires = {"battery_main": {"amps": round(i_peak, 2), **wire_gauge(i_peak)}}
    if branch_peak:
        worst = max(branch_peak)
        wires["actuator_branch"] = {"amps": round(worst, 2), **wire_gauge(worst)}
        rail = simultaneity * i_peak_act
        wires["actuator_rail"] = {"amps": round(rail, 2), **wire_gauge(rail)}
    i5 = p_el_peak / 5.0
    wires["logic_5v"] = {"amps": round(i5, 2), **wire_gauge(i5)}
    status = "pass" if bat else "fail"
    reason = (f"{p_avg:.1f} W average, {e_needed:.1f} Wh for {runtime_h:g} h, peak "
              f"{i_peak:.1f} A at {v_bat:.1f} V")
    if bat:
        reason += f": {bat['name']} ({bat['wh']:g} Wh, {bat['i_max_a']:.0f} A max, " \
                  f"{bat['mass_g']:g} g)"
        runtime = bat["wh"] * DOD * ETA_DCDC / max(p_avg, 1e-9)
    else:
        reason += ": no pack in the table is big enough (split the load or shorten runtime)"
        runtime = None
    rail_a = simultaneity * i_peak_act
    rail_note = None
    if v_max and v_max < v_bat - 0.5 and i_peak_act > 0:
        rail_note = (f"actuator rail {v_max:g} V from a BEC rated >= {1.5 * rail_a:.0f} A "
                     "(never 4+ servos from the 5 V logic rail)")
    return {"status": status, "reason": reason, "p_avg_w": round(p_avg, 2),
            "p_actuators_w": round(p_act, 2), "p_electronics_w": round(p_el, 2),
            "e_needed_wh": round(e_needed, 2), "i_peak_a": round(i_peak, 2),
            "battery_voltage_v": round(v_bat, 1), "battery": bat,
            "runtime_h_with_pick": round(runtime, 2) if runtime else None,
            "wires": wires, "actuators": rows, "rail_note": rail_note,
            "assumptions": {"dod": DOD, "eta_dcdc": ETA_DCDC, "simultaneity": simultaneity,
                            "peak_headroom": PEAK_HEADROOM, "runtime_h": runtime_h}}


# ---------------------------------------------------------------- thermal

H_NATURAL = (5.0, 10.0)      # W/m2K [J]
H_FAN = (25.0, 50.0)


def thermal_estimate(power_w: float, *, size_mm: list[float] | None = None,
                     area_m2: float | None = None, fan: bool = False, t_amb_c: float = 25.0,
                     max_service_c: float | None = None, material: str | None = None,
                     board_limit_c: float | None = None) -> dict:
    """Research §3.7 tier 1: T_in = T_amb + P / (h A), h over the natural (or fan) range."""
    if area_m2 is None:
        if not size_mm:
            raise ValueError("give the enclosure size_mm or area_m2")
        x, y, z = (s / 1000.0 for s in size_mm)
        area_m2 = 2 * (x * y + y * z + x * z)
    lo_h, hi_h = H_FAN if fan else H_NATURAL
    dt_hi, dt_lo = power_w / (lo_h * area_m2), power_w / (hi_h * area_m2)
    t_hi, t_lo = t_amb_c + dt_hi, t_amb_c + dt_lo
    status, reasons = "pass", []
    if max_service_c is not None:
        if t_hi > max_service_c:
            status = "fail" if t_lo > max_service_c else "warn"
            reasons.append(f"inside {t_lo:.0f}-{t_hi:.0f} C vs the {material or 'enclosure'} "
                           f"limit {max_service_c:g} C")
        elif t_hi > max_service_c - 10:
            status = "warn"
            reasons.append(f"within 10 C of the {material or 'enclosure'} limit "
                           f"{max_service_c:g} C")
    if board_limit_c is not None and t_hi > board_limit_c - 25:
        # the board runs ~20-25 C over the air around it [J]
        status = "warn" if status == "pass" else status
        reasons.append(f"board limit {board_limit_c:g} C is close (it runs ~20-25 C over the "
                       "air inside)")
    if status != "pass":
        reasons.append("run sim_run type thermal on the enclosure, add vents or a fan")
    return {"status": status, "t_inside_c": [round(t_lo, 1), round(t_hi, 1)],
            "delta_t_k": [round(dt_lo, 1), round(dt_hi, 1)], "area_m2": round(area_m2, 5),
            "h_w_m2k": [lo_h, hi_h], "power_w": power_w,
            "reason": "; ".join(reasons) or f"inside {t_lo:.0f}-{t_hi:.0f} C at "
                                             f"{power_w:g} W ({'fan' if fan else 'natural'})"}


# ---------------------------------------------------------------- static actuator sizing

def static_torques(instances: list[dict], tree: list[dict], *, root: str, free: bool,
                   feet: list[str], joint_ids: list[str] | None = None,
                   pose_fn=None, samples: int = 13) -> list[dict]:
    """Research §3.1 static worst case per joint: tau = |axis . sum (c_i - p) x m_i g| over
    everything distal to the joint (the side away from the root), at the worst of `samples`
    values across its ROM (other joints at their current value), via `pose_fn(values) ->
    (instances, joints)` re-solving the assembly. A legged robot also carries its weight: at the
    current pose each foot pushes W / n_feet up, and the joint holds that moment too.

    instances: [{id, mass_kg, com (m, world)}]; tree: [{id, kind, a, b, anchor (m), axis,
    value, limits}] (the spanning tree)."""
    total = sum(i["mass_kg"] for i in instances)
    out = []
    for j in tree:
        if j["kind"] not in ("revolute", "slider"):
            continue
        if joint_ids and j["id"] not in joint_ids:
            continue
        side = _distal(tree, j, root)
        lim = j.get("limits") or ([-90.0, 90.0] if j["kind"] == "revolute" else [-20.0, 20.0])
        vals = np.linspace(float(lim[0]), float(lim[1]), samples)
        worst, worst_v = 0.0, float(j.get("value") or 0)
        for v in vals:
            inst, joints = pose_fn({j["id"]: float(v)}) if pose_fn else (instances, tree)
            jj = next(x for x in joints if x["id"] == j["id"])
            t = _gravity_torque(inst, side, jj)
            if t > worst:
                worst, worst_v = t, float(v)
        standing = 0.0
        if free and feet:
            my_feet = [f for f in feet if f in side]
            if my_feet:
                inst, joints = (instances, tree)
                byid = {i["id"]: i for i in inst}
                p, ax = np.array(j["anchor"]), np.array(j["axis"])
                tq = np.zeros(3)
                for f in my_feet:
                    fp = np.array(byid[f]["foot"])
                    tq += np.cross(fp - p, np.array([0.0, 0.0, total * G / len(feet)]))
                for iid in side:
                    tq += np.cross(np.array(byid[iid]["com"]) - p,
                                   np.array([0.0, 0.0, -byid[iid]["mass_kg"] * G]))
                standing = abs(float(np.dot(tq, ax)))
        out.append({"joint_id": j["id"], "kind": j["kind"],
                    "swing_nm": round(worst, 4), "worst_value": round(worst_v, 2),
                    "standing_nm": round(standing, 4),
                    "static_nm": round(max(worst, standing), 4),
                    "distal_mass_kg": round(sum(i["mass_kg"] for i in instances
                                                if i["id"] in side), 4)})
    return out


def _distal(tree: list[dict], j: dict, root: str) -> set[str]:
    adj: dict[str, list[str]] = {}
    for e in tree:
        if e is j:
            continue
        adj.setdefault(e["a"], []).append(e["b"])
        adj.setdefault(e["b"], []).append(e["a"])
    seen, stack = {root}, [root]
    while stack:
        x = stack.pop()
        for y in adj.get(x, []):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    far = j["b"] if j["a"] in seen else j["a"]
    side, stack = {far}, [far]
    while stack:
        x = stack.pop()
        for y in adj.get(x, []):
            if y not in side and y not in seen:
                side.add(y)
                stack.append(y)
    return side


def _gravity_torque(inst: list[dict], side: set[str], j: dict) -> float:
    p, ax = np.array(j["anchor"]), np.array(j["axis"])
    tq = np.zeros(3)
    fz = 0.0
    for i in inst:
        if i["id"] in side:
            tq += np.cross(np.array(i["com"]) - p, np.array([0.0, 0.0, -i["mass_kg"] * G]))
            fz += i["mass_kg"] * G
    if j["kind"] == "slider":
        return abs(float(np.dot(np.array([0.0, 0.0, -fz]), ax)))
    return abs(float(np.dot(tq, ax)))


def size_rows(static: list[dict], perfs: dict[str, act.Perf | None], *,
              dynamic_factor: float = 1.5) -> list[dict]:
    """Static torque x dynamic factor vs each joint's actuator (0.6 x stall for servos)."""
    rows = []
    for s in static:
        need = s["static_nm"] * dynamic_factor
        perf = perfs.get(s["joint_id"])
        row = {**s, "dynamic_factor": dynamic_factor, "design_nm": round(need, 4)}
        if perf is None:
            cands = act.candidates(need, s["static_nm"], 0.0)
            row.update(status="not_checked", actuator=None, candidates=[c.id for c in cands[:4]],
                       reason=f"no actuator assigned: needs {need:.2f} N m (static "
                              f"{s['static_nm']:.2f} x {dynamic_factor:g})")
        else:
            use = need / perf.stall_torque_nm
            m = perf.peak_margin
            status = "pass" if use <= m else "warn" if use <= min(1.0, m + 0.2) else "fail"
            row.update(status=status, actuator=perf.id, stall_nm=perf.stall_torque_nm,
                       use_of_stall=round(use, 3),
                       reason=f"{need:.2f} N m design torque is {use:.0%} of {perf.name}'s "
                              f"{perf.stall_torque_nm:g} N m stall (rule <= {m:.0%})")
            if status != "pass":
                row["candidates"] = [c.id for c in act.candidates(need, s["static_nm"], 0.0)[:4]]
        rows.append(row)
    return rows


# ---------------------------------------------------------------- cable routing

def route(start: list[float], goal: list[float], obstacles: list[tuple], *, voxel: float = 5.0,
          clearance: float = 3.0, bounds: tuple | None = None) -> dict:
    """A* on a voxel grid (mm) from start to goal around axis-aligned obstacle boxes
    [(lo, hi)], each inflated by `clearance`. Returns {path: [[x, y, z]...], length_mm} or
    {path: None} when boxed in. A start or goal inside a box may leave it (its own part)."""
    s, g = np.array(start, float), np.array(goal, float)
    pts = [s, g] + [np.array(b[0]) for b in obstacles] + [np.array(b[1]) for b in obstacles]
    lo = np.min(pts, axis=0) - 20 if bounds is None else np.array(bounds[0], float)
    hi = np.max(pts, axis=0) + 20 if bounds is None else np.array(bounds[1], float)
    n = np.maximum(np.ceil((hi - lo) / voxel).astype(int) + 1, 1)
    if int(np.prod(n)) > 400_000:
        voxel = float(np.max(hi - lo) / 70.0)
        n = np.maximum(np.ceil((hi - lo) / voxel).astype(int) + 1, 1)
    blocked = np.zeros(n, dtype=bool)
    for blo, bhi in obstacles:
        a = np.clip(np.floor((np.array(blo) - clearance - lo) / voxel).astype(int), 0, n - 1)
        b = np.clip(np.ceil((np.array(bhi) + clearance - lo) / voxel).astype(int), 0, n - 1)
        blocked[a[0]:b[0] + 1, a[1]:b[1] + 1, a[2]:b[2] + 1] = True
    cell = lambda p: tuple(np.clip(np.round((p - lo) / voxel).astype(int), 0, n - 1))  # noqa: E731
    cs, cg = cell(s), cell(g)
    for c0 in (cs, cg):            # the ends may sit on (inflated) neighbours: free 2 voxels
        a = np.clip(np.array(c0) - 2, 0, n - 1)
        b = np.clip(np.array(c0) + 2, 0, n - 1)
        blocked[a[0]:b[0] + 1, a[1]:b[1] + 1, a[2]:b[2] + 1] = False
    openq = [(0.0, cs)]
    came: dict = {cs: None}
    cost = {cs: 0.0}
    steps = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    found = False
    while openq:
        _, c = heapq.heappop(openq)
        if c == cg:
            found = True
            break
        for dx, dy, dz in steps:
            nb = (c[0] + dx, c[1] + dy, c[2] + dz)
            if not (0 <= nb[0] < n[0] and 0 <= nb[1] < n[1] and 0 <= nb[2] < n[2]):
                continue
            if blocked[nb]:
                continue
            nc = cost[c] + 1.0
            if nc < cost.get(nb, math.inf):
                cost[nb] = nc
                came[nb] = c
                h = abs(nb[0] - cg[0]) + abs(nb[1] - cg[1]) + abs(nb[2] - cg[2])
                heapq.heappush(openq, (nc + h, nb))
    if not found:
        return {"path": None, "length_mm": None}
    cells, c = [], cg
    while c is not None:
        cells.append(c)
        c = came[c]
    cells.reverse()
    path = [s.tolist()] + [(lo + np.array(c) * voxel).tolist() for c in cells[1:-1]] + [g.tolist()]
    path = _simplify(path)
    length = sum(float(np.linalg.norm(np.array(b) - np.array(a))) for a, b in zip(path, path[1:]))
    return {"path": [[round(x, 1) for x in p] for p in path], "length_mm": round(length, 1),
            "voxel_mm": voxel}


def _simplify(path: list) -> list:
    """Drop the middle points of straight runs."""
    if len(path) < 3:
        return path
    out = [path[0]]
    for a, b, c in zip(path, path[1:], path[2:]):
        d1, d2 = np.array(b) - np.array(a), np.array(c) - np.array(b)
        if np.linalg.norm(np.cross(d1, d2)) > 1e-6:
            out.append(b)
    out.append(path[-1])
    return out


def service_loop_mm(od_mm: float, rom_deg: float) -> float:
    """Extra length at a moving joint: r_bend x ROM (rad) + 20 mm, r_bend = 8 x OD [J]."""
    return 8.0 * od_mm * math.radians(abs(rom_deg)) + 20.0
