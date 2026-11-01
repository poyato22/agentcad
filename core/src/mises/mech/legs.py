"""Legs (research 2026-11-01 §2.2 rows 37-38): a quadruped leg with the knee motor at the hip
and a belt to the knee (Mini-Cheetah style), and one with a pushrod four-bar knee (Go1 style).

Legs swing in the XZ plane (X forward, Z up) about joint axes on +Y. A link part is built along
its local +X; through a joint whose parent-side axis is +Y and whose link-side axis is the
link's +Z, the link's +X points along (sin v, 0, cos v) for an absolute joint angle v (so v = 180
points straight down)."""
from __future__ import annotations

import math

from .card import Z, fr
from .kit import Kit, pin_design
from .library import P, mechanism

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)


def _dir(v_deg: float) -> tuple[float, float]:
    """(x, z) unit direction of a link at absolute angle v about +Y."""
    v = math.radians(v_deg)
    return math.sin(v), math.cos(v)


def _local(origin: tuple[float, float], v_deg: float, pt: tuple[float, float]) -> tuple[float, float]:
    """A world (x, z) point in a link's local (x, y) at angle v (local y = (cos v, -sin v))."""
    dx, dz = pt[0] - origin[0], pt[1] - origin[1]
    v = math.radians(v_deg)
    return (dx * math.sin(v) + dz * math.cos(v), dx * math.cos(v) - dz * math.sin(v))


def _angle(p, q) -> float:
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1]))


_LEG = {"thigh": P(200.0, 60.0, 600.0, "mm", "hip-to-knee"), "shank": P(200.0, 60.0, 600.0, "mm", "knee-to-foot"),
        "hip_deg": P(150.0, 100.0, 200.0, "deg", "initial thigh angle (180 = straight down)"),
        "knee_deg": P(60.0, 20.0, 150.0, "deg", "initial knee bend"),
        "actuator": P("actuator_mini_cheetah_class", choices=("actuator_mini_cheetah_class",
                                                            "cubemars_ak80_9_actuator", "unitree_go_m8010_actuator"),
                      unit="", doc="catalog actuator (hip and knee)"),
        "body_mass_kg": P(9.0, 0.5, 60.0, "kg", "robot mass (for the static knee torque)")}


def _actuator(item: str) -> dict:
    from ..catalog.builtin import builtin_by_id
    it = builtin_by_id().get(item)
    if it is None:
        raise ValueError(f"no catalog actuator {item}")
    return it


def _hip(k: Kit, p: dict) -> tuple[str, str, dict, float, float, float]:
    """Body plate, the hip block on a hip-abduction joint (axis X) and the hip-flex actuator on
    the block's +Y face. Returns (block, hip-ab joint, actuator item, actuator radius, length,
    block height)."""
    it = _actuator(p["actuator"])
    sh = it["shape"]
    rad, hgt = float(sh["d"]) / 2, float(sh["h"])
    hh = 2 * rad + 10
    k.part("body", "frame_plate", "body mount", "Al 6061-T6", length=2 * rad + 60,
           width=2 * hgt + 60, thickness=6.0, bores=[], origin_x=-rad - 20)
    k.inst("body", "body", "body (grounded)")
    k.part("hip_block", "slider_block", "hip block", "Al 6061-T6", length=hh, width=20.0,
           height=hh, pin_d=0.0)
    hb = k.inst("hip_block")
    jab = k.joint("hip_ab", "revolute", "body", fr((0, 0, -6.0), X), hb, fr((0, 0, hh), X),
                  limits=[-30.0, 30.0],
                  design={"bearing": "actuator output bearing", "retention": "bolted flange",
                          "actuator": {"item_id": p["actuator"], "ratio": 1}})
    k.part("actuator", catalog=p["actuator"], label=it["name"], material=None)
    a1 = k.inst("hip_actuator", "actuator", "hip-flex actuator")
    k.fixed("hip_actuator_mount", hb, fr((0, 10.0, hh / 2), Y), a1, fr())
    k.plan.contacts += [("body", hb), (hb, a1)]
    return hb, jab, it, rad, hgt, hh


