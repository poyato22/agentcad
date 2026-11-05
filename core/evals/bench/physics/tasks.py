"""Mises-Physics: 20 tasks whose requirements can only be verified with physics, graded
independently on the delivered geometry (our own CalculiX decks, mass properties, tipping,
Barrowman + 1-D flight), never on either system's self-report.

The DEV / TEST split is fixed here, before any tuning: 5 DEV tasks (one per grader type) may be
looked at while improving Mises; the 15 TEST tasks are held out and only run for the final
numbers. Score = fraction of requirements passed; strict = all passed. Each task also reports
its key physical numbers (mass, margin, frequency ...) in `metrics`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..functional import fea
from ..functional import geom as G
from ..functional.tasks import Checks, _need, _near, _parts, _perp, _single
from . import mech

AL = dict(E=68900.0, nu=0.33, rho=2.70, yield_=276.0)       # 6061-T6
STEEL = dict(E=210000.0, nu=0.30, rho=7.85, yield_=355.0)    # S355
AL7075 = dict(E=71700.0, nu=0.33, rho=2.81, yield_=503.0)


@dataclass
class PTask:
    id: str
    category: str
    split: str                      # dev | test
    prompt: str
    check: Callable[[Path, Checks, dict], None]


def _fea_static(out, w, mat, fixed, loads, tag="fea", exclude=None):
    return fea.solve(out / "result.step", out / tag, E=mat["E"], nu=mat["nu"], fixed=fixed,
                     loads=loads, exclude=exclude)


def _mass_g(shape, rho):
    return G.volume(shape) * rho / 1000.0


def _valid(out, c):
    from ..geometry import validity
    c.add("valid solid geometry", lambda: (validity(out / "result.step")[0].valid, ""))


def _plane_tol(lo, hi):
    return 1e-3 + 2e-5 * float(np.max(hi - lo))


# ---------------------------------------------------------------------------- structural
def chk_bracket_200n(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    mass = _mass_g(w, AL["rho"])
    m["mass_g"] = round(mass, 2)
    c.add("mounting face on x = 0", lambda: (abs(lo[0]) <= 0.05, f"xmin {lo[0]:.3f}"))
    hole = lambda: [h for h in G.holes(w, 2.45, 2.55) if abs(abs(h.axis[1]) - 1) < 1e-3]  # noqa
    c.add("Ø5 load hole, axis Y, centre at x = 60, z = 0",
          lambda: (any(abs(h.point[0] - 60) <= 0.1 and abs(h.point[2]) <= 0.1 for h in hole()),
                   f"{len(hole())} Ø5 Y-axis holes"))
    c.add("mass ≤ 25 g", lambda: (mass <= 25, f"{mass:.2f} g"))

    def run():
        if "r" not in m:
            h = [h for h in hole() if abs(h.point[0] - 60) <= 0.1][0]
            ax = np.array([0, 1.0, 0])

            def rad(x):
                return np.linalg.norm(_perp(x - h.point, ax), axis=1)
            r = _fea_static(out, w, AL, lambda x: fea.plane_mask(x, 0, lo[0], _plane_tol(lo, hi)),
                            lambda x, free: fea.split_force((rad(x) <= 2.55) & (x[:, 2] <= h.point[2])
                                                            & free, [0, 0, -200]),
                            exclude=lambda e: rad(e) <= 4.0)
            m["r"] = r
            m["vm_p995_mpa"] = round(r.vm_p995, 1)
            m["sf"] = round(AL["yield_"] / r.vm_p995, 2)
        return m["r"]
    c.add("200 N at the hole: SF ≥ 2 on yield (FEA)",
          lambda: (AL["yield_"] / run().vm_p995 >= 2, f"SF {AL['yield_'] / run().vm_p995:.2f}"))


def chk_shelf_l(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    mass = _mass_g(w, AL["rho"])
    m["mass_g"] = round(mass, 1)
    c.add("wall face x = 0, top face z = 0, arm to x = 120",
          lambda: (abs(lo[0]) <= 0.05 and abs(hi[2]) <= 0.05 and abs(hi[0] - 120) <= 0.5,
                   f"x {lo[0]:.2f}..{hi[0]:.2f}, ztop {hi[2]:.2f}"))
    c.add("mass ≤ 60 g", lambda: (mass <= 60, f"{mass:.1f} g"))

    def run():
        if "r" not in m:
            tol = _plane_tol(lo, hi)
            r = _fea_static(out, w, AL, lambda x: fea.plane_mask(x, 0, lo[0], tol),
                            lambda x, free: fea.split_force(
                                fea.plane_mask(x, 2, hi[2], tol) & (x[:, 0] >= hi[0] - 10) & free,
                                [0, 0, -500]))
            m["r"] = r
            m["vm_p995_mpa"], m["defl_mm"] = round(r.vm_p995, 1), round(r.u_max, 3)
        return m["r"]
    c.add("500 N on the last 10 mm: SF ≥ 2 (FEA)",
          lambda: (AL["yield_"] / run().vm_p995 >= 2, f"SF {AL['yield_'] / run().vm_p995:.2f}"))
    c.add("deflection ≤ 0.3 mm (FEA)", lambda: (run().u_max <= 0.3, f"{run().u_max:.3f} mm"))


def chk_crank(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    mass = _mass_g(w, AL7075["rho"])
    m["mass_g"] = round(mass, 1)
    sp = lambda: G.holes(w, 5.95, 6.05)  # noqa: E731
    pd = lambda: G.holes(w, 4.45, 4.55)  # noqa: E731

    def holes():
        a, b = sp(), pd()
        if len(a) != 1 or len(b) != 1:
            return False, f"{len(a)} Ø12, {len(b)} Ø9 holes"
        d = np.linalg.norm(_perp(b[0].point - a[0].point, a[0].axis))
        return (abs(d - 150) <= 0.1 and abs(abs(a[0].axis[2]) - 1) < 1e-3
                and np.linalg.norm(_perp(a[0].point, a[0].axis)) <= 0.05), f"centres {d:.2f} apart"
    c.add("Ø12 spindle hole on the Z axis, Ø9 pedal hole 150 mm away", holes)
    c.add("mass ≤ 150 g", lambda: (mass <= 150, f"{mass:.1f} g"))

    def run():
        if "r" not in m:
            a, b = sp()[0], pd()[0]
            ax = np.array([0, 0, 1.0])
            along = _perp(b.point - a.point, ax)
            along /= np.linalg.norm(along)
            side = np.cross(ax, along)

            def rad(x, p):
                return np.linalg.norm(_perp(x - p, ax), axis=1)
            r = _fea_static(out, w, AL7075, lambda x: rad(x, a.point) <= 6.05,
                            lambda x, free: fea.split_force((rad(x, b.point) <= 4.55)
                                                            & ((x - b.point) @ side >= 0) & free,
                                                            1200 * side),
                            exclude=lambda e: (rad(e, a.point) <= 8.5) | (rad(e, b.point) <= 7))
            m["r"] = r
            m["vm_p995_mpa"], m["defl_mm"] = round(r.vm_p995, 1), round(r.u_max, 3)
        return m["r"]
    c.add("1.2 kN at the pedal: SF ≥ 1.5 on 7075 yield (FEA)",
          lambda: (AL7075["yield_"] / run().vm_p995 >= 1.5, f"SF {AL7075['yield_'] / run().vm_p995:.2f}"))
    c.add("pedal-hole deflection ≤ 3 mm (FEA)", lambda: (run().u_max <= 3.0, f"{run().u_max:.3f} mm"))


def chk_spring_beam(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    c.add("fixed end on x = 0, free end on x = 100",
          lambda: (abs(lo[0]) <= 0.05 and abs(hi[0] - 100) <= 0.1, f"x {lo[0]:.2f}..{hi[0]:.2f}"))

    def run():
        if "k" not in m:
            tol = _plane_tol(lo, hi)
            r = _fea_static(out, w, STEEL, lambda x: fea.plane_mask(x, 0, lo[0], tol),
                            lambda x, free: fea.split_force(fea.plane_mask(x, 0, hi[0], tol) & free,
                                                            [0, 0, -100]))
            end = fea.plane_mask(r.coords, 0, hi[0], tol)
            dz = -float(np.mean(r.disp[end][:, 2]))
            m["k_n_per_mm"] = round(100 / dz, 2)
            m["k"] = 100 / dz
        return m["k"]
    c.add("tip stiffness 50 ± 5 N/mm (FEA, 100 N on the end face)",
          lambda: (45 <= run() <= 55, f"{run():.2f} N/mm"))
    c.add("mass ≤ 120 g", lambda: (_mass_g(w, STEEL["rho"]) <= 120, f"{_mass_g(w, STEEL['rho']):.1f} g"))


def chk_torsion_tube(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    c.add("z 0..300 on the Z axis", lambda: (abs(lo[2]) <= 0.05 and abs(hi[2] - 300) <= 0.2
                                             and np.all(np.abs((lo + hi)[:2] / 2) <= 0.05),
                                             f"z {lo[2]:.2f}..{hi[2]:.2f}"))

    def run():
        if "k" not in m:
            tol = _plane_tol(lo, hi)
            T = 10_000.0                                       # 10 N·m
            r = _fea_static(out, w, STEEL, lambda x: fea.plane_mask(x, 2, lo[2], tol),
                            lambda x, free: fea.torque_forces(
                                x, fea.plane_mask(x, 2, hi[2], tol) & free, [0, 0, 0], [0, 0, 1], T))
            end = fea.plane_mask(r.coords, 2, hi[2], tol)
            rv = r.coords[end] * np.array([1, 1, 0])
            rr = np.linalg.norm(rv, axis=1)
            tang = np.cross([0, 0, 1], rv) / np.maximum(rr, 1e-9)[:, None] ** 2
            ok = rr > 0.5 * rr.max()
            th = float(np.mean(np.sum(r.disp[end] * tang, axis=1)[ok]))
            m["k"] = T / 1000 / th
            m["k_nm_per_rad"] = round(m["k"], 1)
        return m["k"]
    c.add("torsional stiffness 200 ± 20 N·m/rad (FEA)", lambda: (180 <= run() <= 220, f"{run():.1f} N·m/rad"))
    c.add("mass ≤ 150 g", lambda: (_mass_g(w, STEEL["rho"]) <= 150, f"{_mass_g(w, STEEL['rho']):.1f} g"))


def chk_flexure(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    c.add("fixed pad face on x = 0, moving pad end face on x = 60",
          lambda: (abs(lo[0]) <= 0.05 and abs(hi[0] - 60) <= 0.1, f"x {lo[0]:.2f}..{hi[0]:.2f}"))

    def run():
        if "k" not in m:
            tol = _plane_tol(lo, hi)
            Mz = 50.0                                          # N·mm about Y, a force couple
            end = lambda x: fea.plane_mask(x, 0, hi[0], tol)   # noqa: E731

            def loads(x, free):
                msk = end(x) & free
                zc = x[msk, 2].mean()
                f = np.zeros_like(x)
                dz = x[:, 2] - zc
                s = (dz[msk] ** 2).sum()
                f[msk, 0] = -Mz * dz[msk] / s                  # sum F·dz = -Mz: rotation about +Y
                return f
            r = _fea_static(out, w, AL, lambda x: fea.plane_mask(x, 0, lo[0], tol), loads)
            e = end(r.coords)
            z, ux = r.coords[e, 2], r.disp[e, 0]
            th = -np.polyfit(z, ux, 1)[0]
            m["k"] = Mz / 1000 / th
            m["k_nm_per_rad"] = round(m["k"], 3)
        return m["k"]
    c.add("rotational stiffness 0.5 ± 0.1 N·m/rad about Y (FEA)",
          lambda: (0.4 <= run() <= 0.6, f"{run():.3f} N·m/rad"))


# ---------------------------------------------------------------------------- modal / buckling
def chk_rib_plate(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    d = hi - lo
    mass = _mass_g(w, AL["rho"])
    m["mass_g"] = round(mass, 1)
    c.add("150 x 100 footprint, ≤ 20 high, clamped edge on x = 0",
          lambda: (abs(d[0] - 150) <= 0.2 and abs(d[1] - 100) <= 0.2 and d[2] <= 20.01
                   and abs(lo[0]) <= 0.05, f"{d.round(2).tolist()}"))
    c.add("mass ≤ 120 g", lambda: (mass <= 120, f"{mass:.1f} g"))

    def f1():
        if "f" not in m:
            fr = fea.modal(out / "result.step", out / "modal", E=AL["E"], nu=AL["nu"],
                           rho_g_cm3=AL["rho"],
                           fixed=lambda x: fea.plane_mask(x, 0, lo[0], _plane_tol(lo, hi)))
            m["f"] = fr[0]
            m["f1_hz"] = round(fr[0], 1)
        return m["f"]
    c.add("first natural frequency ≥ 400 Hz (FEA modal)", lambda: (f1() >= 400, f"{f1():.1f} Hz"))


def chk_mast(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    d = hi - lo
    mass = _mass_g(w, AL["rho"])
    m["mass_g"] = round(mass, 1)
    c.add("300 tall from z = 0, within 40 x 40",
          lambda: (abs(lo[2]) <= 0.05 and abs(d[2] - 300) <= 0.2 and d[0] <= 40.01 and d[1] <= 40.01,
                   f"{d.round(2).tolist()}"))
    c.add("mass ≤ 100 g", lambda: (mass <= 100, f"{mass:.1f} g"))

    def f1():
        if "f" not in m:
            fr = fea.modal(out / "result.step", out / "modal", E=AL["E"], nu=AL["nu"],
                           rho_g_cm3=AL["rho"],
                           fixed=lambda x: fea.plane_mask(x, 2, lo[2], _plane_tol(lo, hi)))
            m["f"] = fr[0]
            m["f1_hz"] = round(fr[0], 1)
        return m["f"]
    c.add("first natural frequency ≥ 250 Hz, base clamped (FEA modal)",
          lambda: (f1() >= 250, f"{f1():.1f} Hz"))


def chk_tuning_bar(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    c.add("clamped end face on x = 0", lambda: (abs(lo[0]) <= 0.05, f"xmin {lo[0]:.3f}"))

    def f1():
        if "f" not in m:
            fr = fea.modal(out / "result.step", out / "modal", E=STEEL["E"], nu=STEEL["nu"],
                           rho_g_cm3=STEEL["rho"],
                           fixed=lambda x: fea.plane_mask(x, 0, lo[0], _plane_tol(lo, hi)))
            m["f"] = fr[0]
            m["f1_hz"] = round(fr[0], 1)
        return m["f"]
    c.add("first natural frequency 440 ± 10 Hz (FEA modal)", lambda: (430 <= f1() <= 450, f"{f1():.1f} Hz"))
    c.add("length ≥ 100 mm", lambda: (hi[0] - lo[0] >= 100, f"{hi[0] - lo[0]:.1f}"))


def chk_drone_arm(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    mass = _mass_g(w, AL["rho"])
    m["mass_g"] = round(mass, 2)
    c.add("root face x = 0, tip at x = 120", lambda: (abs(lo[0]) <= 0.05 and abs(hi[0] - 120) <= 0.2,
                                                      f"x {lo[0]:.2f}..{hi[0]:.2f}"))
    c.add("mass ≤ 15 g", lambda: (mass <= 15, f"{mass:.2f} g"))

    def f1():
        if "f" not in m:
            fr = fea.modal(out / "result.step", out / "modal", E=AL["E"], nu=AL["nu"],
                           rho_g_cm3=AL["rho"],
                           fixed=lambda x: fea.plane_mask(x, 0, lo[0], _plane_tol(lo, hi)))
            m["f"] = fr[0]
            m["f1_hz"] = round(fr[0], 1)
        return m["f"]
    c.add("first natural frequency ≥ 300 Hz (FEA modal)", lambda: (f1() >= 300, f"{f1():.1f} Hz"))


def chk_column(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    mass = _mass_g(w, STEEL["rho"])
    m["mass_g"] = round(mass, 1)
    c.add("z 0..250", lambda: (abs(lo[2]) <= 0.05 and abs(hi[2] - 250) <= 0.2, f"z {lo[2]:.2f}..{hi[2]:.2f}"))
    c.add("mass ≤ 160 g", lambda: (mass <= 160, f"{mass:.1f} g"))

    def bf():
        if "b" not in m:
            tol = _plane_tol(lo, hi)
            m["b"] = fea.buckle(out / "result.step", out / "buckle", E=STEEL["E"], nu=STEEL["nu"],
                                fixed=lambda x: fea.plane_mask(x, 2, lo[2], tol),
                                loads=lambda x, free: fea.split_force(
                                    fea.plane_mask(x, 2, hi[2], tol) & free, [0, 0, -3000]))
            m["buckling_factor"] = round(m["b"], 2)
        return m["b"]
    c.add("3 kN axial: buckling factor ≥ 3 (FEA, base fixed, top free)", lambda: (bf() >= 3, f"{bf():.2f}"))


# ---------------------------------------------------------------------------- dynamics / mass
def chk_arm(out: Path, c: Checks, m: dict):
    got, _ = _parts(out, ["upper_arm", "forearm"])
    if not c.add("parts upper_arm + forearm", lambda: _need(got, ["upper_arm", "forearm"])):
        return
    ua, fa = got["upper_arm"], got["forearm"]
    sh = lambda: [h for h in G.holes(ua, 3.95, 4.05) if np.linalg.norm(_perp(h.point, h.axis)) <= 0.05]  # noqa

    def geometry():
        s = sh()
        el = [h for h in G.holes(ua, 3.95, 4.05) if abs(np.linalg.norm(_perp(h.point, np.array([0, 0, 1.0]))) - 200) <= 0.1]
        tip = [h for h in G.holes(fa, 3.95, 4.05) if abs(h.point[0] - 350) <= 0.1 and abs(h.point[1]) <= 0.1]
        ok = bool(s) and bool(el) and bool(tip) and all(abs(abs(h.axis[2]) - 1) < 1e-3 for h in s + el + tip)
        return ok, f"shoulder {len(s)}, elbow {len(el)}, tip {len(tip)} (Ø8, axes Z)"
    c.add("Ø8 joint holes: shoulder at origin, elbow x = 200, tip x = 350 (axes Z)", geometry)

    def torques():
        if "t" not in m:
            g = mech.G0
            alpha = 3.0
            mu, mf = mech.mass_props(ua, AL["rho"]), mech.mass_props(fa, AL["rho"])
            iu = mech.inertia_about(ua, AL["rho"], (0, 0, 0), (0, 0, 1))
            i_f = mech.inertia_about(fa, AL["rho"], (0, 0, 0), (0, 0, 1))
            pay, xp = 0.3, 0.350
            t_sh = g * (mu.mass_kg * mu.com_mm[0] + mf.mass_kg * mf.com_mm[0]) / 1000 + g * pay * xp \
                + alpha * (iu + i_f + pay * xp ** 2)
            i_f_el = mech.inertia_about(fa, AL["rho"], (200, 0, 0), (0, 0, 1))
            t_el = g * mf.mass_kg * (mf.com_mm[0] - 200) / 1000 + g * pay * (xp - 0.2) \
                + alpha * (i_f_el + pay * (xp - 0.2) ** 2)
            m["t"] = (t_sh, t_el)
            m["shoulder_nm"], m["elbow_nm"] = round(t_sh, 3), round(t_el, 3)
            m["links_mass_g"] = round(1000 * (mu.mass_kg + mf.mass_kg), 1)
        return m["t"]
    c.add("shoulder torque ≤ 1.5 N·m (horizontal, +3 rad/s², 0.3 kg at the tip)",
          lambda: (torques()[0] <= 1.5, f"{torques()[0]:.3f} N·m"))
    c.add("elbow torque ≤ 0.5 N·m", lambda: (torques()[1] <= 0.5, f"{torques()[1]:.3f} N·m"))

    def stiff():
        if "d" not in m:
            s = sh()[0]
            ax = np.array([0, 0, 1.0])
            lo, hi = G.bbox(ua)
            el = np.array([200.0, 0, 0])

            def rad(x, p):
                return np.linalg.norm(_perp(x - p, ax), axis=1)
            F = (0.3 + mech.mass_props(fa, AL["rho"]).mass_kg) * mech.G0 * 3
            r = fea.solve(out / "components" / _comp_name(out, "upper_arm"), out / "fea_ua",
                          E=AL["E"], nu=AL["nu"], fixed=lambda x: rad(x, s.point) <= 4.05,
                          loads=lambda x, free: fea.split_force((rad(x, el) <= 4.05) & free,
                                                                [0, -F, 0]))
            m["d"] = r.u_max
            m["upper_arm_defl_mm"] = round(r.u_max, 3)
        return m["d"]
    c.add("upper arm: elbow deflection ≤ 0.5 mm under 3 g of forearm + payload (FEA)",
          lambda: (stiff() <= 0.5, f"{stiff():.3f} mm"))


def _comp_name(out: Path, key: str) -> str:
    for p in sorted((out / "components").glob("*.step")):
        if key in p.stem.lower():
            return p.name
    raise RuntimeError(f"no component {key}")


def chk_flywheel(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    mp = mech.mass_props(w, STEEL["rho"])
    I = mech.inertia_about(w, STEEL["rho"], (0, 0, 0), (0, 0, 1))
    m.update(mass_g=round(mp.mass_kg * 1000, 1), inertia_kgm2=round(I, 6))
    lo, hi = G.bbox(w)
    c.add("Ø10 bore on the Z axis", lambda: (any(np.linalg.norm(_perp(h.point, h.axis)) <= 0.05
                                                 and abs(abs(h.axis[2]) - 1) < 1e-3
                                                 for h in G.holes(w, 4.95, 5.05)), ""))
    c.add("OD ≤ 120", lambda: (max(hi[0] - lo[0], hi[1] - lo[1]) <= 120.01, f"{max(hi[0] - lo[0], hi[1] - lo[1]):.2f}"))
    c.add("mass ≤ 650 g", lambda: (mp.mass_kg <= 0.65, f"{mp.mass_kg * 1000:.1f} g"))
    c.add("inertia about Z ≥ 1.5e-3 kg·m²", lambda: (I >= 1.5e-3, f"{I:.5f} kg·m²"))
    c.add("balanced: centre of mass on the axis (≤ 0.05 mm)",
          lambda: (np.linalg.norm(mp.com_mm[:2]) <= 0.05, f"{np.linalg.norm(mp.com_mm[:2]):.4f} mm"))


def chk_pendulum(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    lo, hi = G.bbox(w)
    piv = lambda: G.holes(w, 1.95, 2.05)  # noqa: E731
    c.add("one Ø4 pivot hole", lambda: (len(piv()) == 1, f"{len(piv())}"))
    c.add("longest dimension ≤ 300", lambda: (max(hi - lo) <= 300.01, f"{max(hi - lo):.1f}"))

    def period():
        if "T" not in m:
            h = piv()[0]
            mp = mech.mass_props(w, STEEL["rho"])
            I = mech.inertia_about(w, STEEL["rho"], h.point, h.axis)
            dvec = _perp(mp.com_mm - h.point, h.axis)
            dist = float(np.linalg.norm(dvec)) / 1000
            m["T"] = 2 * math.pi * math.sqrt(I / (mp.mass_kg * mech.G0 * dist)) if dist > 0 else math.inf
            m["period_s"] = round(m["T"], 4)
            m["mass_g"] = round(mp.mass_kg * 1000, 1)
        return m["T"]
    c.add("small-swing period 1.00 ± 0.02 s about the pivot", lambda: (abs(period() - 1.0) <= 0.02, f"{period():.4f} s"))


def chk_lever(out: Path, c: Checks, m: dict):
    w = _single(out)
    _valid(out, c)
    piv = lambda: [h for h in G.holes(w, 2.95, 3.05) if np.linalg.norm(_perp(h.point, h.axis)) <= 0.05]  # noqa
    load = lambda: [h for h in G.holes(w, 2.45, 2.55) if abs(h.point[0] - 120) <= 0.1]  # noqa
    c.add("Ø6 pivot on the Z axis at the origin, Ø5 load hole at x = 120",
          lambda: (bool(piv()) and bool(load()), f"{len(piv())} pivot, {len(load())} load holes"))

    def bal():
        mp = mech.mass_props(w, STEEL["rho"]) + mech.point_mass(0.5, (120, 0, mech.mass_props(w, STEEL["rho"]).com_mm[2]))
        d = float(np.linalg.norm(mp.com_mm[:2]))
        m["imbalance_mm"] = round(d, 3)
        m["mass_g"] = round(1000 * (mp.mass_kg - 0.5), 1)
        return d <= 0.5, f"COM {d:.3f} mm from the pivot axis"
    c.add("balanced with 0.5 kg at the load hole (COM ≤ 0.5 mm from pivot)", bal)
    c.add("lever mass ≤ 1.2 kg", lambda: (mech.mass_props(w, STEEL["rho"]).mass_kg <= 1.2,
                                          f"{mech.mass_props(w, STEEL['rho']).mass_kg * 1000:.0f} g"))


# ---------------------------------------------------------------------------- stability
def _tip_check(out, c, m, payload_kg, payload_xyz, max_struct_kg, footprint, min_deg, rho):
    whole, comps = G.load_result(out)
    if whole is None:
        raise RuntimeError("no result")
    solids = list(comps.values()) or [whole]
    mp = None
    for s in solids:
        p = mech.mass_props(s, rho)
        mp = p if mp is None else mp + p
    struct = mp.mass_kg
    lo, hi = G.bbox(whole)
    hull = mech.contact_hull(whole)
    tot = mp + mech.point_mass(payload_kg, payload_xyz)
    ang = mech.tipping_angle_deg(hull, tot.com_mm, lo[2])
    m.update(structure_kg=round(struct, 3), tip_angle_deg=round(ang, 2),
             com_height_mm=round(tot.com_mm[2] - lo[2], 1))
    c.add(f"footprint ≤ {footprint} x {footprint}",
          lambda: ((hi - lo)[0] <= footprint + 0.01 and (hi - lo)[1] <= footprint + 0.01,
                   f"{(hi - lo)[:2].round(1).tolist()}"))
    c.add(f"structure mass ≤ {max_struct_kg} kg", lambda: (struct <= max_struct_kg, f"{struct:.3f} kg"))
    c.add(f"does not tip at {min_deg}° in any direction (with the payload)",
          lambda: (ang >= min_deg, f"tips at {ang:.2f}°"))
    return lo, hi


def chk_robot_base(out: Path, c: Checks, m: dict):
    _valid(out, c)
    lo, hi = _tip_check(out, c, m, 2.0, (0, 0, 600), 1.5, 250, 15, AL["rho"])
    c.add("payload mount at 600 mm (top of the structure ≥ 590)", lambda: (hi[2] - lo[2] >= 590, f"{hi[2] - lo[2]:.1f}"))
    c.add("floor at z = 0, centred on the Z axis",
          lambda: (abs(lo[2]) <= 0.05 and np.all(np.abs((lo + hi)[:2] / 2) <= 2), f"zmin {lo[2]:.2f}"))


def chk_lamp_base(out: Path, c: Checks, m: dict):
    _valid(out, c)
    lo, hi = _tip_check(out, c, m, 0.8, (220, 0, 400), 2.0, 150, 10, STEEL["rho"])
    c.add("floor at z = 0, centred on the Z axis",
          lambda: (abs(lo[2]) <= 0.05 and np.all(np.abs((lo + hi)[:2] / 2) <= 1), f"zmin {lo[2]:.2f}"))


# ---------------------------------------------------------------------------- gears
def chk_gear_3to1(out: Path, c: Checks, m: dict):
    from ..functional.tasks import _gear_axis, _tip_d
    whole, comps = G.load_result(out)
    pin = G.find(comps, "pinion")
    gear = next((s for n, s in comps.items() if "gear" in n.lower() and "pinion" not in n.lower()), None)
    if not c.add("parts pinion + gear", lambda: (pin is not None and gear is not None, "")):
        return
    ap, ag = (lambda: _gear_axis(pin, 3)), (lambda: _gear_axis(gear, 3))

    def ratio():
        rp, rg = _tip_d(pin, ap()) / 2, _tip_d(gear, ag()) / 2
        zp = G.count_teeth(pin, ap(), rp - 0.8)
        zg = G.count_teeth(gear, ag(), rg - 0.8)
        m.update(z_pinion=zp, z_gear=zg, ratio=round(zg / zp, 4) if zp else None)
        return zp > 0 and abs(zg / zp - 3) <= 0.03, f"{zg}/{zp} = {zg / max(zp, 1):.3f}"
    c.add("ratio 3:1 ± 1% (tooth counts)", ratio)

    def cd():
        a, b = ap(), ag()
        d = float(np.linalg.norm(_perp(b.point - a.point, a.axis)))
        m["centre_distance_mm"] = round(d, 3)
        return d <= 60.0 and abs(abs(a.axis @ b.axis) - 1) < 1e-4, f"{d:.3f} mm"
    c.add("parallel axes, centre distance ≤ 60", cd)

    def sweep():
        a, b = ap(), ag()
        zp = m.get("z_pinion") or 1
        step = 360 / zp
        worst, gap = 0.0, 0.0
        for dd in np.linspace(0, step, 7):
            p2 = G.rotate(pin, a.point, a.axis, dd)
            g2 = G.rotate(gear, b.point, a.axis, -dd / 3)
            worst = max(worst, G.common_volume(p2, g2))
            gap = max(gap, G.min_distance(p2, g2))
        m["sweep_overlap_mm3"] = round(worst, 3)
        return worst <= 0.5 and gap <= 0.3, f"worst overlap {worst:.3f} mm3, max gap {gap:.3f}"
    c.add("meshes without interference through one pinion tooth pitch", sweep)


# ---------------------------------------------------------------------------- rocketry
def rocket_geometry(whole) -> dict:
    """Body radius, nose length/volume (outer envelope), fins planform and count, aft z, from
    the delivered solid (nose tip up, axis on Z)."""
    cyl = [x for x in G.cylinders(whole) if not x.concave and abs(abs(x.axis[2]) - 1) < 1e-3
           and np.linalg.norm(_perp(x.point, np.array([0, 0, 1.0]))) <= 0.1]
    if not cyl:
        raise RuntimeError("no body tube on the Z axis")
    body = max(cyl, key=lambda x: x.length)
    R = body.radius
    v, _ = whole.tessellate(0.02, 0.1)
    pts = np.array([tuple(p) for p in v])
    r = np.linalg.norm(pts[:, :2], axis=1)
    ztip, zaft = pts[:, 2].max(), pts[:, 2].min()
    full = pts[r >= R * 0.995, 2]
    zbase = full.max()
    nose_len = ztip - zbase
    zs = np.linspace(zbase, ztip, 200)
    rr = []
    near = pts[pts[:, 2] >= zbase - 1]
    for z0 in zs:
        sel = near[np.abs(near[:, 2] - z0) <= (ztip - zbase) / 150 + 0.2]
        rr.append(np.linalg.norm(sel[:, :2], axis=1).max() if len(sel) else 0.0)
    rr = np.minimum(np.array(rr), R)
    vol = float(np.trapezoid(np.pi * rr ** 2, zs))
    # fins: everything outside the body radius below the nose
    import build123d as b3
    tube = b3.Pos(0, 0, (ztip + zaft) / 2) * b3.Cylinder(R + 0.05, (ztip - zaft) + 10)
    fins = G.cut_shape(whole, tube)
    fs = [s for s in fins.solids()]
    vmax = max((s.volume for s in fs), default=0)
    fs = [s for s in fs if s.volume > 0.05 * vmax]
    if not fs:
        raise RuntimeError("no fins outside the body tube")
    f0 = max(fs, key=lambda s: s.volume)
    fv, _ = f0.tessellate(0.02, 0.1)
    fp = np.array([tuple(p) for p in fv])
    fr = np.linalg.norm(fp[:, :2], axis=1)
    rmax = fr.max()
    root = fp[fr <= R + 0.6]
    tip = fp[fr >= rmax - 0.6]
    cr = root[:, 2].max() - root[:, 2].min()
    ct = tip[:, 2].max() - tip[:, 2].min()
    xr = root[:, 2].max() - tip[:, 2].max()
    return {"R": R, "ztip": ztip, "zaft": zaft, "nose_len": nose_len, "nose_vol": vol,
            "fins": {"n": len(fs), "cr": cr, "ct": ct, "s": rmax - R, "xr": xr,
                     "xb": ztip - root[:, 2].max()}}


def _rocket(out: Path, c: Checks, m: dict, motor: str, margin, apogee):
    whole, comps = G.load_result(out)
    if whole is None:
        raise RuntimeError("no result")
    _valid(out, c)
    rho_of = (lambda n: 7.85 if "ballast" in n.lower() else 1.24)
    solids = comps or {"rocket": whole}
    mp = None
    for n, s in solids.items():
        p = mech.mass_props(s, rho_of(n))
        mp = p if mp is None else mp + p
    geo = {}

    def g():
        if not geo:
            body = next((s for n, s in solids.items() if "rocket" in n.lower()), None)
            if body is None:
                body = max(whole.solids(), key=lambda s: s.volume)
            geo.update(rocket_geometry(body))
        return geo
    mount = lambda: [h for h in G.holes(whole, 9.0, 9.3) if abs(abs(h.axis[2]) - 1) < 1e-3  # noqa
                     and h.length >= 68]
    c.add("Ø18 motor mount ≥ 70 deep at the aft end", lambda: (bool(mount()), f"{len(mount())} mounts"))

    def stab():
        gg = g()
        mot = mech.MOTORS[motor]
        tot = mp + mech.point_mass(mot["mass"], (0, 0, gg["zaft"] + mot["len"] / 2))
        cg_from_tip = gg["ztip"] - tot.com_mm[2]
        cp, cn = mech.barrowman_cp(gg["nose_len"], gg["nose_vol"], gg["R"], gg["fins"])
        sm = (cp - cg_from_tip) / (2 * gg["R"])
        m.update(static_margin_cal=round(sm, 3), cp_mm=round(cp, 1), cg_mm=round(cg_from_tip, 1),
                 n_fins=gg["fins"]["n"], structure_g=round(mp.mass_kg * 1000, 1),
                 liftoff_g=round(tot.mass_kg * 1000, 1))
        return sm
    c.add(f"static margin {margin[0]}–{margin[1]} cal (Barrowman, loaded motor)",
          lambda: (margin[0] <= stab() <= margin[1], f"{stab():.3f} cal"))

    def apo():
        gg = g()
        h = mech.apogee_m(motor, mp.mass_kg, 2 * gg["R"])
        m["apogee_m"] = round(h, 1)
        return h
    c.add(f"apogee {apogee[0]}–{apogee[1]} m on {motor} (Cd 0.5, 1-D)",
          lambda: (apogee[0] <= apo() <= apogee[1], f"{apo():.1f} m"))


def chk_rocket_c6(out, c, m):
    _rocket(out, c, m, "C6-5", (1.0, 2.0), (225, 275))


def chk_rocket_b6(out, c, m):
    _rocket(out, c, m, "B6-4", (1.5, 2.5), (85, 125))


# ---------------------------------------------------------------------------- the suite
ROCKET_RULES = (
    " Model it as one solid named rocket, nose tip up, axis on the Z axis, with an Ø18.0 mm "
    "motor mount bore at least 70 mm deep open at the aft end (the motor sits flush with the "
    "aft end). Material: printed PLA, 1.24 g/cm³, as solid as you model it; you may add a "
    "separate part named ballast (steel, 7.85 g/cm³) inside the nose. Assume Cd = 0.5 on the "
    "body cross-section, sea-level air, a vertical flight, the motor's published thrust curve, "
    "and coasting to apogee (ignore the delay charge).")

TASKS: list[PTask] = [
    # ---- DEV (one per grader type; may be inspected while improving Mises)
    PTask("PH01_bracket_200N", "structural", "dev", (
        "Design a 6061-T6 aluminium bracket (E = 68.9 GPa, ν = 0.33, yield 276 MPa, density 2.70 "
        "g/cm³) that holds 200 N with a safety factor of at least 2 on yield and weighs at most 25 g. "
        "Its mounting face lies on the plane x = 0 and is fully fixed; the load hangs (−Z) from a Ø5 "
        "mm hole whose axis is along Y with its centre at x = 60, z = 0. One solid."), chk_bracket_200n),
    PTask("PH03_sensor_mast", "modal", "dev", (
        "Design a 300 mm tall aluminium sensor mast (6061: E = 68.9 GPa, ν = 0.33, density 2.70 "
        "g/cm³) standing on the plane z = 0, where its base face is clamped. It must fit in a 40 x 40 "
        "mm footprint, weigh at most 100 g, and have a first natural frequency of at least 250 Hz. "
        "One solid."), chk_mast),
    PTask("PH05_two_link_arm", "dynamics", "dev", (
        "Design a two-link robot arm in 6061 aluminium (density 2.70 g/cm³, E = 68.9 GPa). Parts: "
        "upper_arm with Ø8 joint holes at the shoulder (axis Z through the origin) and the elbow "
        "(x = 200), and forearm with Ø8 holes at the elbow (x = 200) and the tip (x = 350); the arm "
        "lies along +X in the horizontal pose shown, gravity is −Y, joint axes are along Z. A 0.3 kg "
        "payload hangs at the tip hole. With the arm horizontal and accelerating upward at 3 rad/s² "
        "about the shoulder (elbow locked), the shoulder torque must stay ≤ 1.5 N·m and the elbow "
        "torque ≤ 0.5 N·m; the upper arm must deflect ≤ 0.5 mm at the elbow under 3 g of forearm + "
        "payload weight. Ignore motors and fasteners. " + "Make them separate parts with exactly "
        "these names: upper_arm, forearm."), chk_arm),
    PTask("PH08_robot_base", "stability", "dev", (
        "Design the aluminium structure (density 2.70 g/cm³) of a mobile robot: a base that stands on "
        "the floor (z = 0) within a 250 x 250 mm footprint, centred on the Z axis, and a mast that "
        "carries a 2.0 kg payload whose centre of mass is at (0, 0, 600) mm. The structure may weigh "
        "at most 1.5 kg and the robot with its payload must not tip over when the floor tilts 15° in "
        "any direction."), chk_robot_base),
    PTask("PH11_rocket_c6", "rocketry", "dev", (
        "Design a model rocket for an Estes C6-5 motor (10 N·s, 24.1 g loaded, 12.5 g propellant) "
        "with a static margin of 1.0–2.0 calibers (Barrowman, loaded motor) and an apogee of 225–275 "
        "m." + ROCKET_RULES), chk_rocket_c6),
    # ---- TEST (held out)
    PTask("PH02_ribbed_plate", "modal", "test", (
        "Design a stiffened 6061 aluminium plate (E = 68.9 GPa, ν = 0.33, density 2.70 g/cm³) with a "
        "150 x 100 mm footprint and at most 20 mm overall height, clamped along its 100 mm edge face "
        "on the plane x = 0 and free elsewhere. Its first natural frequency must be at least 400 Hz "
        "and it may weigh at most 120 g. One solid."), chk_rib_plate),
    PTask("PH04_column_buckling", "buckling", "test", (
        "Design a 250 mm steel column (E = 210 GPa, ν = 0.3, density 7.85 g/cm³) from z = 0 to z = 250: "
        "its base face is fixed and its top face carries 3 kN of axial compression (−Z) with the top "
        "free to sway. The linear buckling load factor must be at least 3 and the mass at most 160 g. "
        "One solid."), chk_column),
    PTask("PH06_flywheel", "dynamics", "test", (
        "Design a steel flywheel (density 7.85 g/cm³) on the Z axis with a Ø10 mm bore, outside "
        "diameter at most 120 mm, mass at most 650 g, and a moment of inertia about its axis of at "
        "least 1.5e-3 kg·m². It must be balanced (centre of mass on the axis). One solid."), chk_flywheel),
    PTask("PH07_pendulum", "dynamics", "test", (
        "Design a steel compound pendulum (density 7.85 g/cm³) that swings about a Ø4 mm pivot hole "
        "with a small-swing period of 1.00 ± 0.02 s. Its longest dimension may be at most 300 mm. One "
        "solid with exactly one Ø4 hole."), chk_pendulum),
    PTask("PH09_gear_3to1", "mechanisms", "test", (
        "Design a spur gear pair with a 3:1 reduction (±1%), involute teeth, module at least 1 mm, "
        "8 mm face width, a Ø6 mm bore in each gear, parallel axes along Z at most 60 mm apart, "
        "assembled meshing and phased so the teeth do not overlap. Make them separate parts with "
        "exactly these names: pinion, gear."), chk_gear_3to1),
    PTask("PH10_flexure_hinge", "structural", "test", (
        "Design a monolithic 6061 aluminium flexure hinge (E = 68.9 GPa, ν = 0.33): a fixed pad whose "
        "face lies on the plane x = 0, and a moving pad whose end face lies on the plane x = 60, "
        "joined by a flexure. A pure moment about the Y axis applied to the moving pad's end face must "
        "give a rotational stiffness of 0.5 ± 0.1 N·m/rad. One solid."), chk_flexure),
    PTask("PH12_rocket_b6", "rocketry", "test", (
        "Design a four-fin model rocket for an Estes B6-4 motor (5 N·s, 19.8 g loaded, 6.2 g "
        "propellant) with a static margin of 1.5–2.5 calibers (Barrowman, loaded motor) and an apogee "
        "of 85–125 m." + ROCKET_RULES), chk_rocket_b6),
    PTask("PH13_spring_beam", "structural", "test", (
        "Design a steel leaf-spring beam (E = 210 GPa, ν = 0.3, density 7.85 g/cm³) fixed on its end "
        "face at x = 0 and loaded on its free end face at x = 100: the tip stiffness must be 50 ± 5 "
        "N/mm and the mass at most 120 g. One solid."), chk_spring_beam),
    PTask("PH14_torsion_tube", "structural", "test", (
        "Design a steel torsion tube (E = 210 GPa, ν = 0.3, density 7.85 g/cm³) on the Z axis from "
        "z = 0 to z = 300, fixed at z = 0, with a torsional stiffness of 200 ± 20 N·m/rad measured at "
        "the z = 300 end, and mass at most 150 g. One solid."), chk_torsion_tube),
    PTask("PH15_shelf_bracket", "structural", "test", (
        "Design a 6061-T6 aluminium shelf bracket (E = 68.9 GPa, ν = 0.33, yield 276 MPa, density "
        "2.70 g/cm³): wall face on x = 0 (fixed), flat top face on z = 0 reaching to x = 120. A 500 N "
        "load (−Z) acts on the last 10 mm of the top face. Safety factor at least 2 on yield, "
        "deflection at most 0.3 mm, mass at most 60 g. One solid."), chk_shelf_l),
    PTask("PH16_crank_arm", "structural", "test", (
        "Design a 7075-T6 aluminium bicycle crank arm (E = 71.7 GPa, ν = 0.33, yield 503 MPa, density "
        "2.81 g/cm³): a Ø12 mm spindle hole on the Z axis at the origin, a Ø9 mm pedal hole 150 mm "
        "away, both axes along Z. With the spindle hole fixed, a 1.2 kN pedal force perpendicular to "
        "the arm in the plane of rotation must give a safety factor of at least 1.5 on yield and at "
        "most 3 mm deflection; mass at most 150 g. One solid."), chk_crank),
    PTask("PH17_tuning_bar", "modal", "test", (
        "Design a steel tuning bar (E = 210 GPa, ν = 0.3, density 7.85 g/cm³) at least 100 mm long, "
        "clamped on its end face on the plane x = 0, whose first natural frequency is 440 ± 10 Hz. "
        "One solid."), chk_tuning_bar),
    PTask("PH18_balanced_lever", "dynamics", "test", (
        "Design a steel lever (density 7.85 g/cm³) with a Ø6 mm pivot hole on the Z axis at the origin "
        "and a Ø5 mm load hole at x = 120. With a 0.5 kg load hung at the load hole, the lever plus "
        "load must balance about the pivot (combined centre of mass within 0.5 mm of the pivot axis); "
        "the lever may weigh at most 1.2 kg. One solid."), chk_lever),
    PTask("PH19_lamp_base", "stability", "test", (
        "Design a steel base (density 7.85 g/cm³) for a desk lamp: it stands on a table (z = 0), "
        "centred on the Z axis, within a Ø150 mm (150 x 150) footprint, at most 2.0 kg. The lamp arm "
        "it carries (not modelled) weighs 0.8 kg with its centre of mass at (220, 0, 400) mm. The lamp "
        "must not tip when the table tilts 10° in any direction."), chk_lamp_base),
    PTask("PH20_drone_arm", "modal", "test", (
        "Design a 6061 aluminium drone arm (E = 68.9 GPa, ν = 0.33, density 2.70 g/cm³): a 120 mm "
        "cantilever from its root face on the plane x = 0 (clamped) to its tip at x = 120, at most "
        "15 g, with a first natural frequency of at least 300 Hz. One solid."), chk_drone_arm),
]

BY_ID = {t.id: t for t in TASKS}


def score(task_id: str, out: Path) -> dict:
    t = BY_ID[task_id]
    c = Checks()
    metrics: dict = {}
    try:
        if not (Path(out) / "result.step").is_file():
            c.add("result exported", lambda: (False, "no result.step"))
        else:
            t.check(Path(out), c, metrics)
    except Exception as e:  # noqa: BLE001
        c.add("grader", lambda: (False, f"{type(e).__name__}: {e}"))
    n = len(c.items)
    passed = sum(r.passed for r in c.items)
    metrics = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str)) or v is None}
    return {"id": task_id, "category": t.category, "split": t.split, "n_req": n, "passed": passed,
            "score": passed / n if n else 0.0, "strict": n > 0 and passed == n,
            "metrics": metrics, "requirements": [r.__dict__ for r in c.items]}
