"""Drivetrain mechanisms (E21, research 2026-11-01 §1 and §2.2 rows 1-18, 40-43): gear pairs
and trains, planetary and cycloidal reducers, timing belts, screws, rails, shaft supports and
motor mounts. Every gear mesh is phased (half a tooth for an external pair, the ring phase for
a planetary) and coupled, so turning the input turns everything downstream."""
from __future__ import annotations

import math

from . import sizing
from .card import Z, fr
from .kit import BEARINGS, Kit, bearing_for, pin_design
from .library import P, mechanism
from .shapes import (GT2, GT2_STOCK, HTD, belt_center, belt_length, mesh_phase, ring_phase)

GEAR_MATERIALS = ("PLA", "PETG", "PA-CF", "PA6-CF", "POM", "PA12", "Brass C360", "Steel 4140")
X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)


def _allow(material: str) -> float:
    from ..sim.materials import get_material
    return get_material(material).allowable_mpa


def _gear_sizing(z1: int, z2: int, m: float, width: float, torque: float,
                 material: str) -> dict:
    sf = sizing.gear_safety(torque, m, z1, width, _allow(material))
    return {"ratio": round(z2 / z1, 4), "center_distance_mm": m * (z1 + z2) / 2,
            "contact_ratio": round(sizing.contact_ratio(z1, z2, m), 3),
            "pinion_bending_mpa": round(sizing.lewis_stress_mpa(torque, m, z1, width), 2),
            "pinion_bending_sf": round(sf, 2), "efficiency": 0.98,
            "output_torque_nm": round(torque * z2 / z1 * 0.98, 4),
            "input_torque_nm": torque, "material": material}


def _gear_warnings(z1: int, z2: int, m: float, sz: dict) -> list[str]:
    w = []
    if min(z1, z2) < 17:
        w.append(f"{min(z1, z2)} teeth is below 17: undercut (modelled) weakens the root; "
                 "consider a profile shift")
    if sz["contact_ratio"] < 1.2:
        w.append(f"contact ratio {sz['contact_ratio']} < 1.2")
    if sz["pinion_bending_sf"] < 2:
        w.append(f"pinion bending safety factor {sz['pinion_bending_sf']} < 2: widen the face, "
                 "raise the module or pick a stronger material")
    if m < 1.0:
        w.append("printed gears below module 1 lose most of their tooth to tolerance [J]")
    return w


_GEAR_PARAMS = {
    "module": P(1.0, 0.5, 4.0, "mm", "tooth size (pitch d = module x teeth)"),
    "z1": P(20, 12, 120, "teeth", "pinion (input) teeth"),
    "z2": P(40, 12, 240, "teeth", "gear (output) teeth"),
    "width": P(6.0, 2.0, 40.0, "mm", "face width"),
    "shaft_d": P(8, 3, 20, "mm", "shaft diameter (a catalog bearing per shaft)"),
    "input_torque_nm": P(0.5, 0.0, 200.0, "N*m", "input torque for the sizing checks"),
    "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material"),
}


def _pair(p: dict, helix: float = 0.0) -> Kit:
    m, z1, z2, w, d = p["module"], p["z1"], p["z2"], p["width"], p["shaft_d"]
    a = m * (z1 + z2) / 2
    _cid, D, B, bore = bearing_for(d)
    t = B
    k = Kit("frame")
    ra, rb = m * (z1 + 2) / 2, m * (z2 + 2) / 2
    k.plate("frame", a + ra + rb + 20, 2 * max(ra, rb) + 20, t,
            [(0, 0, D + 0.1), (a, 0, D + 0.1)], origin_x=-ra)
    L = t + 2 + 1 + w + 4
    s1, j1, z0 = k.shaft_on_bearing("in", "frame", 0, 0, t, bore, L)
    s2, j2, _ = k.shaft_on_bearing("out", "frame", a, 0, t, bore, L)
    k.part("pinion", "gear_spur", f"pinion {z1}T", p["material"], module=m, teeth=z1,
           width=w, bore=float(bore), helix_deg=helix)
    k.part("gear", "gear_spur", f"gear {z2}T", p["material"], module=m, teeth=z2, width=w,
           bore=float(bore), phase_deg=round(mesh_phase(z1, 0.0, z2, 0.0), 6), helix_deg=-helix)
    k.on_shaft("pinion", s1, "pinion", 1.0, z0)
    k.on_shaft("gear", s2, "gear", 1.0, z0)
    k.couple(j2, "gear", -z1 / z2, j1)
    k.plan.contacts.append(("pinion", "gear"))
    k.plan.inputs.append(j1)
    k.port("input", "rot", s1, (0, 0, z0), Z, "input shaft (couple a motor here)")
    k.port("output", "rot", s2, (a, 0, z0), Z, "output shaft")
    k.port("mount", "mount", "frame", (0, 0, -t), (0, 0, -1), "frame plate underside")
    return k


@mechanism("spur_pair", "Spur gear pair", "gear",
           "Two parallel shafts on bearings in a frame plate, phased involute spur gears.",
           ["reduce speed", "increase torque", "transmit rotation between parallel shafts",
            "reverse rotation"], _GEAR_PARAMS,
           ["contact_ratio", "lewis_bending", "undercut", "interference_sweep"], "rot->rot")
def spur_pair(p: dict):
    k = _pair(p)
    sz = _gear_sizing(p["z1"], p["z2"], p["module"], p["width"], p["input_torque_nm"],
                      p["material"])
    k.plan.sizing = sz
    k.plan.transmission = {"ratio": sz["ratio"], "output_per_input": round(-p["z1"] / p["z2"], 5)}
    k.plan.warnings += _gear_warnings(p["z1"], p["z2"], p["module"], sz)
    return k.plan


@mechanism("helical_pair", "Helical gear pair", "gear",
           "Parallel-shaft helical gears (opposite hands): quieter than spur, with axial thrust.",
           ["reduce speed", "quiet gear drive", "transmit rotation between parallel shafts"],
           {**_GEAR_PARAMS, "helix_deg": P(20.0, 5.0, 35.0, "deg", "helix angle")},
           ["contact_ratio", "lewis_bending", "axial_thrust", "interference_sweep"], "rot->rot")
def helical_pair(p: dict):
    k = _pair(p, helix=p["helix_deg"])
    sz = _gear_sizing(p["z1"], p["z2"], p["module"], p["width"], p["input_torque_nm"],
                      p["material"])
    ft = 2 * p["input_torque_nm"] * 1000 / (p["module"] * p["z1"])
    sz["axial_thrust_n"] = round(ft * math.tan(math.radians(p["helix_deg"])), 2)
    k.plan.sizing = sz
    k.plan.transmission = {"ratio": sz["ratio"]}
    k.plan.warnings += _gear_warnings(p["z1"], p["z2"], p["module"], sz)
    k.plan.warnings.append(f"axial thrust {sz['axial_thrust_n']} N per gear: retain the gears "
                           "axially (shoulder + snap ring) and use bearings that take thrust")
    k.plan.notes = ("transverse module = module (py_gearworks helical); the centre distance "
                    "is m (z1 + z2) / 2")
    return k.plan


@mechanism("bevel_pair", "Bevel gear pair (90 deg)", "gear",
           "Intersecting shafts at 90 degrees: a vertical gear shaft in the plate, a horizontal "
           "pinion shaft in a pillow block.",
           ["turn a corner", "right angle drive", "transmit rotation between perpendicular "
            "shafts", "reduce speed"],
           {"module": P(1.5, 0.75, 4.0, "mm", "module"), "z1": P(15, 10, 60, "teeth", "pinion"),
            "z2": P(30, 10, 120, "teeth", "gear"), "width": P(6.0, 3.0, 20.0, "mm", "face"),
            "shaft_d": P(8, 4, 12, "mm", "shaft"),
            "input_torque_nm": P(0.3, 0.0, 50.0, "N*m", "input torque"),
            "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material")},
           ["lewis_bending", "axial_thrust", "interference_sweep"], "rot->rot")
