"""Hand-made reference designs for the AgentCAD-Physics graders: each shows its task is feasible
and that the grader passes a correct design (checker self-test only; never shown to either
system).

    cd core && uv run python -m evals.bench.physics.reference OUT [task ...]
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import build123d as b3
from build123d import Box, Cylinder, Pos, Rot

from ..functional.reference import involute_gear, write
from . import tasks as T


def ph01():
    # L-free: a tapered I-ish arm from the wall to a boss with the Ø5 hole (axis Y)
    base = Pos(1.5, 0, 0) * Box(3, 24, 24)
    with b3.BuildSketch(b3.Plane.XZ) as sk:
        b3.Polygon((0, -10), (0, 10), (60, 3.5), (60, -3.5), align=None)
    web = b3.extrude(sk.sketch, 2.5, both=True)
    with b3.BuildSketch(b3.Plane.XY) as sk2:
        b3.Polygon((0, -10), (0, 10), (60, 6), (60, -6), align=None)
    flange_top = Pos(0, 0, 0) * b3.extrude(sk2.sketch, 1.5)
    boss = Pos(60, 0, 0) * Rot(90, 0, 0) * Cylinder(6, 12)
    part = base + web + boss
    part -= Pos(60, 0, 0) * Rot(90, 0, 0) * Cylinder(2.5, 30)
    del flange_top
    return {"bracket": part}


def ph02():
    t = 1.6
    p = Pos(75, 0, t / 2) * Box(150, 100, t)
    for y in (-49.3, -24.5, 0, 24.5, 49.3):
        p += Pos(75, y, t + 7) * Box(150, 1.4, 14)
    return {"plate": p}


def ph03():
    return {"mast": Pos(0, 0, 150) * (Cylinder(15, 300) - Cylinder(14, 300))}


def ph04():
    return {"column": Pos(0, 0, 125) * (Cylinder(12.5, 250) - Cylinder(11.5, 250))}


def ph05():
    def link(x0, x1, w, t):
        L = x1 - x0
        b = Pos((x0 + x1) / 2, 0, 0) * b3.extrude(b3.SlotCenterToCenter(L, w), t / 2, both=True)
        return b - Pos(x0, 0, 0) * Cylinder(4, 3 * t) - Pos(x1, 0, 0) * Cylinder(4, 3 * t)
    ua = link(0, 200, 22, 3.5) - Pos(100, 0, 0) * Box(150, 8, 10)     # a lightened, deep link
    fa = Pos(0, 0, 6) * link(200, 350, 12, 3)
    return {"upper_arm": ua, "forearm": fa}


def ph06():
    rim = Cylinder(60, 16) - Cylinder(48, 16)
    web = Cylinder(48.5, 1.5)
    hub = Cylinder(10, 16)
    return {"flywheel": rim + web + hub - Cylinder(5, 30)}


def ph07():
    from . import mech

    def make(zb):
        rod = Pos(0, -130, 0) * Box(4, 266, 4)
        bob = Pos(0, -zb, 0) * Cylinder(25, 10)
        return rod + bob + Cylinder(5, 4) - Cylinder(2, 20)
    lo_z, hi_z = 80.0, 268.0
    for _ in range(40):
        zb = (lo_z + hi_z) / 2
        sh = make(zb)
        mp = mech.mass_props(sh, 7.85)
        I = mech.inertia_about(sh, 7.85, (0, 0, 0), (0, 0, 1))
        d = abs(mp.com_mm[1]) / 1000
        Tp = 2 * math.pi * math.sqrt(I / (mp.mass_kg * 9.80665 * d))
        if Tp > 1.0:
            hi_z = zb
        else:
            lo_z = zb
    return {"pendulum": make((lo_z + hi_z) / 2)}


def ph08():
    base = Pos(0, 0, 3.5) * Box(250, 250, 7)
    mast = Pos(0, 0, 7 + 296.5) * (Cylinder(15, 593) - Cylinder(14, 593))
    return {"base": base, "mast": mast}


def ph09():
    p = involute_gear(1.5, 20, 8, 6)
    g = involute_gear(1.5, 60, 8, 6)
    g = Pos(60, 0, 0) * Rot(0, 0, 180 / 60) * g
    return {"pinion": p, "gear": g}


def ph10():
    pad1 = Pos(10, 0, 0) * Box(20, 10, 20)
    pad2 = Pos(50, 0, 0) * Box(20, 10, 20)
    blade = Pos(30, 0, 0) * Box(20, 10, 0.56)
    return {"flexure": pad1 + blade + pad2}


def _rocket(body_len, body_r=12.5, nose_len=70, fin_cr=55, fin_ct=25, fin_s=30, fin_sweep=25,
            n_fins=3, wall=1.2, ballast_g=0.0):
    tube = Pos(0, 0, body_len / 2) * (Cylinder(body_r, body_len) - Cylinder(body_r - wall, body_len))
    mount = Pos(0, 0, 40) * (Cylinder(body_r - wall, 80) - Cylinder(9.1, 80))
    ring = Pos(0, 0, 1.5) * (Cylinder(body_r - wall, 3) - Cylinder(9.1, 3))
    # ogive-ish nose by revolving a profile (solid)
    pts = [(0, 0)] + [(body_r * math.sin(math.pi / 2 * (1 - i / 20)) ** 0.9, nose_len * i / 20)
                      for i in range(21)]
    pts[-1] = (0, nose_len)
    with b3.BuildSketch(b3.Plane.XZ) as sk:
        with b3.BuildLine():
            b3.Polyline(*[(x, z) for x, z in pts], close=True)
        b3.make_face()
    nose = Pos(0, 0, body_len) * b3.revolve(sk.sketch, b3.Axis.Z)
    rocket = tube + mount + ring + nose
    with b3.BuildSketch(b3.Plane.XZ) as fsk:
        b3.Polygon((body_r - 0.5, 0), (body_r - 0.5, fin_cr), (body_r + fin_s, fin_cr - fin_sweep),
                   (body_r + fin_s, fin_cr - fin_sweep - fin_ct), align=None)
    fin = b3.extrude(fsk.sketch, 1.0, both=True)
    for k in range(n_fins):
        rocket += Rot(0, 0, 360 * k / n_fins) * fin
    out = {"rocket": rocket}
    if ballast_g:
        h = ballast_g / 7.85 * 1000 / (math.pi * 8 ** 2)
        out["ballast"] = Pos(0, 0, body_len + h / 2 + 1) * Cylinder(8, h)
        out["rocket"] = rocket - out["ballast"]
    return out


def _tuned(motor, margin, apogee, n_fins, body_len):
    """Search fin span and nose ballast with the graders' own model until both targets hold."""
    from . import mech, tasks as T
    best = None
    for span, bal, bl in ((sp, b, L) for sp in (18, 22, 26, 30) for b in (0, 4, 8, 12, 20, 30)
                          for L in (body_len, int(body_len * 0.75), int(body_len * 0.55))):
        if True:
            parts = _rocket(bl, fin_s=span, n_fins=n_fins, ballast_g=bal)
            mp = mech.mass_props(parts["rocket"], 1.24)
            if "ballast" in parts:
                mp = mp + mech.mass_props(parts["ballast"], 7.85)
            g = T.rocket_geometry(parts["rocket"])
            mot = mech.MOTORS[motor]
            tot = mp + mech.point_mass(mot["mass"], (0, 0, g["zaft"] + mot["len"] / 2))
            cp, _ = mech.barrowman_cp(g["nose_len"], g["nose_vol"], g["R"], g["fins"])
            sm = (cp - (g["ztip"] - tot.com_mm[2])) / (2 * g["R"])
            h = mech.apogee_m(motor, mp.mass_kg, 2 * g["R"])
            err = abs(sm - sum(margin) / 2) / (margin[1] - margin[0]) + abs(h - sum(apogee) / 2) / (apogee[1] - apogee[0])
            if best is None or err < best[0]:
                best = (err, parts)
    return best[1]