def _knee_actuator(k: Kit, hb: str, hgt: float, hh: float, y_out: float) -> str:
    """The knee actuator, outboard on the hip axis with its output face at y = y_out (facing
    -Y), held to the hip block (by a bracket in the real build)."""
    a2 = k.inst("knee_actuator", "actuator", "knee actuator (at the hip)")
    k.fixed("knee_actuator_mount", hb, fr((0, y_out + hgt, hh / 2), (0, -1, 0)), a2, fr())
    k.plan.contacts.append((hb, a2))
    k.plan.warnings.append("the knee actuator is outboard on the hip axis; add the bracket "
                           "that ties it to the hip block")
    return a2


@mechanism("quadruped_leg_belt", "Quadruped leg, knee belt drive (Mini-Cheetah style)", "leg",
           "Hip abduction, hip flexion and a knee driven by a motor at the hip through a belt "
           "inside the thigh: low leg inertia.",
           ["walk", "quadruped", "leg", "legged robot", "jump", "knee belt"],
           {**_LEG, "belt_ratio": P(1.5, 1.0, 3.0, "", "knee pulley / drive pulley teeth"),
            "pulley_teeth": P(20, 16, 40, "teeth", "drive pulley")},
           ["actuator_torque", "belt_tension", "knee_rom", "rom_sweep"], "rot->path")
def quadruped_leg_belt(p: dict):
    Lt, Ls, h0, kb = p["thigh"], p["shank"], p["hip_deg"], p["knee_deg"]
    k = Kit("body")
    hb, jab, it, rad, hgt, hh = _hip(k, p)
    a1 = "hip_actuator"
    z1 = p["pulley_teeth"]
    z2 = round(z1 * p["belt_ratio"])
    tw = 24.0
    k.part("thigh", "link_bar", "thigh", "Al 7075-T6", length=Lt, width=tw, thickness=8.0,
           bore=10.0)
    ti = k.inst("thigh")
    jh = k.joint("hip_flex", "revolute", a1, fr((0, 0, hgt + 1.0)), ti, fr(), value=h0,
                 limits=[90.0, 240.0],
                 design={"bearing": "actuator output bearing", "retention": "bolted flange",
                         "actuator": {"item_id": p["actuator"], "ratio": 1}})
    # thigh y = 11+h .. 19+h; pulleys and belt 21+h .. 30+h; the knee actuator beyond them
    a2 = _knee_actuator(k, hb, hgt, hh, 10.0 + hgt + 1.0 + 8.0 + 2.0 + 9.0 + 3.0)
    k.part("drive_pulley", "gt2_pulley", f"knee drive pulley {z1}T", "PA-CF", teeth=z1,
           width=9.0, bore=10.0)
    k.part("knee_pulley", "gt2_pulley", f"knee pulley {z2}T", "PA-CF", teeth=z2, width=9.0,
           bore=10.0)
    dp = k.inst("drive_pulley")
    jkm = k.joint("knee_motor", "revolute", ti, fr((0, 0, 8.0 + 2.0)), dp, fr(), value=0.0,
                  design={"bearing": "bearing_6000_deep_groove", "retention": "snap ring",
                          "actuator": {"item_id": p["actuator"], "ratio": z2 / z1}})
    pd1, pd2 = z1 * 2 / math.pi, z2 * 2 / math.pi
    k.part("belt", "timing_belt", "knee belt (GT2 9 mm)", "TPU 95A", pitch_d1=round(pd1, 5),
           pitch_d2=round(pd2, 5), center=Lt, width=9.0)
    bi = k.inst("belt")
    k.fixed("belt_in_thigh", ti, fr((0, 0, 8.0 + 2.0)), bi, fr())
    k.part("shank", "link_bar", "shank", "PA-CF", length=Ls, width=18.0, thickness=8.0,
           bore=10.0, bore2=4.0)
    si = k.inst("shank")
    jk = k.joint("knee", "revolute", ti, fr((Lt, 0, -9.0)), si, fr(), value=kb,
                 limits=[10.0, 160.0],
                 design={"bearing": "bearing_6000_deep_groove", "retention": "shoulder bolt",
                         "rom_deg": [10.0, 160.0]})
    kp = k.inst("knee_pulley")
    k.fixed("knee_pulley_on_shank", si, fr((0, 0, 8.0 + 9.0 + 2.0)), kp, fr())
    k.couple(jk, "belt", z1 / z2, jkm, offset=kb)
    k.part("foot", "ball_stud", "foot", "TPU 95A", ball_d=24.0, neck_d=8.0, neck_l=4.0,
           flange_d=12.0, flange_t=2.0)
    fi = k.inst("foot")
    k.fixed("foot_on_shank", si, fr((Ls, 0, 4.0), X), fi, fr((0, 0, 0), X))
    k.plan.contacts += [(a1, ti), (ti, dp), (ti, si), ("belt", dp), ("belt", kp), (si, kp),
                        (si, fi), ("belt", ti), (a2, dp)]
    k.plan.inputs += [jab, jh, jkm]
    k.plan.dof_intended = 3
    hx, hz = 0.0, 0.0
    tx, tz = _dir(h0)
    sx, sz = _dir(h0 + kb)
    foot = (hx + Lt * tx + Ls * sx, hz + Lt * tz + Ls * sz)
    k.port("foot", "end_effector", fi, (foot[0], 0.0, foot[1]), (0, 0, -1), "foot (approx.)")
    k.port("mount", "mount", "body", (0, 0, -6.0), (0, 0, 1), "body side")
    stall = (it.get("perf") or {}).get("stall_torque_nm")
    g = p["body_mass_kg"] * 9.81 / 2        # two legs on the ground in a trot
    lever = abs(Ls * sx) / 1000
    need = g * lever
    k.plan.sizing = {"knee_static_torque_nm": round(need, 2),
                     "knee_actuator_peak_nm": stall,
                     "knee_peak_at_joint_nm": round(stall * z2 / z1, 2) if stall else None,
                     "belt_ratio": round(z2 / z1, 3)}
    k.plan.rom = {"knee": [10.0, 160.0], "hip_flex": [90.0, 240.0], "hip_ab": [150.0, 210.0]}
    if stall and need and stall * z2 / z1 / need < 2:
        k.plan.warnings.append("knee torque margin < 2 for a static two-leg stance: raise the "
                               "belt ratio or pick a stronger actuator")
    k.plan.notes = ("the knee is coupled to the knee pulley's angle relative to the thigh "
                    "(knee = bend0 + z1/z2 x pulley); the knee motor's own angle is hip + that")
    return k.plan