def bevel_pair(p: dict):
    m, z1, z2, w, d = p["module"], p["z1"], p["z2"], p["width"], p["shaft_d"]
    _cid, D, B, bore = bearing_for(d)
    t = B
    k = Kit("frame")
    r2 = m * z2 / 2
    k.plate("frame", 2 * r2 + 60, 2 * r2 + 30, t, [(0, 0, D + 0.1)], origin_x=-r2 - 5)
    L2 = t + 2 + 1 + w + 6
    s2, j2, z0 = k.shaft_on_bearing("out", "frame", 0, 0, t, bore, L2)
    gz = 2.0                                         # gear 2 base above the plate
    k.part("gear", "gear_bevel", f"bevel gear {z2}T", p["material"], module=m, teeth=z1,
           mate_teeth=z2, width=w, member=2, bore=float(bore))
    k.on_shaft("gear", s2, "gear", gz, z0)
    # the pinion: member 1 frame (m z2/2, 0, m z1/2) on axis -X relative to gear 2's base
    px, pz = m * z2 / 2, gz + m * z1 / 2
    hx = px + w + 14.0                               # the pillow block beyond the pinion
    hb = pz + 0.0
    k.part("block", "bearing_block", "pillow block", "PETG", bearing_od=D, bearing_width=B,
           shaft_d=float(bore), center_height=hb, base_w=40.0)
    bi = k.inst("block")
    # block bearing axis is its +Y: the fixed joint turns +Y onto -X (frames' axes match)
    k.fixed("block_on_frame", "frame", fr((hx, 0, hb), (-1, 0, 0)), bi, fr((0, 0, hb), Y))
    cid = bearing_for(d)[0]
    k.part(cid, catalog=cid, label="bearing", material=None)
    bb = k.inst("in_bearing", cid, "pinion bearing")
    k.fixed("in_bearing_seat", "block", fr((0, -B / 2 - 0.75, hb), Y), bb, fr())
    k.part("in_shaft", "shaft", "pinion shaft", "Steel 1018", d=float(bore),
           length=float(hx + 8.0 - px + 2.0))
    si = k.inst("in_shaft")
    j1 = k.joint("in_rev", "revolute", "frame", fr((hx + 8.0, 0, pz), (-1, 0, 0)), si, fr(),
                 design=pin_design(bore, bearing=cid, retention="snap ring"))
    k.part("pinion", "gear_bevel", f"bevel pinion {z1}T", p["material"], module=m, teeth=z1,
           mate_teeth=z2, width=w, member=1, bore=float(bore))
    pi_ = k.inst("pinion")
    k.fixed("pinion_on_shaft", si, fr((0, 0, hx + 8.0 - px)), pi_, fr())
    k.couple(j2, "gear", -z1 / z2, j1)
    k.plan.contacts += [("pinion", "gear"), (si, "pinion"), (bb, si), ("block", bb)]
    k.plan.inputs.append(j1)
    k.port("input", "rot", si, (hx + 8, 0, pz), X, "horizontal pinion shaft")
    k.port("output", "rot", s2, (0, 0, z0), Z, "vertical gear shaft")
    sz = _gear_sizing(z1, z2, m, w, p["input_torque_nm"], p["material"])
    sz.pop("center_distance_mm")
    sz["pitch_cone_deg"] = [round(math.degrees(math.atan2(z1, z2)), 3),
                            round(90 - math.degrees(math.atan2(z1, z2)), 3)]
    k.plan.sizing = sz
    k.plan.transmission = {"ratio": sz["ratio"]}
    k.plan.warnings += _gear_warnings(z1, z2, m, sz)
    k.plan.warnings.append("bevel gears push apart axially: shim the pinion to set the mesh "
                           "and retain both gears")
    return k.plan


@mechanism("worm_drive", "Worm drive", "gear",
           "Worm on a horizontal shaft driving a worm wheel: high ratio in one stage, usually "
           "self-locking.", ["high reduction", "self locking", "hold position without power",
                             "right angle drive", "reduce speed"],
           {"module": P(1.0, 0.5, 3.0, "mm", "axial module"),
            "starts": P(1, 1, 4, "", "worm starts (thread count)"),
            "q": P(10.0, 6.0, 16.0, "", "diameter quotient (worm pitch d / module)"),
            "z_wheel": P(30, 18, 100, "teeth", "wheel teeth"),
            "width": P(6.0, 3.0, 20.0, "mm", "wheel face"),
            "input_torque_nm": P(0.1, 0.0, 20.0, "N*m", "worm torque")},
           ["self_locking", "efficiency", "heat", "axial_thrust", "interference_sweep"],
           "rot->rot")
def worm_drive(p: dict):
    m, n, q, z, w = p["module"], p["starts"], p["q"], p["z_wheel"], p["width"]
    a = m * (q + z) / 2
    worm_len = max(30.0, 4 * math.pi * m)
    k = Kit("frame")
    _cid, D, B, bore = bearing_for(8)
    t = B
    k.plate("frame", a + m * z / 2 + 50, worm_len + 70, t, [(0, 0, D + 0.1)],
            origin_x=-m * z / 2 - 10)
    sw, jw, z0 = k.shaft_on_bearing("out", "frame", 0, 0, t, bore, t + 2 + 2 + w + 6)
    wz = 2.0 + w / 2                                 # wheel mid-plane above the plate
    k.part("wheel", "worm_pair_member", f"worm wheel {z}T", "PA-CF", module=m, starts=n,
           diameter_quotient=q, teeth=z, worm_length=worm_len, wheel_width=w, member="wheel",
           bore=float(bore))
    k.on_shaft("wheel", sw, "wheel", wz, z0)
    k.part("worm", "worm_pair_member", f"worm {n}-start", "Brass C360", module=m, starts=n,
           diameter_quotient=q, teeth=z, worm_length=worm_len, wheel_width=w, member="worm",
           bore=5.0)
    k.part("block", "bearing_block", "worm support", "PETG", bearing_od=16.0, bearing_width=5.0,
           shaft_d=5.0, center_height=wz, base_w=34.0)
    for side, sy in (("a", worm_len / 2 + 8), ("b", -worm_len / 2 - 8)):
        bi = k.inst(f"block_{side}", "block", f"worm support {side}")
        k.fixed(f"block_{side}_on_frame", "frame", fr((a, sy, wz), Y), bi, fr((0, 0, wz), Y))
    k.part("worm_shaft", "shaft", "worm shaft", "Steel 1018", d=5.0,
           length=worm_len + 2 * 8 + 12)
    ws = k.inst("worm_shaft")
    jin = k.joint("worm_rev", "revolute", "frame", fr((a, -worm_len / 2 - 14, wz), Y), ws, fr(),
                  design=pin_design(5, bearing="bearing_625_deep_groove",
                                    retention="shaft collar"))
    wi = k.inst("worm")
    k.fixed("worm_on_shaft", ws, fr((0, 0, worm_len / 2 + 14)), wi, fr())
    # the worm advances one tooth of the wheel per 360/starts deg: wheel = worm x starts/z
    k.couple(jw, "gear", n / z, jin)
    k.plan.contacts += [("worm", "wheel"), (ws, "worm"), ("block_a", ws), ("block_b", ws)]
    k.plan.inputs.append(jin)
    k.port("input", "rot", ws, (a, -worm_len / 2 - 14, wz), Y, "worm shaft")
    k.port("output", "rot", sw, (0, 0, z0), Z, "wheel shaft")
    lam = math.degrees(math.atan(n / q))
    eff = sizing.worm_efficiency(lam, 0.1)
    k.plan.sizing = {"ratio": z / n, "center_distance_mm": a, "lead_angle_deg": round(lam, 3),
                     "efficiency": round(eff, 3), "self_locking": lam < math.degrees(
                         math.atan(0.1)),
                     "output_torque_nm": round(p["input_torque_nm"] * z / n * eff, 4)}
    k.plan.transmission = {"ratio": z / n}
    k.plan.warnings.append(f"efficiency ~{eff:.0%} (mu 0.1 assumed): the rest is heat in the "
                           "mesh; check the wheel temperature at continuous duty")
    k.plan.notes = "the coupling sign follows a right-hand worm; check the animation direction"
    return k.plan


@mechanism("rack_pinion", "Rack and pinion", "gear",
           "Pinion on a bearing-supported shaft driving a rack on a linear slide.",
           ["convert rotation to linear", "linear motion", "steering", "gripper drive"],
           {"module": P(1.0, 0.5, 3.0, "mm", "module"),
            "z": P(20, 12, 60, "teeth", "pinion teeth"),
            "rack_teeth": P(40, 10, 200, "teeth", "rack teeth (length = pi m teeth)"),
            "width": P(6.0, 3.0, 20.0, "mm", "face"), "shaft_d": P(8, 3, 12, "mm", "shaft"),
            "input_torque_nm": P(0.3, 0.0, 20.0, "N*m", "pinion torque"),
            "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material")},
           ["lewis_bending", "stroke", "interference_sweep"], "rot->lin")
def rack_pinion(p: dict):
    m, z, n, w = p["module"], p["z"], p["rack_teeth"], p["width"]
    r = m * z / 2
    length = math.pi * m * n
    _cid, D, B, bore = bearing_for(p["shaft_d"])
    t = B
    k = Kit("frame")
    k.plate("frame", length + 30, 2 * r + 50, t, [(0, r, D + 0.1)], origin_x=-length / 2 - 5)
    s, j, z0 = k.shaft_on_bearing("in", "frame", 0, r, t, bore, t + 2 + 2 + w + 4)
    k.part("pinion", "gear_spur", f"pinion {z}T", p["material"], module=m, teeth=z, width=w,
           bore=float(bore), phase_deg=-90.0)
    k.on_shaft("pinion", s, "pinion", 2.0, z0)
    k.part("rack", "gear_rack", f"rack {n}T", p["material"], module=m, teeth=n, width=w,
           base=4.0)
    ri = k.inst("rack")
    stroke = length - 2 * math.pi * m * 2 - 2 * r
    half = max(0.0, stroke / 2)
    jr = k.joint("rack_slide", "slider", "frame", fr((0, 0, 2.0), X), ri, fr((0, 0, 0), X),
                 limits=[-half, half],
                 design={"bearing": "printed", "clearance_mm": 0.3, "retention": "guide rail"})
    k.couple(jr, "rack", r * math.pi / 180, j)
    k.plan.contacts += [("pinion", "rack"), ("frame", "rack")]
    k.plan.inputs.append(j)
    k.port("input", "rot", s, (0, r, z0), Z, "pinion shaft")
    k.port("output", "lin", ri, (0, 0, 2), X, "rack")
    sz = {"mm_per_deg": round(r * math.pi / 180, 5), "pitch_radius_mm": r,
          "force_n": round(p["input_torque_nm"] * 1000 / r, 2), "stroke_mm": round(2 * half, 2),
          "pinion_bending_sf": round(sizing.gear_safety(p["input_torque_nm"], m, z, w,
                                                        _allow(p["material"])), 2)}
    k.plan.sizing = sz
    k.plan.transmission = {"mm_per_rev": round(2 * math.pi * r, 4)}
    k.plan.rom = {"rack_slide": [-half, half]}
    k.plan.warnings.append("the rack runs on the plate as a printed slide: add a guide (rail or "
                           "V-groove) so it cannot lift off the pinion")
    return k.plan


