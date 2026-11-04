"""Thrust-vector control as a mechanism: a 2-axis gimballed motor mount for a model rocket.

Layout (mechanism frame: +Z is the rocket's nose direction, the motor's nozzle points -Z, the
gimbal pivot is the origin):
- `mount` (grounded): the thrust ring. A ring around the outer gimbal with the pitch pin bores on
  the X axis, a bottom flange out to the airframe ID (glued in as a centering / thrust ring) and
  two posts the servos screw to. Peak thrust goes motor -> tube lip -> yaw pins -> outer ring ->
  pitch pins -> mount -> airframe.
- `outer` ring: pitch, about X, on two steel pins in the mount; a pushrod tab on top (+Y side).
- `tube`: the motor mount tube (yaw, about Y, on two pins in the outer ring) with a thrust lip at
  its forward end and a pushrod tab (+X side).
- two micro servos on the posts, each with a horn and a pushrod: the pitch rod is a planar
  four-bar (all pins parallel to X); the yaw rod hangs from a pin-and-clevis universal at its
  horn and meets its tab in a ball loop, because the yaw tab swings out of the rod's plane when
  the outer ring pitches.

Every part is placed in the world first and each joint's frames and value are derived from those
poses, so the inserted pose is exact (no loop residual at rest).
"""
from __future__ import annotations

import math

from ..assembly.solve import frame_matrix, inverse_rigid, matmul
from . import sizing
from .card import fr
from .kit import PIN_CLEAR, Kit, pin_design
from .library import P, mechanism

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
Z = (0.0, 0.0, 1.0)
I4 = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _pose(origin, x_axis, z_axis) -> list[list[float]]:
    """World pose whose local X / Z are the given world unit vectors (Y = Z x X)."""
    x, z = x_axis, z_axis
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    o = origin
    return [[x[0], y[0], z[0], float(o[0])], [x[1], y[1], z[1], float(o[1])],
            [x[2], y[2], z[2], float(o[2])], [0.0, 0.0, 0.0, 1.0]]


def _pt(m, p) -> tuple:
    return tuple(round(sum(m[i][k] * p[k] for k in range(3)) + m[i][3], 9) for i in range(3))


def _dir(m, v) -> tuple:
    return tuple(round(sum(m[i][k] * v[k] for k in range(3)), 12) for i in range(3))


class _World:
    """Places instances at world poses and writes joints between them from a world point and
    axis: the part-local frames and the revolute value that reproduce the poses exactly."""

    def __init__(self, kit: Kit):
        self.k = kit
        self.W: dict[str, list[list[float]]] = {}

    def frames(self, a: str, b: str, p, axis) -> tuple[tuple, tuple, float]:
        ia, ib = inverse_rigid(self.W[a]), inverse_rigid(self.W[b])
        fa = (_pt(ia, p), _dir(ia, axis))
        fb = (_pt(ib, p), _dir(ib, axis))
        # child pose = Wa Fa R(v) Fb^-1  =>  R(v) = Fa^-1 Wa^-1 Wb Fb
        r = matmul(inverse_rigid(frame_matrix(*fa)), matmul(ia, matmul(self.W[b],
                                                                       frame_matrix(*fb))))
        v = math.degrees(math.atan2(r[1][0], r[0][0]))
        return fr(*fa), fr(*fb), round(v, 9)

    def revolute(self, key: str, a: str, b: str, p, axis, limits=None, design=None) -> str:
        fa, fb, v = self.frames(a, b, p, axis)
        return self.k.joint(key, "revolute", a, fa, b, fb, value=v, limits=limits, design=design)

    def fixed_pose(self, key: str, a: str, b: str, p, axis, fb_local) -> None:
        """Fix b to a: b's pose follows from a's frame (p, axis) and b's own frame fb_local."""
        ia = inverse_rigid(self.W[a])
        fa = (_pt(ia, p), _dir(ia, axis))
        self.W[b] = matmul(self.W[a], matmul(frame_matrix(*fa),
                                             inverse_rigid(frame_matrix(*fb_local))))
        self.k.fixed(key, a, fr(*fa), b, fr(*fb_local))

    def driven(self, key: str, a: str, b: str, p, axis, origin, limits, design) -> str:
        """A revolute whose value is 0 at the placed pose (a servo output): b's pose follows
        from a's frame at (p, axis) with b's own origin at `origin` on the axis."""
        ia = inverse_rigid(self.W[a])
        fa = (_pt(ia, p), _dir(ia, axis))
        q = sum((p[i] - origin[i]) * axis[i] for i in range(3))
        fb = ((0.0, 0.0, round(q, 9)), Z)
        self.W[b] = matmul(self.W[a], matmul(frame_matrix(*fa), inverse_rigid(frame_matrix(*fb))))
        return self.k.joint(key, "revolute", a, fr(*fa), b, fr(*fb), value=0.0, limits=limits,
                            design=design)

    def local_xy(self, inst: str, p) -> list[float]:
        q = _pt(inverse_rigid(self.W[inst]), p)
        return [round(q[0], 4), round(q[1], 4)]

    def loop(self, key: str, a: str, b: str, p, axis, kind: str, drivers: list[str]) -> str:
        fa, fb, _v = self.frames(a, b, p, axis)
        return self.k.loop(key, a, fa, b, fb, type=kind, drivers=drivers)