@mechanism("quadruped_leg_linkage", "Quadruped leg, pushrod knee (Go1 style)", "leg",
           "Both motors at the hip; the knee motor's crank pushes a rod to a lever behind the "
           "knee (a four-bar inside the thigh).",
           ["walk", "quadruped", "leg", "legged robot", "pushrod knee"],
           {**_LEG, "crank": P(30.0, 10.0, 120.0, "mm", "knee crank radius"),
            "lever": P(30.0, 10.0, 120.0, "mm", "shank lever behind the knee")},
           ["actuator_torque", "transmission_angle", "knee_rom", "rom_sweep"], "rot->path")
def quadruped_leg_linkage(p: dict):
    Lt, Ls, h0, kb, rc, rl = (p["thigh"], p["shank"], p["hip_deg"], p["knee_deg"], p["crank"],
                              p["lever"])
    k = Kit("body")
    hb, jab, it, rad, hgt, hh = _hip(k, p)
    a1 = "hip_actuator"
    k.part("thigh", "link_bar", "thigh", "Al 7075-T6", length=Lt, width=22.0, thickness=8.0,
           bore=10.0)
    ti = k.inst("thigh")
    jh = k.joint("hip_flex", "revolute", a1, fr((0, 0, hgt + 1.0)), ti, fr(), value=h0,
                 limits=[90.0, 240.0],
                 design={"bearing": "actuator output bearing", "retention": "bolted flange",
                         "actuator": {"item_id": p["actuator"], "ratio": 1}})
    # positions in the thigh's own frame (hip at the origin, knee at (Lt, 0))
    shank_rel = kb                                    # shank angle relative to the thigh
    lever_dir = math.radians(shank_rel + 180.0 - 25.0)  # the lever points back from the knee
    S = (Lt + rl * math.cos(lever_dir), rl * math.sin(lever_dir))
    # crank angle (relative to the thigh) that closes the loop with a rod of length ~ thigh
    rod = math.dist((0, 0), S) * 0.98
    from .kit import circle_intersect
    C = circle_intersect((0.0, 0.0), rc, S, rod, side=1)
    crank_ang = math.degrees(math.atan2(C[1], C[0]))
    k.part("crank", "link_bar", "knee crank", "Al 7075-T6", length=rc, width=14.0, thickness=6.0,
           bore=10.0, bore2=4.3)
    ci = k.inst("crank")
    jc = k.joint("knee_motor", "revolute", ti, fr((0, 0, 8.0 + 1.0)), ci, fr(), value=crank_ang,
                 design={"bearing": "actuator output bearing", "retention": "bolted",
                         "actuator": {"item_id": p["actuator"], "ratio": 1}})
    k.part("rod", "link_bar", "pushrod", "Al 7075-T6", length=round(rod, 4), width=8.0,
           thickness=5.0, bore=4.3)
    ri = k.inst("rod")
    rod_ang = math.degrees(math.atan2(S[1] - C[1], S[0] - C[0]))
    jr = k.joint("rod_pin", "revolute", ci, fr((rc, 0, 6.0 + 1.0)), ri, fr(),
                 value=rod_ang - crank_ang, design=pin_design(4.0, bearing="bushing"))
    lever_local = (rl * math.cos(math.radians(180 - 25)), rl * math.sin(math.radians(180 - 25)))
    k.part("shank", "link_plate", "shank with knee lever", "PA-CF",
           points=[[0.0, 0.0], [Ls, 0.0], [round(lever_local[0], 4), round(lever_local[1], 4)]],
           width=16.0, thickness=8.0, bore=4.3)
    si = k.inst("shank")
    jk = k.joint("knee", "revolute", ti, fr((Lt, 0, -9.0)), si, fr(), value=shank_rel,
                 limits=[10.0, 160.0],
                 design={"bearing": "bearing_6000_deep_groove", "retention": "shoulder bolt",
                         "rom_deg": [10.0, 160.0]})
    # the rod end meets the lever: rod (z = 9 + 7) above the shank (z = -9)
    k.loop("knee_loop", ri, fr((round(rod, 4), 0, 0)), si,
           fr((round(lever_local[0], 4), round(lever_local[1], 4), 9.0 + 8.0 + 9.0 + 7.0 - 8.0)),
           drivers=[jc])
    k.part("foot", "ball_stud", "foot", "TPU 95A", ball_d=24.0, neck_d=8.0, neck_l=4.0,
           flange_d=12.0, flange_t=2.0)
    fi = k.inst("foot")
    k.fixed("foot_on_shank", si, fr((Ls, 0, 4.0), X), fi, fr((0, 0, 0), X))
    a2 = _knee_actuator(k, hb, hgt, hh, 10.0 + hgt + 1.0 + 8.0 + 1.0 + 6.0 + 1.0 + 5.0 + 3.0)
    k.plan.contacts += [(a1, ti), (ti, ci), (ci, ri), (ri, si), (ti, si), (si, fi), (a2, ci)]
    k.plan.inputs += [jab, jh, jc]
    k.plan.dof_intended = 3
    k.plan.rom = {"knee": [10.0, 160.0]}
    stall = (it.get("perf") or {}).get("stall_torque_nm")
    k.plan.sizing = {"crank_mm": rc, "lever_mm": rl, "rod_mm": round(rod, 2),
                     "knee_actuator_peak_nm": stall,
                     "knee_ratio_at_start": round(rl / rc, 3)}
    k.plan.warnings.append("near full extension the pushrod and crank line up (singular): keep "
                           "the knee ROM away from it")
    return k.plan