@mechanism("gear_train_compound", "Compound gear train (2 stages)", "gear",
           "Two spur stages on three shafts (an intermediate shaft carries a gear and a "
           "pinion): high ratio, same direction out.",
           ["high reduction", "reduce speed", "increase torque", "gearbox"],
           {"module": P(1.0, 0.5, 3.0, "mm", "module"),
            "z1": P(12, 12, 40, "teeth", "stage 1 pinion"), "z2": P(36, 20, 120, "teeth", "stage 1 gear"),
            "z3": P(12, 12, 40, "teeth", "stage 2 pinion"), "z4": P(36, 20, 120, "teeth", "stage 2 gear"),
            "width": P(6.0, 3.0, 20.0, "mm", "face"),
            "input_torque_nm": P(0.2, 0.0, 20.0, "N*m", "input torque"),
            "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material")},
           ["contact_ratio", "lewis_bending", "shaft_spacing", "interference_sweep"], "rot->rot")
def gear_train_compound(p: dict):
    m, w = p["module"], p["width"]
    z1, z2, z3, z4 = p["z1"], p["z2"], p["z3"], p["z4"]
    a1, a2 = m * (z1 + z2) / 2, m * (z3 + z4) / 2
    _cid, D, B, bore = bearing_for(8)
    t = B
    # shafts in an L so stage 2 sits beside stage 1 (third shaft at 90 deg around shaft 2)
    x2, y2 = a1, 0.0
    x3, y3 = a1, a2
    k = Kit("frame")
    rmax = m * (max(z2, z4) + 2) / 2
    k.plate("frame", a1 + 2 * rmax + 20, a2 + 2 * rmax + 20, t,
            [(0, 0, D + 0.1), (x2, y2, D + 0.1), (x3, y3, D + 0.1)], origin_x=-rmax)
    L = t + 2 + 1 + 2 * w + 6
    s1, j1, z0 = k.shaft_on_bearing("in", "frame", 0, 0, t, bore, L)
    s2, j2, _ = k.shaft_on_bearing("mid", "frame", x2, y2, t, bore, L)
    s3, j3, _ = k.shaft_on_bearing("out", "frame", x3, y3, t, bore, L)
    k.part("pinion1", "gear_spur", f"stage 1 pinion {z1}T", p["material"], module=m,
           teeth=z1, width=w, bore=8.0)
    ph2 = mesh_phase(z1, 0.0, z2, 0.0)
    k.part("gear2", "gear_spur", f"stage 1 gear {z2}T", p["material"], module=m, teeth=z2,
           width=w, bore=8.0, phase_deg=round(ph2, 6))
    # stage 2 pinion on the mid shaft (upper layer), meshing toward +Y
    k.part("pinion3", "gear_spur", f"stage 2 pinion {z3}T", p["material"], module=m, teeth=z3,
           width=w, bore=8.0)
    ph4 = mesh_phase(z3, 0.0, z4, 90.0)
    k.part("gear4", "gear_spur", f"stage 2 gear {z4}T", p["material"], module=m, teeth=z4,
           width=w, bore=8.0, phase_deg=round(ph4, 6))
    k.on_shaft("pinion1", s1, "pinion1", 1.0, z0)
    k.on_shaft("gear2", s2, "gear2", 1.0, z0)
    k.on_shaft("pinion3", s2, "pinion3", 1.0 + w + 0.5, z0)
    k.on_shaft("gear4", s3, "gear4", 1.0 + w + 0.5, z0)
    k.couple(j2, "gear", -z1 / z2, j1)
    k.couple(j3, "gear", (z1 / z2) * (z3 / z4), j1)
    k.plan.contacts += [("pinion1", "gear2"), ("pinion3", "gear4"), ("gear2", "pinion3")]
    k.plan.inputs.append(j1)
    k.port("input", "rot", s1, (0, 0, z0), Z)
    k.port("output", "rot", s3, (x3, y3, z0), Z)
    ratio = z2 / z1 * z4 / z3
    t2 = p["input_torque_nm"] * z2 / z1 * 0.98
    k.plan.sizing = {"ratio": round(ratio, 4), "efficiency": round(0.98 ** 2, 4),
                     "stage1": _gear_sizing(z1, z2, m, w, p["input_torque_nm"], p["material"]),
                     "stage2": _gear_sizing(z3, z4, m, w, t2, p["material"])}
    k.plan.transmission = {"ratio": round(ratio, 4), "direction": "same as input"}
    for st in ("stage1", "stage2"):
        k.plan.warnings += [f"{st}: {x}" for x in _gear_warnings(
            *((z1, z2) if st == "stage1" else (z3, z4)), m, k.plan.sizing[st])]
    if a2 - 4.0 < m * (z2 + 2) / 2 + 0.5:
        k.plan.warnings.append("the output shaft passes through stage 1's gear: raise z3 + z4 "
                               "or lower z2")
    return k.plan


@mechanism("planetary", "Planetary gearset (ring fixed)", "reducer",
           "Sun input, carrier output, ring fixed: ratio 1 + z_ring / z_sun in one coaxial stage.",
           ["reduce speed", "coaxial reduction", "compact gearbox", "actuator", "increase torque"],
           {"module": P(1.0, 0.5, 3.0, "mm", "module"),
            "zs": P(12, 9, 60, "teeth", "sun"), "zp": P(12, 9, 60, "teeth", "planet"),
            "n_planets": P(3, 2, 6, "", "planets"), "width": P(6.0, 3.0, 30.0, "mm", "face"),
            "input_torque_nm": P(0.3, 0.0, 50.0, "N*m", "sun torque"),
            "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material")},
           ["assembly_condition", "lewis_bending", "ring_phase", "interference_sweep"],
           "rot->rot")
def planetary(p: dict):
    m, zs, zp, n, w = p["module"], p["zs"], p["zp"], p["n_planets"], p["width"]
    zr = zs + 2 * zp
    bad = sizing.planetary_ok(zs, zp, zr, n)
    if bad:
        raise ValueError("; ".join(bad))
    a = m * (zs + zp) / 2
    k = Kit("ring")
    rim = 4.0
    # the ring phase that fits planet 0 (the assembly condition makes it fit every planet)
    rph = ring_phase(zp, mesh_phase(zs, 0.0, zp, 0.0), zr, 0.0)
    k.part("ring", "gear_ring", f"ring {zr}T (fixed)", p["material"], module=m, teeth=zr,
           width=w, rim=rim, phase_deg=round(rph, 6), mount_holes=6)
    k.inst("ring", "ring", "ring (grounded)")
    plates = 3.0
    k.part("carrier", "link_plate", "carrier", "PETG",
           points=[[0.0, 0.0]] + [[round(a * math.cos(2 * math.pi * i / n), 4),
                                   round(a * math.sin(2 * math.pi * i / n), 4)] for i in range(n)],
           width=m * zp * 0.9, thickness=plates, bore=3.3)
    ci = k.inst("carrier")
    jc = k.joint("carrier_rev", "revolute", "ring", fr((0, 0, w + 0.6)), ci, fr(),
                 design={"bearing": "bushing", "retention": "cover plate",
                         "clearance_mm": 0.3})
    k.part("sun", "gear_spur", f"sun {zs}T", p["material"], module=m, teeth=zs, width=w,
           bore=5.0, hub_d=8.0, hub_h=0.0)
    si = k.inst("sun")
    js = k.joint("sun_rev", "revolute", "ring", fr((0, 0, 0)), si, fr(),
                 design={"bearing": "bearing_625_deep_groove", "shaft": {"d_mm": 5.0},
                         "retention": "set screw", "clearance_mm": 0.0})
    k.part("planet", "gear_spur", f"planet {zp}T", p["material"], module=m, teeth=zp, width=w,
           bore=3.3)
    k_c = zs / (zs + zr)
    planet_ratio = -(zr / zp) * k_c
    for i in range(n):
        th = 360.0 * i / n
        php = mesh_phase(zs, 0.0, zp, th)               # planet's absolute rotation
        pi_ = k.inst(f"planet_{i}", "planet", f"planet {i + 1}")
        cx, cy = a * math.cos(math.radians(th)), a * math.sin(math.radians(th))
        jp = k.joint(f"planet_{i}_rev", "revolute", ci, fr((cx, cy, -w - 0.6)), pi_, fr(),
                     value=php, design=pin_design(3.0, bearing="bushing", retention="cover plate"))
        k.couple(jp, "gear", planet_ratio, js, offset=php)
        k.plan.contacts += [("sun", pi_), ("ring", pi_), (ci, pi_)]
    k.couple(jc, "gear", k_c, js)
    k.plan.inputs.append(js)
    k.port("input", "rot", si, (0, 0, 0), Z, "sun (5 mm motor shaft)")
    k.port("output", "rot", ci, (0, 0, w + 0.6), Z, "carrier")
    k.port("mount", "mount", "ring", (0, 0, 0), (0, 0, -1), "ring rim holes")
    ratio = 1 + zr / zs
    sf = sizing.gear_safety(p["input_torque_nm"] / n, m, zs, w, _allow(p["material"]))
    k.plan.sizing = {"ratio": ratio, "z_ring": zr, "center_distance_mm": a,
                     "efficiency": 0.95, "output_torque_nm": round(p["input_torque_nm"] * ratio
                                                                   * 0.95, 4),
                     "sun_bending_sf": round(sf, 2), "assembly_condition": "ok"}
    k.plan.transmission = {"ratio": ratio, "willis": "(w_s - w_c)/(w_r - w_c) = -z_r/z_s"}
    if sf < 2:
        k.plan.warnings.append(f"sun bending safety factor {sf:.2f} < 2")
    k.plan.notes = ("ring phase: 180/z_ring (the research spike's 34 mm3 overlap without it); "
                    "planets phased by mesh_phase and coupled to the sun")
    return k.plan


