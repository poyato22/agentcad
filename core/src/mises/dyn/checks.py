"""Verdicts on dynamics results (Plan 10 C3, research §3.1, §3.3, §3.4, §4.5): joint torque
vs the actuator's curve with margin, stability margins, ROM collisions, and the accuracy
statements every result card carries. Pure functions (host side, no MuJoCo).

Status: pass / warn / fail, or not_checked when there is nothing to check against (a joint
with no actuator assigned: the needed torque is still reported, with candidates).
"""
from __future__ import annotations

import math

from ..engineering import actuators as act

ORDER = {"fail": 3, "warn": 2, "pass": 1, "not_checked": 0}

#: research §4.5, stated on every result card (guide-only bands)
ACCURACY = {
    "dynamics": [
        "Guide only: rigid bodies (link compliance and gear backlash ignored).",
        "Servos are a PD model (stall at about 3 deg of error); 'servo' is what a stiff "
        "position servo draws tracking the motion, 'ideal' is inverse dynamics of the motion "
        "the robot actually made. Sizing uses the larger.",
        "Contacts are soft (MuJoCo): impact peaks depend on contact stiffness and are "
        "estimates; static and quasi-static results are accurate (exact on the 2-link check).",
        "Mass is B-rep volume x density (exact for solid parts); infill, fasteners and wiring "
        "(+5-10%) are not included unless modelled.",
    ],
    "stability": [
        "Guide only: support polygon from the simulated floor contacts; CoP from contact "
        "forces (soft contacts).",
        "The tilt test tilts gravity in 2.5 deg steps (friction 0.9): the angle is within "
        "one step; sliding can come before tipping.",
    ],
    "rom": [
        "Guide only: each part is its bounding box, so a hit can be a near miss; confirm "
        "hits with assembly_check sweep (exact B-rep).",
        "A part and the part it is jointed to are not checked against each other.",
    ],
    "fem": [
        "Guide only: link loads are the rigid-body dynamics peak (joint reactions at the "
        "anchors), applied as a remote force + moment on the bore of the joint where the "
        "load enters; the link is held at its parent joint's bore.",
        "The link's own weight and inertia are left out (small next to the joint loads).",
        "FEM error band as on the study card (mesh convergence); printed parts use the "
        "material's FDM knockdown.",
    ],
}


def _worst(rows: list[dict]) -> str:
    checked = [r["status"] for r in rows if r["status"] != "not_checked"]
    return max(checked, key=lambda s: ORDER[s]) if checked else "not_checked"


def worst(statuses) -> str:
    s = [x for x in statuses if x and x != "not_checked"]
    return max(s, key=lambda x: ORDER[x]) if s else "not_checked"


def torque_row(j: dict, perf: act.Perf | None, *, margin: float | None = None) -> dict:
    """One joint of a dyn_torque result against its actuator."""
    peak = max(float(j["peak_servo"]), float(j["peak_inverse"]))
    rms = max(float(j["rms_servo"]), float(j["rms_inverse"]))
    w = float(j["peak_speed"])
    row = {"peak_nm": round(peak, 4), "rms_nm": round(rms, 4),
           "peak_servo_nm": j["peak_servo"], "peak_ideal_nm": j["peak_inverse"],
           "peak_speed_rpm": round(w * 60 / (2 * math.pi), 2),
           "peak_power_w": j.get("peak_power_w"),
           "tracking_error_deg": round(math.degrees(float(j.get("max_tracking_error", 0))), 2)}
    if perf is None:
        need = peak / 0.6
        cands = act.candidates(peak, rms, w)
        row.update(status="not_checked", actuator=None,
                   reason=f"no actuator on this joint: it needs about {peak:.2f} N m peak, "
                          f"{rms:.2f} N m rms at up to {row['peak_speed_rpm']:.0f} rpm "
                          f"(a servo of >= {need:.2f} N m stall at the 0.6 margin)",
                   candidates=[c.id for c in cands[:4]])
        return row
    m = margin if margin is not None else perf.peak_margin
    use = peak / perf.stall_torque_nm if perf.stall_torque_nm else math.inf
    # every (speed, torque) point under the torque-speed line, with the speed margin
    ts_margin = min(((perf.torque_available(p[0] / 0.8) - p[1]) / perf.stall_torque_nm
                     for p in j.get("points") or []), default=1.0)
    reasons = []
    status = "pass"
    if use > m:
        status = "warn" if use <= min(1.0, m + 0.2) else "fail"
        reasons.append(f"peak {peak:.2f} N m is {use:.0%} of stall {perf.stall_torque_nm:g} "
                       f"N m (margin rule <= {m:.0%})")
    if rms > perf.rated_torque_nm:
        s = "warn" if rms <= 1.2 * perf.rated_torque_nm else "fail"
        status = worst([status, s])
        reasons.append(f"rms {rms:.2f} N m over the rated {perf.rated_torque_nm:g} N m "
                       "(it overheats in continuous use)")
    if ts_margin < 0:
        status = worst([status, "warn" if ts_margin > -0.1 else "fail"])
        reasons.append("the motion needs more speed at torque than the torque-speed line "
                       "allows (with 20% speed margin)")
    if row["tracking_error_deg"] > 5.0:
        status = worst([status, "warn"])
        reasons.append(f"tracking error {row['tracking_error_deg']:.1f} deg: the servo "
                       "saturates or lags")
    row.update(status=status, actuator=perf.id, actuator_name=perf.name,
               stall_nm=perf.stall_torque_nm, rated_nm=perf.rated_torque_nm,
               use_of_stall=round(use, 3), margin_rule=m,
               torque_speed_margin=round(ts_margin, 3),
               reason="; ".join(reasons) or f"peak {use:.0%} of stall, rms within rated")
    if status != "pass":
        row["candidates"] = [c.id for c in act.candidates(peak, rms, w)[:4]]
    return row


