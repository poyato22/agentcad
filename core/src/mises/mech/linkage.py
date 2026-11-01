"""Linkages and intermittent mechanisms (research 2026-11-01 §2.2 rows 19-29): closed chains
built with the planar-linkage kit (pins on +Z, one Z layer per link, loop closures solved by the
assembly solve), plus Geneva, cam and ratchet drives."""
from __future__ import annotations

import math

from . import sizing
from .card import Z, fr
from .kit import Kit, circle_intersect, pin_design, planar_linkage
from .library import P, mechanism

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
_LINK = {"width": P(10.0, 5.0, 40.0, "mm", "link width"),
         "thickness": P(4.0, 2.0, 15.0, "mm", "link thickness"),
         "pin_d": P(3.0, 2.0, 10.0, "mm", "pin diameter")}


def _kw(p: dict) -> dict:
    return {"width": p["width"], "thickness": p["thickness"], "pin_d": p["pin_d"]}


def _sample(fn, lo: float, hi: float, n: int = 37) -> list[list[float]]:
    out = []
    for k in range(n):
        t = lo + (hi - lo) * k / (n - 1)
        v = fn(t)
        if v is not None:
            out.append([round(t, 3), round(v, 4)])
    return out


def _unwrap(curve: list[list[float]]) -> list[list[float]]:
    out, prev = [], None
    for t, v in curve:
        if prev is not None:
            while v - prev > 180:
                v -= 360
            while v - prev < -180:
                v += 360
        out.append([t, round(v, 4)])
        prev = v
    return out


@mechanism("four_bar", "Four-bar linkage", "linkage",
           "Crank, coupler, rocker and ground: turns a rotation into an oscillation or a coupler "
           "curve (Grashof crank-rocker by default).",
           ["convert rotation to oscillation", "rocker", "coupler curve", "path generation",
            "wiper", "leg", "linkage"],
           {"ground": P(100.0, 10.0, 1000.0, "mm", "ground link d (pivot to pivot)"),
            "crank": P(40.0, 5.0, 1000.0, "mm", "input crank a"),
            "coupler": P(120.0, 5.0, 1000.0, "mm", "coupler b"),
            "rocker": P(80.0, 5.0, 1000.0, "mm", "output rocker c"),
            "coupler_point_u": P(0.0, -1000.0, 1000.0, "mm", "coupler point along the coupler "
                                 "(0 = none)"),
            "coupler_point_v": P(0.0, -1000.0, 1000.0, "mm", "... and across it"),
            "crank_deg": P(60.0, -360.0, 360.0, "deg", "initial crank angle"),
            "input_torque_nm": P(0.5, 0.0, 100.0, "N*m", "crank torque"), **_LINK},
           ["grashof", "transmission_angle", "toggle", "rom_sweep"], "rot->rot")
def four_bar(p: dict):
    d, a, b, c = p["ground"], p["crank"], p["coupler"], p["rocker"]
    t0 = p["crank_deg"]
    A = (a * math.cos(math.radians(t0)), a * math.sin(math.radians(t0)))
    B = circle_intersect(A, b, (d, 0.0), c, side=-1)
    nodes = {"O2": (0.0, 0.0), "O4": (d, 0.0), "A": A, "B": B}
    coupler = ["A", "B"]
    if p["coupler_point_u"] or p["coupler_point_v"]:
        u, v = p["coupler_point_u"], p["coupler_point_v"]
        ang = math.atan2(B[1] - A[1], B[0] - A[0])
        nodes["P"] = (A[0] + u * math.cos(ang) - v * math.sin(ang),
                      A[1] + u * math.sin(ang) + v * math.cos(ang))
        coupler.append("P")
    k = Kit("ground")
    info = planar_linkage(k, nodes, [("crank", ["O2", "A"]), ("coupler", coupler),
                                     ("rocker", ["O4", "B"])], ["O2", "O4"], ("crank", "O2"),
                          **_kw(p))
    g = sizing.grashof([d, a, b, c])
    # which assembly branch the start pose is on: compare with the analytic closure
    t4 = math.degrees(math.atan2(B[1], B[0] - d))
    br = min((1, -1), key=lambda s: abs(((sizing.freudenstein_rocker(t0, a, b, c, d, s) or 1e9)
                                         - t4 + 180) % 360 - 180))
    full = g["kind"] in ("crank-rocker", "double crank (drag link)")
    lo, hi = (0.0, 360.0) if full else (t0 - 60, t0 + 60)
    curve = _unwrap(_sample(lambda t: sizing.freudenstein_rocker(t, a, b, c, d, br), lo, hi))
    mus = [sizing.transmission_angle_deg(t, a, b, c, d) for t, _ in curve]
    k.port("input", "rot", "crank", (0, 0, 0), Z, "crank pivot")
    k.port("output", "rot", "rocker", (d, 0, 0), Z, "rocker pivot")
    if "P" in nodes:
        k.port("coupler_point", "end_effector", "coupler", (*nodes["P"], 0.0), Z)
    k.plan.transmission = {"curve": curve, "curve_axes": ["crank deg", "rocker deg"]}
    outs = [v for _, v in curve]
    k.plan.rom = {info["driver"]: [lo, hi], "rocker_deg": [min(outs), max(outs)] if outs else None}
    k.plan.sizing = {**g, "transmission_angle_deg": [round(min(mus), 2), round(max(mus), 2)]
                     if mus else None, "branch": br}
    if not g["grashof"]:
        k.plan.warnings.append("non-Grashof: no link turns fully; drive it within its ROM")
    if mus and (min(mus) < 40 or max(mus) > 140):
        k.plan.warnings.append(f"transmission angle {min(mus):.0f}-{max(mus):.0f} deg leaves "
                               "40-140: poor force transmission near the extremes")
    return k.plan