def _servo_deg(hub, hr: float, tip0, tab0, rod_l: float, theta_deg: float) -> float:
    """Planar four-bar: the CCW servo angle (deg) that turns the gimbal by theta_deg CCW about
    the origin, from the rest pose (horn tip tip0, tab tab0), in the channel's 2-D plane."""
    from .kit import circle_intersect
    t = math.radians(theta_deg)
    tab = (tab0[0] * math.cos(t) - tab0[1] * math.sin(t),
           tab0[0] * math.sin(t) + tab0[1] * math.cos(t))
    cands = []
    for side in (1, -1):
        try:
            cands.append(circle_intersect(hub, hr, tab, rod_l, side))
        except ValueError:
            pass
    if not cands:
        raise ValueError(f"the pushrod cannot reach the tab at {theta_deg:g} deg: lengthen "
                         "horn_r or reduce max_deflection_deg")
    tip = min(cands, key=lambda q: math.dist(q, tip0))
    a0 = math.atan2(tip0[1] - hub[1], tip0[0] - hub[0])
    a1 = math.atan2(tip[1] - hub[1], tip[0] - hub[0])
    return math.degrees(math.atan2(math.sin(a1 - a0), math.cos(a1 - a0)))


def _servo(item: str) -> dict:
    from ..catalog.builtin import builtin_by_id
    it = builtin_by_id().get(item)
    if it is None:
        raise ValueError(f"unknown servo {item}")
    return it


@mechanism("tvc_gimbal", "Thrust-vector gimbal, 2-axis servo (model rocket)", "joint",
           "A motor mount tube in an outer gimbal ring on crossed steel pins inside a thrust "
           "ring, each axis driven by a micro servo through a horn and pushrod: thrust-vector "
           "control (TVC) for a model rocket motor, +-5 deg pitch and yaw.",
           ["tvc", "thrust vector control", "thrust vectoring", "gimbal", "gimbal ring",
            "gimballed motor mount", "rocket", "motor mount", "pushrod", "servo linkage",
            "2 axis", "pitch yaw"],
           {"motor_d": P(29.0, 13.0, 54.0, "mm", "motor diameter (18, 24, 29, 38 ...)"),
            "airframe_id": P(76.2, 40.0, 160.0, "mm", "airframe inner diameter (the thrust "
                             "ring's flange)"),
            "servo": P("servo_sg90_micro", choices=("servo_sg90_micro",
                                                    "servo_mg996r_standard"),
                       unit="", doc="catalog servo"),
            "max_deflection_deg": P(5.0, 1.0, 10.0, "deg", "gimbal deflection each way"),
            "horn_r": P(8.0, 5.0, 20.0, "mm", "servo horn radius to the pushrod hole"),
            "pin_d": P(3.0, 2.0, 6.0, "mm", "gimbal pins (steel)"),
            "peak_thrust_n": P(80.0, 5.0, 2000.0, "N", "motor peak thrust (sizes pins/lugs)"),
            "motor_mass_g": P(130.0, 5.0, 3000.0, "g", "loaded motor mass (slew inertia)"),
            "motor_len": P(124.0, 30.0, 700.0, "mm", "motor length; its forward end sits on "
                           "the tube's lip"),
            "slew_s": P(0.06, 0.01, 1.0, "s", "time for a full -max..+max gimbal swing"),
            "material": P("PETG", choices=("PETG", "PLA", "ABS", "ASA", "PA12", "PA-CF"),
                          unit="", doc="printed rings, tabs and horns"),
            "servo_layout": P("auto", choices=("auto", "outboard", "inboard"), unit="",
                              doc="pitch servo beside the gimbal (short stack, needs ~76 mm "
                                  "ID for 29 mm) or over the motor (fits 66 mm BT-80); auto "
                                  "picks outboard when it fits")},
           ["servo_torque", "pin_shear", "lug_bearing", "rom_sweep"], "rot->rot")