@mechanism("planetary_compound", "Compound planetary (stepped planets)", "reducer",
           "Sun drives stepped planets meshing a fixed ring and an output ring: 20-100:1 in one "
           "coaxial stage, popular in printed arms.",
           ["high reduction", "coaxial reduction", "robot arm joint", "actuator"],
           {"module": P(1.0, 0.6, 2.5, "mm", "module of stage 1"),
            "zs": P(12, 9, 30, "teeth", "sun"), "zp1": P(18, 9, 40, "teeth", "planet, ring-1 step"),
            "zp2": P(15, 9, 40, "teeth", "planet, ring-2 step"),
            "n_planets": P(3, 2, 4, "", "planets"), "width": P(6.0, 3.0, 20.0, "mm", "face per step"),
            "input_torque_nm": P(0.2, 0.0, 20.0, "N*m", "sun torque"),
            "material": P("POM", choices=GEAR_MATERIALS, unit="", doc="gear material")},
           ["assembly_condition", "lewis_bending", "interference_sweep"], "rot->rot")
def planetary_compound(p: dict):
    m, zs, zp1, zp2, n, w = (p["module"], p["zs"], p["zp1"], p["zp2"], p["n_planets"],
                             p["width"])
    zr1 = zs + 2 * zp1
    bad = sizing.planetary_ok(zs, zp1, zr1, n)
    if bad:
        raise ValueError("; ".join(bad))
    a = m * (zs + zp1) / 2
    if zp2 == zp1:
        raise ValueError("the planet's two steps need different tooth counts")
    # ring 2 meshes the second step at the same centre distance and module:
    # z_r2 = z_s + z_p1 + z_p2 (each planet's second step is clocked to fit it)
    zr2 = zs + zp1 + zp2
    m2 = 2 * a / (zr2 - zp2)
    k = Kit("ring1")
    k.part("ring1", "gear_ring", f"fixed ring {zr1}T", p["material"], module=m, teeth=zr1,
           width=w, rim=4.0, mount_holes=6)
    k.inst("ring1", "ring1", "fixed ring")
    k.part("sun", "gear_spur", f"sun {zs}T", p["material"], module=m, teeth=zs, width=w,
           bore=5.0)
    si = k.inst("sun")
    js = k.joint("sun_rev", "revolute", "ring1", fr(), si, fr(),
                 design={"bearing": "bearing_625_deep_groove", "retention": "set screw"})
    th0 = mesh_phase(zs, 0.0, zp1, 0.0)
    r2ph = ring_phase(zp2, th0, zr2, 0.0)
    k.part("ring2", "gear_ring", f"output ring {zr2}T", p["material"], module=m2, teeth=zr2,
           width=w, rim=4.0, mount_holes=6, phase_deg=round(r2ph, 6))
    r2 = k.inst("ring2", "ring2", "output ring")
    jr2 = k.joint("ring2_rev", "revolute", "ring1", fr((0, 0, w + 0.5)), r2, fr(),
                  design={"bearing": "bearing_6800_deep_groove", "retention": "cover plate"})
    k.part("carrier", "link_plate", "carrier (floating)", "PETG",
           points=[[0.0, 0.0]] + [[round(a * math.cos(2 * math.pi * i / n), 4),
                                   round(a * math.sin(2 * math.pi * i / n), 4)] for i in range(n)],
           width=m * zp2 * 0.9, thickness=3.0, bore=3.3)
    ci = k.inst("carrier")
    jc = k.joint("carrier_rev", "revolute", "ring1", fr((0, 0, -3.6)), ci, fr())
    # planet = two stacked gears (one part per step, fixed together)
    k.part("planet1", "gear_spur", f"planet step 1 {zp1}T", p["material"], module=m, teeth=zp1,
           width=w, bore=3.3)
    kc = zs / (zs + zr1)
    planet_rel = -(zr1 / zp1) * kc
    ratio = (1 + zr1 / zs) / (1 - (zr1 * zp2) / (zp1 * zr2))
    for i in range(n):
        th = 360.0 * i / n
        php = mesh_phase(zs, 0.0, zp1, th)
        cx, cy = a * math.cos(math.radians(th)), a * math.sin(math.radians(th))
        p1 = k.inst(f"planet_{i}", "planet1", f"planet {i + 1}")
        jp = k.joint(f"planet_{i}_rev", "revolute", ci, fr((cx, cy, 3.6)), p1, fr(), value=php,
                     design=pin_design(3.0, bearing="bushing", retention="cover plate"))
        k.couple(jp, "gear", planet_rel, js, offset=php)
        # step 2 clocked so it meshes ring 2 where this planet sits
        want = th + (r2ph - th) * zr2 / zp2
        k.part(f"planet2_{i}", "gear_spur", f"planet {i + 1} step 2 {zp2}T", p["material"],
               module=m2, teeth=zp2, width=w, bore=3.3, phase_deg=round(want - php, 6))
        p2 = k.inst(f"planet_{i}_step2", f"planet2_{i}", f"planet {i + 1} step 2")
        k.fixed(f"planet_{i}_stack", p1, fr((0, 0, w + 0.5)), p2, fr())
        k.plan.contacts += [("sun", p1), ("ring1", p1), (r2, p2), (p1, p2), (ci, p1)]
    k.couple(jc, "gear", kc, js)
    k.couple(jr2, "gear", 1 / ratio, js)
    k.plan.inputs.append(js)
    k.port("input", "rot", si, (0, 0, 0), Z, "sun")
    k.port("output", "rot", r2, (0, 0, w + 0.5), Z, "output ring")
    k.plan.sizing = {"ratio": round(ratio, 3), "z_ring1": zr1, "z_ring2": zr2,
                     "module_ring2": round(m2, 4), "efficiency": 0.8,
                     "output_torque_nm": round(p["input_torque_nm"] * abs(ratio) * 0.8, 3)}
    k.plan.transmission = {"ratio": round(ratio, 3)}
    k.plan.warnings.append("each planet's second step is clocked differently (one part per "
                           "planet): print them as marked")
    k.plan.notes = ("i = (1 + z_r1/z_s) / (1 - z_r1 z_p2 / (z_p1 z_r2)); the output ring is "
                    "coupled to the sun at 1/i")
    return k.plan


@mechanism("cycloidal_reducer", "Cycloidal reducer", "reducer",
           "Eccentric input drives a cycloid disc against a pin ring; output pins take the "
           "wobble out: (pins-1):1, compact and backlash-tolerant.",
           ["high reduction", "robot joint", "compact reducer", "backdrivable reducer",
            "actuator"],
           {"pins": P(12, 8, 40, "", "ring pins (lobes = pins - 1, ratio = lobes)"),
            "pin_circle_d": P(60.0, 30.0, 200.0, "mm", "pin circle"),
            "pin_d": P(5.0, 2.0, 12.0, "mm", "pin diameter"),
            "eccentricity": P(1.5, 0.5, 5.0, "mm", "input eccentricity (< pin radius)"),
            "width": P(6.0, 3.0, 20.0, "mm", "disc thickness"),
            "input_torque_nm": P(0.3, 0.0, 20.0, "N*m", "input torque")},
           ["pin_bending", "eccentric_balance", "interference_sweep"], "rot->rot")
def cycloidal_reducer(p: dict):
    n, R, e, w = p["pins"], p["pin_circle_d"], p["eccentricity"], p["width"]
    lobes = n - 1
    k = Kit("housing")
    k.part("housing", "pin_ring", f"pin ring {n} pins", "PETG", pins=n, pin_circle_d=R,
           pin_d=p["pin_d"], width=w, housing_od=R + 2 * p["pin_d"] + 16)
    k.inst("housing", "housing", "pin ring (grounded)")
    k.part("cam", "shaft", "eccentric input shaft", "Steel 1018", d=8.0, length=w + 14)
    ci = k.inst("cam", "cam", "input shaft")
    jin = k.joint("input_rev", "revolute", "housing", fr((0, 0, -3.0 - 4.0)), ci, fr(),
                  design={"bearing": "bearing_608_deep_groove", "retention": "snap ring"})
    bore = 22.0 + 0.1
    k.part("disc", "cycloid_disc", f"cycloid disc {lobes} lobes", "POM", pins=n,
           pin_circle_d=R, pin_d=p["pin_d"], eccentricity=e, width=w, bore=bore,
           output_holes=6, hole_circle_d=R * 0.55, output_pin_d=4.0)
    # the disc rides the eccentric: placed through a revolute on the input shaft offset by e
    # (its own rotation relative to the shaft follows the input at -(1 + 1/lobes))
    di = k.inst("disc", "disc", "cycloid disc")
    jd = k.joint("disc_on_eccentric", "revolute", ci, fr((e, 0, 7.0)), di, fr(),
                 design={"bearing": "bearing_6004_deep_groove", "retention": "spacer"})
    k.couple(jd, "gear", -(1 + 1 / lobes), jin)
    k.part("output", "link_plate", "output flange", "PETG",
           points=[[0.0, 0.0]] + [[round(R * 0.275 * math.cos(2 * math.pi * i / 6), 4),
                                   round(R * 0.275 * math.sin(2 * math.pi * i / 6), 4)]
                                  for i in range(6)], width=10.0, thickness=4.0, bore=4.0)
    oi = k.inst("output")
    jo = k.joint("output_rev", "revolute", "housing", fr((0, 0, w + 1.0)), oi, fr(),
                 design={"bearing": "bearing_6800_deep_groove", "retention": "cover"})
    k.couple(jo, "gear", -1 / lobes, jin)
    k.plan.contacts += [("housing", "disc"), ("disc", "cam"), ("output", "disc"),
                        ("housing", "cam")]
    k.plan.inputs.append(jin)
    k.port("input", "rot", ci, (0, 0, -7.0), Z, "eccentric shaft")
    k.port("output", "rot", oi, (0, 0, w + 1.0), Z, "output flange (6 pins through the disc)")
    k.plan.sizing = {"ratio": lobes, "direction": "reversed", "efficiency": 0.85,
                     "output_torque_nm": round(p["input_torque_nm"] * lobes * 0.85, 3),
                     "eccentricity_mm": e}
    k.plan.transmission = {"ratio": lobes}
    k.plan.warnings.append("one disc is unbalanced at speed: use two discs at 180 deg for "
                           "anything above a few hundred rpm")
    k.plan.notes = ("the disc's absolute rotation is -input/lobes; relative to the input shaft "
                    "it turns -(1 + 1/lobes)")
    return k.plan