@mechanism("parallelogram_linkage", "Parallelogram linkage", "linkage",
           "Two equal parallel cranks and a coupler: the coupler translates without rotating.",
           ["keep orientation", "translate without rotation", "parallel motion", "lift platform",
            "linkage"],
           {"length": P(80.0, 10.0, 1000.0, "mm", "crank length"),
            "spacing": P(60.0, 10.0, 1000.0, "mm", "pivot spacing (= coupler)"),
            "crank_deg": P(60.0, 10.0, 170.0, "deg", "initial angle"), **_LINK},
           ["toggle", "rom_sweep"], "rot->lin")
def parallelogram_linkage(p: dict):
    L, s, t0 = p["length"], p["spacing"], p["crank_deg"]
    A = (L * math.cos(math.radians(t0)), L * math.sin(math.radians(t0)))
    nodes = {"O2": (0.0, 0.0), "O4": (s, 0.0), "A": A, "B": (A[0] + s, A[1])}
    k = Kit("ground")
    info = planar_linkage(k, nodes, [("crank_a", ["O2", "A"]), ("coupler", ["A", "B"]),
                                     ("crank_b", ["O4", "B"])], ["O2", "O4"],
                          ("crank_a", "O2"), limits={"crank_a": [10.0, 170.0]}, **_kw(p))
    k.port("input", "rot", "crank_a", (0, 0, 0), Z)
    k.port("output", "end_effector", "coupler", (*A, 0.0), Z, "coupler (stays level)")
    k.plan.rom = {info["driver"]: [10.0, 170.0]}
    k.plan.transmission = {"output": "coupler translates on a circle of radius = crank"}
    k.plan.warnings.append("singular when the links line up with the ground (0 / 180 deg): "
                           "the limits keep it out; add a third bar for full rotation")
    return k.plan


@mechanism("watt_linkage", "Watt straight-line linkage", "linkage",
           "Two opposed rockers and a short coupler whose midpoint moves on a near-straight line "
           "(axle locating, suspension).",
           ["straight line", "suspension", "axle location", "guide without a rail", "linkage"],
           {"rocker": P(60.0, 10.0, 1000.0, "mm", "rocker length"),
            "coupler": P(30.0, 5.0, 500.0, "mm", "coupler length"),
            "swing_deg": P(20.0, 5.0, 40.0, "deg", "rocker swing each way"), **_LINK},
           ["straightness", "rom_sweep"], "rot->lin")
def watt_linkage(p: dict):
    r, c, sw = p["rocker"], p["coupler"], p["swing_deg"]
    nodes = {"O2": (0.0, 0.0), "A": (r, 0.0), "B": (r, c), "O4": (2 * r, c), "M": (r, c / 2)}
    k = Kit("ground")
    info = planar_linkage(k, nodes, [("rocker_a", ["O2", "A"]), ("coupler", ["A", "B", "M"]),
                                     ("rocker_b", ["O4", "B"])], ["O2", "O4"],
                          ("rocker_a", "O2"), limits={"rocker_a": [-sw, sw]}, **_kw(p))
    k.port("input", "rot", "rocker_a", (0, 0, 0), Z)
    k.port("output", "end_effector", "coupler", (r, c / 2, 0), Z, "midpoint (straight path)")
    k.plan.rom = {info["driver"]: [-sw, sw]}
    k.plan.transmission = {"path": "near-straight vertical line through the midpoint"}
    return k.plan


