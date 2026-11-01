"""Sizing formulas for mechanisms (research 2026-11-01 §2.3): pure functions, SI-ish units
(N, mm, N*m, MPa, rpm). Each returns plain numbers (or a dict with the inputs it used), so a
card can show its working. Allowables come from the materials table (sim/materials.py).
"""
from __future__ import annotations

import math

# Lewis form factor Y for 20 deg full-depth teeth (Shigley table 14-2)
_LEWIS = [(12, 0.245), (13, 0.261), (14, 0.277), (15, 0.290), (16, 0.296), (17, 0.303),
          (18, 0.309), (19, 0.314), (20, 0.322), (21, 0.328), (22, 0.331), (24, 0.337),
          (26, 0.346), (28, 0.353), (30, 0.359), (34, 0.371), (38, 0.384), (43, 0.397),
          (50, 0.409), (60, 0.422), (75, 0.435), (100, 0.447), (150, 0.460), (300, 0.472),
          (400, 0.480)]


def lewis_y(teeth: int) -> float:
    """Lewis form factor Y(z) (20 deg full depth), linear between table rows."""
    z = max(12, min(400, int(teeth)))
    for (z0, y0), (z1, y1) in zip(_LEWIS, _LEWIS[1:]):
        if z0 <= z <= z1:
            return y0 + (y1 - y0) * (z - z0) / (z1 - z0)
    return _LEWIS[-1][1]


def lewis_stress_mpa(torque_nm: float, module: float, teeth: int, width: float,
                     kv: float = 1.0) -> float:
    """Gear tooth bending stress sigma = F_t Kv / (b m Y), F_t = 2 T / d (d = m z)."""
    d = module * teeth
    ft = 2 * torque_nm * 1000.0 / d
    return ft * kv / (width * module * lewis_y(teeth))


def gear_safety(torque_nm: float, module: float, teeth: int, width: float,
                allowable_mpa: float, fatigue_factor: float = 0.5) -> float:
    """Bending safety factor of the pinion; polymer teeth use half the allowable for fatigue
    [J, research §2.3]."""
    s = lewis_stress_mpa(torque_nm, module, teeth, width)
    return allowable_mpa * fatigue_factor / s if s > 0 else math.inf


def contact_ratio(z1: int, z2: int, module: float = 1.0, pressure_deg: float = 20.0) -> float:
    """External spur pair contact ratio (standard addendum = m): length of action / base pitch."""
    a = math.radians(pressure_deg)
    r1, r2 = module * z1 / 2, module * z2 / 2
    ra1, ra2 = r1 + module, r2 + module
    rb1, rb2 = r1 * math.cos(a), r2 * math.cos(a)
    c = r1 + r2
    la = math.sqrt(ra1 ** 2 - rb1 ** 2) + math.sqrt(ra2 ** 2 - rb2 ** 2) - c * math.sin(a)
    return la / (math.pi * module * math.cos(a))


def planetary_ok(zs: int, zp: int, zr: int, n: int) -> list[str]:
    """Problems with a simple planetary's tooth counts (empty = fine): z_r = z_s + 2 z_p, the
    assembly condition (z_s + z_r) / n integer, and planets not touching each other."""
    out = []
    if zr != zs + 2 * zp:
        out.append(f"ring teeth must be sun + 2 x planet = {zs + 2 * zp}")
    if (zs + zr) % n:
        out.append(f"(sun + ring) / planets = {(zs + zr) / n:g} is not an integer: the planets "
                   "cannot be spaced evenly")
    a = (zs + zp) / 2
    if 2 * a * math.sin(math.pi / n) <= zp + 2:
        out.append("neighbouring planets overlap: use fewer or smaller planets")
    return out


def belt_teeth_in_mesh(z_small: int, pd_small: float, pd_large: float, center: float) -> float:
    """Teeth engaged on the small pulley: z * wrap / 360, wrap = 180 - 2 asin((R - r) / C)."""
    wrap = 180.0 - 2 * math.degrees(math.asin((pd_large - pd_small) / 2 / center))
    return z_small * wrap / 360.0


def belt_pull_n(torque_nm: float, pitch_d: float) -> float:
    """Effective belt pull F_e = T / r."""
    return torque_nm * 1000.0 / (pitch_d / 2)


def screw_force_n(torque_nm: float, lead_mm: float, efficiency: float) -> float:
    """Axial force from a lead/ball screw: F = 2 pi eta T / p."""
    return 2 * math.pi * efficiency * torque_nm * 1000.0 / lead_mm


def screw_efficiency(lead_mm: float, d_mm: float, mu: float = 0.15) -> float:
    """Lead-screw efficiency eta = tan(lambda) / tan(lambda + phi), lambda = atan(p / (pi d_m)),
    phi = atan(mu) (mu ~0.15 brass/POM nut on steel, ~0.005 for a ball screw)."""
    lam = math.atan(lead_mm / (math.pi * d_mm))
    phi = math.atan(mu)
    return math.tan(lam) / math.tan(lam + phi)


def screw_backdrivable(lead_mm: float, d_mm: float, mu: float = 0.15) -> bool:
    """Back-drives when tan(lambda) > mu."""
    return lead_mm / (math.pi * d_mm) > mu


def euler_buckling_n(e_mpa: float, d_mm: float, length_mm: float, k: float = 1.0) -> float:
    """Euler buckling load of a round column F_cr = pi^2 E I / (K L)^2 (K: 1 pinned-pinned,
    0.7 fixed-pinned, 0.5 fixed-fixed, 2 free end)."""
    i = math.pi * d_mm ** 4 / 64
    return math.pi ** 2 * e_mpa * i / (k * length_mm) ** 2