@mechanism("strain_wave", "Strain-wave (harmonic) reducer, schematic", "reducer",
           "Wave generator, flexspline (z_f) and circular spline (z_f + 2): 30-160:1 coaxial. "
           "The flexspline is drawn undeformed (schematic).",
           ["very high reduction", "zero backlash", "robot wrist", "compact reducer"],
           {"module": P(0.5, 0.3, 1.0, "mm", "module"), "zf": P(100, 50, 200, "teeth", "flexspline"),
            "width": P(8.0, 4.0, 20.0, "mm", "tooth width"),
            "input_torque_nm": P(0.05, 0.0, 5.0, "N*m", "input torque")},
           ["flexspline_fatigue", "ratcheting"], "rot->rot")
def strain_wave(p: dict):
    m, zf, w = p["module"], p["zf"], p["width"]
    zc = zf + 2
    k = Kit("circular")
    k.part("circular", "gear_ring", f"circular spline {zc}T", "PA-CF", module=m, teeth=zc,
           width=w, rim=4.0, mount_holes=8)
    k.inst("circular", "circular", "circular spline (grounded)")
    # schematic flexspline: a thin toothed cup drawn one module smaller (undeformed)
    k.part("flex", "gear_spur", f"flexspline {zf}T (schematic)", "TPU 95A", module=m * 0.96,
           teeth=zf, width=w, bore=m * zf - 4.0)
    fi = k.inst("flex")
    jf = k.joint("flex_rev", "revolute", "circular", fr((0, 0, 0)), fi, fr())
    k.part("wave", "link_plate", "wave generator (elliptical cam)", "PETG",
           points=[[0.0, 0.0], [round(m * zf / 2 - 4.5, 3), 0.0],
                   [round(-(m * zf / 2 - 4.5), 3), 0.0]], width=12.0, thickness=w, bore=5.0)
    wi = k.inst("wave")
    jw = k.joint("wave_rev", "revolute", "circular", fr((0, 0, 0)), wi, fr())
    ratio = zf / (zc - zf)
    k.couple(jf, "gear", -1 / ratio, jw)
    k.plan.inputs.append(jw)
    k.plan.contacts += [("circular", "flex"), ("flex", "wave")]
    k.port("input", "rot", wi, (0, 0, 0), Z, "wave generator")
    k.port("output", "rot", fi, (0, 0, 0), Z, "flexspline")
    k.plan.sizing = {"ratio": ratio, "z_circular": zc, "efficiency": 0.7}
    k.plan.transmission = {"ratio": ratio, "direction": "reversed"}
    k.plan.warnings += ["schematic: the flexspline is drawn undeformed and does not mesh; model "
                        "the real elliptical deflection before printing",
                        "flexspline fatigue: printed TPU/PA works at low torque only [J]"]
    return k.plan


def _belt(p: dict, kind: str):
    z1, z2, w = p["z1"], p["z2"], p["width"]
    if kind == "gt2":
        pitch, pld, bt = GT2["pitch"], GT2["pld"], GT2["belt_t"]
    else:
        pitch = float(p["pitch"])
        pld, _gr, _gd, bt = HTD[int(pitch)]
    pd1, pd2 = z1 * pitch / math.pi, z2 * pitch / math.pi
    want = belt_length(pd1, pd2, p["center"])
    teeth = math.ceil(want / pitch)
    stock = [s for s in GT2_STOCK if s >= teeth] if kind == "gt2" else []
    belt_teeth = stock[0] if stock else teeth
    c = belt_center(pd1, pd2, belt_teeth * pitch)
    _cid, D, B, bore = bearing_for(p["shaft_d"])
    t = B
    k = Kit("frame")
    r2 = pd2 / 2 + 3
    k.plate("frame", c + pd1 / 2 + r2 + 25, 2 * r2 + 20, t, [(0, 0, D + 0.1), (c, 0, D + 0.1)],
            origin_x=-pd1 / 2 - 5)
    L = t + 2 + 2 + w + 2 + 6
    s1, j1, z0 = k.shaft_on_bearing("drive", "frame", 0, 0, t, bore, L)
    s2, j2, _ = k.shaft_on_bearing("driven", "frame", c, 0, t, bore, L)
    if kind == "gt2":
        k.part("pulley1", "gt2_pulley", f"GT2 pulley {z1}T", "PETG", teeth=z1, width=w,
               bore=float(bore))
        k.part("pulley2", "gt2_pulley", f"GT2 pulley {z2}T", "PETG", teeth=z2, width=w,
               bore=float(bore))
    else:
        k.part("pulley1", "htd_pulley", f"HTD {int(pitch)}M pulley {z1}T", "PETG", teeth=z1,
               pitch=int(pitch), width=w, bore=float(bore))
        k.part("pulley2", "htd_pulley", f"HTD {int(pitch)}M pulley {z2}T", "PETG", teeth=z2,
               pitch=int(pitch), width=w, bore=float(bore))
    k.on_shaft("pulley1", s1, "pulley1", 3.0, z0)
    k.on_shaft("pulley2", s2, "pulley2", 3.0, z0)
    k.part("belt", "timing_belt", f"{kind.upper()} belt {belt_teeth}T x {w:g} mm", "TPU 95A",
           pitch_d1=round(pd1, 5), pitch_d2=round(pd2, 5), center=round(c, 5), width=w,
           thickness=bt, pld=pld)
    bi = k.inst("belt")
    k.fixed("belt_on_frame", "frame", fr((0, 0, 3.0)), bi, fr())
    k.couple(j2, "belt", z1 / z2, j1)
    k.plan.contacts += [("belt", "pulley1"), ("belt", "pulley2")]
    k.plan.inputs.append(j1)
    k.port("input", "rot", s1, (0, 0, z0), Z, "drive pulley shaft (motor here)")
    k.port("output", "rot", s2, (c, 0, z0), Z)
    small = min(z1, z2)
    tim = sizing.belt_teeth_in_mesh(small, min(pd1, pd2), max(pd1, pd2), c)
    pull = sizing.belt_pull_n(p["input_torque_nm"], pd1)
    k.plan.sizing = {"ratio": round(z2 / z1, 4), "pitch_d_mm": [round(pd1, 3), round(pd2, 3)],
                     "belt_teeth": belt_teeth, "belt_length_mm": belt_teeth * pitch,
                     "center_mm": round(c, 3), "wrap_small_deg": round(
                         360 * tim / small, 1), "teeth_in_mesh": round(tim, 1),
                     "belt_pull_n": round(pull, 1),
                     "tension_n_suggested": round(max(20.0, 0.5 * pull), 1),
                     "stock_belt": bool(stock) if kind == "gt2" else None}
    k.plan.transmission = {"ratio": round(z2 / z1, 4), "direction": "same"}
    if tim < 6:
        k.plan.warnings.append(f"only {tim:.1f} teeth in mesh on the small pulley (< 6): the "
                               "belt can skip; increase the centre distance or the small pulley")
    if kind == "gt2" and pull > 120:
        k.plan.warnings.append(f"belt pull {pull:.0f} N is above a 6 mm GT2's ~100-150 N working "
                               "tension: use a 9-10 mm belt or HTD 3M/5M")
    k.plan.warnings.append("a fixed centre distance cannot tension the belt: slot one bearing "
                           "seat or add an idler")
    return k.plan


_BELT = {"z1": P(20, 10, 80, "teeth", "drive pulley"), "z2": P(60, 10, 160, "teeth", "driven"),
         "center": P(80.0, 20.0, 600.0, "mm", "target centre distance (snapped to a stock belt)"),
         "width": P(6.0, 6.0, 15.0, "mm", "belt width"), "shaft_d": P(8, 5, 12, "mm", "shafts"),
         "input_torque_nm": P(0.4, 0.0, 30.0, "N*m", "drive torque")}


@mechanism("belt_drive_gt2", "GT2 timing belt drive", "belt",
           "Two GT2 pulleys on bearing-supported shafts with a stock closed belt; the centre "
           "distance is solved exactly from the belt length.",
           ["reduce speed", "transmit rotation over a distance", "quiet drive",
            "quadruped knee drive", "move a motor away from a joint"], _BELT,
           ["teeth_in_mesh", "belt_tension", "stock_length"], "rot->rot")
def belt_drive_gt2(p: dict):
    return _belt(p, "gt2")


@mechanism("belt_drive_htd", "HTD timing belt drive", "belt",
           "HTD 3M/5M/8M pulleys and belt for higher torque than GT2.",
           ["reduce speed", "high torque belt", "transmit rotation over a distance"],
           {**_BELT, "pitch": P(5, choices=(3, 5, 8), unit="mm", doc="HTD pitch"),
            "width": P(10.0, 6.0, 30.0, "mm", "belt width")},
           ["teeth_in_mesh", "belt_tension"], "rot->rot")
def belt_drive_htd(p: dict):
    return _belt(p, "htd")