def tvc_gimbal(p: dict):
    it = _servo(p["servo"])
    sh = it["shape"]
    out_o = tuple(float(c) for c in ((it.get("perf") or {}).get("output_origin_mm")
                                     or (sh["l"] / 2 - sh["w"] / 2, 0.0, sh["h"])))
    md, mat, defl = p["motor_d"], p["material"], p["max_deflection_deg"]
    pin_d, hr = p["pin_d"], p["horn_r"]
    hr_p = hr                                 # the pitch horn (inboard: capped, see below)
    bore = pin_d + PIN_CLEAR
    rod_pin = 2.0
    rod_bore = rod_pin + PIN_CLEAR
    gap = 1.5 + md * 0.01                     # radial clearance between nested rings
    # nested radii: tube (motor), outer ring, mount
    t_ri = md / 2 + 0.2
    t_ro = t_ri + 2.0
    o_ri = t_ro + gap
    o_ro = o_ri + 3.0
    m_ri = o_ro + gap + 0.3
    m_ro = m_ri + 3.5
    R = p["airframe_id"] / 2
    tube_h = max(40.0, md * 1.7)
    ring_h = 10.0
    sw, sl, shh = sh["w"], sh["l"], sh["h"]
    if m_ro + 2.0 > R:
        raise ValueError(f"the airframe ID {p['airframe_id']:g} mm is too small for a "
                         f"{md:g} mm motor gimbal (needs > {2 * (m_ro + 2):.0f} mm)")

    k = Kit("mount")
    w = _World(k)
    horn_t, rod_t, clr = 2.0, 2.5, 0.3
    # ---- pitch channel (outer ring about X), tab on +Y; rod plane normal X
    tab_w = 6.0
    p_tab_x = -(tab_w / 2 + 7.0)                       # tab centre x
    p_tab_y = (o_ri + o_ro) / 2
    p_tab_z = ring_h / 2 + 6.0                         # tab pin height
    # the tab overhangs the ring (toward the mount) only above z_tab0, clear of the mount
    # through the pitch swing; a riser inside the ring's footprint joins it to the ring
    z_tab0 = ring_h / 2 + 3.0
    rx1 = p_tab_x + tab_w / 2
    rx0 = rx1 - 2.5
    ry0 = math.sqrt(max(0.0, o_ri ** 2 - rx1 ** 2)) + 0.3
    ry1 = math.sqrt(max(0.0, (o_ro - 0.1) ** 2 - rx0 ** 2))
    if ry1 - ry0 < 1.0:
        raise ValueError("no room for the pitch tab riser: thicker outer ring needed")
    p_rod_x0 = p_tab_x - tab_w / 2 - 0.25              # rod faces: x0 (tab side) .. x0 - rod_t
    # horn sits on the tab's side of the rod, above the tab: between rod and servo face
    p_horn_x = p_rod_x0 + clr                          # horn faces: p_horn_x .. + horn_t
    p_face_x = p_horn_x + horn_t + clr                 # servo output face (axis -X)
    span = max(sl, float(sh.get("tab_l") or sl))      # body + mounting ears, along the servo
    out_top = span / 2 - out_o[0]                      # servo above / below its axis
    out_bot = span / 2 + out_o[0]
    # ---- yaw channel (tube about Y), tab on +X; rod plane normal Y
    y_tab_x = t_ri + (t_ro - t_ri) / 2 - 0.2
    # the yaw rod runs in a clevis on the X axis (y = 0): pitching the outer ring then barely
    # lifts the yaw tab (little pitch -> yaw cross-coupling)
    y_tab_z = tube_h / 2 + 5.0
    y_rod_y0 = -rod_t / 2                              # rod faces y0 .. y0 + rod_t
    y_horn_y = y_rod_y0 - clr                          # horn faces y_horn_y - horn_t .. y_horn_y
    y_face_y = y_horn_y - horn_t - clr                 # servo output face (axis +Y)
    y_hub_x = y_tab_x - hr                             # the yaw servo sits over the motor
    y_rod_l = 25.0
    y_z = y_tab_z + y_rod_l
    # servo bodies (output axis world -X / +Y, long axis vertical): bottom faces for the posts
    p_body_x1 = p_face_x + shh                         # pitch servo bottom face
    y_body_y1 = y_face_y - shh                         # yaw servo bottom face
    z_lo = -ring_h / 2 + 3.0                           # flange top
    post_t = 3.0
    px0, px1 = p_body_x1, p_body_x1 + post_t           # the pitch post's x span
    y_post_x0 = y_hub_x - sw / 2
    y_post_x1 = y_hub_x + sw / 2
    y_post = [(y_post_x0 + y_post_x1) / 2, y_body_y1 - post_t / 2,
              (z_lo + y_z + out_top) / 2, y_post_x1 - y_post_x0, post_t,
              y_z + out_top - z_lo]
    y_corner = math.hypot(y_post_x1, y_body_y1 - post_t)
    out_corner = max(math.hypot(px1, p_tab_y + hr + sw / 2),
                     math.hypot(p_tab_y + hr + sw / 2, p_face_x))
    tol = 0.3                                          # the flange edge meets the tube wall
    layout = p["servo_layout"]
    if layout == "auto":
        layout = "outboard" if max(out_corner, y_corner) <= R - tol else "inboard"
    if layout == "outboard":
        # pitch servo outside the outer ring, its post straight up from the flange
        p_hub_y = p_tab_y + hr
        p_z = p_tab_z + 29.0
        posts = [[px0 + post_t / 2, p_hub_y, (z_lo + p_z + out_top) / 2, post_t, sw,
                  p_z + out_top - z_lo]]
        corner = max(out_corner, y_corner)
    else:
        # pitch servo over the motor (clear of the tilting tube top), horn reaching out to the
        # tab; its post is a block behind the servo on a column outside the gimbal
        # the pitch servo body must stay clear of the yaw rod, which swings toward -X as the
        # tube yaws: cap the pitch horn so the body's near side stays off the X axis
        rod_sway = y_tab_z * math.sin(math.radians(defl + 2.0))   # the rod foot, pitched
        hr_p = min(hr, p_tab_y - (rod_t / 2 + 1.0 + rod_sway + sw / 2))
        if hr_p < 4.0:
            raise ValueError("no room for an inboard pitch servo beside the yaw pushrod")
        p_hub_y = p_tab_y - hr_p
        p_z = max(p_tab_z + 29.0, tube_h / 2 + 6.0 + out_bot)
        col_y0 = math.sqrt(max(0.0, (o_ro + gap + 0.5) ** 2 - px0 ** 2))
        col_y1 = math.sqrt(max(0.0, (R - tol) ** 2 - px1 ** 2))
        if col_y1 - col_y0 < 4.0:
            raise ValueError(f"the airframe ID {p['airframe_id']:g} mm leaves no room for the "
                             "pitch servo's post column; use a larger airframe")
        # the block stays clear of the yaw clevis cheeks as the tube pitches and yaws
        cheek_y = rod_t / 2 + 1.0 + 2.7 + (y_tab_z + 3.0) * math.sin(math.radians(defl + 2))
        yb0 = max(p_hub_y - sw / 2, cheek_y + 0.5)
        posts = [[px0 + post_t / 2, (yb0 + col_y1) / 2, (p_z - out_bot + p_z + out_top) / 2,
                  post_t, col_y1 - yb0, out_bot + out_top],
                 [px0 + post_t / 2, (col_y0 + col_y1) / 2, (z_lo + p_z - out_bot + 0.5) / 2,
                  post_t, col_y1 - col_y0, p_z - out_bot + 0.5 - z_lo]]
        corner = max(math.hypot(px1, col_y1), y_corner)
    p_rod_l = p_z - p_tab_z
    if corner > R - tol + 1e-6:
        raise ValueError(f"the servos need an airframe ID over {2 * (corner + tol):.0f} mm "
                         f"(have {p['airframe_id']:g}) in the {layout} layout; "
                         + ("try servo_layout inboard, a smaller horn_r or a larger airframe"
                            if layout == "outboard" else
                            "a larger horn_r (it moves the yaw servo toward the axis), a "
                            "larger airframe ID (a thinner printed wall, or a 2.6 in BT-80 "
                            "tube at 65.6 mm ID) or a smaller servo"))

    k.part("mount", "gimbal_ring", "thrust ring + servo mount", mat, id=2 * m_ri, od=2 * m_ro,
           height=ring_h, pin_d=bore, pin_angles=[0.0, 180.0], flange_od=2 * R,
           flange_t=3.0, boxes=[[round(c, 4) for c in b] for b in (*posts, y_post)])
    k.inst("mount", "mount", "thrust ring (grounded)")
    w.W["mount"] = I4
    k.part("outer", "gimbal_ring", "outer gimbal ring (pitch)", mat, id=2 * o_ri, od=2 * o_ro,
           height=ring_h, pin_d=bore, pin_angles=[0.0, 90.0, 180.0, 270.0],
           boxes=[[round(c, 4) for c in (p_tab_x, p_tab_y, (z_tab0 + p_tab_z + 3.0) / 2, tab_w,
                                         o_ro - o_ri, p_tab_z + 3.0 - z_tab0)],
                  [round(c, 4) for c in ((rx0 + rx1) / 2, (ry0 + ry1) / 2,
                                         (ring_h / 2 - 0.5 + z_tab0 + 0.5) / 2, rx1 - rx0,
                                         ry1 - ry0, z_tab0 + 0.5 - (ring_h / 2 - 0.5))]],
           holes=[[p_tab_x, p_tab_y, p_tab_z, 1.0, 0.0, 0.0, rod_bore, tab_w + 2]])
    k.inst("outer", "outer", "outer gimbal ring")
    w.W["outer"] = I4
    k.part("tube", "gimbal_ring", f"motor mount tube ({md:g} mm)", mat, id=2 * t_ri,
           od=2 * t_ro, height=tube_h, pin_d=bore, pin_angles=[90.0, 270.0],
           lip_id=md - 4.0, lip_t=2.0,
           boxes=[[y_tab_x, sy * (rod_t / 2 + 1.0 + 1.35), (tube_h / 2 + y_tab_z + 3.0) / 2,
                   3.0, 2.7, y_tab_z + 3.0 - tube_h / 2 + 0.5] for sy in (-1, 1)],
           holes=[[y_tab_x, 0.0, y_tab_z, 0.0, 1.0, 0.0, rod_bore, 14.0]])
    k.inst("tube", "tube", "motor mount tube")
    w.W["tube"] = I4
    lim = [-(defl + 2.0), defl + 2.0]           # mechanical stops past the servo travel
    jp = w.revolute("pitch", "mount", "outer", (0, 0, 0), X, limits=lim,
                    design={**pin_design(pin_d, bearing="printed bushing", retention="e-clip"),
                            "rom_deg": [-defl, defl]})
    jy = w.revolute("yaw", "outer", "tube", (0, 0, 0), Y, limits=lim,
                    design={**pin_design(pin_d, bearing="printed bushing", retention="e-clip"),
                            "rom_deg": [-defl, defl]})
    # steel gimbal pins (2 per axis), fixed in the outer member's bores
    k.part("pin_pitch", "pin", "pitch pin", "Steel 1018", d=pin_d - 0.05,
           length=round(m_ro - o_ri - 0.6, 3))
    k.part("pin_yaw", "pin", "yaw pin", "Steel 1018", d=pin_d - 0.05,
           length=round(o_ro - t_ri - 0.6, 3))
    for n, ang in (("pitch_pin_a", 0.0), ("pitch_pin_b", 180.0)):
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        k.inst(n, "pin_pitch", n.replace("_", " "))
        w.fixed_pose(f"{n}_in_mount", "mount", n, (c * (o_ri + 0.3), s * (o_ri + 0.3), 0.0),
                     (c, s, 0.0), ((0.0, 0.0, 0.0), Z))
        k.plan.contacts += [("mount", n), ("outer", n)]
    for n, ang in (("yaw_pin_a", 90.0), ("yaw_pin_b", 270.0)):
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        k.inst(n, "pin_yaw", n.replace("_", " "))
        w.fixed_pose(f"{n}_in_outer", "outer", n, (c * (t_ri + 0.3), s * (t_ri + 0.3), 0.0),
                     (c, s, 0.0), ((0.0, 0.0, 0.0), Z))
        k.plan.contacts += [("outer", n), ("tube", n)]

    # ---- servos (fixed to the mount posts)
    k.part("servo", catalog=p["servo"], label=it["name"], material=None)
    k.inst("servo_pitch", "servo", "pitch servo")
    w.fixed_pose("servo_pitch_on_post", "mount", "servo_pitch", (p_face_x, p_hub_y, p_z),
                 (-1.0, 0.0, 0.0), (out_o, Z))
    k.inst("servo_yaw", "servo", "yaw servo")
    w.fixed_pose("servo_yaw_on_post", "mount", "servo_yaw", (y_hub_x, y_face_y, y_z),
                 (0.0, 1.0, 0.0), (out_o, Z))
    k.plan.contacts += [("mount", "servo_pitch"), ("mount", "servo_yaw")]
    k.part("rod_pitch", "link_bar", "pitch pushrod", mat, length=p_rod_l, width=5.0,
           thickness=rod_t, bore=rod_bore)
    k.part("rod_yaw", "link_bar", "yaw pushrod", mat, length=y_rod_l, width=5.0,
           thickness=rod_t, bore=rod_bore)
    k.part("clevis_pin", "pin", "pushrod clevis pin", "Steel 1018", d=rod_pin - 0.05,
           length=round(horn_t + clr + rod_t, 3))
    # servo travel for exactly -max..+max gimbal: each channel is a planar four-bar when the
    # other axis is at 0 (pitch in the YZ plane, yaw in the ZX plane); the pitch servo turns
    # about -X, so its joint value is minus the CCW angle about +X
    sp = [-_servo_deg((p_hub_y, p_z), hr_p, (p_tab_y, p_z), (p_tab_y, p_tab_z), p_rod_l, t)
          for t in (-defl, defl)]
    sy_ = [_servo_deg((y_z, y_hub_x), hr, (y_z, y_tab_x), (y_tab_z, y_tab_x), y_rod_l, t)
           for t in (-defl, defl)]
    s_rng_p = sorted(round(v, 3) for v in sp)
    s_rng_y = sorted(round(v, 3) for v in sy_)
    ratio_p = 2 * defl / (s_rng_p[1] - s_rng_p[0])     # gimbal deg per servo deg (mean)
    ratio_y = 2 * defl / (s_rng_y[1] - s_rng_y[0])
    s_lim_p = max(abs(v) for v in s_rng_p)
    s_lim_y = max(abs(v) for v in s_rng_y)
    act = {"item_id": p["servo"], "ratio": 1}
    # pitch: horn hub at (p_horn_x, p_hub_y, p_z), its tip toward -Y; pins along -X
    k.inst("horn_pitch", "horn_pitch", "pitch servo horn")
    hp = w.driven("pitch_servo", "servo_pitch", "horn_pitch", (p_face_x, p_hub_y, p_z),
                  (-1.0, 0.0, 0.0), (p_horn_x + horn_t, p_hub_y, p_z), limits=s_rng_p,
                  design={"bearing": "servo output bearing", "retention": "horn screw",
                          "actuator": act, "rom_deg": s_rng_p})
    k.part("horn_pitch", "link_plate", "pitch servo horn", mat,
           points=[[0.0, 0.0], w.local_xy("horn_pitch", (p_horn_x + horn_t, p_tab_y, p_z))],
           width=5.0, thickness=horn_t, bore=rod_bore)
    w.W["rod_pitch"] = _pose((p_rod_x0, p_tab_y, p_z), (0, 0, -1), (-1, 0, 0))
    k.inst("rod_pitch", "rod_pitch", "pitch pushrod")
    w.revolute("pitch_rod_top", "horn_pitch", "rod_pitch", (p_rod_x0, p_tab_y, p_z), (-1, 0, 0),
               design=pin_design(rod_pin, bearing="bushing (Z-bend / clevis)",
                                 retention="clevis clip"))
    w.loop("pitch_rod_tab", "rod_pitch", "outer", (p_rod_x0, p_tab_y, p_tab_z), (-1, 0, 0),
           "revolute", [hp])
    k.plan.contacts += [("servo_pitch", "horn_pitch"), ("horn_pitch", "rod_pitch"),
                        ("rod_pitch", "outer")]
    # yaw: horn hub at (y_hub_x, y_horn_y, y_z), tip toward +X; pins along +Y
    k.inst("horn_yaw", "horn_yaw", "yaw servo horn")
    hy = w.driven("yaw_servo", "servo_yaw", "horn_yaw", (y_hub_x, y_face_y, y_z),
                  (0.0, 1.0, 0.0), (y_hub_x, y_horn_y - horn_t, y_z), limits=s_rng_y,
                  design={"bearing": "servo output bearing", "retention": "horn screw",
                          "actuator": act, "rom_deg": s_rng_y})
    k.part("horn_yaw", "link_plate", "yaw servo horn", mat,
           points=[[0.0, 0.0], w.local_xy("horn_yaw", (y_tab_x, y_horn_y - horn_t, y_z))],
           width=5.0, thickness=horn_t, bore=rod_bore)
    w.W["clevis_yaw"] = _pose((y_tab_x, y_horn_y - horn_t, y_z), (-1, 0, 0), (0, 1, 0))
    k.inst("clevis_yaw", "clevis_pin", "yaw clevis pin")
    w.revolute("yaw_clevis", "horn_yaw", "clevis_yaw", (y_tab_x, y_horn_y, y_z), (0, 1, 0),
               design=pin_design(rod_pin, bearing="bushing (clevis)", retention="clevis clip"))
    w.W["rod_yaw"] = _pose((y_tab_x, y_rod_y0, y_z), (0, 0, -1), (0, 1, 0))
    k.inst("rod_yaw", "rod_yaw", "yaw pushrod")
    w.revolute("yaw_rod_top", "clevis_yaw", "rod_yaw", (y_tab_x, y_rod_y0 + rod_t / 2, y_z), X,
               design=pin_design(rod_pin, bearing="clevis (cross pin)",
                                 retention="clevis clip"))
    w.loop("yaw_rod_tab", "rod_yaw", "tube", (y_tab_x, y_rod_y0 + rod_t / 2, y_tab_z), Y,
           "ball", [hy])
    k.plan.contacts += [("servo_yaw", "horn_yaw"), ("horn_yaw", "clevis_yaw"),
                        ("clevis_yaw", "rod_yaw"), ("rod_yaw", "tube"), ("horn_yaw", "rod_yaw")]
    k.plan.inputs += [hp, hy]
    k.plan.dof_intended = 2
    k.port("mount", "mount", "mount", (0, 0, -ring_h / 2), (0, 0, -1),
           "thrust ring flange: glue to the airframe (aft face)")
    k.port("motor", "end_effector", "tube", (0, 0, tube_h / 2 - 2.0), (0, 0, -1),
           f"motor bore: a {md:g} mm motor's forward end bears on the lip, nozzle toward -Z")
    k.port("output", "rot", "tube", (0, 0, 0), (0, 0, -1), "gimballed thrust axis")

    # ---- sizing
    stall = (it.get("perf") or {}).get("stall_torque_nm")
    rpm = (it.get("perf") or {}).get("no_load_rpm") or 100.0
    m_kg = p["motor_mass_g"] / 1000
    L = p["motor_len"] / 1000
    zc = (tube_h / 2 - 2.0) / 1000 - L / 2          # motor CoM below the pivot (negative)
    j_kgm2 = m_kg * (L * L / 12 + zc * zc) + 0.004 * (o_ro / 1000) ** 2
    th = math.radians(defl)
    # bang-bang swing -max..+max in slew_s: alpha = 4 * (2 th) / t^2
    alpha = 8 * th / p["slew_s"] ** 2
    t_inert = j_kgm2 * alpha
    t_offset = m_kg * 9.81 * 5.0 * abs(zc) * math.sin(th)     # 5 g boost, CoM off the pivot
    t_gimbal = t_inert + t_offset
    t_servo = t_gimbal * max(ratio_p, ratio_y)
    servo_rate = rpm * 6.0                                     # deg/s at the horn
    need_rate = 2 * defl / p["slew_s"] / min(ratio_p, ratio_y)
    F = p["peak_thrust_n"]
    shear = sizing.pin_shear_mpa(F / 2, pin_d, 1)              # 2 pins, single shear each
    bearing = sizing.lug_bearing_mpa(F / 2, pin_d, 3.0)        # ring wall 3 mm
    from ..sim.materials import get_material
    ys = get_material(mat).yield_mpa
    k.plan.sizing = {
        "pitch_deg_per_servo_deg": round(ratio_p, 3), "yaw_deg_per_servo_deg": round(ratio_y, 3),
        "servo_travel_deg": [-max(s_lim_p, s_lim_y), max(s_lim_p, s_lim_y)],
        "motor_inertia_about_pivot_kgm2": round(j_kgm2, 7),
        "slew_torque_gimbal_nm": round(t_gimbal, 4), "slew_torque_servo_nm": round(t_servo, 4),
        "servo_stall_nm": stall,
        "servo_torque_margin": round(stall / t_servo, 2) if stall and t_servo else None,
        "servo_rate_deg_s": round(servo_rate, 1), "needed_rate_deg_s": round(need_rate, 1),
        "pin_shear_mpa": round(shear, 2), "lug_bearing_mpa": round(bearing, 2),
        "lug_bearing_sf": round(ys / bearing, 2) if bearing else None,
        "motor_com_below_pivot_mm": round(-zc * 1000, 1), "servo_layout": layout,
        "stack_height_mm": round(max(p_z, y_z) + out_top, 1)}
    k.plan.transmission = {"pitch": f"gimbal ~ asin({hr_p:g} sin(servo) / {p_tab_y:.1f})",
                           "yaw": f"gimbal ~ asin({hr:g} sin(servo) / {y_tab_x:.1f})"}
    k.plan.rom = {"pitch": [-defl, defl], "yaw": [-defl, defl],
                  "pitch_servo": s_rng_p, "yaw_servo": s_rng_y}
    if stall and t_servo and stall / t_servo < 2:
        k.plan.warnings.append(f"servo torque margin {stall / t_servo:.1f}x < 2 for a "
                               f"{p['slew_s']:g} s swing: slow the slew, lengthen the tabs or "
                               "use a stronger servo")
    if need_rate > servo_rate:
        k.plan.warnings.append(f"the servo needs {need_rate:.0f} deg/s for a {p['slew_s']:g} s "
                               f"swing but turns {servo_rate:.0f} deg/s: the gimbal is "
                               "rate-limited (raise horn_r or accept a slower slew)")
    if ys / bearing < 2:
        k.plan.warnings.append(f"gimbal lug bearing SF {ys / bearing:.1f} < 2 at "
                               f"{F:g} N: thicker ring walls, bigger pins or a stronger "
                               "material")
    k.plan.warnings.append("the motor is not part of the mechanism: add it (rocket_motor) so "
                           "the mass, CG and slew inertia include it; with the motor CoM "
                           f"{-zc * 1000:.0f} mm below the pivot, thrust and boost load add "
                           "a restoring torque the servos must hold")
    k.plan.notes = ("pitch = outer ring about X, yaw = tube about Y; drive pitch_servo / "
                    "yaw_servo (the gimbal joints follow through the pushrod loops). The yaw "
                    "rod's clevis is a universal so pitch does not bind it (a little "
                    "pitch->yaw cross-coupling is real: calibrate it out in the flight code).")
    return k.plan
