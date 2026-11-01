"""Drivetrain and mechanism geometry for part scripts (Plan 10 E21/E28, research 2026-11-01 §1).

Loaded by mises_shapes.py and re-exported there, so every name in __all__ is callable from a
part script as `ms.<name>(...)`. Self-contained like mises_shapes: stdlib, build123d, OCP,
bd_warehouse (Apache-2.0: rack, worm, bearings, steppers) and py_gearworks (Apache-2.0, pinned
0.0.24: spur, helical, ring, bevel gears) only, never mises imports. The host reads this file
with `ast` (recipe_doc), so the first docstring line of each name is its index entry.

Conventions: millimetres. A rotating part (gear, pulley, shaft, link) has its axis on local +Z
through the origin and spans z = 0 .. width, so a revolute joint frame is origin [0, 0, 0],
axis [0, 0, 1]. A link runs along +X: its first bore at the origin, its second at (length, 0).
Parts that come out of a meshing set built in place (bevel pairs, worm pairs) are returned in
their own joint frame (`frame=(origin, axis)`), using the same frame convention as the assembly
solve, so placing them through that joint frame at value 0 reproduces the mesh exactly.
Every recipe checks its result and raises ValueError with the reason.
"""
from __future__ import annotations

import math
from typing import Sequence

import build123d as bd

__all__ = [
    # gears
    "gear_spur", "gear_ring", "gear_bevel", "gear_rack", "worm_pair_member", "cycloid_disc",
    "pin_ring",
    # belts
    "gt2_pulley", "htd_pulley", "timing_belt",
    # shafts, couplers, bearings, mounts
    "shaft", "shaft_collar", "coupler_rigid", "bearing", "bearing_block", "frame_plate",
    "motor_mount", "stepper_motor", "servo_body", "servo_bracket", "bldc_actuator",
    # linkages and joints
    "link_bar", "link_plate", "pin", "slider_block", "lead_screw", "lead_nut", "linear_rail",
    "rail_carriage", "cam_disc", "follower", "geneva_wheel", "geneva_driver", "ratchet_wheel",
    "pawl", "hinge_leaf", "ball_stud", "ball_socket", "u_yoke", "cross_spider",
    "flexure_hinge", "snap_cantilever", "gripper_jaw", "wheel", "prop_mount", "extrusion",
]

TAU = 2 * math.pi
#: plain-number helpers part scripts can call too (`ms.mesh_phase`, ...): not shape recipes
HELPERS = ["mesh_phase", "ring_phase", "belt_length", "belt_center"]


# ---------------------------------------------------------------- helpers

def _check(shape, what: str):
    """A valid, closed, positive-volume solid (or a compound of them), else ValueError."""
    shape = shape.part if hasattr(shape, "part") else shape
    if shape is None or not hasattr(shape, "wrapped") or shape.wrapped is None:
        raise ValueError(f"{what}: no shape was produced")
    if not shape.solids():
        raise ValueError(f"{what}: the result has no solid")
    vol = shape.volume
    if not (math.isfinite(vol) and vol > 0):
        raise ValueError(f"{what}: zero or negative volume ({vol})")
    if not shape.is_valid:
        fixed = shape.fix() if hasattr(shape, "fix") else shape
        if not fixed.is_valid:
            raise ValueError(f"{what}: OCCT reports the shape as invalid; check the sizes")
        shape = fixed
    return _flat(shape)


def _flat(shape):
    """A library assembly (bd_warehouse bearing, stepper: a Compound with located children)
    as a plain Compound of its solids, so moving it moves every solid exactly once."""
    if isinstance(shape, bd.Compound) and getattr(shape, "children", None):
        return bd.Compound(list(shape.solids()))
    return shape


def _pos(v: float, what: str) -> float:
    v = float(v)
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"{what} must be a positive number of mm (got {v})")
    return v


def _frame(origin: Sequence[float], axis: Sequence[float]) -> list[list[float]]:
    """The assembly solve's frame matrix (assembly/solve.py frame_matrix, same convention):
    local Z = axis, local X from the axis deterministically."""
    n = math.sqrt(sum(c * c for c in axis))
    if n < 1e-12:
        raise ValueError("frame axis is zero-length")
    z = [c / n for c in axis]
    up = [0.0, 0.0, 1.0] if abs(z[2]) < 0.9 else [1.0, 0.0, 0.0]
    x = [up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0]]
    nx = math.sqrt(sum(c * c for c in x))
    x = [c / nx for c in x]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    o = [float(c) for c in origin]
    return [[x[0], y[0], z[0], o[0]], [x[1], y[1], z[1], o[1]], [x[2], y[2], z[2], o[2]],
            [0.0, 0.0, 0.0, 1.0]]


def _location(m) -> bd.Location:
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3])
    return bd.Location(t)