@mechanism("chebyshev_linkage", "Chebyshev straight-line linkage", "linkage",
           "Crossed four-bar (ground 2, rockers 2.5, coupler 1) whose coupler midpoint runs "
           "nearly straight.", ["straight line", "walking", "guide", "linkage"],
           {"unit": P(20.0, 5.0, 200.0, "mm", "the length 1 of the 2 : 2.5 : 1 ratios"),
            "swing_deg": P(15.0, 5.0, 30.0, "deg", "rocker swing each way"), **_LINK},
           ["straightness", "rom_sweep"], "rot->lin")
def chebyshev_linkage(p: dict):
    u, sw = p["unit"], p["swing_deg"]
    nodes = {"O2": (0.0, 0.0), "O4": (2 * u, 0.0), "A": (0.5 * u, 2 * u), "B": (1.5 * u, 2 * u),
             "M": (u, 2 * u)}
    k = Kit("ground")
    info = planar_linkage(k, nodes, [("rocker_a", ["O2", "B"]), ("coupler", ["A", "B", "M"]),
                                     ("rocker_b", ["O4", "A"])], ["O2", "O4"],
                          ("rocker_a", "O2"),
                          limits={"rocker_a": [53.13 - sw, 53.13 + sw]}, **_kw(p))
    k.port("input", "rot", "rocker_a", (0, 0, 0), Z)
    k.port("output", "end_effector", "coupler", (u, 2 * u, 0), Z, "midpoint (straight path)")
    k.plan.rom = {info["driver"]: [53.13 - sw, 53.13 + sw]}
    k.plan.transmission = {"path": "near-straight horizontal line through the midpoint"}
    return k.plan


# Theo Jansen's "holy numbers" (Strandbeest), in his units
_JANSEN = dict(a=38.0, b=41.5, c=39.3, d=40.1, e=55.8, f=39.4, g=36.7, h=65.7, i=49.0, j=50.0,
               k=61.9, l=7.8, m=15.0)


def jansen_nodes(scale: float, crank_deg: float) -> dict[str, tuple[float, float]]:
    """Jansen leg node positions (crank centre at the origin) for a crank angle."""
    J = {k: v * scale for k, v in _JANSEN.items()}
    O, Q = (0.0, 0.0), (-J["a"], -J["l"])
    t = math.radians(crank_deg)
    A = (J["m"] * math.cos(t), J["m"] * math.sin(t))
    C = circle_intersect(A, J["j"], Q, J["b"], side=1)
    if C[1] < Q[1]:
        C = circle_intersect(A, J["j"], Q, J["b"], side=-1)
    D = circle_intersect(A, J["k"], Q, J["c"], side=-1)
    if D[1] > Q[1]:
        D = circle_intersect(A, J["k"], Q, J["c"], side=1)
    # the upper triangle: b = Q-C, d = Q-E, e = C-E (E outboard, left of the pivot)
    E = min((circle_intersect(Q, J["d"], C, J["e"], side=s) for s in (1, -1)),
            key=lambda pt: pt[0])
    F = min((circle_intersect(E, J["f"], D, J["g"], side=s) for s in (1, -1)),
            key=lambda pt: pt[0])
    G = min((circle_intersect(F, J["h"], D, J["i"], side=s) for s in (1, -1)),
            key=lambda pt: pt[1])
    return {"O": O, "Q": Q, "A": A, "C": C, "D": D, "E": E, "F": F, "G": G}


@mechanism("jansen_leg", "Theo Jansen leg", "leg",
           "One crank drives a 12-link leg (two loops of the Strandbeest's holy numbers) whose "
           "foot traces a flat stride and a lifted return.",
           ["walk", "walking robot", "leg", "strandbeest", "single motor walker"],
           {"scale": P(1.0, 0.3, 5.0, "", "x Jansen's numbers (crank 15 at 1.0)"),
            "crank_deg": P(0.0, -180.0, 180.0, "deg", "initial crank angle"), **_LINK},
           ["foot_path", "rom_sweep", "crank_torque"], "rot->path")