def torque_rows(result: dict, mapping: dict) -> list[dict]:
    rows = []
    for j in result["joints"]:
        a = mapping["actuators"].get(j["actuator"]) or {}
        perf = act.resolve(a.get("actuator")) if a.get("actuator") else None
        rows.append({"joint_id": a.get("joint_id") or j["joint"], **torque_row(j, perf)})
    return rows


def stability_rows(st: dict | None, traj: dict | None) -> list[dict]:
    rows = []
    if st is not None and st.get("applicable") is False:
        return [{"check": "static", "status": "not_checked", "reason": st.get("note")}]
    if st is not None:
        if not st.get("standing"):
            rows.append({"check": "static", "status": "fail", "reason": st.get("note")})
        else:
            mm = st["support_margin_m"] * 1000
            s = "pass" if mm >= 20 else "warn" if mm >= 10 else "fail"
            rows.append({"check": "static", "status": s, "margin_mm": round(mm, 1),
                         "tip_angle_deg": st["static_tip_angle_deg"],
                         "reason": f"CoM {mm:.0f} mm inside the support polygon, static tip "
                                   f"angle {st['static_tip_angle_deg']:.1f} deg"})
            tilt = st.get("tilt_test")
            if tilt:
                t = tilt.get("min_tip_deg")
                s = ("pass" if t is None or t >= 15 else "warn" if t >= 8 else "fail")
                rows.append({"check": "tilt", "status": s, "tips_at_deg": t,
                             "reason": (f"tips on a {t:g} deg slope" if t is not None else
                                        f"no tip-over up to {tilt.get('max_tested_deg'):g} deg")})
    if traj is not None and traj.get("min_cop_margin_m") is not None:
        mm = traj["min_cop_margin_m"] * 1000
        s = "pass" if mm >= 20 else "warn" if mm >= 10 else "fail"
        rows.append({"check": "motion", "status": s, "margin_mm": round(mm, 1),
                     "at_s": traj.get("at_s"),
                     "reason": f"CoP stays {mm:.0f} mm inside the support polygon during the "
                               "motion (min)"})
    return rows


def rom_rows(rom: dict, names: dict[str, str]) -> list[dict]:
    if not rom["collisions"]:
        return [{"status": "pass", "reason": f"no collisions in {rom['poses']} poses over "
                                             f"{len(rom['joints'])} joint(s)"}]
    out = []
    for c in rom["collisions"]:
        a, b = (names.get(x, x) for x in c["bodies"])
        out.append({"status": "fail", "pair": [a, b], "depth_mm": c["depth_mm"],
                    "pose": c["pose"], "count": c.get("count"),
                    "reason": f"{a} hits {b} ({c['depth_mm']:.1f} mm deep) in "
                              f"{c.get('count', 1)} of {rom['poses']} poses"})
    return out


def overall(rows: list[dict]) -> str:
    return _worst(rows)