@mechanism("belt_linear_axis", "Belt-driven linear axis (GT2 + MGN rail)", "linear",
           "A GT2 belt loop between a drive pulley and an idler moves a carriage on an MGN rail.",
           ["linear motion", "convert rotation to linear", "gantry axis", "fast long stroke"],
           {"length": P(300.0, 100.0, 1500.0, "mm", "rail length"),
            "z": P(20, 16, 40, "teeth", "pulley teeth"),
            "rail": P("MGN12", choices=("MGN9", "MGN12", "MGN15"), unit="", doc="rail size"),
            "input_torque_nm": P(0.4, 0.0, 3.0, "N*m", "motor torque")},
           ["teeth_in_mesh", "belt_tension", "stroke", "carriage_moment"], "rot->lin")
def belt_linear_axis(p: dict):
    L, z = p["length"], p["z"]
    pd = z * 2.0 / math.pi
    _cid, D, B, bore = bearing_for(5)
    t = B
    k = Kit("frame")
    c = L - 40
    k.plate("frame", L + 20, 60, t, [(0, 22, D + 0.1), (c, 22, D + 0.1)], origin_x=-10)
    s1, j1, z0 = k.shaft_on_bearing("drive", "frame", 0, 22, t, bore, t + 2 + 3 + 6 + 3 + 4)
    s2, j2, _ = k.shaft_on_bearing("idler", "frame", c, 22, t, bore, t + 2 + 3 + 6 + 3 + 4)
    k.part("pulley", "gt2_pulley", f"GT2 pulley {z}T", "PETG", teeth=z, width=6.0,
           bore=float(bore))
    k.on_shaft("pulley1", s1, "pulley", 3.0, z0)
    k.on_shaft("pulley2", s2, "pulley", 3.0, z0)
    k.part("belt", "timing_belt", "GT2 belt loop", "TPU 95A", pitch_d1=round(pd, 5),
           pitch_d2=round(pd, 5), center=round(c, 4), width=6.0)
    bi = k.inst("belt")
    k.fixed("belt_on_frame", "frame", fr((0, 22, 3.0)), bi, fr())
    k.couple(j2, "belt", 1.0, j1)
    k.part("rail", "linear_rail", f"{p['rail']} rail", "Steel 1018", rail=p["rail"],
           length=round(L - 20, 3))
    ri = k.inst("rail")
    k.fixed("rail_on_frame", "frame", fr((0, -10, 0)), ri, fr())
    k.part("carriage", "rail_carriage", f"{p['rail']} carriage", "Steel 1018", rail=p["rail"])
    ci = k.inst("carriage")
    half = (L - 20) / 2 - 30
    jc = k.joint("carriage_slide", "slider", "frame", fr(((L - 20) / 2, -10, 0), X), ci,
                 fr((0, 0, 0), X), limits=[-half, half],
                 design={"bearing": "MGN carriage", "retention": "end stops"})
    k.couple(jc, "rack", pd / 2 * math.pi / 180, j1)
    k.plan.contacts += [("belt", "pulley1"), ("belt", "pulley2"), ("rail", ci), ("frame", "rail")]
    k.plan.inputs.append(j1)
    k.port("input", "rot", s1, (0, 22, z0), Z, "drive pulley (NEMA17 below)")
    k.port("output", "lin", ci, ((L - 20) / 2, -10, 0), X, "carriage top (M3 x4)")
    pull = sizing.belt_pull_n(p["input_torque_nm"], pd)
    k.plan.sizing = {"mm_per_rev": round(math.pi * pd, 3), "stroke_mm": round(2 * half, 1),
                     "belt_pull_n": round(pull, 1), "carriage_force_n": round(pull, 1)}
    k.plan.transmission = {"mm_per_rev": round(math.pi * pd, 3)}
    k.plan.rom = {"carriage_slide": [-half, half]}
    k.plan.warnings.append("the belt band is drawn closed around both pulleys; clamp its two "
                           "ends to the carriage in the real build, and add endstops")
    return k.plan


def _screw(p: dict, ball: bool):
    L, d, lead = p["length"], p["d"], p["lead"]
    k = Kit("frame")
    _cid, D, B, bore = bearing_for(8 if d >= 8 else d)
    t = B
    k.part("frame", "frame_plate", "frame", "PETG", length=60.0, width=60.0, thickness=t,
           bores=[[0.0, 0.0, D + 0.1]], origin_x=-20.0)
    k.inst("frame", "frame", "frame")
    s, j, z0 = k.shaft_on_bearing("screw", "frame", 0, 0, t, bore, 4.0)
    k.part("screw", "lead_screw", f"{'ball' if ball else 'lead'} screw d{d:g} x lead {lead:g}",
           "Steel 1018" if not ball else "Steel 4140 QT", d=d, lead=lead, length=L)
    si = k.inst("screw")
    k.fixed("screw_on_shaft", s, fr((0, 0, 3.0)), si, fr())
    k.part("nut", "lead_nut", "ball nut" if ball else "lead nut", "Brass C360" if not ball
           else "Steel 4140 QT", d=d + 0.4, flange_d=d * 2.6, body_d=d * 1.3 + 2,
           length=d * 1.8, bolt_circle_d=d * 2.0)
    ni = k.inst("nut")
    lo, hi = 20.0, L - d * 1.8 - 10
    jn = k.joint("nut_slide", "slider", "frame", fr((0, 0, 1.0), Z), ni, fr((0, 0, 0), Z),
                 value=lo, limits=[lo, hi],
                 design={"bearing": "guide rods" if not ball else "linear rail",
                         "retention": "anti-rotation"})
    k.couple(jn, "screw", lead / 360.0, j, offset=lo)
    k.plan.contacts += [("screw", "nut"), (s, "screw")]
    k.plan.inputs.append(j)
    k.port("input", "rot", s, (0, 0, z0), Z, "screw end (coupler to the motor)")
    k.port("output", "lin", ni, (0, 0, lo), Z, "nut flange")
    eff = 0.9 if ball else sizing.screw_efficiency(lead, d - lead / 2 if lead < d else d * 0.8,
                                                   0.15)
    from ..sim.materials import get_material
    e = get_material("Steel 1018").e_mpa
    force = sizing.screw_force_n(p["input_torque_nm"], lead, eff)
    k.plan.sizing = {"lead_mm": lead, "efficiency": round(eff, 3), "force_n": round(force, 1),
                     "back_drivable": ball or sizing.screw_backdrivable(lead, d, 0.15),
                     "buckling_n": round(sizing.euler_buckling_n(e, d * 0.8, L, 0.7), 0),
                     "critical_rpm": round(sizing.screw_critical_rpm(d * 0.8, L), 0),
                     "stroke_mm": round(hi - lo, 1)}
    k.plan.transmission = {"mm_per_rev": lead}
    k.plan.rom = {"nut_slide": [lo, hi]}
    if k.plan.sizing["back_drivable"]:
        k.plan.warnings.append("back-drivable: a vertical axis needs a brake or a holding "
                               "motor")
    k.plan.warnings.append("the nut must not turn: guide it (rods or a rail)")
    return k.plan


_SCREW = {"length": P(200.0, 50.0, 1000.0, "mm", "screw length"),
          "d": P(8.0, 6.0, 20.0, "mm", "diameter"), "lead": P(8.0, 1.0, 40.0, "mm", "lead per rev"),
          "input_torque_nm": P(0.3, 0.0, 10.0, "N*m", "motor torque")}


@mechanism("lead_screw_axis", "Lead screw axis (Tr8)", "linear",
           "A trapezoidal lead screw on a thrust bearing with a brass nut: strong, slow, "
           "self-locking at fine leads.", ["linear motion", "convert rotation to linear",
                                           "lift", "z axis", "self locking", "precise slow"],
           _SCREW, ["buckling", "critical_speed", "back_drive", "efficiency"], "rot->lin")
def lead_screw_axis(p: dict):
    return _screw(p, ball=False)


@mechanism("ball_screw_axis", "Ball screw axis (SFU1204/1605)", "linear",
           "A ball screw: ~90% efficient and back-drivable (needs a brake vertically).",
           ["linear motion", "convert rotation to linear", "high efficiency", "precise"],
           {**_SCREW, "d": P(12.0, 12.0, 20.0, "mm", "diameter"),
            "lead": P(4.0, 2.0, 10.0, "mm", "lead")},
           ["buckling", "critical_speed", "back_drive"], "rot->lin")
def ball_screw_axis(p: dict):
    return _screw(p, ball=True)


@mechanism("linear_rail_carriage", "Linear rail and carriage (MGN)", "linear",
           "An MGN miniature rail with one or two carriages: a precise slide for moment loads.",
           ["linear guide", "slide", "linear motion", "support a carriage"],
           {"rail": P("MGN12", choices=("MGN9", "MGN12", "MGN15"), unit="", doc="size"),
            "length": P(200.0, 60.0, 1500.0, "mm", "rail"),
            "carriages": P(1, 1, 2, "", "carriages (2 for moment loads)")},
           ["carriage_moment", "stroke"], "lin->lin")
def linear_rail_carriage(p: dict):
    L = p["length"]
    k = Kit("rail")
    k.part("rail", "linear_rail", f"{p['rail']} rail", "Steel 1018", rail=p["rail"], length=L)
    k.inst("rail", "rail", "rail (grounded)")
    k.part("carriage", "rail_carriage", f"{p['rail']} carriage", "Steel 1018", rail=p["rail"])
    half = L / 2 - 30 * p["carriages"]
    ci = k.inst("carriage")
    jc = k.joint("slide", "slider", "rail", fr((L / 2, 0, 0), X), ci, fr((0, 0, 0), X),
                 limits=[-half, half], design={"bearing": "MGN carriage",
                                               "retention": "end stops"})
    k.plan.inputs.append(jc)
    k.plan.contacts.append(("rail", "carriage"))
    if p["carriages"] == 2:
        c2 = k.inst("carriage_2", "carriage", "carriage 2")
        k.fixed("carriage_pair", ci, fr((48.0, 0, 0)), c2, fr())
        k.plan.contacts.append(("rail", c2))
    k.port("output", "lin", ci, (L / 2, 0, 0), X, "carriage top")
    k.port("mount", "mount", "rail", (0, 0, 0), (0, 0, -1), "rail holes")
    k.plan.dof_intended = 1
    k.plan.rom = {"slide": [-half, half]}
    k.plan.sizing = {"stroke_mm": round(2 * half, 1)}
    k.plan.notes = "load ratings: see the rail maker's C / M_x / M_y / M_z for the size"
    return k.plan