def ph11():
    return _tuned("C6-5", (1.0, 2.0), (225, 275), 3, 300)


def ph12():
    return _tuned("B6-4", (1.5, 2.5), (85, 125), 4, 260)


def ph13():
    # cantilever k = 3EI/L^3 = 50 N/mm -> I = 50*1e6/(3*210000) = 79.4 mm^4
    b = 20.0
    h = (12 * 79.4 / b) ** (1 / 3)
    return {"spring": Pos(50, 0, 0) * Box(100, b, h)}


def ph14():
    # k = GJ/L = 200 N·m/rad -> J = 200e3*300/80769 = 742.9 mm^4
    G = 210000 / 2.6
    J = 200e3 * 300 / G
    ro = 6.0
    ri = (ro ** 4 - J * 32 / math.pi / 16) ** 0.25 if False else (ro ** 4 - 2 * J / math.pi) ** 0.25
    return {"tube": Pos(0, 0, 150) * (Cylinder(ro, 300) - Cylinder(ri, 300))}


def ph15():
    top = Pos(60, 0, -2) * Box(120, 16, 4)
    back = Pos(2, 0, -35) * Box(4, 16, 70)
    with b3.BuildSketch(b3.Plane.XZ) as sk:
        b3.Polygon((0, 0), (118, 0), (0, -68), align=None)
    gus = b3.extrude(sk.sketch, 1.45, both=True)
    return {"bracket": top + back + gus}


