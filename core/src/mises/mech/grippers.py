"""Grippers (research 2026-11-01 §2.2 rows 35-36): a rack-and-pinion parallel gripper and a
gear-synchronised parallelogram-finger gripper."""
from __future__ import annotations

import math

from . import sizing
from .card import Z, fr
from .kit import Kit, bearing_for, pin_design, planar_linkage
from .library import P, mechanism
from .shapes import mesh_phase

X = (1.0, 0.0, 0.0)


@mechanism("parallel_gripper", "Parallel-jaw gripper (rack and pinion)", "gripper",
           "One pinion drives two racked jaws in opposite directions: parallel jaws, equal "
           "speed, grip force tau / r per jaw.",
           ["grip", "gripper", "pick and place", "parallel jaws", "end effector"],
           {"module": P(1.0, 0.5, 2.0, "mm", "module"), "z": P(16, 12, 40, "teeth", "pinion"),
            "rack_teeth": P(16, 10, 60, "teeth", "rack teeth per jaw"),
            "width": P(6.0, 3.0, 15.0, "mm", "face"),
            "finger": P(25.0, 10.0, 120.0, "mm", "finger height"),
            "input_torque_nm": P(0.4, 0.0, 10.0, "N*m", "pinion (servo) torque")},
           ["grip_force", "jaw_deflection", "lewis_bending", "rom_sweep"], "rot->lin")
def parallel_gripper(p: dict):
    m, z, n, w = p["module"], p["z"], p["rack_teeth"], p["width"]
    r = m * z / 2
    rl = math.pi * m * n
    k = Kit("base")
    _cid, D, B, bore = bearing_for(5)
    k.plate("base", rl * 2 + 30, 2 * r + 50, B, [(0, 0, D + 0.1)], origin_x=-rl - 5)
    s, j, z0 = k.shaft_on_bearing("pinion", "base", 0, 0, B, bore, B + 2 + 2 + w + 3)
    k.part("pinion", "gear_spur", f"pinion {z}T", "POM", module=m, teeth=z, width=w,
           bore=float(bore), phase_deg=-90.0)
    k.on_shaft("pinion_gear", s, "pinion", 2.0, z0, "pinion")
    k.part("jaw", "gripper_jaw", "jaw", "PA-CF", length=rl, height=p["finger"], thickness=w,
           module=m, rack_teeth=n, finger=p["finger"])
    stroke = rl / 2 - r - 6
    ja_i = k.inst("jaw_a", "jaw", "jaw A")
    ja = k.joint("jaw_a_slide", "slider", "base", fr((-rl / 2, -r, 2.0), X), ja_i,
                 fr((0, 0, 0), X), limits=[-stroke, 4.0],
                 design={"bearing": "printed dovetail slide", "clearance_mm": 0.3,
                         "retention": "cover plate"})
    xb = rl / 2 + (0.0 if z % 2 == 0 else math.pi * m / 2)
    jb_i = k.inst("jaw_b", "jaw", "jaw B")
    jb = k.joint("jaw_b_slide", "slider", "base", fr((xb, r, 2.0), (-1, 0, 0)), jb_i,
                 fr((0, 0, 0), X), limits=[-stroke, 4.0],
                 design={"bearing": "printed dovetail slide", "clearance_mm": 0.3,
                         "retention": "cover plate"})
    mm_deg = r * math.pi / 180
    k.couple(ja, "rack", mm_deg, j)
    k.couple(jb, "rack", mm_deg, j)
    k.plan.contacts += [("pinion_gear", "jaw_a"), ("pinion_gear", "jaw_b"), ("base", "jaw_a"),
                        ("base", "jaw_b")]
    k.plan.inputs.append(j)
    k.port("input", "rot", s, (0, 0, z0), Z, "pinion shaft (servo horn)")
    k.port("tcp", "end_effector", "base", (0, 0, 2.0 + w + p["finger"]), Z, "between the fingers")
    k.plan.sizing = {"grip_force_n_per_jaw": round(p["input_torque_nm"] * 1000 / r / 2, 2),
                     "stroke_mm_per_jaw": round(stroke + 4.0, 2), "mm_per_deg": round(mm_deg, 5)}
    k.plan.rom = {"pinion_rev": [round(-stroke / mm_deg, 1), round(4.0 / mm_deg, 1)]}
    k.plan.notes = "positive pinion angle opens the jaws; negative closes them"
    return k.plan


