"""Joints as mechanisms (research 2026-11-01 §2.2 rows 30-34, 42): hinges, a living hinge,
ball and universal joints, a servo pan-tilt and servo arm joint, a snap fit. Each carries the
E27 design data: bearing or bushing, pin and retention, clearance and range of motion."""
from __future__ import annotations

import math

from . import sizing
from .card import Z, fr
from .kit import Kit, PIN_CLEAR, pin_design
from .library import P, mechanism

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)


def _servo(item: str) -> dict:
    from ..catalog.builtin import builtin_by_id
    it = builtin_by_id().get(item)
    if it is None:
        raise ValueError(f"unknown servo {item}")
    return it


def _servo_out(it: dict) -> tuple[float, float, float]:
    perf = it.get("perf") or {}
    o = perf.get("output_origin_mm")
    if o:
        return tuple(float(c) for c in o)
    sh = it["shape"]
    return (sh["l"] / 2 - sh["w"] / 2, 0.0, sh["h"])


@mechanism("hinge", "Pin hinge with knuckles", "joint",
           "Two leaves with alternating knuckles on a steel pin: a door, lid or flap pivot.",
           ["hinge", "lid", "door", "fold", "flap", "pivot"],
           {"length": P(40.0, 10.0, 400.0, "mm", "hinge length"), "width": P(20.0, 5.0, 200.0, "mm", "leaf width"),
            "thickness": P(3.0, 1.5, 10.0, "mm", "leaf thickness"), "pin_d": P(3.0, 1.5, 10.0, "mm", "pin"),
            "knuckles": P(5, 3, 15, "", "knuckles (odd)"), "load_n": P(20.0, 0.0, 5000.0, "N", "load on the hinge")},
           ["pin_shear", "lug_bearing", "rom_sweep"], "rot->rot")
