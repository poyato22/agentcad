"""Hand-written reference answers for the AgentCAD-Functional checkers (checker self-tests only;
never shown to either system). Each returns {name: shape} placed in world coordinates, and
edit tasks return (before, after)."""
from __future__ import annotations

import math
from pathlib import Path

import build123d as b3
from build123d import Axis, Box, Cylinder, Pos, Rot


def write(parts: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "components").mkdir(exist_ok=True)
    for n, s in parts.items():
        b3.export_step(s, str(out / "components" / f"{n}.step"))
    shapes = list(parts.values())
    whole = shapes[0] if len(shapes) == 1 else b3.Compound(children=shapes)
    b3.export_step(whole, str(out / "result.step"))


def f1():
    block = Box(40, 40, 20) - Cylinder(10.0075 / 2, 20)
    return {"block": block, "pin": Cylinder(9.9905 / 2, 30)}


def _flange():
    f = Cylinder(60, 12) - Cylinder(25, 12)
    for i in range(6):
        a = math.radians(60 * i)
        f -= Pos(45 * math.cos(a), 45 * math.sin(a), 0) * Cylinder(4.5, 12)
    return f


def f2():
    return {"flange_a": Pos(0, 0, 6) * _flange(), "flange_b": Pos(0, 0, -6) * _flange()}


def f3():
    box = Pos(0, 0, 15) * Box(60, 40, 30) - Pos(0, 0, 16) * Box(56, 36, 28)
    lid = Pos(0, 0, 31) * Box(60, 40, 2) + (Pos(0, 0, 28) * Box(55.6, 35.6, 4)
                                            - Pos(0, 0, 28) * Box(53.2, 33.2, 4))
    return {"box": box, "lid": lid}


def f4():
    shaft = Cylinder(10, 80)
    slot = b3.extrude(b3.SlotOverall(30, 6), 10)
    slot = Pos(0, 0, 0) * Rot(0, 90, 0) * slot          # slot along Z, depth along +X
    shaft -= Pos(6.5, 0, 0) * slot
    key = Pos(6.5, 0, 0) * Rot(0, 90, 0) * b3.extrude(b3.SlotOverall(30, 6), 6)
    return {"shaft": shaft, "key": key}


def _link(L, w=12, t=5):
    bar = b3.extrude(b3.SlotCenterToCenter(L, w), t)
    return bar - Pos(-L / 2, 0, 0) * Cylinder(3, 3 * t) - Pos(L / 2, 0, 0) * Cylinder(3, 3 * t)


def _place(shape, p0, p1, z):
    """Link with holes at its local (-L/2,0),(L/2,0) moved so they sit on p0, p1 at height z."""
    ang = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))
    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, z)
    return Pos(*mid) * Rot(0, 0, ang) * shape


def k1():
    o2, o4 = (0.0, 0.0), (100.0, 0.0)
    th = math.radians(60)
    a = (40 * math.cos(th), 40 * math.sin(th))
    # B: circle(A,120) ∩ circle(O4,80), upper branch
    dx, dy = o4[0] - a[0], o4[1] - a[1]
    L = math.hypot(dx, dy)
    aa = (120 ** 2 - 80 ** 2 + L ** 2) / (2 * L)
    h = math.sqrt(120 ** 2 - aa ** 2)
    ex, ey = dx / L, dy / L
    b = (a[0] + aa * ex - h * ey, a[1] + aa * ey + h * ex)
    if b[1] < 0:
        b = (a[0] + aa * ex + h * ey, a[1] + aa * ey - h * ex)
    return {"ground": _place(_link(100, 16, 5), o2, o4, -2.5),
            "crank": _place(_link(40), o2, a, 7.5),
            "coupler": _place(_link(120), a, b, 17.5),
            "rocker": _place(_link(80), o4, b, 2.5 + 5)}


def k4():
    # pin along X at y=0,z=0; leaf_a toward -Y, leaf_b toward +Y, both in the z∈[-1.5,1.5] plane
    def leaf(sign, knuckles):
        body = Pos(0, sign * 18, 0) * Box(50, 30, 3)
        body -= Pos(0, 0, 0) * Box(52, 6.2, 4)            # clear the knuckle zone
        for x0 in knuckles:
            body += Pos(x0, 0, 0) * Rot(0, 90, 0) * Cylinder(3, 10)
            body += Pos(x0, sign * 1.5, 0) * Box(10, 3, 3)
        return body - Rot(0, 90, 0) * Cylinder(2.1, 60)
    la = leaf(-1, [-20, 0, 20])
    lb = leaf(1, [-10, 10])
    # stop: a tab on leaf_a's knuckle side that leaf_b hits past ~100°
    la += Pos(0, -5, 1.5 + 9) * Box(50, 2, 18)       # a fence leaf_b meets past ~100°
    return {"leaf_a": la, "leaf_b": lb, "pin": Rot(0, 90, 0) * Cylinder(2, 50)}


def s1():
    return {"arm": Pos(75, 0, 0) * Box(150, 10, 30)}