@mechanism("vslot_wheel_carriage", "V-slot wheel carriage on 2020 extrusion", "linear",
           "A plate on four V-wheels rolling along an aluminium extrusion (OpenBuilds style).",
           ["linear guide", "slide", "gantry", "cheap linear motion"],
           {"length": P(300.0, 100.0, 1500.0, "mm", "extrusion length"),
            "wheel_d": P(24.0, 15.0, 30.0, "mm", "wheel OD")}, ["preload", "stroke"], "lin->lin")
def vslot_wheel_carriage(p: dict):
    L, wd = p["length"], p["wheel_d"]
    k = Kit("rail")
    k.part("rail", "extrusion", "2020 extrusion", "Al 6061-T6", size=20.0, length=L)
    k.inst("rail", "rail", "extrusion (grounded)")
    k.part("plate", "frame_plate", "carriage plate", "Al 6061-T6", length=70.0, width=20 + 2 * wd,
           thickness=5.0, bores=[[sx, sy, 5.0] for sx in (-15.0, 35.0)
                                 for sy in (-(10 + wd / 2), 10 + wd / 2)], origin_x=-25.0)
    pi_ = k.inst("plate")
    half = L / 2 - 40
    jc = k.joint("slide", "slider", "rail", fr((L / 2, 0, 10.0 + 6.0 + 5.0), X), pi_,
                 fr((10.0, 0, 0), X), limits=[-half, half],
                 design={"bearing": "V-wheels (625 bearings)", "retention": "eccentric spacers"})
    k.part("wheel", "wheel", "V-wheel", "POM", d=wd, width=10.0, bore=5.0, hub_d=10.0, tread=3.0)
    for i, (sx, sy) in enumerate([(-15.0, -(10 + wd / 2)), (-15.0, 10 + wd / 2),
                                  (35.0, -(10 + wd / 2)), (35.0, 10 + wd / 2)]):
        wi = k.inst(f"wheel_{i}", "wheel", f"wheel {i + 1}")
        k.joint(f"wheel_{i}_rev", "revolute", pi_, fr((sx, sy, -5.0 - 1.0), (0, 0, -1)), wi, fr(),
                design={"bearing": "bearing_625_deep_groove", "retention": "M5 bolt"})
        k.plan.contacts += [("rail", wi), (pi_, wi)]
    k.plan.inputs.append(jc)
    k.port("output", "lin", pi_, (L / 2, 0, 21.0), X, "carriage plate")
    k.plan.rom = {"slide": [-half, half]}
    k.plan.dof_intended = 5             # the slide + four free-spinning wheels
    k.plan.warnings.append("the wheels spin free in this model (rolling is not coupled); set the "
                           "preload with eccentric spacers on one side")
    return k.plan


@mechanism("pillow_block_shaft", "Shaft in two pillow blocks", "support",
           "A horizontal shaft carried by two bearing blocks with snap-ring retention: the base of "
           "any rotating axle.", ["support a shaft", "axle", "bearing housing", "rotating joint"],
           {"shaft_d": P(8, 5, 20, "mm", "shaft"), "span": P(60.0, 20.0, 400.0, "mm", "block spacing"),
            "center_height": P(20.0, 12.0, 80.0, "mm", "axis height"),
            "radial_load_n": P(50.0, 0.0, 5000.0, "N", "load at mid-span"),
            "rpm": P(60.0, 1.0, 10000.0, "rpm", "speed for bearing life")},
           ["bearing_l10", "shaft_stress", "retention"], "rot->rot")
def pillow_block_shaft(p: dict):
    d, span, h = p["shaft_d"], p["span"], p["center_height"]
    cid, D, B, bore = bearing_for(d)
    k = Kit("base")
    k.part("base", "frame_plate", "base", "PETG", length=span + 60, width=50.0, thickness=5.0,
           bores=[], origin_x=-span / 2 - 20)
    k.inst("base", "base", "base")
    k.part("block", "bearing_block", "pillow block", "PETG", bearing_od=D, bearing_width=B,
           shaft_d=float(bore), center_height=h, base_w=40.0)
    k.part(cid, catalog=cid, label="bearing", material=None)
    for side, x in (("a", -span / 2), ("b", span / 2)):
        bi = k.inst(f"block_{side}", "block", f"pillow block {side}")
        k.fixed(f"block_{side}_on_base", "base", fr((x, 0, h), X), bi, fr((0, 0, h), Y))
        bb = k.inst(f"bearing_{side}", cid, f"bearing {side}")
        k.fixed(f"bearing_{side}_seat", bi, fr((0, -B / 2 - 0.75, h), Y), bb, fr())
        k.plan.contacts.append((bi, bb))
    k.part("shaft", "shaft", "axle", "Steel 1018", d=float(bore), length=span + 30,
           grooves=[15.0 - B / 2 - 2.5, span + 15.0 + B / 2 + 2.5])
    si = k.inst("shaft")
    j = k.joint("axle_rev", "revolute", "base", fr((-span / 2 - 15, 0, h), X), si, fr(),
                design={"bearing": cid, "shaft": {"d_mm": float(bore), "material": "Steel 1018"},
                        "retention": "snap ring", "load_n": p["radial_load_n"]})
    k.plan.contacts += [("bearing_a", si), ("bearing_b", si)]
    k.plan.inputs.append(j)
    k.port("input", "rot", si, (-span / 2 - 15, 0, h), X, "shaft end")
    k.port("output", "rot", si, (span / 2 + 15, 0, h), X, "shaft end")
    from ..catalog.builtin import builtin_by_id
    c = next((s.get("value") for s in builtin_by_id().get(cid, {}).get("specs", [])
              if s.get("key") in ("dynamic_load", "C", "dynamic_load_rating")), None)
    load = p["radial_load_n"] / 2
    m_mid = p["radial_load_n"] * span / 4 / 1000
    k.plan.sizing = {"bearing": cid, "bearing_load_n": load,
                     "shaft": sizing.shaft_stress_mpa(0.0, m_mid, bore, kt=2.0)}
    if isinstance(c, (int, float)):
        cn = float(c) * (1000.0 if c < 100 else 1.0)
        k.plan.sizing["bearing_l10_h"] = round(sizing.bearing_l10_hours(cn, load, p["rpm"]), 0)
    return k.plan


@mechanism("stepper_coupler_shaft", "NEMA stepper, mount, coupler and output shaft", "motor",
           "A stepper on a printed mount plate, a rigid coupler to an output shaft that runs in "
           "its own bearing (the motor never carries the side load).",
           ["motor mount", "attach a motor", "drive a shaft", "actuator", "stepper"],
           {"frame": P("NEMA17", choices=("NEMA17", "NEMA23"), unit="", doc="motor frame"),
            "shaft_d": P(8, 5, 12, "mm", "output shaft"),
            "slots": P(0.0, 0.0, 20.0, "mm", "slot length for belt tensioning (0 = holes)")},
           ["motor_torque", "coupler", "side_load"], "rot->rot")
def stepper_coupler_shaft(p: dict):
    frame, d = p["frame"], p["shaft_d"]
    item = "nema17_stepper" if frame == "NEMA17" else None
    k = Kit("mount")
    k.part("mount", "motor_mount", f"{frame} mount", "PETG", frame=frame, plate=5.0,
           slots=p["slots"])
    k.inst("mount", "mount", "motor mount (grounded)")
    if item:
        k.part("motor", catalog=item, label="NEMA17 stepper", material=None)
    else:
        k.part("motor", "stepper_motor", f"{frame} stepper", "Steel 1018", frame=frame)
    mi = k.inst("motor")
    k.fixed("motor_on_mount", "mount", fr((0, 0, 0), (0, 0, -1)), mi, fr((0, 0, 0), (0, 0, -1)))
    cid, D, B, bore = bearing_for(d)
    shaft_m = 5.0 if frame == "NEMA17" else 8.0
    k.part("coupler", "coupler_rigid", f"coupler {shaft_m:g}-{bore:g}", "Al 6061-T6",
           d1=shaft_m, d2=float(bore), length=25.0, od=max(18.0, bore + 10))
    k.part("shaft", "shaft", "output shaft", "Steel 1018", d=float(bore), length=60.0)
    ci = k.inst("coupler")
    jm = k.joint("motor_rev", "revolute", "mount", fr((0, 0, 5.0 + 6.0)), ci, fr(),
                 design={"bearing": "motor bearings + " + cid, "actuator": {
                     "item_id": item or frame.lower() + "_stepper", "ratio": 1},
                     "retention": "set screws"})
    si = k.inst("shaft")
    k.fixed("shaft_in_coupler", ci, fr((0, 0, 12.5)), si, fr())
    # the output bearing in a block above the coupler, so the motor takes no side load
    k.part("block", "frame_plate", "bearing plate", "PETG", length=50.0, width=50.0,
           thickness=B, bores=[[0.0, 0.0, D + 0.1]], origin_x=-15.0)
    bi = k.inst("block", "block", "bearing plate")
    k.fixed("plate_on_mount", "mount", fr((0, 0, 5.0 + 6.0 + 25.0 + 4.0 + B)), bi, fr())
    k.part(cid, catalog=cid, label="bearing", material=None)
    bb = k.inst("bearing", cid, "output bearing")
    k.fixed("bearing_seat", bi, fr((0, 0, -B)), bb, fr())
    k.plan.contacts += [("motor", "coupler"), ("coupler", "shaft"), ("bearing", "shaft"),
                        ("block", "bearing"), ("mount", "motor")]
    k.plan.inputs.append(jm)
    k.plan.warnings.append("add standoffs between the mount and the bearing plate (here the "
                           "plate is fixed to the mount directly)")
    k.port("output", "rot", si, (0, 0, 5.0 + 6.0 + 12.5 + 60.0), Z, "output shaft end")
    k.port("mount", "mount", "mount", (0, 0, 0), (0, 0, -1))
    from ..catalog.builtin import builtin_by_id
    perf = (builtin_by_id().get(item or "", {}) or {}).get("perf") or {}
    k.plan.sizing = {"holding_torque_nm": perf.get("stall_torque_nm"),
                     "motor": item or frame}
    return k.plan