@mechanism("linkage_gripper", "Parallelogram-finger gripper", "gripper",
           "Two meshing gears (one driven by the servo) swing two parallelogram fingers that stay "
           "parallel while they close.",
           ["grip", "gripper", "fingers", "claw", "end effector", "parallel fingers"],
           {"module": P(1.0, 0.6, 2.0, "mm", "gear module"), "z": P(20, 12, 40, "teeth", "gear teeth"),
            "crank": P(40.0, 15.0, 150.0, "mm", "finger crank length"),
            "idler_offset": P(15.0, 6.0, 60.0, "mm", "idler pivot below the gear"),
            "finger": P(30.0, 10.0, 120.0, "mm", "finger tip above the coupler"),
            "open_deg": P(70.0, 40.0, 110.0, "deg", "initial crank angle from horizontal"),
            "input_torque_nm": P(0.4, 0.0, 10.0, "N*m", "servo torque")},
           ["grip_force", "rom_sweep"], "rot->rot")
def linkage_gripper(p: dict):
    m, z, L, h, f, a0 = (p["module"], p["z"], p["crank"], p["idler_offset"], p["finger"],
                         p["open_deg"])
    g = m * z / 2
    aL, aR = 180.0 - a0, a0
    def pt(c, ang):
        return (c[0] + L * math.cos(math.radians(ang)), c[1] + L * math.sin(math.radians(ang)))
    PL, PR, QL, QR = (-g, 0.0), (g, 0.0), (-g, -h), (g, -h)
    AL, AR, BL, BR = pt(PL, aL), pt(PR, aR), pt(QL, aL), pt(QR, aR)
    nodes = {"PL": PL, "PR": PR, "QL": QL, "QR": QR, "AL": AL, "AR": AR, "BL": BL, "BR": BR,
             "TL": (AL[0], AL[1] + f), "TR": (AR[0], AR[1] + f)}
    k = Kit("palm")
    links = [("crank_l", ["PL", "AL"]), ("idler_l", ["QL", "BL"]),
             ("finger_l", ["AL", "BL", "TL"]), ("crank_r", ["PR", "AR"]),
             ("idler_r", ["QR", "BR"]), ("finger_r", ["AR", "BR", "TR"])]
    info = planar_linkage(k, nodes, links, ["PL", "PR", "QL", "QR"], ("crank_l", "PL"),
                          ground_key="palm", width=8.0, thickness=4.0, pin_d=3.0)
    jl, jr = info["joints"][("crank_l", "PL")], info["joints"][("crank_r", "PR")]
    # right fingers' loop is driven by its own crank, which the gears couple to the left one
    for lp in k.plan.loops:
        if lp.b in ("idler_r", "finger_r") or lp.a in ("idler_r", "finger_r"):
            lp.drivers = [jr]
    k.couple(jr, "gear", -1.0, jl, offset=aR + aL)
    # the two gears (on the cranks' pivots, one layer below the links)
    k.part("gear_l", "gear_spur", f"sync gear {z}T", "POM", module=m, teeth=z, width=4.0,
           bore=3.3, phase_deg=round(-aL, 6))
    k.part("gear_r", "gear_spur", f"sync gear {z}T (phased)", "POM", module=m, teeth=z,
           width=4.0, bore=3.3, phase_deg=round(mesh_phase(z, 0.0, z, 0.0) - aR, 6))
    gl, gr_ = k.inst("gear_l"), k.inst("gear_r")
    top = (len(links) + 1) * info["pitch"]          # one layer above every link
    for gi, crank in ((gl, "crank_l"), (gr_, "crank_r")):
        k.fixed(f"{gi}_on_crank", crank, fr((0, 0, top - info["layer"][crank] * info["pitch"])),
                gi, fr())
    k.plan.contacts += [("gear_l", "gear_r"), ("gear_l", "crank_l"), ("gear_r", "crank_r"),
                        ("palm", "gear_l"), ("palm", "gear_r")]
    k.port("input", "rot", "crank_l", (*PL, 0.0), Z, "servo on the left gear")
    k.port("tcp", "end_effector", "palm", (0.0, AL[1] + f, 0.0), Z, "between the finger tips")
    lo = max(20.0, a0 - 60)
    k.plan.rom = {jl: [180 - (a0 + 20), 180 - lo]}
    force = p["input_torque_nm"] * 1000 / L / 2
    k.plan.sizing = {"tip_force_n_per_finger_approx": round(force, 2),
                     "opening_mm": round(AR[0] - AL[0], 1)}
    k.plan.notes = "each finger is a parallelogram (stays parallel); the gears sync the sides"
    return k.plan