def screw_critical_rpm(d_root_mm: float, length_mm: float, f: float = 15.1) -> float:
    """Screw critical speed n_c = f * d_r / L^2 * 1e7 rpm (THK ball-screw catalogue form; end
    fixity f: 21.9 fixed-fixed, 15.1 fixed-supported, 9.7 supported-supported, 3.4
    fixed-free) [V]. Run below ~80% of it."""
    return f * d_root_mm / length_mm ** 2 * 1e7


def shaft_stress_mpa(torque_nm: float, moment_nm: float, d_mm: float, kt: float = 1.0) -> dict:
    """Shaft torsion tau = 16 T / (pi d^3), bending sigma = 32 M / (pi d^3), von Mises
    sqrt(sigma^2 + 3 tau^2), times a stress concentration kt (keyway / D-flat ~2 [J])."""
    tau = 16 * torque_nm * 1000.0 / (math.pi * d_mm ** 3) * kt
    sig = 32 * moment_nm * 1000.0 / (math.pi * d_mm ** 3) * kt
    return {"tau_mpa": tau, "sigma_mpa": sig, "von_mises_mpa": math.sqrt(sig ** 2 + 3 * tau ** 2)}


def bearing_l10_hours(c_n: float, p_n: float, rpm: float) -> float:
    """Ball bearing basic rating life L10 = (C / P)^3 x 1e6 revolutions, in hours at `rpm`."""
    if p_n <= 0 or rpm <= 0:
        return math.inf
    return (c_n / p_n) ** 3 * 1e6 / (60 * rpm)


def pin_shear_mpa(force_n: float, d_mm: float, shear_planes: int = 2) -> float:
    """Pin shear tau = F / (n pi d^2 / 4) (double shear by default)."""
    return force_n / (shear_planes * math.pi * d_mm ** 2 / 4)


def lug_bearing_mpa(force_n: float, d_mm: float, t_mm: float) -> float:
    """Bearing stress of a pin on a printed lug F / (d t); edge distance >= 1.5 d [J]."""
    return force_n / (d_mm * t_mm)


def snap_fit_strain(t_mm: float, deflection_mm: float, length_mm: float) -> float:
    """Cantilever snap-fit strain eps = 1.5 t y / L^2 (allow PLA ~2%, PETG ~4% [J, BASF])."""
    return 1.5 * t_mm * deflection_mm / length_mm ** 2


def flexure_strain(t_mm: float, bend_radius_mm: float) -> float:
    """Living-hinge strain eps = t / (2 R)."""
    return t_mm / (2 * bend_radius_mm)


def grashof(links: list[float]) -> dict:
    """Grashof classification of a four-bar [ground, crank, coupler, rocker]: s + l <= p + q
    means one link fully rotates. Returns {grashof, kind, s_plus_l, p_plus_q}."""
    d, a, b, c = (float(x) for x in links)
    srt = sorted([d, a, b, c])
    s, l = srt[0], srt[-1]
    pq = sum(srt) - s - l
    g = s + l <= pq + 1e-9
    if not g:
        kind = "non-Grashof triple rocker"
    elif abs(s + l - pq) < 1e-9:
        kind = "change point (parallelogram or deltoid)"
    elif s == a:
        kind = "crank-rocker"
    elif s == d:
        kind = "double crank (drag link)"
    elif s == b:
        kind = "double rocker (Grashof)"
    else:
        kind = "rocker-crank"
    return {"grashof": g, "kind": kind, "s_plus_l": s + l, "p_plus_q": pq}


def freudenstein_rocker(t2_deg: float, a: float, b: float, c: float, d: float,
                        branch: int = 1) -> float | None:
    """Rocker angle (deg) of a four-bar (ground d on +X from the crank pivot, crank a, coupler b,
    rocker c) for crank angle t2; None where the loop cannot close."""
    t2 = math.radians(t2_deg)
    px, py = a * math.cos(t2), a * math.sin(t2)
    dx, dy = px - d, py
    r = math.hypot(dx, dy)
    cos_g = (c * c + r * r - b * b) / (2 * c * r) if r > 0 else 2.0
    if abs(cos_g) > 1:
        return None
    return math.degrees(math.atan2(dy, dx) + branch * math.acos(cos_g))


def transmission_angle_deg(t2_deg: float, a: float, b: float, c: float, d: float) -> float:
    """Four-bar transmission angle mu (between coupler and rocker): cos mu =
    (b^2 + c^2 - a^2 - d^2 + 2 a d cos t2) / (2 b c). Keep it within 40-140 deg."""
    t2 = math.radians(t2_deg)
    v = (b * b + c * c - a * a - d * d + 2 * a * d * math.cos(t2)) / (2 * b * c)
    return math.degrees(math.acos(max(-1.0, min(1.0, v))))


def slider_crank_x(theta_deg: float, r: float, l: float, e: float = 0.0) -> float:
    """Slider position x(theta) = r cos(theta) + sqrt(l^2 - (r sin(theta) - e)^2)."""
    t = math.radians(theta_deg)
    return r * math.cos(t) + math.sqrt(l * l - (r * math.sin(t) - e) ** 2)


def worm_efficiency(lead_angle_deg: float, mu: float = 0.1) -> float:
    """Worm efficiency eta = tan(lambda) / tan(lambda + phi); self-locking when lambda < phi."""
    lam = math.radians(lead_angle_deg)
    return math.tan(lam) / math.tan(lam + math.atan(mu))