@mechanism("wheel_gearmotor", "Wheel on a gearmotor", "mobility",
           "A drive wheel on a 37D-class metal gearmotor in a bracket: a rover or robot base "
           "wheel.", ["drive wheel", "rover", "mobile robot", "wheel"],
           {"wheel_d": P(80.0, 30.0, 200.0, "mm", "wheel"), "width": P(25.0, 10.0, 60.0, "mm", "tyre"),
            "motor": P("gearmotor_37d_class", choices=("gearmotor_25d_class",
                                                       "gearmotor_37d_class"), unit="",
                       doc="catalog gearmotor"),
            "robot_mass_kg": P(3.0, 0.1, 50.0, "kg", "for traction"),
            "wheels_driven": P(2, 1, 6, "", "driven wheels")},
           ["traction", "motor_torque"], "rot->rot")
def wheel_gearmotor(p: dict):
    k = Kit("bracket")
    big = p["motor"] == "gearmotor_37d_class"
    md, ml = (37.0, 65.0) if big else (25.0, 60.0)
    k.part("bracket", "servo_bracket", "motor bracket", "PETG", servo_w=md, servo_h=md * 1.6,
           wall=4.0, length=ml, horn_d=md)
    k.inst("bracket", "bracket", "bracket (grounded)")
    k.part("motor", catalog=p["motor"], label=p["motor"].replace("_", " "), material=None)
    mi = k.inst("motor")
    axis_z = md * 1.6 * 0.6
    k.fixed("motor_in_bracket", "bracket", fr((0, -md / 2 - 6, axis_z), (0, 1, 0)), mi,
            fr((0, 0, 0)))
    k.part("wheel", "wheel", "wheel", "TPU 95A", d=p["wheel_d"], width=p["width"], bore=6.0,
           hub_d=20.0)
    wi = k.inst("wheel")
    j = k.joint("axle", "revolute", "bracket", fr((0, md / 2 + 8, axis_z), (0, 1, 0)), wi, fr(),
                design={"bearing": "motor output bearing", "retention": "set screw",
                        "actuator": {"item_id": p["motor"], "ratio": 1}})
    k.plan.contacts += [("bracket", "motor"), ("motor", "wheel")]
    k.plan.inputs.append(j)
    k.port("output", "rot", wi, (0, md / 2 + 8, axis_z), Y, "wheel")
    k.port("mount", "mount", "bracket", (0, 0, 0), (0, 0, -1), "bracket base")
    r = p["wheel_d"] / 2 / 1000
    n = p["robot_mass_kg"] * 9.81 / max(1, p["wheels_driven"])
    k.plan.sizing = {"torque_to_slip_nm": round(0.8 * n * r, 3), "mu": 0.8,
                     "speed_per_rpm_mm_s": round(math.pi * p["wheel_d"] / 60, 2)}
    k.plan.notes = "traction torque tau = mu N r; pick a ratio whose stall torque exceeds it"
    return k.plan


@mechanism("prop_motor_mount", "Prop motor mount (multirotor arm)", "mobility",
           "A BLDC motor on an arm-end mount with its swept prop disc.",
           ["drone", "multirotor", "propeller", "thrust"],
           {"motor": P("motor_2207_class", choices=("motor_1404_class", "motor_2207_class",
                                                   "motor_2306_class", "motor_2806_5_class"),
                       unit="", doc="catalog motor"),
            "prop": P("prop_5in_class", choices=("prop_3in_class", "prop_5in_class",
                                                 "prop_7in_class"), unit="", doc="catalog prop"),
            "arm_l": P(80.0, 30.0, 300.0, "mm", "arm length")},
           ["thrust", "prop_clearance"], "rot->rot")
def prop_motor_mount(p: dict):
    from ..catalog.builtin import builtin_by_id
    motor = builtin_by_id()[p["motor"]]
    sh = motor["shape"]
    pat = float(sh["pattern"][0])
    k = Kit("mount")
    k.part("mount", "prop_mount", "arm end mount", "PA-CF", pattern=pat,
           motor_d=float(sh["bell_d"]), arm_l=p["arm_l"])
    k.inst("mount", "mount", "arm mount (grounded)")
    k.part("motor", catalog=p["motor"], label=motor["name"], material=None)
    mi = k.inst("motor")
    k.fixed("motor_on_mount", "mount", fr(), mi, fr())
    k.part("prop", catalog=p["prop"], label=p["prop"].replace("_", " "), material=None)
    pi_ = k.inst("prop")
    j = k.joint("prop_rev", "revolute", mi, fr((0, 0, float(sh["height"]) + 1.0)), pi_, fr(),
                design={"bearing": "motor bearings", "retention": "prop nut",
                        "actuator": {"item_id": p["motor"], "ratio": 1}})
    k.plan.inputs.append(j)
    k.plan.contacts += [("mount", "motor"), ("motor", "prop")]
    k.port("mount", "mount", "mount", (-p["arm_l"], 0, 0), (-1, 0, 0), "arm root")
    thrust = next((s["value"] for s in motor["specs"] if s["key"] == "max_thrust"), None)
    k.plan.sizing = {"max_thrust_n": thrust}
    return k.plan


@mechanism("bevel_differential", "Bevel differential", "gear",
           "Open bevel differential: a carrier with two spider gears between two side gears; "
           "the carrier speed is the mean of the wheels'.",
           ["differential", "rover", "split torque", "two outputs"],
           {"module": P(1.5, 1.0, 3.0, "mm", "module"), "z_side": P(20, 12, 40, "teeth", "side gears"),
            "z_spider": P(12, 10, 30, "teeth", "spider gears"),
            "width": P(6.0, 3.0, 12.0, "mm", "face")}, ["interference_sweep"], "rot->rot")
def bevel_differential(p: dict):
    m, zs, zp, w = p["module"], p["z_side"], p["z_spider"], p["width"]
    k = Kit("housing")
    k.part("housing", "frame_plate", "housing base", "PETG", length=m * zs + 80, width=60.0,
           thickness=5.0, bores=[], origin_x=-(m * zs + 80) / 2 + 10)
    k.inst("housing", "housing", "housing")
    hz = m * zs / 2 + 15
    rc = m * zp / 2 + 3
    # carrier = a cross shaft for the spiders, turning about X
    k.part("carrier", "cross_spider", "carrier cross", "Steel 1018", pin_d=6.0,
           span=round(2 * (m * zs / 2) + 4, 3), core=10.0)
    ci = k.inst("carrier")
    jc = k.joint("carrier_rev", "revolute", "housing", fr((0, 0, hz), X), ci, fr((0, 0, 0), X),
                 design={"bearing": "bearing_6000_deep_groove", "retention": "snap ring"})
    # side gears: member 2 of (spider, side) pairs on +-X; spiders: member 1 on +-Y
    k.part("side", "gear_bevel", f"side gear {zs}T", "POM", module=m, teeth=zp, mate_teeth=zs,
           width=w, member=2, bore=8.0)
    k.part("spider", "gear_bevel", f"spider {zp}T", "POM", module=m, teeth=zp, mate_teeth=zs,
           width=w, member=1, bore=6.2)
    apex = m * zp / 2
    jl = k.joint("left_rev", "revolute", ci, fr((-apex, 0, 0), X), k.inst("side_left", "side",
                 "left side gear"), fr(), design={"bearing": "bushing", "retention": "carrier"})
    jr = k.joint("right_rev", "revolute", ci, fr((apex, 0, 0), (-1, 0, 0)),
                 k.inst("side_right", "side", "right side gear"), fr(),
                 design={"bearing": "bushing", "retention": "carrier"})
    k.couple(jr, "gear", 1.0, jl)
    rs = m * zs / 2
    for i, sy in enumerate((1.0, -1.0)):
        sp = k.inst(f"spider_{i}", "spider", f"spider {i + 1}")
        js = k.joint(f"spider_{i}_rev", "revolute", ci, fr((0, sy * rs, 0), (0, -sy, 0)), sp,
                     fr((0, 0, 0)), design=pin_design(6.0, bearing="bushing",
                                                      retention="cross pin"))
        k.couple(js, "gear", sy * zs / zp, jl)
        k.plan.contacts += [("side_left", sp), ("side_right", sp), (ci, sp)]
    k.plan.inputs += [jc, jl]
    k.plan.dof_intended = 2
    k.port("input", "rot", ci, (0, 0, hz), X, "carrier (ring gear bolts here)")
    k.port("left", "rot", "side_left", (-apex, 0, hz), X, "left output")
    k.port("right", "rot", "side_right", (apex, 0, hz), X, "right output")
    k.plan.transmission = {"carrier": "w_c = (w_L + w_R) / 2"}
    k.plan.warnings.append("the spider and side gear phases are not solved jointly: check the "
                           "interference sweep and shift a spider by half a tooth if they clash")
    k.plan.notes = ("2 DOF: the carrier angle and the left gear's angle relative to the carrier; "
                    "the right gear turns the opposite way relative to the carrier")
    return k.plan