def jansen_leg(p: dict):
    s = p["scale"]
    nodes = jansen_nodes(s, p["crank_deg"])
    k = Kit("ground")
    links = [("crank", ["O", "A"]), ("link_j", ["A", "C"]), ("link_k", ["A", "D"]),
             ("triangle_bde", ["Q", "C", "E"]), ("link_c", ["Q", "D"]), ("link_f", ["E", "F"]),
             ("foot_ghi", ["D", "F", "G"])]
    info = planar_linkage(k, nodes, links, ["O", "Q"], ("crank", "O"), **_kw(p))
    k.port("input", "rot", "crank", (0, 0, 0), Z, "crank (pair legs at 180 deg)")
    k.port("foot", "end_effector", "foot_ghi", (*nodes["G"], 0.0), Z, "foot")
    path = []
    for deg in range(0, 360, 15):
        try:
            g = jansen_nodes(s, deg)["G"]
            path.append([deg, round(g[0], 2), round(g[1], 2)])
        except ValueError:
            continue
    xs, ys = [q[1] for q in path], [q[2] for q in path]
    k.plan.transmission = {"foot_path": path, "path_axes": ["crank deg", "x mm", "y mm"]}
    k.plan.sizing = {"stride_mm": round(max(xs) - min(xs), 1), "lift_mm": round(max(ys) - min(ys), 1)}
    k.plan.rom = {info["driver"]: [0.0, 360.0]}
    k.plan.notes = "pair two legs on one crank at 180 deg (three pairs for a stable walker)"
    return k.plan


@mechanism("slider_crank", "Slider-crank", "linkage",
           "Crank and connecting rod drive a slider: piston, pump, pusher.",
           ["convert rotation to linear", "reciprocating", "piston", "pump", "pusher", "punch"],
           {"crank": P(20.0, 5.0, 500.0, "mm", "crank radius r"),
            "rod": P(70.0, 15.0, 2000.0, "mm", "rod length l (l/r >= 3)"),
            "offset": P(0.0, -200.0, 200.0, "mm", "slider line offset e"),
            "crank_deg": P(60.0, -360.0, 360.0, "deg", "initial crank angle"),
            "input_torque_nm": P(0.5, 0.0, 100.0, "N*m", "crank torque"), **_LINK},
           ["rod_ratio", "transmission_angle", "stroke", "rom_sweep"], "rot->lin")
def slider_crank(p: dict):
    r, l, e, t0 = p["crank"], p["rod"], p["offset"], p["crank_deg"]
    if l < r + abs(e):
        raise ValueError("the rod must be longer than crank + offset")
    bore = p["pin_d"] + 0.3
    th = p["thickness"]
    x0 = sizing.slider_crank_x(t0, r, l, e)
    k = Kit("ground")
    k.part("ground", "frame_plate", "base", "PETG", length=r + l + r + 60, width=4 * r + 40,
           thickness=5.0, bores=[[0.0, 0.0, bore]], origin_x=-r - 10)
    k.inst("ground", "ground", "base")
    k.part("crank", "link_bar", "crank", "PETG", length=r, width=p["width"], thickness=th,
           bore=bore)
    ci = k.inst("crank")
    jin = k.joint("crank_rev", "revolute", "ground", fr((0, 0, 0)), ci, fr(), value=t0,
                  design=pin_design(p["pin_d"]))
    k.part("rod", "link_bar", "connecting rod", "PETG", length=l, width=p["width"], thickness=th,
           bore=bore)
    ri = k.inst("rod")
    A = (r * math.cos(math.radians(t0)), r * math.sin(math.radians(t0)))
    rod_ang = math.degrees(math.atan2(e - A[1], x0 - A[0]))
    jr = k.joint("rod_rev", "revolute", ci, fr((r, 0, th + 1)), ri, fr(), value=rod_ang - t0,
                 design=pin_design(p["pin_d"]))
    k.part("slider", "slider_block", "slider", "POM", length=30.0, width=20.0, height=th,
           pin_d=bore)
    si = k.inst("slider")
    js = k.joint("slide", "slider", "ground", fr((0, e, 0), X), si, fr((0, 0, 0), X), value=x0,
                 limits=[l - r - abs(e) - 1, l + r + 1],
                 design={"bearing": "printed guide", "clearance_mm": 0.3})
    k.loop("loop_pin", "rod", fr((l, 0, 0)), si, fr((0, 0, th + 1)), drivers=[jin])
    k.plan.contacts += [("ground", ci), (ci, ri), (ri, si), ("ground", si)]
    k.plan.inputs.append(jin)
    k.plan.planar = True
    k.port("input", "rot", ci, (0, 0, 0), Z, "crank")
    k.port("output", "lin", si, (x0, e, 0), X, "slider")
    xs = [sizing.slider_crank_x(t, r, l, e) for t in range(0, 360, 5)]
    k.plan.transmission = {"curve": [[t, round(sizing.slider_crank_x(t, r, l, e), 3)]
                                     for t in range(0, 361, 15)],
                           "curve_axes": ["crank deg", "slider mm"]}
    k.plan.sizing = {"stroke_mm": round(max(xs) - min(xs), 3), "rod_ratio": round(l / r, 3),
                     "peak_force_n": round(p["input_torque_nm"] * 1000 / r, 1)}
    k.plan.rom = {jin: [0.0, 360.0], js: [round(min(xs), 2), round(max(xs), 2)]}
    if l / r < 3:
        k.plan.warnings.append(f"l/r = {l / r:.2f} < 3: high side load on the slider [J]")
    return k.plan