def hinge(p: dict):
    L, w, t, d, n = p["length"], p["width"], p["thickness"], p["pin_d"], p["knuckles"]
    k = Kit("leaf_a")
    k.part("leaf_a", "hinge_leaf", "hinge leaf A", "PETG", length=L, width=w, thickness=t,
           pin_d=d, knuckles=n, side=0, gap=PIN_CLEAR)
    k.inst("leaf_a", "leaf_a", "leaf A (grounded)")
    k.part("leaf_b", "hinge_leaf", "hinge leaf B", "PETG", length=L, width=w, thickness=t,
           pin_d=d, knuckles=n, side=1, gap=PIN_CLEAR)
    bi = k.inst("leaf_b")
    j = k.joint("hinge_rev", "revolute", "leaf_a", fr((0, 0, 0), X), bi, fr((0, 0, 0), X),
                limits=[0.0, 175.0], design=pin_design(d, bearing="bushing",
                                                       retention="peened pin / e-clips"))
    k.part("pin", "pin", "hinge pin", "Steel 1018", d=d, length=L + 2)
    pi_ = k.inst("pin")
    k.fixed("pin_in_leaf", "leaf_a", fr((-1.0, 0, 0), X), pi_, fr())
    k.plan.contacts += [("leaf_a", "leaf_b"), ("leaf_a", "pin"), ("leaf_b", "pin")]
    k.plan.inputs.append(j)
    k.port("mount", "mount", "leaf_a", (L / 2, w / 2 + d, 0), Z, "leaf A face")
    k.port("output", "rot", bi, (0, 0, 0), X, "leaf B")
    shear = sizing.pin_shear_mpa(p["load_n"], d, 2)
    seg = L / n
    bearing = sizing.lug_bearing_mpa(p["load_n"] / max(1, n // 2), d, seg)
    k.plan.sizing = {"pin_shear_mpa": round(shear, 2), "knuckle_bearing_mpa": round(bearing, 2)}
    k.plan.rom = {"hinge_rev": [0.0, 175.0]}
    return k.plan


@mechanism("living_hinge", "Living hinge (flexure)", "joint",
           "A thin printed web between two pads: a hinge with no parts (PP, PETG, TPU).",
           ["flexure", "living hinge", "compliant joint", "fold", "lid", "no parts hinge"],
           {"length": P(40.0, 10.0, 200.0, "mm", "overall"), "width": P(10.0, 3.0, 100.0, "mm", "width"),
            "thickness": P(4.0, 1.0, 10.0, "mm", "pad thickness"), "flex_t": P(0.6, 0.3, 2.0, "mm", "web"),
            "flex_l": P(4.0, 1.0, 20.0, "mm", "web length"),
            "material": P("PETG", choices=("PETG", "TPU 95A", "PA12", "PLA"), unit="", doc="material")},
           ["flexure_strain", "stiffness"], "rot->rot")
def living_hinge(p: dict):
    L, w, t, ft, fl = p["length"], p["width"], p["thickness"], p["flex_t"], p["flex_l"]
    half = (L - fl) / 2
    k = Kit("pad_a")
    k.part("pad_a", "slider_block", "pad A + web", p["material"], length=half, width=w, height=t,
           pin_d=0.0)
    k.inst("pad_a", "pad_a", "pad A (grounded)")
    k.part("pad_b", "slider_block", "pad B", p["material"], length=half, width=w, height=t,
           pin_d=0.0)
    bi = k.inst("pad_b")
    k.part("web", "slider_block", "flex web", p["material"], length=fl, width=w, height=ft,
           pin_d=0.0)
    wi = k.inst("web")
    k.fixed("web_on_a", "pad_a", fr((half / 2 + fl / 2, 0, 0)), wi, fr())
    j = k.joint("flex_rev", "revolute", "pad_a", fr((half / 2 + fl / 2, 0, 0), Y), bi,
                fr((-half / 2 - fl / 2, 0, 0), Y), limits=[0.0, 90.0],
                design={"bearing": "none", "retention": "one piece", "rom_deg": [0.0, 90.0]})
    k.plan.contacts += [("pad_a", "web"), ("web", "pad_b")]
    k.plan.inputs.append(j)
    k.port("mount", "mount", "pad_a", (0, 0, 0), (0, 0, -1), "pad A underside")
    k.port("output", "rot", bi, (half / 2 + fl / 2, 0, 0), Y, "pad B (bends about the web)")
    from ..sim.materials import get_material
    mat = get_material(p["material"])
    i = w * ft ** 3 / 12
    eps90 = sizing.flexure_strain(ft, fl / (math.pi / 2))
    allow = {"PETG": 0.04, "TPU 95A": 0.5, "PA12": 0.08, "PLA": 0.02}[p["material"]]
    k.plan.sizing = {"stiffness_nmm_per_rad": round(mat.e_mpa * i / fl, 3),
                     "strain_at_90deg": round(eps90, 4), "allowable_strain": allow}
    if eps90 > allow:
        k.plan.warnings.append(f"strain {eps90:.1%} at 90 deg exceeds ~{allow:.0%} for "
                               f"{p['material']}: thin or lengthen the web")
    k.plan.notes = "print as ONE part (the web flat on the bed); modelled as pads + web to move"
    return k.plan


@mechanism("ball_joint", "Ball and socket joint", "joint",
           "A ball stud in a snap-in socket with a cone opening: 3 rotations within a cone.",
           ["ball joint", "spherical joint", "3 dof", "rod end", "linkage end", "neck"],
           {"ball_d": P(12.0, 4.0, 60.0, "mm", "ball"), "cone_deg": P(30.0, 10.0, 60.0, "deg", "tilt each way"),
            "clearance": P(0.25, 0.1, 0.6, "mm", "radial clearance")}, ["snap_retention", "rom_sweep"],
           "rot->rot")
def ball_joint(p: dict):
    bd_, cone = p["ball_d"], p["cone_deg"]
    k = Kit("socket")
    k.part("socket", "ball_socket", "socket", "PETG", ball_d=bd_, wall=max(2.5, bd_ * 0.25),
           clearance=p["clearance"], cone_deg=cone, length=bd_ * 1.6)
    k.inst("socket", "socket", "socket (grounded)")
    k.part("stud", "ball_stud", "ball stud", "Steel 1018", ball_d=bd_, neck_d=bd_ * 0.5,
           neck_l=bd_, flange_d=bd_ * 1.3)
    si = k.inst("stud")
    j = k.joint("ball", "ball", "socket", fr(), si, fr(), value=[0.0, 0.0, 0.0],
                limits=[-cone, cone], design={"bearing": "printed socket",
                                              "retention": "snap-in lip",
                                              "clearance_mm": p["clearance"],
                                              "rom_deg": [-cone, cone]})
    k.plan.contacts.append(("socket", "stud"))
    k.plan.inputs.append(j)
    k.plan.dof_intended = 3
    k.port("output", "rot", si, (0, 0, -bd_), (0, 0, -1), "stud flange")
    k.plan.rom = {"ball": [-cone, cone]}
    k.plan.warnings.append("the ball must snap past the socket lip: check the lip strain "
                           "(snap fit) for the material")
    return k.plan


@mechanism("universal_joint", "Universal (Cardan) joint", "joint",
           "Two yokes and a cross transmit rotation between shafts at an angle; the output speed "
           "fluctuates with the angle (use two for constant velocity).",
           ["universal joint", "cardan", "drive shaft at an angle", "transmit rotation at an angle"],
           {"shaft_d": P(8.0, 4.0, 20.0, "mm", "shafts"), "span": P(20.0, 10.0, 60.0, "mm", "cross span"),
            "angle_deg": P(20.0, 0.0, 30.0, "deg", "shaft angle beta")}, ["angle_fluctuation", "rom_sweep"],
           "rot->rot")
def universal_joint(p: dict):
    d, s, beta = p["shaft_d"], p["span"], p["angle_deg"]
    hub_l = 15.0
    pin_d = max(3.0, s * 0.2)
    zl = s / 2 + 2                         # yoke-local height of the pin axis
    k = Kit("base")
    k.part("base", "frame_plate", "base", "PETG", length=80.0, width=60.0, thickness=5.0,
           bores=[[0.0, 0.0, d + 0.4]], origin_x=-30.0)
    k.inst("base", "base", "base")
    k.part("yoke", "u_yoke", "yoke", "Steel 1018", shaft_bore=d, pin_d=pin_d, span=s,
           hub_d=max(18.0, d + 10), hub_l=hub_l)
    ya = k.inst("yoke_in", "yoke", "input yoke")
    zc = hub_l + 2 + zl
    jin = k.joint("input_rev", "revolute", "base", fr((0, 0, hub_l + 2)), ya, fr(),
                  design={"bearing": "bearing_608_deep_groove", "retention": "snap ring"})
    k.part("cross", "cross_spider", "cross", "Steel 4140 QT", pin_d=pin_d, span=s + 6,
           core=pin_d * 2)
    ci = k.inst("cross")
    jx = k.joint("cross_rev", "revolute", ya, fr((0, 0, zl), X), ci, fr((0, 0, 0), X),
                 limits=[-45.0, 45.0], design=pin_design(pin_d, bearing="needle / bushing",
                                                         retention="snap ring"))
    yb = k.inst("yoke_out", "yoke", "output yoke")
    jy = k.joint("yoke_rev", "revolute", ci, fr((0, 0, 0), Y), yb, fr((0, 0, zl), X),
                 value=180.0, limits=[135.0, 225.0],
                 design=pin_design(pin_d, bearing="needle / bushing", retention="snap ring"))
    # the output shaft runs in a bearing at angle beta (about Y): close that loop
    reach = zl + hub_l
    b = math.radians(beta)
    tip = (reach * math.sin(b), 0.0, zc + reach * math.cos(b))
    k.loop("output_bearing", "base", fr(tip, (math.sin(b), 0.0, math.cos(b))), yb,
           fr((0, 0, -hub_l), (0, 0, -1)), drivers=[jin])
    k.plan.contacts += [(ya, ci), (ci, yb), ("base", ya)]
    k.plan.inputs.append(jin)
    k.plan.planar = True        # a spherical chain counts like a planar one: 3(n-1) - 2j
    k.port("input", "rot", ya, (0, 0, 0), Z, "input shaft")
    k.port("output", "rot", yb, tip, (math.sin(b), 0, math.cos(b)), "output shaft")
    k.plan.sizing = {"speed_ratio_range": [round(math.cos(b), 4), round(1 / math.cos(b), 4)],
                     "fluctuation_pct": round((1 / math.cos(b) - math.cos(b)) * 100, 2)}
    k.plan.transmission = {"w2/w1": "cos(b) / (1 - sin^2(b) cos^2(theta1))"}
    if beta > 25:
        k.plan.warnings.append(f"{beta:g} deg: large speed fluctuation; use a double U-joint")
    return k.plan


@mechanism("servo_pan_tilt", "Servo pan-tilt", "joint",
           "A pan servo turns a U bracket that carries a tilting platform on a second servo: "
           "camera or sensor heads.", ["pan tilt", "camera mount", "gimbal", "turret", "aim"],
           {"servo": P("servo_mg996r_standard", choices=("servo_sg90_micro", "servo_mg996r_standard"),
                       unit="", doc="catalog servo"),
            "payload_g": P(100.0, 0.0, 2000.0, "g", "payload on the tilt platform"),
            "payload_offset": P(20.0, 0.0, 200.0, "mm", "payload CoM off the tilt axis")},
           ["servo_torque", "com_over_pan_axis", "rom_sweep"], "rot->rot")
def servo_pan_tilt(p: dict):
    it = _servo(p["servo"])
    sh = it["shape"]
    ox, oy, oz = _servo_out(it)
    k = Kit("base")
    k.part("base", "frame_plate", "base", "PETG", length=sh["tab_l"] + 30, width=sh["w"] + 40,
           thickness=4.0, bores=[], origin_x=-(sh["tab_l"] + 30) / 2 + 10)
    k.inst("base", "base", "base")
    k.part("servo", catalog=p["servo"], label=it["name"], material=None)
    s1 = k.inst("pan_servo", "servo", "pan servo")
    k.fixed("pan_servo_on_base", "base", fr((-ox, 0, 0)), s1, fr())
    sw, shh = sh["w"], sh["h"]
    k.part("bracket", "servo_bracket", "pan bracket", "PETG", servo_w=sw, servo_h=shh, wall=3.0,
           length=sh["l"], horn_d=20.0)
    bi = k.inst("bracket")
    jp = k.joint("pan", "revolute", s1, fr((ox, oy, oz + 2.0)), bi, fr(), limits=[-90.0, 90.0],
                 design={"bearing": "servo output bearing", "retention": "horn screw",
                         "actuator": {"item_id": p["servo"], "ratio": 1},
                         "rom_deg": [-90.0, 90.0]})
    k.part("platform", "link_plate", "tilt platform", "PETG",
           points=[[0.0, 0.0], [40.0, 0.0], [40.0, 30.0], [0.0, 30.0]], width=8.0,
           thickness=4.0, bore=3.3)
    ti = k.inst("platform")
    jt = k.joint("tilt", "revolute", bi, fr((0, sw / 2 + 3.0 + 1.0, shh * 0.6), Y), ti,
                 fr((0.0, 15.0, 0.0), (0, 0, 1)), limits=[-60.0, 60.0],
                 design={"bearing": "servo output + idler bushing", "retention": "horn screw",
                         "actuator": {"item_id": p["servo"], "ratio": 1}})
    k.plan.contacts += [("base", s1), (s1, bi), (bi, ti)]
    k.plan.inputs += [jp, jt]
    k.plan.dof_intended = 2
    k.port("mount", "mount", "base", (0, 0, -4), (0, 0, -1))
    k.port("output", "end_effector", ti, (20, 15, 4), Z, "tilt platform")
    stall = (it.get("perf") or {}).get("stall_torque_nm") or next(
        (s["value"] for s in it["specs"] if s["key"] == "stall_torque"), None)
    need = p["payload_g"] / 1000 * 9.81 * p["payload_offset"] / 1000
    k.plan.sizing = {"tilt_static_torque_nm": round(need, 4), "servo_stall_nm": stall,
                     "margin": round(stall / need, 2) if stall and need else None}
    if stall and need and stall / need < 2:
        k.plan.warnings.append(f"tilt servo margin {stall / need:.1f}x < 2 at stall: move the "
                               "payload onto the tilt axis or pick a stronger servo")
    k.plan.warnings.append("the tilt servo body is not drawn: mount it on the bracket arm "
                           "outside the idler side")
    return k.plan


@mechanism("servo_arm_joint", "Servo arm joint", "joint",
           "A servo in a base drives a link through its horn: the simplest actuated arm joint.",
           ["arm joint", "servo joint", "actuated pivot", "robot arm", "rotate a link"],
           {"servo": P("servo_mg996r_standard", choices=("servo_sg90_micro", "servo_mg996r_standard"),
                       unit="", doc="catalog servo"),
            "link_l": P(80.0, 20.0, 400.0, "mm", "link length"),
            "payload_g": P(100.0, 0.0, 3000.0, "g", "load at the link tip")},
           ["servo_torque", "horn_bearing", "rom_sweep"], "rot->rot")
def servo_arm_joint(p: dict):
    it = _servo(p["servo"])
    sh = it["shape"]
    ox, oy, oz = _servo_out(it)
    k = Kit("base")
    k.part("base", "frame_plate", "servo base", "PETG", length=sh["tab_l"] + 20,
           width=sh["w"] + 30, thickness=4.0, bores=[], origin_x=-(sh["tab_l"] + 20) / 2 + 10)
    k.inst("base", "base", "base")
    k.part("servo", catalog=p["servo"], label=it["name"], material=None)
    si = k.inst("servo")
    k.fixed("servo_on_base", "base", fr((-ox, 0, 0)), si, fr())
    k.part("link", "link_bar", "arm link", "PETG", length=p["link_l"], width=14.0, thickness=5.0,
           bore=6.0)
    li = k.inst("link")
    j = k.joint("joint", "revolute", si, fr((ox, oy, oz + 3.0)), li, fr(), limits=[-90.0, 90.0],
                design={"bearing": "servo output bearing + idler", "retention": "horn screw",
                        "actuator": {"item_id": p["servo"], "ratio": 1},
                        "rom_deg": [-90.0, 90.0]})
    k.plan.contacts += [("base", si), (si, li)]
    k.plan.inputs.append(j)
    k.port("output", "end_effector", li, (ox + p["link_l"], 0, oz + 3), Z, "link tip")
    stall = (it.get("perf") or {}).get("stall_torque_nm")
    need = p["payload_g"] / 1000 * 9.81 * p["link_l"] / 1000
    k.plan.sizing = {"static_torque_nm": round(need, 4), "servo_stall_nm": stall,
                     "margin": round(stall / need, 2) if stall and need else None}
    if stall and need and stall / need < 2:
        k.plan.warnings.append(f"servo margin {stall / need:.1f}x < 2: shorten the link, lighten "
                               "the payload, or gear the servo down")
    return k.plan


@mechanism("snap_fit", "Cantilever snap fit", "joint",
           "A printed hook that deflects and catches a window in the mating part.",
           ["snap fit", "clip", "latch", "tool-free assembly", "enclosure lid"],
           {"t": P(1.5, 0.8, 4.0, "mm", "beam thickness"), "length": P(12.0, 5.0, 40.0, "mm", "beam length"),
            "hook": P(1.0, 0.3, 3.0, "mm", "hook depth (= deflection)"),
            "material": P("PETG", choices=("PLA", "PETG", "ABS", "PA12", "PC"), unit="", doc="material")},
           ["snap_strain", "insertion_force"], "lin->lin")
def snap_fit(p: dict):
    t, L, h = p["t"], p["length"], p["hook"]
    k = Kit("hook")
    k.part("hook", "snap_cantilever", "snap hook", p["material"], t=t, length=L, width=6.0,
           hook_h=h, hook_l=2.0, base=4.0)
    k.inst("hook", "hook", "hook part (grounded)")
    k.part("catch", "frame_plate", "mating wall", p["material"], length=16.0, width=10.0,
           thickness=2.0, bores=[], corner_holes=0.0, origin_x=-8.0 + 10.0)
    ci = k.inst("catch")
    j = k.joint("engage", "slider", "hook", fr((t / 2 + h + 0.3 + 1.0, 0, 4.0 + L - 2.5),
                                              (0, 0, -1)), ci, fr((-1.0, 0, -1.0), (0, 0, -1)),
                value=-8.0, limits=[-8.0, 0.0])
    k.plan.contacts.append(("hook", "catch"))
    k.plan.inputs.append(j)
    k.port("mount", "mount", "hook", (0, 0, 0), (0, 0, -1), "hook base")
    k.port("output", "lin", ci, (0, 0, 4.0 + L), (0, 0, -1), "mating wall (push down to snap)")
    eps = sizing.snap_fit_strain(t, h, L)
    allow = {"PLA": 0.02, "PETG": 0.04, "ABS": 0.025, "PA12": 0.06, "PC": 0.04}[p["material"]]
    k.plan.sizing = {"strain": round(eps, 4), "allowable_strain": allow}
    if eps > allow:
        k.plan.warnings.append(f"snap strain {eps:.1%} > {allow:.0%} for {p['material']}: "
                               "lengthen or thin the beam, or reduce the hook")
    k.plan.notes = ("print the beam along the layers (flat), never with layers across the bend; "
                    "engaging (0) deflects the hook into the wall by design")
    return k.plan