def ph16():
    t = 14
    arm = Cylinder(14, t) + Pos(150, 0, 0) * Cylinder(11, t) + Pos(75, 0, 0) * Box(150, 18, t)
    arm -= Cylinder(6, 3 * t) + Pos(150, 0, 0) * Cylinder(4.5, 3 * t)
    return {"crank": arm}


def ph17():
    # f1 = 1.875^2/(2 pi L^2) sqrt(EI/rho A), rectangular b x h: sqrt(E h^2 / 12 rho)
    L, b = 120.0, 10.0
    E, rho = 210e9, 7850
    h = 440 * 2 * math.pi * (L / 1000) ** 2 / 1.875 ** 2 / math.sqrt(E / (12 * rho)) * 1000
    return {"bar": Pos(L / 2, 0, 0) * Box(L, b, h)}


def ph18():
    from . import mech

    def make(xc):
        lever = Box(270, 20, 10)
        lever += Pos(xc, 0, 0) * Box(40, 50, 40)
        return lever - Cylinder(3, 30) - Pos(120, 0, 0) * Cylinder(2.5, 30)
    lo_x, hi_x = -114.0, 0.0
    for _ in range(40):
        xc = (lo_x + hi_x) / 2
        sh = make(xc)
        mp = mech.mass_props(sh, 7.85) + mech.point_mass(0.5, (120, 0, 0))
        if mp.com_mm[0] > 0:
            hi_x = xc
        else:
            lo_x = xc
    return {"lever": make((lo_x + hi_x) / 2)}


def ph19():
    plate = Pos(0, 0, 2) * Cylinder(75, 4)
    weight = Pos(-45, 0, 4 + 12) * Box(60, 120, 24)
    return {"base": plate + weight}


def ph20():
    with b3.BuildSketch(b3.Plane.YZ) as sk:
        b3.Rectangle(12, 12)
        b3.Rectangle(10.4, 10.4, mode=b3.Mode.SUBTRACT)
    tube = b3.extrude(sk.sketch, 120)
    return {"arm": tube}


REFS = {k: v for k, v in globals().items() if k.startswith("ph") and callable(v)}


def build(task_id: str, out: Path) -> None:
    write(REFS["ph" + task_id[2:4]](), out)


def main(argv):
    out = Path(argv[0])
    ids = argv[1:] or [t.id for t in T.TASKS]
    for tid in ids:
        d = out / tid
        t0 = time.time()
        try:
            build(tid, d)
        except Exception as e:  # noqa: BLE001
            print(f"{tid}: reference failed: {type(e).__name__}: {e}")
            continue
        r = T.score(tid, d)
        print(f"{tid}: {r['passed']}/{r['n_req']} {r['metrics']} ({time.time() - t0:.0f}s)", flush=True)
        for q in r["requirements"]:
            if not q["passed"]:
                print(f"    FAIL {q['name']}: {q['detail']}")


if __name__ == "__main__":
    main(sys.argv[1:])