@mechanism("scotch_yoke", "Scotch yoke", "linkage",
           "A crank pin in a slotted yoke: pure harmonic linear motion x = r sin(theta).",
           ["convert rotation to linear", "harmonic motion", "reciprocating", "shaker"],
           {"crank": P(20.0, 5.0, 300.0, "mm", "crank radius"),
            "crank_deg": P(30.0, -360.0, 360.0, "deg", "initial crank angle"), **_LINK},
           ["slot_wear", "rom_sweep"], "rot->lin")
def scotch_yoke(p: dict):
    r, t0, th = p["crank"], p["crank_deg"], p["thickness"]
    bore = p["pin_d"] + 0.3
    k = Kit("ground")
    k.part("ground", "frame_plate", "base", "PETG", length=4 * r + 80, width=4 * r + 40,
           thickness=5.0, bores=[[0.0, 0.0, bore]], origin_x=-2 * r - 30)
    k.inst("ground", "ground", "base")
    k.part("yoke", "slider_block", "yoke", "POM", length=bore + 14, width=2 * r + 30, height=th,
           pin_d=0.0, slot_w=bore + 4.6, slot_l=2 * r + 14)
    yi = k.inst("yoke")
    x = r * math.cos(math.radians(t0))
    y = r * math.sin(math.radians(t0))
    jy = k.joint("yoke_slide", "slider", "ground", fr((0, 0, th + 1), X), yi, fr((0, 0, 0), X),
                 value=x, limits=[-r - 1, r + 1], design={"bearing": "guide rods"})
    k.part("block", "slider_block", "slot block", "POM", length=bore + 4, width=10.0,
           height=th, pin_d=bore)
    bi = k.inst("block")
    jb = k.joint("block_slide", "slider", yi, fr((0, 0, 0), Y), bi, fr((0, 0, 0), Y), value=y,
                 limits=[-r - 1, r + 1])
    k.part("crank", "link_bar", "crank", "PETG", length=r, width=p["width"], thickness=th,
           bore=bore)
    ci = k.inst("crank")
    jin = k.joint("crank_rev", "revolute", bi, fr((0, 0, -th - 1)), ci, fr((r, 0, 0)), value=t0,
                  design=pin_design(p["pin_d"]))
    k.loop("loop_pivot", "ground", fr((0, 0, 0)), ci, fr((0, 0, 0)), drivers=[jin])
    k.plan.contacts += [("ground", yi), (yi, bi), (bi, ci), ("ground", ci)]
    k.plan.inputs.append(jin)
    k.plan.planar = True
    k.port("input", "rot", ci, (0, 0, 0), Z, "crank")
    k.port("output", "lin", yi, (x, 0, th + 1), X, "yoke")
    k.plan.transmission = {"x": "r cos(theta) (pure harmonic)", "stroke_mm": 2 * r}
    k.plan.rom = {jin: [0.0, 360.0]}
    k.plan.notes = ("modelled as yoke slider + slot block + crank, closed at the crank pivot; "
                    "the block slides in the yoke slot")
    return k.plan


