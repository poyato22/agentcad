"""Rocket aerodynamics (Plan 11 F6): the Barrowman center of pressure and a component drag build-up.

Geometry is axisymmetric along the rocket axis, positions in metres from the nose tip.

Center of pressure: J. S. Barrowman, "The Practical Calculation of the Aerodynamic
Characteristics of Slender Finned Vehicles" (1967), as summarised for model rockets in
Barrowman & Barrowman, "The Theoretical Prediction of the Center of Pressure", NARAM-8 R&D
(1966, the basis of NAR TIR-33):
- nose: CN = 2; X = 0.666 L (cone), 0.466 L (tangent ogive), 0.5 L (parabolic / Haack /
  elliptical: 0.5 is the common slender approximation).
- conical transition: CN = 2 [(d_aft/d)^2 - (d_fore/d)^2],
  X = X_t + L/3 [1 + (1 - d_fore/d_aft) / (1 - (d_fore/d_aft)^2)].
- N fins: CN = [1 + R/(S + R)] 4N (S/d)^2 / (1 + sqrt(1 + (2 L_F / (C_R + C_T))^2)),
  X = X_B + X_R (C_R + 2 C_T) / (3 (C_R + C_T)) + [(C_R + C_T) - C_R C_T / (C_R + C_T)] / 6,
  L_F the mid-chord line length. (N = 3 or 4 as derived; N fins scale linearly, the method's
  usual extension.)
- the body tube itself: CN = 0 at small angles of attack.
Valid subsonic (M < ~0.8) at small angles of attack.

Drag: the OpenRocket technical documentation's subsonic build-up (S. Niskanen, "Development of an
Open Source model rocket simulation software", 2009, ch. 3.4): skin friction (turbulent or
roughness-limited, with the body's fineness and the fins' thickness corrections), body pressure
drag (nose ~0 below M 0.8 for a smooth nose; boattail by the length / diameter-change rule),
fin leading- and trailing-edge pressure drag by profile, and base drag 0.12 + 0.13 M^2.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---- the atmosphere (ISA troposphere)

R_AIR = 287.05
GAMMA = 1.4
G0 = 9.80665


@dataclass(frozen=True)
class Air:
    rho: float          # kg/m3
    p: float            # Pa
    t: float            # K
    a: float            # speed of sound m/s
    nu: float           # kinematic viscosity m2/s


def isa(h_m: float, *, t0_k: float = 288.15, p0_pa: float = 101325.0) -> Air:
    """ISA troposphere (to 11 km) from a launch site at `t0_k`, `p0_pa` (h above that site)."""
    h = max(0.0, min(h_m, 11000.0))
    t = t0_k - 0.0065 * h
    p = p0_pa * (t / t0_k) ** 5.25588
    rho = p / (R_AIR * t)
    mu = 1.458e-6 * t ** 1.5 / (t + 110.4)          # Sutherland
    return Air(rho, p, t, math.sqrt(GAMMA * R_AIR * t), mu / rho)


def site_air(alt_m: float = 0.0, temp_c: float | None = None) -> tuple[float, float]:
    """(T0 K, p0 Pa) at a launch site `alt_m` above sea level, optionally at `temp_c`."""
    t_std = 288.15 - 0.0065 * max(0.0, alt_m)
    p0 = 101325.0 * (t_std / 288.15) ** 5.25588
    return (temp_c + 273.15 if temp_c is not None else t_std), p0


# ---- geometry

NOSE_X = {"conical": 0.666, "ogive": 0.466, "parabolic": 0.5, "haack": 0.5,
          "elliptical": 0.333, "power": 0.5}
# fraction of the nose's length x its base area that is its volume (for wetted area/CG notes)
ROUGHNESS_M = {"polished": 2e-6, "smooth": 20e-6, "paint": 60e-6, "printed": 100e-6,
               "rough": 250e-6}


@dataclass
class Nose:
    shape: str
    length: float           # m
    diameter: float         # base diameter m


@dataclass
class Section:
    """A tube (fore == aft) or a conical transition, from `x` (m from tip) for `length`."""
    x: float
    length: float
    d_fore: float
    d_aft: float

    @property
    def is_transition(self) -> bool:
        return abs(self.d_fore - self.d_aft) > 1e-6


@dataclass
class Fins:
    count: int
    root: float             # root chord m
    tip: float              # tip chord m
    span: float             # semi-span (root to tip) m
    sweep: float            # LE sweep distance, root LE to tip LE, m
    thickness: float        # m
    x_le: float             # root leading edge, m from tip
    body_d: float           # body diameter at the fins, m
    profile: str = "square"     # square | rounded | airfoil
    midchord: float | None = None   # mid-chord line length when given (Barrowman's L_F)

    @property
    def area(self) -> float:
        return (self.root + self.tip) / 2 * self.span

    @property
    def lf(self) -> float:
        if self.midchord is not None:
            return self.midchord
        return math.hypot(self.span, self.sweep + self.tip / 2 - self.root / 2)


@dataclass
class Body:
    nose: Nose
    sections: list[Section]
    fins: list[Fins]
    ref_d: float            # reference diameter (largest body diameter) m

    @property
    def length(self) -> float:
        end = self.nose.length
        for s in self.sections:
            end = max(end, s.x + s.length)
        return end

    @property
    def aft_d(self) -> float:
        return self.sections[-1].d_aft if self.sections else self.nose.diameter

    @property
    def ref_area(self) -> float:
        return math.pi * self.ref_d ** 2 / 4


# ---- Barrowman

@dataclass
class CpTerm:
    name: str
    cn: float
    x: float                # m from the tip


def barrowman(body: Body) -> tuple[float, float, list[CpTerm]]:
    """(CP m from tip, total CN_alpha per rad, the terms)."""
    d = body.ref_d
    terms: list[CpTerm] = []
    n = body.nose
    terms.append(CpTerm(f"nose ({n.shape})", 2.0 * (n.diameter / d) ** 2,
                        NOSE_X.get(n.shape, 0.466) * n.length))
    for i, s in enumerate(body.sections):
        if not s.is_transition:
            continue
        cn = 2.0 * ((s.d_aft / d) ** 2 - (s.d_fore / d) ** 2)
        r = s.d_fore / s.d_aft
        x = s.x + s.length / 3 * (1 + (1 - r) / (1 - r * r))
        terms.append(CpTerm("boattail" if s.d_aft < s.d_fore else "shoulder", cn, x))
    for k, f in enumerate(body.fins):
        rr = f.body_d / 2
        kfb = 1 + rr / (f.span + rr)
        cn = kfb * (4 * f.count * (f.span / d) ** 2) / (
            1 + math.sqrt(1 + (2 * f.lf / (f.root + f.tip)) ** 2))
        s = f.root + f.tip
        x = f.x_le + f.sweep * (f.root + 2 * f.tip) / (3 * s) + (s - f.root * f.tip / s) / 6
        terms.append(CpTerm(f"fins ({f.count})" if len(body.fins) == 1 else f"fin set {k + 1}",
                            cn, x))
    cn_tot = sum(t.cn for t in terms)
    cp = sum(t.cn * t.x for t in terms) / cn_tot if cn_tot > 0 else 0.0
    return cp, cn_tot, terms


# ---- wetted areas

def _nose_wet(n: Nose, steps: int = 60) -> float:
    r0, L = n.diameter / 2, n.length
    if n.shape == "ogive":
        rho = (r0 * r0 + L * L) / (2 * r0)

        def r(x: float) -> float:          # x from the tip
            return math.sqrt(max(rho * rho - (L - x) ** 2, 0.0)) + r0 - rho
    elif n.shape == "conical":
        def r(x: float) -> float:
            return r0 * x / L
    elif n.shape == "elliptical":
        def r(x: float) -> float:
            return r0 * math.sqrt(max(0.0, 1 - ((L - x) / L) ** 2))
    else:                                   # parabolic / haack / power: a 1/2-power body
        def r(x: float) -> float:
            return r0 * math.sqrt(max(0.0, x / L))
    area = 0.0
    for i in range(steps):
        x0, x1 = L * i / steps, L * (i + 1) / steps
        a, b = r(x0), r(x1)
        area += math.pi * (a + b) * math.hypot(x1 - x0, b - a)
    return area


def _section_wet(s: Section) -> float:
    r1, r2 = s.d_fore / 2, s.d_aft / 2
    return math.pi * (r1 + r2) * math.hypot(s.length, r1 - r2)


# ---- drag

@dataclass
class DragBreakdown:
    cd: float
    friction: float
    pressure: float
    base: float
    parasitic: float

    def as_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}


def _cf(re: float, mach: float, rough_m: float, length: float) -> float:
    """Skin friction coefficient (OpenRocket techdoc eq. 3.81-3.84)."""
    if re < 1e4:
        cf = 1.48e-2
        return cf * (1 - 0.1 * mach * mach)
    turb = 1.0 / (1.50 * math.log(re) - 5.6) ** 2 * (1 - 0.1 * mach * mach)
    if rough_m <= 0:
        return turb
    re_crit = 51.0 * (rough_m / length) ** -1.039
    if re < re_crit:
        return turb
    return 0.032 * (rough_m / length) ** 0.2 / (1 + 0.18 * mach * mach)


def _base_cd(mach: float) -> float:
    return 0.12 + 0.13 * mach * mach if mach < 1 else 0.25 / mach


def _stag(mach: float) -> float:
    """Stagnation pressure coefficient for a blunt face (OpenRocket eq. 3.86)."""
    if mach < 1:
        return 0.85 * (1 + mach * mach / 4 + mach ** 4 / 40)
    return 0.85 * (1.84 - 0.76 / (mach * mach) + 0.166 / mach ** 4 + 0.035 / mach ** 6)


def drag(body: Body, v: float, air: Air, *, roughness_m: float = 60e-6,
         parasitic_cd: float = 0.0) -> DragBreakdown:
    """Zero-angle-of-attack drag coefficient at speed v (referenced to the body's ref area)."""
    aref = body.ref_area
    L = body.length
    mach = v / air.a
    re = max(v, 0.1) * L / air.nu
    cf = _cf(re, mach, roughness_m, L)
    # body: nose + sections, with the fineness correction (1 + 1/(2 f_B))
    body_wet = _nose_wet(body.nose) + sum(_section_wet(s) for s in body.sections)
    fb = L / body.ref_d
    fric = cf * (1 + 1 / (2 * fb)) * body_wet
    pressure = 0.0
    for f in body.fins:
        mac = (f.root + f.tip) / 2
        wet = 2 * f.area * f.count
        fric += cf * (1 + 2 * f.thickness / mac) * wet
        front = f.thickness * f.span * f.count
        sweep_le = math.atan2(f.sweep, f.span)
        if f.profile == "square":
            le = _stag(mach) * math.cos(sweep_le) ** 2
            te = _base_cd(mach)
        elif f.profile == "rounded":
            le = ((1 - mach * mach) ** -0.417 - 1) * math.cos(sweep_le) ** 2 if mach < 0.9 else 0.3
            te = _base_cd(mach) / 2
        else:                                     # airfoil: no blunt faces
            le, te = 0.0, 0.0
        pressure += (le + te) * front
    friction = fric / aref
    # boattails / shoulders
    for s in body.sections:
        if not s.is_transition:
            continue
        a1, a2 = math.pi * s.d_fore ** 2 / 4, math.pi * s.d_aft ** 2 / 4
        if s.d_aft < s.d_fore:                    # boattail (OpenRocket eq. 3.88)
            gamma = s.length / (s.d_fore - s.d_aft)
            c = 1.0 if gamma < 1 else 0.0 if gamma > 3 else (3 - gamma) / 2
            pressure += c * _base_cd(mach) * (a1 - a2)
        else:                                     # a shoulder: a conical-nose-like face
            phi = math.atan2((s.d_aft - s.d_fore) / 2, s.length)
            pressure += 0.8 * math.sin(phi) ** 2 * (a2 - a1)
    if body.nose.shape == "conical":
        phi = math.atan2(body.nose.diameter / 2, body.nose.length)
        pressure += 0.8 * math.sin(phi) ** 2 * math.pi * body.nose.diameter ** 2 / 4
    pressure /= aref
    base = _base_cd(mach) * (math.pi * body.aft_d ** 2 / 4) / aref
    cd = friction + pressure + base + parasitic_cd
    return DragBreakdown(cd, friction, pressure, base, parasitic_cd)