def s3():
    t = 10
    link = Pos(-50, 0, 0) * Cylinder(18, t) + Pos(50, 0, 0) * Cylinder(18, t) + Box(100, 16, t)
    link -= Pos(-50, 0, 0) * Cylinder(5, 30) + Pos(50, 0, 0) * Cylinder(5, 30)
    return {"link": Pos(50, 0, t / 2) * link}


def s4():
    return {"shaft": Pos(0, 0, 100) * (Cylinder(15, 200) - Cylinder(8, 200))}


def p1():
    p = Pos(0, 0, 6) * Box(100, 60, 12)
    pocket = b3.extrude(b3.RectangleRounded(50, 30, 3), 6)
    p -= Pos(0, 0, 6) * pocket
    for sx in (-1, 1):
        for sy in (-1, 1):
            p -= Pos(sx * 42, sy * 22, 6) * Cylinder(3.3, 12)
    return {"plate": p}


def p3():
    prof = b3.Polyline((0, 0), (40, 0), (40, 2), (4, 2), close=False)
    with b3.BuildSketch() as s:
        with b3.BuildLine():
            b3.Polyline((40, 0), (4, 0))
            b3.RadiusArc((4, 0), (0, 4), 4)
            b3.Polyline((0, 4), (0, 30), (2, 30), (2, 4))
            b3.RadiusArc((2, 4), (4, 2), -2)
            b3.Polyline((4, 2), (40, 2), (40, 0))
        b3.make_face()
    part = b3.extrude(s.sketch, 50)
    for z in (15, 35):
        part -= Pos(24, 1, z) * Rot(90, 0, 0) * Cylinder(2.75, 10)
        part -= Pos(1, 19, z) * Rot(0, 90, 0) * Cylinder(2.75, 10)
    del prof
    return {"bracket": part}


def p4():
    d = math.tan(math.radians(1.5))
    outer = b3.loft([b3.Plane.XY * b3.Rectangle(80 - 2 * 30 * d, 50 - 2 * 30 * d),
                     b3.Plane.XY.offset(30) * b3.Rectangle(80, 50)])
    inner = b3.loft([b3.Plane.XY.offset(2) * b3.Rectangle(76 - 2 * 30 * d, 46 - 2 * 30 * d),
                     b3.Plane.XY.offset(30) * b3.Rectangle(76 - 2 * 2 * d, 46 - 2 * 2 * d)])
    return {"box": outer - inner}


def e1():
    def plate(r, off):
        p = Box(120, 80, 8) - Cylinder(15, 8)
        for sx in (-1, 1):
            for sy in (-1, 1):
                p -= Pos(sx * (60 - off), sy * (40 - off), 0) * Cylinder(r, 8)
        return p
    return {"plate": plate(3, 10)}, {"plate": plate(4, 12)}


def e4():
    def bar(edit):
        b = Box(150, 30, 6)
        if edit:
            b = b3.fillet(b.edges().filter_by(Axis.Z), 5)
        for x in (-50, 50):
            b -= Pos(x, 0, 0) * Cylinder(4, 6)
        if edit:
            b -= b3.extrude(b3.SlotOverall(20, 8), 3, both=True)
        else:
            b -= Cylinder(4, 6)
        return b
    return {"bar": bar(False)}, {"bar": bar(True)}


def e3():
    def encl(cut):
        b = Pos(0, 0, 20) * Box(100, 60, 40) - Pos(0, 0, 21.25) * Box(95, 55, 37.5)
        for sx in (-1, 1):
            for sy in (-1, 1):
                b += Pos(sx * 44, sy * 24, 2.5 + 16.25) * (Cylinder(3.5, 32.5) - Cylinder(1.25, 32.5))
        if cut:
            b -= Pos(48.75, 0, 2.5 + 8 + 3) * Box(2.5, 12, 6)
        return b
    return {"enclosure": encl(False)}, {"enclosure": encl(True)}


def e2():
    def shaft(r):
        body = Pos(0, 0, 50) * Cylinder(r, 100)
        body = b3.chamfer(body.edges().filter_by(b3.GeomType.CIRCLE).sort_by(Axis.Z)[0], 1)
        sp = Pos(0, 0, 107.5) * Cylinder(6, 15)
        sp = b3.chamfer(sp.edges().filter_by(b3.GeomType.CIRCLE).sort_by(Axis.Z)[-1], 1)
        return body + sp
    return {"shaft": shaft(10)}, {"shaft": shaft(12.5)}