@mechanism("geneva_drive", "Geneva drive", "intermittent",
           "A pin crank indexes a slotted wheel 360/n per turn with a locked dwell.",
           ["intermittent motion", "index", "step", "dwell", "turntable"],
           {"slots": P(4, 3, 12, "", "wheel slots"), "center": P(40.0, 15.0, 300.0, "mm", "centre distance"),
            "pin_d": P(5.0, 2.0, 15.0, "mm", "drive pin"), "width": P(5.0, 3.0, 20.0, "mm", "plate")},
           ["slot_clearance", "impact"], "rot->rot")
def geneva_drive(p: dict):
    n, C, w = p["slots"], p["center"], p["width"]
    k = Kit("ground")
    k.part("ground", "frame_plate", "base", "PETG", length=C + 2 * C + 20, width=2.4 * C,
           thickness=5.0, bores=[[0.0, 0.0, 6.3], [C, 0.0, 6.3]], origin_x=-C - 10)
    k.inst("ground", "ground", "base")
    k.part("wheel", "geneva_wheel", f"Geneva wheel {n} slots", "POM", slots=n, center_dist=C,
           pin_d=p["pin_d"], width=w, bore=6.0)
    wi = k.inst("wheel")
    jw = k.joint("wheel_rev", "revolute", "ground", fr((0, 0, 1.0 + w)), wi, fr(),
                 design=pin_design(6.0, bearing="bushing", retention="e-clip"))
    k.part("driver", "geneva_driver", "Geneva driver", "PETG", slots=n, center_dist=C,
           pin_d=p["pin_d"], width=w, bore=6.0)
    di = k.inst("driver")
    jd = k.joint("driver_rev", "revolute", "ground", fr((C, 0, 0.5)), di, fr(), value=180.0,
                 design=pin_design(6.0, bearing="bushing", retention="e-clip"))
    k.plan.inputs += [jd, jw]
    k.plan.contacts += [("wheel", "driver"), ("ground", "wheel"), ("ground", "driver")]
    k.plan.dof_intended = 2
    k.port("input", "rot", di, (C, 0, 0), Z, "driver")
    k.port("output", "rot", wi, (0, 0, 0), Z, "indexed wheel")
    engaged = 180.0 - 360.0 / n
    k.plan.transmission = {"index_deg": 360.0 / n, "engaged_deg_per_turn": engaged,
                           "dwell_fraction": round(1 - engaged / 360.0, 4)}
    k.plan.warnings.append("the intermittent coupling is not modelled: animate the wheel by "
                           f"{360 / n:g} deg per driver turn (the drive pin engages for "
                           f"{engaged:g} deg of it)")
    return k.plan


@mechanism("cam_follower", "Plate cam and translating roller follower", "intermittent",
           "A cycloidal rise-dwell-fall cam lifts a guided roller follower.",
           ["programmed motion", "lift", "valve", "dwell", "convert rotation to linear"],
           {"base_r": P(20.0, 8.0, 200.0, "mm", "base circle"), "lift": P(8.0, 1.0, 100.0, "mm", "lift"),
            "rise_deg": P(120.0, 30.0, 270.0, "deg", "rise"), "dwell_deg": P(60.0, 0.0, 180.0, "deg", "top dwell"),
            "fall_deg": P(120.0, 30.0, 270.0, "deg", "fall"), "width": P(6.0, 3.0, 30.0, "mm", "cam width")},
           ["pressure_angle", "undercut", "spring_preload"], "rot->lin")