def _inverse(m):
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [[r[0][0], r[0][1], r[0][2], t[0]], [r[1][0], r[1][1], r[1][2], t[1]],
            [r[2][0], r[2][1], r[2][2], t[2]], [0.0, 0.0, 0.0, 1.0]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def in_frame(shape, origin: Sequence[float], axis: Sequence[float]):
    """`shape` (in world coordinates) re-expressed so that its joint frame (origin, axis) becomes
    the part's standard frame (origin [0,0,0], axis [0,0,1]): placing the part through a joint
    whose part-side frame is that standard frame and whose other side is (origin, axis) puts it
    back exactly where it was (local = F(0, Z) . F(origin, axis)^-1 . world)."""
    m = _mul(_frame((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)), _inverse(_frame(origin, axis)))
    return shape.moved(_location(m))


def _cyl(r: float, h: float, at=(0.0, 0.0, 0.0)):
    return bd.Pos(*at) * bd.Cylinder(r, h, align=(bd.Align.CENTER, bd.Align.CENTER,
                                                  bd.Align.MIN))


def _bore(shape, d: float, h: float, z0: float = -1.0, at=(0.0, 0.0)):
    if d and d > 0:
        shape = shape - _cyl(d / 2, h + 2.0, (at[0], at[1], z0))
    return shape


def _union(*shapes):
    out = shapes[0]
    for s in shapes[1:]:
        out = out + s
    return out


def _stadium(length: float, width: float, t: float):
    """A bar from (0, 0) to (length, 0) with round ends, z = 0 .. t."""
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            with bd.Locations((length / 2, 0)):
                bd.SlotCenterToCenter(length, width)
        bd.extrude(amount=t)
    return p.part


# ---------------------------------------------------------------- gears

def mesh_phase(z1: int, phi1_deg: float, z2: int, theta_deg: float) -> float:
    """Rotation (deg) of an external gear z2, centred in direction theta from a gear z1 turned
    by phi1, so its teeth sit in z1's gaps (both gears with a tooth on +X at rotation 0)."""
    return theta_deg + 180.0 - 180.0 / z2 + (theta_deg - phi1_deg) * z1 / z2


def ring_phase(zp: int, phip_deg: float, zr: int, theta_deg: float) -> float:
    """Rotation (deg) of an internal ring zr so it meshes a planet zp centred in direction theta
    and turned by phip (py_gearworks ring: a gap on +X at rotation 0)."""
    return theta_deg + (phip_deg - theta_deg) * zp / zr


def gear_spur(module: float = 1.0, teeth: int = 20, width: float = 6.0, bore: float = 0.0,
              phase_deg: float = 0.0, helix_deg: float = 0.0, hub_d: float = 0.0,
              hub_h: float = 0.0, profile_shift: float = 0.0, backlash: float = 0.0):
    """Spur or helical involute gear (py_gearworks): axis +Z, z = 0..width, a tooth on +X.

    `phase_deg` turns it about its axis (use mesh_phase for a meshing pair). `helix_deg` != 0
    makes a helical gear (its mate takes -helix_deg). `hub_d`/`hub_h` add a hub on top; `bore`
    drills the axis. Undercut below 17 teeth is modelled (py_gearworks); keep teeth >= 12."""
    import py_gearworks as pgw
    m, w = _pos(module, "module"), _pos(width, "width")
    z = int(teeth)
    if z < 6:
        raise ValueError("a gear needs at least 6 teeth")
    kw = dict(number_of_teeth=z, module=m, height=w, angle=math.radians(phase_deg),
              profile_shift=float(profile_shift), backlash=float(backlash))
    if helix_deg:
        g = pgw.HelicalGear(helix_angle=math.radians(helix_deg), **kw)
    else:
        g = pgw.SpurGear(**kw)
    part = g.build_part()
    if hub_d and hub_h:
        part = part + _cyl(hub_d / 2, hub_h, (0, 0, w))
    part = _bore(part, bore, w + (hub_h or 0))
    return _check(part, f"gear_spur(z={z}, m={m:g})")


def gear_ring(module: float = 1.0, teeth: int = 60, width: float = 6.0, rim: float = 4.0,
              phase_deg: float = 0.0, mount_holes: int = 0, hole_d: float = 3.4):
    """Internal (ring) gear for planetary sets (py_gearworks): axis +Z, z = 0..width.

    `rim` mm of material outside the tooth roots; `mount_holes` > 0 drills that many axial
    holes on the rim's mid circle. A gap sits on +X at phase 0 (see ring_phase)."""
    import py_gearworks as pgw
    m, w = _pos(module, "module"), _pos(width, "width")
    z = int(teeth)
    if z < 20:
        raise ValueError("a ring gear needs at least 20 teeth")
    g = pgw.SpurRingGear(number_of_teeth=z, module=m, height=w, angle=math.radians(phase_deg),
                         outside_ring_coefficient=max(1.0, float(rim) / m))
    part = g.build_part()
    if mount_holes:
        rr = m * z / 2 + m * 1.2 + rim / 2
        for k in range(int(mount_holes)):
            a = TAU * (k + 0.5) / int(mount_holes)
            part = _bore(part, hole_d, w, at=(rr * math.cos(a), rr * math.sin(a)))
    return _check(part, f"gear_ring(z={z})")


def _bevel_set(module: float, z1: int, z2: int, width: float):
    import py_gearworks as pgw
    d1 = math.atan2(z1, z2)
    b1 = pgw.BevelGear(number_of_teeth=int(z1), cone_angle=2 * d1, module=module, height=width)
    b2 = pgw.BevelGear(number_of_teeth=int(z2), cone_angle=2 * (math.pi / 2 - d1),
                       module=module, height=width)
    b1.mesh_to(b2)
    return b1, b2


def gear_bevel(module: float = 1.5, teeth: int = 15, mate_teeth: int = 30, width: float = 6.0,
               member: int = 1, bore: float = 0.0):
    """One gear of a 90-degree bevel pair (py_gearworks), in its own joint frame.

    The pair's apex is shared: gear 2 (`mate_teeth`) sits at the origin on +Z; gear 1
    (`teeth`) sits at (m*z2/2, 0, m*z1/2) on axis -X. Each member is returned in its joint
    frame: member 2 frame origin [0,0,0] axis [0,0,1]; member 1 frame origin
    [m*z2/2, 0, m*z1/2] axis [-1, 0, 0]. Cone angles are always passed explicitly (the default
    gave invalid solids in the research spike)."""
    m, w = _pos(module, "module"), _pos(width, "width")
    b1, b2 = _bevel_set(m, int(teeth), int(mate_teeth), w)
    if int(member) == 2:
        part = _bore(b2.build_part(), bore, w * 2, z0=-w)
        return _check(part, "gear_bevel member 2")
    origin = (m * int(mate_teeth) / 2, 0.0, m * int(teeth) / 2)
    part = in_frame(b1.build_part(), origin, (-1.0, 0.0, 0.0))
    part = _bore(part, bore, w * 3, z0=-w)
    return _check(part, "gear_bevel member 1")


def gear_rack(module: float = 1.0, teeth: int = 30, width: float = 6.0, base: float = 4.0,
              holes: int = 0, hole_d: float = 3.4):
    """Straight rack (bd_warehouse RackGear) along X, teeth pointing +Y, pitch line on y = 0,
    z = 0..width, centred on x = 0 with a tooth gap at x = 0 (a pinion at (0, m*z/2) turned
    -90 deg meshes)."""
    from bd_warehouse.gear import RackGear
    m, w = _pos(module, "module"), _pos(width, "width")
    r = RackGear(m, int(teeth), 20, w, base_height=float(base))
    r = r.rotate(bd.Axis.X, -90).translate((0, 0, w / 2))
    if holes:
        length = math.pi * m * int(teeth)
        for k in range(int(holes)):
            x = -length / 2 + length * (k + 0.5) / int(holes)
            hole = bd.Pos(x, -m - base / 2, w / 2) * bd.Rot(90, 0, 0) * bd.Cylinder(
                hole_d / 2, base + 4 * m)
            r = r - hole
    return _check(r, "gear_rack")


def worm_pair_member(module: float = 1.0, starts: int = 1, diameter_quotient: float = 10.0,
                     teeth: int = 30, worm_length: float = 30.0, wheel_width: float = 6.0,
                     member: str = "worm", bore: float = 0.0):
    """The worm or the worm wheel of a meshing pair (bd_warehouse WormGear), in its joint frame.

    The wheel sits at the origin on +Z (frame origin [0,0,0] axis [0,0,1]); the worm's axis is
    +Y through (a, 0, 0), a = m*(q + z)/2 (frame origin [a, 0, 0] axis [0, 1, 0])."""
    from bd_warehouse.gear import WormGear
    m = _pos(module, "module")
    pair = WormGear(m, int(starts), float(diameter_quotient), int(teeth), float(worm_length),
                    float(wheel_width))
    worm, wheel = pair.children[0], pair.children[1]
    if member == "wheel":
        part = _bore(wheel, bore, wheel_width * 2, z0=-wheel_width)
        return _check(part, "worm wheel")
    a = m * (float(diameter_quotient) + int(teeth)) / 2
    part = in_frame(worm, (a, 0.0, 0.0), (0.0, 1.0, 0.0))
    part = _bore(part, bore, worm_length * 2, z0=-worm_length)
    return _check(part, "worm")


def cycloid_disc(pins: int = 12, pin_circle_d: float = 60.0, pin_d: float = 5.0,
                 eccentricity: float = 1.5, width: float = 6.0, bore: float = 22.0,
                 output_holes: int = 6, hole_circle_d: float = 34.0, output_pin_d: float = 5.0,
                 samples: int = 720):
    """Cycloidal reducer disc: pins-1 lobes (an epitrochoid offset by the pin radius), axis +Z,
    z = 0..width, centred at the origin; the reducer ratio is (pins-1):1.

    It runs around the eccentric bearing (`bore`) with its centre `eccentricity` from the pin
    ring centre. `output_holes` of d = output_pin_d + 2*eccentricity carry the output pins."""
    n = int(pins)
    if n < 6:
        raise ValueError("a cycloid needs at least 6 pins")
    R, r, e = pin_circle_d / 2, pin_d / 2, float(eccentricity)
    if not (0 < e < r):
        raise ValueError("eccentricity must be positive and below the pin radius")
    pts = []
    for k in range(int(samples)):
        t = TAU * k / samples
        psi = math.atan2(math.sin((1 - n) * t), R / (e * n) - math.cos((1 - n) * t))
        x = R * math.cos(t) - r * math.cos(t + psi) - e * math.cos(n * t)
        y = -R * math.sin(t) + r * math.sin(t + psi) + e * math.sin(n * t)
        pts.append((x, y))
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            with bd.BuildLine():
                bd.Polyline(*pts, close=True)
            bd.make_face()
        bd.extrude(amount=_pos(width, "width"))
    part = _bore(p.part, bore, width)
    hole = output_pin_d + 2 * e
    for k in range(int(output_holes)):
        a = TAU * k / output_holes
        part = _bore(part, hole, width, at=(hole_circle_d / 2 * math.cos(a),
                                            hole_circle_d / 2 * math.sin(a)))
    return _check(part, "cycloid_disc")


def pin_ring(pins: int = 12, pin_circle_d: float = 60.0, pin_d: float = 5.0,
             width: float = 6.0, housing_od: float = 80.0, base: float = 3.0):
    """The fixed pin ring of a cycloidal reducer: a housing with `pins` axial pins on the pin
    circle, axis +Z; the housing floor spans z = -base..0 and the pins z = 0..width."""
    R = pin_circle_d / 2
    if housing_od / 2 < R + pin_d:
        raise ValueError("housing_od must clear the pin circle by a pin diameter")
    wall = _cyl(housing_od / 2, width, (0, 0, 0)) - _cyl(R + pin_d * 0.2, width + 2, (0, 0, -1))
    floor = _cyl(housing_od / 2, base, (0, 0, -base)) - _cyl(R * 0.45, base + 2, (0, 0, -base - 1))
    body = wall + floor
    for k in range(int(pins)):
        a = TAU * k / pins
        body = body + _cyl(pin_d / 2, width, (R * math.cos(a), R * math.sin(a), 0))
    return _check(body, "pin_ring")


# ---------------------------------------------------------------- belts

GT2 = {"pitch": 2.0, "pld": 0.254, "groove_r": 0.555, "groove_depth": 0.75, "belt_t": 1.38}
HTD = {3: (0.381, 0.85, 1.14, 2.4), 5: (0.5715, 1.43, 2.06, 3.8), 8: (0.686, 2.25, 3.38, 5.6)}
#: closed-loop GT2 belt lengths in stock (teeth; vendor listings, research §1.3)
GT2_STOCK = (100, 110, 112, 122, 130, 140, 150, 158, 160, 166, 170, 180, 188, 200, 220, 232,
             250, 280, 300, 400)


def _pulley(n: int, width: float, bore: float, flanges: bool, hub_d: float, hub_h: float,
            pitch: float, pld: float, groove_r: float, groove_depth: float, what: str):
    if n < 10:
        raise ValueError(f"{what}: at least 10 teeth")
    w = _pos(width, "width")
    pd = n * pitch / math.pi
    od = pd - 2 * pld
    with bd.BuildPart() as p:
        bd.Cylinder(od / 2, w, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        with bd.BuildSketch(bd.Plane.XY.offset(-1)):
            with bd.PolarLocations(od / 2 + groove_r - groove_depth, n):
                bd.Circle(groove_r)
        bd.extrude(amount=w + 2, mode=bd.Mode.SUBTRACT)
    part = p.part
    if flanges:
        for z in (-1.0, w):
            part = part + _cyl(od / 2 + 1.0, 1.0, (0, 0, z))
    if hub_d and hub_h:
        part = part + _cyl(hub_d / 2, hub_h, (0, 0, w + (1.0 if flanges else 0.0)))
    part = _bore(part, bore, w + 4 + (hub_h or 0), z0=-2)
    return _check(part, what)


def gt2_pulley(teeth: int = 20, width: float = 6.0, bore: float = 5.0, flanges: bool = True,
               hub_d: float = 0.0, hub_h: float = 0.0):
    """GT2 (2 mm) timing pulley: PD = N*2/pi, OD = PD - 0.508, axis +Z, belt face z = 0..width.

    Tooth gaps follow the Gates PowerGrip GT2 2 mm profile simplified to its 0.555 mm groove
    arc (depth 0.75); flanges (1 mm, +1 mm radius) sit outside z = 0..width."""
    g = GT2
    return _pulley(int(teeth), width, bore, flanges, hub_d, hub_h, g["pitch"], g["pld"],
                   g["groove_r"], g["groove_depth"], f"gt2_pulley({int(teeth)}T)")


def htd_pulley(teeth: int = 20, pitch: int = 5, width: float = 10.0, bore: float = 8.0,
               flanges: bool = True, hub_d: float = 0.0, hub_h: float = 0.0):
    """HTD 3M/5M/8M timing pulley (approximate arc profile): PD = N*pitch/pi, OD = PD - 2*PLD
    (PLD 0.381 / 0.5715 / 0.686 mm), axis +Z, belt face z = 0..width."""
    if int(pitch) not in HTD:
        raise ValueError("HTD pitch is 3, 5 or 8 mm")
    pld, gr, gd, _t = HTD[int(pitch)]
    return _pulley(int(teeth), width, bore, flanges, hub_d, hub_h, float(pitch), pld, gr, gd,
                   f"htd_pulley({int(teeth)}T {int(pitch)}M)")


def belt_length(pd1: float, pd2: float, center: float) -> float:
    """Open-belt pitch length: 2*sqrt(C^2 - (r2-r1)^2) + pi*(r1+r2) + 2*(r2-r1)*asin((r2-r1)/C)."""
    r1, r2 = pd1 / 2, pd2 / 2
    if center <= abs(r2 - r1):
        raise ValueError("the centre distance must exceed the difference of the pitch radii")
    return (2 * math.sqrt(center ** 2 - (r2 - r1) ** 2) + math.pi * (r1 + r2)
            + 2 * (r2 - r1) * math.asin((r2 - r1) / center))


def belt_center(pd1: float, pd2: float, length: float) -> float:
    """The exact centre distance for an open belt of pitch `length` (bisection)."""
    lo, hi = abs(pd2 - pd1) / 2 + 1e-6, length
    if belt_length(pd1, pd2, hi) < length:
        raise ValueError("belt too long for any centre distance")
    for _ in range(100):
        mid = (lo + hi) / 2
        if belt_length(pd1, pd2, mid) < length:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def timing_belt(pitch_d1: float, pitch_d2: float, center: float, width: float = 6.0,
                thickness: float = 1.38, pld: float = 0.254):
    """A closed timing belt modelled as a band around two pulleys (pulley 1 at the origin,
    pulley 2 at (center, 0)), z = 0..width. Its inner face sits just outside the pulley OD (the
    teeth are not modelled, so the belt never overlaps the pulleys)."""
    w = _pos(width, "width")
    inner, outer = -pld + 0.05, thickness - pld
    with bd.BuildPart() as belt:
        for off, mode in ((outer, bd.Mode.ADD), (inner, bd.Mode.SUBTRACT)):
            with bd.BuildSketch():
                bd.Circle(pitch_d1 / 2 + off)
                with bd.Locations((center, 0)):
                    bd.Circle(pitch_d2 / 2 + off)
                bd.make_hull()
            bd.extrude(amount=w, mode=mode)
    return _check(belt.part, "timing_belt")


# ---------------------------------------------------------------- shafts, bearings, mounts

def shaft(d: float = 8.0, length: float = 60.0, flat: float = 0.0, grooves: Sequence[float] = (),
          chamfer: float = 0.5):
    """Round shaft on +Z (z = 0..length): `flat` mm D-flat depth over the whole length,
    `grooves` z positions of DIN 471 snap-ring grooves (width 1.1 mm, groove d = 0.94*d)."""
    d, L = _pos(d, "d"), _pos(length, "length")
    s = _cyl(d / 2, L)
    if chamfer and chamfer < d / 4:
        s = bd.chamfer(s.edges().filter_by(bd.GeomType.CIRCLE), chamfer)
    if flat:
        s = s - bd.Pos(d / 2 - flat + d / 2, 0, L / 2) * bd.Box(d, d * 2, L + 2)
    for z in grooves:
        ring = _cyl(d / 2 + 1, 1.1, (0, 0, float(z) - 0.55)) - _cyl(0.47 * d, 1.1,
                                                                   (0, 0, float(z) - 0.55))
        s = s - ring
    return _check(s, "shaft")


def shaft_collar(bore: float = 8.0, od: float = 16.0, width: float = 8.0, screw_d: float = 3.0):
    """Set-screw shaft collar (axial retention), axis +Z, z = 0..width, radial M-screw hole
    on +X."""
    c = _cyl(od / 2, width) - _cyl(bore / 2, width + 2, (0, 0, -1))
    hole = bd.Pos(od / 4, 0, width / 2) * bd.Rot(0, 90, 0) * bd.Cylinder(screw_d * 0.42,
                                                                         od / 2 + 2)
    return _check(c - hole, "shaft_collar")


def coupler_rigid(d1: float = 5.0, d2: float = 8.0, length: float = 25.0, od: float = 18.0,
                  screw_d: float = 3.0):
    """Rigid shaft coupler (motor shaft d1 to driven shaft d2), axis +Z, z = 0..length, two
    set-screw holes."""
    c = _cyl(od / 2, length)
    c = c - _cyl(d1 / 2, length / 2 + 0.5, (0, 0, -1)) - _cyl(d2 / 2, length / 2 + 1,
                                                               (0, 0, length / 2))
    for z in (length / 4, 3 * length / 4):
        c = c - bd.Pos(od / 4, 0, z) * bd.Rot(0, 90, 0) * bd.Cylinder(screw_d * 0.42, od / 2 + 2)
    return _check(c, "coupler_rigid")


def bearing(bore: float = 8.0, od: float = 22.0, width: float = 7.0, detailed: bool = True):
    """Deep-groove ball bearing (bd_warehouse races + balls when the size exists, e.g. 608 =
    8x22x7, else a ring envelope), axis +Z, z = 0..width."""
    if detailed:
        size = f"M{bore:g}-{od:g}-{width:g}"
        try:
            from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing
            b = SingleRowDeepGrooveBallBearing(size, "SKT")
            b = b.translate((0, 0, width / 2 - (b.bounding_box().min.Z + width / 2)))
            return _check(b, f"bearing {size}")
        except Exception:  # noqa: BLE001 - a size bd_warehouse does not carry: the envelope
            pass
    ring = _cyl(od / 2, width) - _cyl(bore / 2, width + 2, (0, 0, -1))
    return _check(ring, "bearing")


def frame_plate(length: float = 80.0, width: float = 50.0, thickness: float = 5.0,
                bores: Sequence[Sequence[float]] = (), corner_holes: float = 3.4,
                origin_x: float = 0.0):
    """Flat frame plate in XY (z = -thickness..0, top face at z = 0) carrying bearing seats or
    pin holes: `bores` [(x, y, d), ...]; `corner_holes` mounting holes 5 mm from the corners.
    The plate spans x = origin_x - 10 .. origin_x + length - 10, y = -width/2..width/2."""
    L, W, t = _pos(length, "length"), _pos(width, "width"), _pos(thickness, "thickness")
    x0 = origin_x - 10.0
    p = bd.Pos(x0 + L / 2, 0, -t / 2) * bd.Box(L, W, t)
    for x, y, d in bores:
        p = _bore(p, float(d), t, z0=-t - 1, at=(float(x), float(y)))
    if corner_holes:
        for sx in (x0 + 5, x0 + L - 5):
            for sy in (-W / 2 + 5, W / 2 - 5):
                p = _bore(p, corner_holes, t, z0=-t - 1, at=(sx, sy))
    return _check(p, "frame_plate")


def bearing_block(bearing_od: float = 22.0, bearing_width: float = 7.0, shaft_d: float = 8.0,
                  center_height: float = 20.0, fit: str = "slip", base_w: float = 50.0,
                  base_t: float = 6.0, bolt_d: float = 4.5):
    """Pillow block for a ball bearing: the bearing axis runs along +Y through
    (0, 0, center_height), over a base plate (z = 0..base_t) with two bolt holes on X.

    3D-print fit [J, typical FDM]: bore = D - 0.10 (press) or D + 0.10 (slip); a 1.5 mm
    shoulder stops the bearing; the shaft passes through d + 1."""
    D = _pos(bearing_od, "bearing_od")
    bore = D - 0.10 if fit == "press" else D + 0.10
    boss_r = D / 2 + 4
    if center_height < boss_r:
        raise ValueError(f"center_height {center_height:g} must be at least the boss radius "
                         f"{boss_r:g} (bearing OD / 2 + 4)")
    depth = bearing_width + 1.5
    base = bd.Pos(0, 0, base_t / 2) * bd.Box(base_w, depth, base_t)
    web = bd.Pos(0, 0, center_height / 2) * bd.Box(2 * boss_r, depth, center_height)
    boss = bd.Pos(0, 0, center_height) * bd.Rot(90, 0, 0) * bd.Cylinder(boss_r, depth)
    body = base + web + boss
    seat = bd.Pos(0, -1.5 / 2 + 0.01, center_height) * bd.Rot(90, 0, 0) * bd.Cylinder(
        bore / 2, bearing_width + 0.02)
    through = bd.Pos(0, 0, center_height) * bd.Rot(90, 0, 0) * bd.Cylinder(shaft_d / 2 + 0.5,
                                                                            depth + 2)
    body = body - seat - through
    for sx in (-base_w / 2 + 6, base_w / 2 - 6):
        body = _bore(body, bolt_d, base_t, at=(sx, 0))
    return _check(body, "bearing_block")


_NEMA = {"NEMA14": (35.2, 26.0, 22.0, 3.4), "NEMA17": (42.3, 31.0, 22.4, 3.4),
         "NEMA23": (56.4, 47.14, 38.5, 5.2)}


def motor_mount(frame: str = "NEMA17", plate: float = 4.0, slots: float = 0.0,
                margin: float = 5.0):
    """Stepper motor mount plate in XY (z = 0..plate): pilot hole, 4 screw holes on the frame's
    pattern (NEMA14 26 mm, NEMA17 31 mm, NEMA23 47.14 mm), `slots` > 0 turns them into X slots
    that long (belt tensioning). The motor shaft is on +Z through the origin."""
    if frame.upper() not in _NEMA:
        raise ValueError(f"frame is one of {', '.join(_NEMA)}")
    face, pattern, pilot, hole = _NEMA[frame.upper()]
    side = face + 2 * margin + (slots or 0)
    p = bd.Pos(0, 0, plate / 2) * bd.Box(side, face + 2 * margin, plate)
    p = _bore(p, pilot + (slots or 0) * 0 + 0.4, plate)
    for sx in (-pattern / 2, pattern / 2):
        for sy in (-pattern / 2, pattern / 2):
            if slots:
                cut = bd.Pos(sx, sy, plate / 2) * bd.extrude(
                    bd.SlotCenterToCenter(float(slots), hole), amount=plate + 2, both=True)
                p = p - cut
            else:
                p = _bore(p, hole, plate, at=(sx, sy))
    if slots:
        p = p - bd.Pos(0, 0, plate / 2) * bd.extrude(bd.SlotCenterToCenter(float(slots), pilot),
                                                     amount=plate + 2, both=True)
    return _check(p, f"motor_mount({frame})")


def stepper_motor(frame: str = "Nema17", length: float = 0.0):
    """Stepper motor (bd_warehouse OpenBuilds StepperMotor: body z -length..0, shaft on +Z to
    +24 for NEMA17), the mounting face at z = 0."""
    from bd_warehouse.open_builds import StepperMotor
    name = {"NEMA17": "Nema17", "NEMA23": "Nema23"}.get(frame.upper(), frame)
    m = StepperMotor(name, float(length) if length else None)
    return _check(m, f"stepper_motor({frame})")


def servo_body(l: float = 40.7, w: float = 19.7, h: float = 42.9, tab_l: float = 54.0,
               tab_t: float = 2.5, tab_z: float = 29.0, spacing: float = 49.5,
               hole_d: float = 4.5, output_x: float = 10.2, spline_d: float = 6.0):
    """Hobby servo envelope (MG996R defaults) with its output spline on +Z: the output axis is
    +Z through the origin, the body hangs below (top face at z = 0, output boss above)."""
    body = bd.Pos(l / 2 - output_x, 0, -h / 2) * bd.Box(l, w, h)
    tabs = bd.Pos(l / 2 - output_x, 0, -h + tab_z + tab_t / 2) * bd.Box(tab_l, w, tab_t)
    servo = body + tabs
    for sx in (l / 2 - output_x - spacing / 2, l / 2 - output_x + spacing / 2):
        servo = _bore(servo, hole_d, tab_t, z0=-h + tab_z - 1, at=(sx, 0))
    servo = servo + _cyl(spline_d, 2.0, (0, 0, 0)) + _cyl(spline_d / 2, 4.0, (0, 0, 2.0))
    return _check(servo, "servo_body")


def servo_bracket(servo_w: float = 19.7, servo_h: float = 42.9, wall: float = 3.0,
                  length: float = 50.0, horn_d: float = 20.0):
    """U bracket that wraps a servo for pan-tilt or arm joints: a base in XY (z = 0..wall) and
    two arms rising +Z at y = +-(servo_w/2 + wall/2), holed on the pivot axis +Y through
    (0, 0, servo_h * 0.6) (horn on one arm, idler pivot on the other)."""
    gap = servo_w + 1.0
    base = bd.Pos(0, 0, wall / 2) * bd.Box(length * 0.6, gap + 2 * wall, wall)
    arm_h = servo_h * 0.6 + horn_d / 2 + 2
    arms = None
    for sy in (-(gap / 2 + wall / 2), gap / 2 + wall / 2):
        a = bd.Pos(0, sy, arm_h / 2) * bd.Box(horn_d + 6, wall, arm_h)
        arms = a if arms is None else arms + a
    br = base + arms
    axis_z = servo_h * 0.6
    br = br - bd.Pos(0, 0, axis_z) * bd.Rot(90, 0, 0) * bd.Cylinder(3.2, gap + 4 * wall)
    return _check(br, "servo_bracket")


def bldc_actuator(d: float = 96.0, h: float = 40.0, output_d: float = 50.0,
                  output_h: float = 4.0, bolt_circle_d: float = 40.0, bolt_d: float = 3.4):
    """Quasi-direct-drive actuator envelope (Mini-Cheetah class: BLDC + planetary): a can of
    d x h (z = -h..0) with an output flange on +Z (z = 0..output_h), output axis +Z."""
    can = _cyl(d / 2, h, (0, 0, -h))
    flange = _cyl(output_d / 2, output_h, (0, 0, 0))
    act = can + flange
    for k in range(6):
        a = TAU * k / 6
        act = _bore(act, bolt_d, output_h, z0=-0.5,
                    at=(bolt_circle_d / 2 * math.cos(a), bolt_circle_d / 2 * math.sin(a)))
    return _check(act, "bldc_actuator")


# ---------------------------------------------------------------- linkages and joints

def link_bar(length: float = 60.0, width: float = 10.0, thickness: float = 4.0,
             bore: float = 3.2, bore2: float | None = None):
    """Straight link with a bore at each end: from (0, 0) to (length, 0), z = 0..thickness.
    `bore2` (default `bore`) is the far end's hole."""
    L = _pos(length, "length")
    bar = _stadium(L, width, _pos(thickness, "thickness"))
    bar = _bore(bar, bore, thickness)
    bar = _bore(bar, bore if bore2 is None else bore2, thickness, at=(L, 0))
    return _check(bar, "link_bar")


def link_plate(points: Sequence[Sequence[float]] = ((0, 0), (40, 0), (20, 25)),
               width: float = 10.0, thickness: float = 4.0, bore: float = 3.2):
    """Ternary (or larger) link: the convex hull of round pads at `points` (local x, y), a bore
    at each, z = 0..thickness. The first point should be (0, 0)."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        raise ValueError("a link plate needs at least 2 points")
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            for x, y in pts:
                with bd.Locations((x, y)):
                    bd.Circle(width / 2)
            bd.make_hull()
        bd.extrude(amount=_pos(thickness, "thickness"))
    part = p.part
    for x, y in pts:
        part = _bore(part, bore, thickness, at=(x, y))
    return _check(part, "link_plate")


def pin(d: float = 3.0, length: float = 20.0, head_d: float = 0.0, head_h: float = 1.0):
    """Hinge or link pin on +Z (z = 0..length), optional head below z = 0."""
    p = _cyl(_pos(d, "d") / 2, _pos(length, "length"))
    if head_d:
        p = p + _cyl(head_d / 2, head_h, (0, 0, -head_h))
    return _check(p, "pin")


def slider_block(length: float = 30.0, width: float = 20.0, height: float = 15.0,
                 pin_d: float = 3.2, rod_d: float = 0.0, slot_w: float = 0.0,
                 slot_l: float = 0.0):
    """Slider (crosshead) block centred on the origin in XY, z = 0..height, a pin hole on +Z
    through the origin; `rod_d` > 0 bores a guide-rod hole along X; `slot_w` x `slot_l` cuts a
    through slot along Y (a Scotch yoke)."""
    b = bd.Pos(0, 0, height / 2) * bd.Box(length, width, height)
    b = _bore(b, pin_d, height)
    if slot_w and slot_l:
        b = b - bd.Pos(0, 0, height / 2) * bd.Box(slot_w, slot_l, height + 2)
    if rod_d:
        b = b - bd.Pos(0, 0, height / 2) * bd.Rot(0, 90, 0) * bd.Cylinder(rod_d / 2, length + 2)
    return _check(b, "slider_block")


def lead_screw(d: float = 8.0, lead: float = 8.0, length: float = 200.0):
    """Lead screw blank (Tr8x8 default) on +Z, z = 0..length (thread not modelled: the major
    diameter; the lead goes on the card and the joint coupling)."""
    return _check(_cyl(_pos(d, "d") / 2, _pos(length, "length")), "lead_screw")


def lead_nut(d: float = 8.0, flange_d: float = 22.0, flange_t: float = 3.5,
             body_d: float = 10.2, length: float = 15.0, bolt_circle_d: float = 16.0):
    """Flanged lead-screw nut on +Z (z = 0..length), flange at z = 0, 4 bolt holes."""
    n = _cyl(body_d / 2, length) + _cyl(flange_d / 2, flange_t)
    n = _bore(n, d, length)
    for k in range(4):
        a = TAU * k / 4 + math.pi / 4
        n = _bore(n, 3.4, flange_t, at=(bolt_circle_d / 2 * math.cos(a),
                                        bolt_circle_d / 2 * math.sin(a)))
    return _check(n, "lead_nut")


_MGN = {"MGN9": (9.0, 6.5, 20.0, 39.9, 10.0), "MGN12": (12.0, 8.0, 27.0, 45.4, 13.0),
        "MGN15": (15.0, 10.0, 32.0, 58.8, 16.0)}


def linear_rail(rail: str = "MGN12", length: float = 200.0, hole_pitch: float = 25.0):
    """Miniature linear guide rail (Hiwin MGN size, typical catalogue dims) along +X from x = 0,
    z = 0..rail height, with M-screw holes every `hole_pitch`."""
    if rail.upper() not in _MGN:
        raise ValueError(f"rail is one of {', '.join(_MGN)}")
    w, h, _cw, _cl, _ch = _MGN[rail.upper()]
    r = bd.Pos(length / 2, 0, h / 2) * bd.Box(_pos(length, "length"), w, h)
    x = hole_pitch / 2
    while x < length:
        r = _bore(r, 3.5 if w <= 12 else 4.5, h, at=(x, 0))
        x += hole_pitch
    return _check(r, f"linear_rail({rail})")


def rail_carriage(rail: str = "MGN12"):
    """Carriage block for a MGN rail, centred at the origin, riding z = 3..3+height over the
    rail (typical catalogue dims), 4 M3 holes on top."""
    if rail.upper() not in _MGN:
        raise ValueError(f"rail is one of {', '.join(_MGN)}")
    w, h, cw, cl, ch = _MGN[rail.upper()]
    c = bd.Pos(0, 0, 3 + ch / 2) * bd.Box(cl, cw, ch)
    c = c - bd.Pos(0, 0, 3 + (h - 1) / 2) * bd.Box(cl + 2, w + 0.4, h - 1)
    for sx in (-cl / 4, cl / 4):
        for sy in (-cw / 4, cw / 4):
            c = _bore(c, 2.9, ch, z0=3 + ch - 4, at=(sx, sy))
    return _check(c, f"rail_carriage({rail})")


def cam_disc(base_r: float = 20.0, lift: float = 8.0, width: float = 6.0, bore: float = 5.0,
             rise_deg: float = 120.0, dwell_deg: float = 60.0, fall_deg: float = 120.0,
             samples: int = 360):
    """Plate cam with a cycloidal rise - dwell - cycloidal fall - dwell profile (no jerk
    spikes), axis +Z, z = 0..width; the follower is on +Y at angle 0 (base circle)."""
    def s(th: float) -> float:
        th = th % 360.0
        if th < rise_deg:
            b = th / rise_deg
            return lift * (b - math.sin(TAU * b) / TAU)
        if th < rise_deg + dwell_deg:
            return lift
        if th < rise_deg + dwell_deg + fall_deg:
            b = (th - rise_deg - dwell_deg) / fall_deg
            return lift * (1 - (b - math.sin(TAU * b) / TAU))
        return 0.0
    pts = []
    for k in range(int(samples)):
        th = 360.0 * k / samples
        r = base_r + s(th)
        a = math.radians(90.0 - th)        # the cam turns +Z; the follower stays on +Y
        pts.append((r * math.cos(a), r * math.sin(a)))
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            with bd.BuildLine():
                bd.Polyline(*pts, close=True)
            bd.make_face()
        bd.extrude(amount=_pos(width, "width"))
    return _check(_bore(p.part, bore, width), "cam_disc")


def follower(rod_d: float = 6.0, length: float = 40.0, roller_d: float = 10.0,
             roller_w: float = 6.0):
    """Translating roller follower: a rod on +Z (z = 0..length) with a roller boss at the
    origin end (roller axis along X)."""
    rod = _cyl(rod_d / 2, length, (0, 0, roller_d / 2))
    roller = bd.Pos(0, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(roller_d / 2, roller_w)
    return _check(rod + roller, "follower")


def geneva_wheel(slots: int = 4, center_dist: float = 40.0, pin_d: float = 5.0,
                 width: float = 5.0, bore: float = 6.0, clearance: float = 0.3):
    """Geneva wheel with `slots` radial slots, axis +Z, z = 0..width; its driver sits at
    (center_dist, 0) (slot on +X at rotation 0). Wheel radius C*cos(pi/n)."""
    n = int(slots)
    if n < 3:
        raise ValueError("a Geneva wheel needs at least 3 slots")
    C = _pos(center_dist, "center_dist")
    a = C * math.sin(math.pi / n)
    b = C * math.cos(math.pi / n)
    wheel = _cyl(b + pin_d / 2, width)
    depth_in = C - a - pin_d                     # slot bottom radius
    for k in range(n):
        ang = 360.0 * k / n
        slot_len = b + pin_d - depth_in
        cut = bd.Rot(0, 0, ang) * bd.Pos(depth_in + slot_len / 2, 0, width / 2) * bd.Box(
            slot_len + 1, pin_d + 2 * clearance, width + 2)
        wheel = wheel - cut
        # the locking arcs between slots
        mid = math.radians(ang + 180.0 / n)
        wheel = wheel - _cyl(a * 0.9 + clearance, width + 2,
                             (C * math.cos(mid), C * math.sin(mid), -1))
    return _check(_bore(wheel, bore, width), "geneva_wheel")


def geneva_driver(slots: int = 4, center_dist: float = 40.0, pin_d: float = 5.0,
                  width: float = 5.0, bore: float = 6.0):
    """Geneva driver: crank arm with a drive pin at radius C*sin(pi/n) on +X and a locking disc,
    axis +Z, z = 0..width (pin above to z = 2*width)."""
    n = int(slots)
    C = _pos(center_dist, "center_dist")
    a = C * math.sin(math.pi / n)
    arm = _stadium(a, pin_d + 6, width)
    disc = _cyl(a * 0.9, width)
    pin_ = _cyl(pin_d / 2, width, (a, 0, width))
    return _check(_bore(arm + disc + pin_, bore, width), "geneva_driver")


def ratchet_wheel(teeth: int = 16, od: float = 40.0, width: float = 5.0, bore: float = 6.0,
                  depth: float = 3.0):
    """Ratchet wheel (saw teeth, one-way: the pawl locks against clockwise turning), axis +Z,
    z = 0..width."""
    n = int(teeth)
    R, r = od / 2, od / 2 - depth
    pts = []
    for k in range(n):
        a0 = TAU * k / n
        a1 = TAU * (k + 1) / n
        pts += [(R * math.cos(a0), R * math.sin(a0)), (r * math.cos(a1), r * math.sin(a1))]
    with bd.BuildPart() as p:
        with bd.BuildSketch():
            with bd.BuildLine():
                bd.Polyline(*pts, close=True)
            bd.make_face()
        bd.extrude(amount=_pos(width, "width"))
    return _check(_bore(p.part, bore, width), "ratchet_wheel")


def pawl(length: float = 20.0, width: float = 6.0, thickness: float = 5.0, bore: float = 3.2):
    """Ratchet pawl: a pivoting tongue from its bore at the origin toward +X with a tip."""
    p = _stadium(length, width, thickness)
    return _check(_bore(p, bore, thickness), "pawl")


def hinge_leaf(length: float = 40.0, width: float = 20.0, thickness: float = 3.0,
               pin_d: float = 3.0, knuckles: int = 5, side: int = 0, gap: float = 0.3):
    """One leaf of a pin hinge: the hinge axis is +X through the origin (z = 0), the leaf lies
    in +Y (side 0) or -Y (side 1) on z = -thickness/2..+thickness/2. Knuckles alternate between
    the leaves (side 0 takes the even ones); each has the pin bore + clearance."""
    kn = int(knuckles)
    if kn < 2:
        raise ValueError("a hinge needs at least 2 knuckles")
    sgn = 1 if int(side) == 0 else -1
    ko = pin_d / 2 + thickness
    leaf = bd.Pos(length / 2, sgn * (ko + width / 2), 0) * bd.Box(length, width, thickness)
    seg = length / kn
    for k in range(kn):
        if k % 2 != int(side) % 2:
            continue
        x0 = k * seg + (gap / 2 if k else 0)
        x1 = (k + 1) * seg - (gap / 2 if k < kn - 1 else 0)
        knuckle = bd.Pos((x0 + x1) / 2, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(ko, x1 - x0)
        web = bd.Pos((x0 + x1) / 2, sgn * ko / 2, 0) * bd.Box(x1 - x0, ko, thickness)
        leaf = leaf + knuckle + web
    leaf = leaf - bd.Pos(length / 2, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(pin_d / 2 + gap / 2,
                                                                          length + 2)
    return _check(leaf, "hinge_leaf")


def ball_stud(ball_d: float = 12.0, neck_d: float = 6.0, neck_l: float = 12.0,
              flange_d: float = 16.0, flange_t: float = 3.0):
    """Ball stud: a ball centred at the origin on a neck down -Z to a mounting flange."""
    ball = bd.Sphere(ball_d / 2)
    neck = _cyl(neck_d / 2, neck_l, (0, 0, -neck_l))
    flange = _cyl(flange_d / 2, flange_t, (0, 0, -neck_l - flange_t))
    return _check(ball + neck + flange, "ball_stud")


def ball_socket(ball_d: float = 12.0, wall: float = 3.0, clearance: float = 0.25,
                cone_deg: float = 30.0, length: float = 20.0):
    """Ball socket around a ball centred at the origin: a body along +Z, the spherical seat
    (ball + clearance) and an opening cone toward -Z that allows `cone_deg` of tilt."""
    R = ball_d / 2 + clearance
    body = _cyl(R + wall, length, (0, 0, -R * 0.6))
    seat = bd.Sphere(R)
    open_r = (ball_d / 2) * 0.85 + math.tan(math.radians(cone_deg)) * R
    cone = bd.Pos(0, 0, -R * 0.6 - 0.01) * bd.Cone(open_r + 1, ball_d * 0.3, R * 0.6 + 0.02,
                                                   align=(bd.Align.CENTER, bd.Align.CENTER,
                                                          bd.Align.MIN))
    return _check(body - seat - cone, "ball_socket")


def u_yoke(shaft_bore: float = 8.0, pin_d: float = 4.0, span: float = 20.0,
           arm_t: float = 4.0, hub_d: float = 18.0, hub_l: float = 15.0):
    """Universal-joint yoke: a hub on -Z (z = -hub_l..0, shaft bore) and two arms up +Z whose
    pin holes lie on the X axis through (0, 0, span/2 + 2)."""
    hub = _cyl(hub_d / 2, hub_l, (0, 0, -hub_l))
    arm_h = span / 2 + 2 + pin_d * 1.5
    arms = None
    for sx in (-(span / 2 + arm_t / 2), span / 2 + arm_t / 2):
        a = bd.Pos(sx, 0, arm_h / 2) * bd.Box(arm_t, hub_d * 0.8, arm_h)
        arms = a if arms is None else arms + a
    y = hub + arms
    y = y - bd.Pos(0, 0, span / 2 + 2) * bd.Rot(0, 90, 0) * bd.Cylinder(pin_d / 2 + 0.1,
                                                                        span + 4 * arm_t)
    return _check(_bore(y, shaft_bore, hub_l, z0=-hub_l - 1), "u_yoke")


def cross_spider(pin_d: float = 4.0, span: float = 20.0, core: float = 8.0):
    """The cross of a universal joint, centred at the origin: pins along X and Y, each
    `span` long overall."""
    c = bd.Box(core, core, core)
    c = c + bd.Rot(0, 90, 0) * bd.Cylinder(pin_d / 2, span) + bd.Rot(90, 0, 0) * bd.Cylinder(
        pin_d / 2, span)
    return _check(c, "cross_spider")


def flexure_hinge(length: float = 40.0, width: float = 10.0, thickness: float = 4.0,
                  flex_t: float = 0.6, flex_l: float = 4.0):
    """Living hinge (one printed part): two pads along X joined by a thin web of flex_t x flex_l
    at x = 0 (the bend axis is +Y through the origin, on the bottom face z = 0)."""
    half = (length - flex_l) / 2
    a = bd.Pos(-flex_l / 2 - half / 2, 0, thickness / 2) * bd.Box(half, width, thickness)
    b = bd.Pos(flex_l / 2 + half / 2, 0, thickness / 2) * bd.Box(half, width, thickness)
    web = bd.Pos(0, 0, flex_t / 2) * bd.Box(flex_l + 0.2, width, flex_t)
    return _check(a + b + web, "flexure_hinge")


def snap_cantilever(t: float = 1.5, length: float = 12.0, width: float = 6.0,
                    hook_h: float = 1.0, hook_l: float = 2.0, base: float = 4.0):
    """Cantilever snap-fit hook: a beam t x width rising +Z from a base block (z = 0..base) to
    `length`, with a hook of depth hook_h on +X at the tip (strain 1.5*t*y/L^2 on the card)."""
    blk = bd.Pos(0, 0, base / 2) * bd.Box(width * 2, width, base)
    beam = bd.Pos(0, 0, base + length / 2) * bd.Box(t, width, length)
    with bd.BuildPart() as hk:
        with bd.BuildSketch(bd.Plane.XZ):
            bd.Polygon((t / 2, base + length - hook_l), (t / 2 + hook_h, base + length - hook_l),
                       (t / 2, base + length), align=None)
        bd.extrude(amount=width / 2, both=True)
    return _check(blk + beam + hk.part, "snap_cantilever")


def gripper_jaw(length: float = 50.0, height: float = 30.0, thickness: float = 6.0,
                module: float = 1.0, rack_teeth: int = 16, finger: float = 25.0):
    """Parallel-gripper jaw: a rack (teeth +Y, pitch line y = 0, along X from 0) on a carrier
    with a finger rising +Z at the far end."""
    r = gear_rack(module, rack_teeth, thickness, base=4.0)
    rl = math.pi * module * int(rack_teeth)
    r = r.translate((rl / 2, 0, 0))
    finger_ = bd.Pos(rl - 3, -module - 2, thickness + finger / 2) * bd.Box(6, 8, finger)
    pad = bd.Pos(rl - 3, -module - 2, thickness / 2) * bd.Box(6, 8, thickness)
    return _check(r + finger_ + pad, "gripper_jaw")


def wheel(d: float = 80.0, width: float = 25.0, bore: float = 6.0, hub_d: float = 20.0,
          tread: float = 4.0):
    """Wheel on +Z (z = 0..width): a tyre ring of `tread` over a rim with a hub and bore."""
    tyre = _cyl(d / 2, width) - _cyl(d / 2 - tread, width + 2, (0, 0, -1))
    rim = _cyl(d / 2 - tread, width * 0.5, (0, 0, width * 0.25)) - _cyl(hub_d / 2, width,
                                                                       (0, 0, -1))
    hub = _cyl(hub_d / 2, width)
    return _check(_bore(tyre + rim + hub, bore, width), "wheel")


def prop_mount(pattern: float = 16.0, motor_d: float = 28.0, plate: float = 4.0,
               arm_w: float = 20.0, arm_l: float = 60.0, hole_d: float = 3.2):
    """Motor mount at the end of a rotor arm: a pad for an outrunner motor of motor_d (pattern
    pattern x pattern, M3) at the origin, plate z = -plate..0, the arm running to -X."""
    pad = _cyl(motor_d / 2 + 3, plate, (0, 0, -plate))
    arm = bd.Pos(-arm_l / 2, 0, -plate / 2) * bd.Box(arm_l, arm_w, plate)
    m = pad + arm
    m = _bore(m, 7.0, plate, z0=-plate - 1)
    for sx in (-pattern / 2, pattern / 2):
        for sy in (-pattern / 2, pattern / 2):
            m = _bore(m, hole_d, plate, z0=-plate - 1, at=(sx, sy))
    return _check(m, "prop_mount")


def extrusion(size: float = 20.0, length: float = 200.0, slots: bool = True):
    """Aluminium T-slot extrusion envelope (2020 default) along +X from x = 0, centred on the X
    axis, with the centre bore and (simplified) slots."""
    e = bd.Pos(length / 2, 0, 0) * bd.Box(_pos(length, "length"), size, size)
    e = e - bd.Pos(length / 2, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(size * 0.21, length + 2)
    if slots:
        for ry in (0, 90, 180, 270):
            e = e - bd.Rot(ry, 0, 0) * bd.Pos(length / 2, 0, size / 2) * bd.Box(
                length + 2, size * 0.3, size * 0.3)
    return _check(e, "extrusion")