def involute_gear(m, z, width, bore, pa=20.0, backlash=0.05, n=12):
    """A spur gear (standard involute, unshifted) centred on the origin, axis Z."""
    rp = m * z / 2
    rb = rp * math.cos(math.radians(pa))
    ra, rf = rp + m, rp - 1.25 * m

    def inv(a):
        return math.tan(a) - a
    # half tooth angle at the pitch circle, minus backlash
    half = (math.pi / (2 * z)) - backlash / (2 * rp) + inv(math.radians(pa))
    pts_r = []
    r0 = max(rb, rf)
    for i in range(n + 1):
        r = r0 + (ra - r0) * i / n
        a = math.acos(min(1.0, rb / r))
        th = half - inv(a)
        pts_r.append((r, th))
    pts = []
    for k in range(z):
        c = 2 * math.pi * k / z
        left = [(r * math.cos(c - th), r * math.sin(c - th)) for r, th in pts_r]
        right = [(r * math.cos(c + th), r * math.sin(c + th)) for r, th in reversed(pts_r)]
        root_a = c + math.pi / z
        pts += left + right
        pts.append((rf * math.cos(root_a - 0.25 * math.pi / z), rf * math.sin(root_a - 0.25 * math.pi / z)))
        pts.append((rf * math.cos(root_a + 0.25 * math.pi / z), rf * math.sin(root_a + 0.25 * math.pi / z)))
    # rotate so the last root points come before tooth 0 (keep polygon order)
    face = b3.Face(b3.Wire.make_polygon([b3.Vector(x, y, 0) for x, y in pts], close=True))
    g = b3.extrude(face, width) - Cylinder(bore / 2, 3 * width)
    return Pos(0, 0, -width / 2) * g


def k2():
    p = involute_gear(2, 20, 10, 8)
    g = involute_gear(2, 40, 10, 8)
    g = Pos(60, 0, 0) * Rot(0, 0, 180 / 40) * g    # a gap of the gear faces the pinion tooth
    return {"pinion": p, "gear": g}


def rack(m, length, width, height, backlash=0.05):
    p = math.pi * m
    pa = math.radians(20)
    pts = [(0, 0)]
    x = 0.0
    n = int(length / p) + 1
    for k in range(n + 1):
        xc = k * p                                # tooth centre on the pitch line (y = 0)
        hw_tip = p / 4 - m * math.tan(pa) - backlash / 2
        hw_root = p / 4 + 1.25 * m * math.tan(pa) - backlash / 2
        for (xx, yy) in ((xc - hw_root, -1.25 * m), (xc - hw_tip, m), (xc + hw_tip, m),
                         (xc + hw_root, -1.25 * m)):
            pts.append((xx, yy))
    poly = [(x0, y0) for x0, y0 in pts[1:]]
    base = -height + m
    outline = [(poly[0][0], base)] + poly + [(poly[-1][0], base)]
    face = b3.Face(b3.Wire.make_polygon([b3.Vector(x0, y0, 0) for x0, y0 in outline], close=True))
    r = b3.extrude(face, width)
    zc = r.center().Z
    lo = poly[0][0]
    r = r & Pos(lo + 2 + length / 2, 0, zc) * Box(length, 100, width)
    return Pos(0, 0, -zc) * r


def k3():
    p = involute_gear(1.5, 18, 12, 8)
    p = Rot(0, 0, -90) * p                       # a tooth pointing at -Y (toward the rack)
    pitch = math.pi * 1.5
    k = round(75 / pitch - 0.5)
    dx = 75 - (k + 0.5) * pitch                  # rack tooth centres at -75 + k·p: a gap at x=0
    r = Pos(-75 + dx, -13.5, 0) * rack(1.5, 150, 12, 15)
    return {"pinion": p, "rack": r}


def s2():
    top = Pos(100, 0, -2) * Box(200, 20, 4)
    back = Pos(2, 0, -50) * Box(4, 20, 100)
    with b3.BuildSketch(b3.Plane.XZ) as sk:
        b3.Polygon((0, 0), (195, 0), (0, -100), align=None)
    gus = b3.extrude(sk.sketch, 1.25, both=True)
    return {"bracket": top + back + gus}


def p2():
    with b3.BuildSketch(b3.Plane.XZ) as sk:
        b3.Polygon((0, 0), (90, 0), (90, 12), (78, 12), (74, 6), (60, 6),
                   (20, 6 + 40 * math.tan(math.radians(65))), (8, 100), (0, 100), align=None)
    return {"stand": b3.extrude(sk.sketch, 40, both=True)}


SINGLE = {"F1_pin_hole_fit": f1, "F2_bolted_flanges": f2, "F3_box_lid_fit": f3,
          "F4_shaft_keyway": f4, "K1_four_bar": k1, "K2_spur_gear_pair": k2,
          "K3_rack_and_pinion": k3, "K4_hinge_with_stop": k4, "S2_shelf_bracket": s2,
          "P2_fdm_phone_stand": p2,
          "S1_cantilever_arm": s1, "S3_tension_link": s3, "S4_torsion_shaft": s4,
          "P1_cnc_plate": p1, "P3_sheet_metal_bracket": p3, "P4_moulded_box": p4}
EDITS = {"E1_edit_hole_pattern": e1, "E2_edit_shaft_diameter": e2,
         "E3_edit_enclosure_cutout": e3, "E4_edit_bar_slot": e4}


def build(task_id: str, out: Path) -> None:
    if task_id in SINGLE:
        write(SINGLE[task_id](), out)
    else:
        before, after = EDITS[task_id]()
        write(before, out / "stage_0")
        write(after, out)