def cam_follower(p: dict):
    if p["rise_deg"] + p["dwell_deg"] + p["fall_deg"] > 360:
        raise ValueError("rise + dwell + fall must fit in 360 deg")
    rb, h, w = p["base_r"], p["lift"], p["width"]
    k = Kit("ground")
    k.part("ground", "frame_plate", "base", "PETG", length=2 * rb + 2 * h + 40,
           width=2 * (rb + h) + 80, thickness=5.0, bores=[[0.0, 0.0, 5.3]],
           origin_x=-rb - h - 10)
    k.inst("ground", "ground", "base")
    k.part("cam", "cam_disc", "cam", "POM", base_r=rb, lift=h, width=w, bore=5.0,
           rise_deg=p["rise_deg"], dwell_deg=p["dwell_deg"], fall_deg=p["fall_deg"])
    ci = k.inst("cam")
    jc = k.joint("cam_rev", "revolute", "ground", fr((0, 0, 1.0)), ci, fr(),
                 design={"bearing": "bearing_625_deep_groove", "retention": "set screw"})
    k.part("follower", "follower", "roller follower", "POM", rod_d=6.0, length=40.0,
           roller_d=10.0, roller_w=w)
    fi = k.inst("follower")
    jf = k.joint("follower_slide", "slider", "ground", fr((0, rb + 5.0, 1.0 + w / 2), Y), fi,
                 fr((0, 0, 0)), limits=[0.0, h], design={"bearing": "bushing guide",
                                                         "retention": "spring"})
    k.plan.inputs += [jc, jf]
    k.plan.dof_intended = 2
    k.plan.contacts += [("cam", "follower"), ("ground", "cam"), ("ground", "follower")]
    k.port("input", "rot", ci, (0, 0, 1), Z, "cam shaft")
    k.port("output", "lin", fi, (0, rb + 5, 1 + w / 2), Y, "follower")
    beta = math.radians(p["rise_deg"])
    pa = math.degrees(math.atan(h * 2 / beta / (rb + h / 2)))
    k.plan.sizing = {"max_pressure_angle_deg": round(pa, 1)}
    k.plan.transmission = {"profile": "cycloidal rise / dwell / cycloidal fall",
                           "lift_mm": h}
    if pa > 30:
        k.plan.warnings.append(f"pressure angle ~{pa:.0f} deg > 30 for a translating follower: "
                               "raise the base circle or lengthen the rise")
    k.plan.warnings.append("the cam-follower contact is not a joint coupling: animate the "
                           "follower with the lift profile s(theta) (card transmission)")
    return k.plan


@mechanism("ratchet_pawl", "Ratchet and pawl", "intermittent",
           "A toothed wheel that turns one way; a spring pawl blocks the other.",
           ["one way", "anti reverse", "hold", "winch", "brake"],
           {"teeth": P(16, 8, 60, "", "teeth"), "od": P(40.0, 15.0, 200.0, "mm", "wheel OD"),
            "width": P(5.0, 3.0, 20.0, "mm", "thickness")}, ["tooth_shear", "spring"], "rot->rot")
def ratchet_pawl(p: dict):
    od, w = p["od"], p["width"]
    k = Kit("ground")
    k.part("ground", "frame_plate", "base", "PETG", length=od + 50, width=od + 30, thickness=5.0,
           bores=[[0.0, 0.0, 6.3], [od / 2 + 12, od / 2 - 2, 3.3]], origin_x=-od / 2 - 5)
    k.inst("ground", "ground", "base")
    k.part("wheel", "ratchet_wheel", "ratchet wheel", "POM", teeth=p["teeth"], od=od, width=w,
           bore=6.0)
    wi = k.inst("wheel")
    jw = k.joint("wheel_rev", "revolute", "ground", fr((0, 0, 1.0)), wi, fr(),
                 design=pin_design(6.0, bearing="bushing"))
    k.part("pawl", "pawl", "pawl", "POM", length=od * 0.45, width=6.0, thickness=w, bore=3.3)
    pi_ = k.inst("pawl")
    px, py, pl = od / 2 + 12, od / 2 - 2, od * 0.45
    # the pawl's round tip (r 3) just clear of the tooth tips: |tip| = od/2 + 3.3
    lo, hi = 180.0, 250.0
    for _ in range(60):
        mid = (lo + hi) / 2
        tip = math.hypot(px + pl * math.cos(math.radians(mid)), py + pl * math.sin(math.radians(mid)))
        lo, hi = (mid, hi) if tip < od / 2 + 3.3 else (lo, mid)
    rest = round(lo, 2)
    jp = k.joint("pawl_rev", "revolute", "ground", fr((px, py, 1.0)), pi_,
                 fr(), value=rest, limits=[rest, rest + 25.0],
                 design=pin_design(3.0, bearing="bushing", retention="torsion spring"))
    k.plan.inputs += [jw, jp]
    k.plan.dof_intended = 2
    k.plan.contacts += [("wheel", "pawl"), ("ground", "wheel"), ("ground", "pawl")]
    k.port("input", "rot", wi, (0, 0, 1), Z, "wheel")
    k.plan.warnings.append("the one-way lock is not modelled: keep the pawl spring-loaded on the "
                           "teeth")
    return k.plan
