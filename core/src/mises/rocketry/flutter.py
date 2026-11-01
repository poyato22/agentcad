"""Fin flutter speed (Plan 11 F6): NACA TN-4197.

D. J. Martin, "Summary of Flutter Experiences as a Guide to the Preliminary Design of Lifting
Surfaces on Missiles", NACA TN 4197 (1958), equation (18), p. 14:

    (V_f / a)^2 = G_E / [ 39.3 A^3 / ((t/c)^3 (A + 2)) * (lambda + 1)/2 * (p / p0) ]

where 39.3 = (24 eps / pi) gamma p0 with eps = 0.25 (elastic axis at the 50% chord), gamma =
1.4 and p0 = 14.7 psi (eq. 16-17). Written in consistent units (G and p both in Pa):

    V_f = a sqrt( G (t/c)^3 (A + 2) / (1.337 A^3 (lambda + 1) p) ),   1.337 = 12 eps gamma / pi

A = span^2 / fin area (aspect ratio of one fin), lambda = tip / root chord, t/c = thickness over
root chord, a and p at the flight condition. Some popular restatements carry an extra 1/2 in the
denominator, which raises V_f by sqrt(2) (unconservative); this module follows the report.

The method is a screening estimate (the report's own data scatter; real fins add glue joints and
print-layer weakness): Mises asks for V_f >= 1.5 x the maximum flight speed and fails at <= 1.0.
"""
from __future__ import annotations

import math

EPS = 0.25
FLUTTER_K = 12 * EPS * 1.4 / math.pi        # 1.3369
MIN_RATIO = 1.5


def flutter_speed(g_pa: float, *, root: float, tip: float, span: float, thickness: float,
                  a: float, p: float) -> float:
    """V_f in m/s (lengths in any one unit; g_pa and p in Pa; a in m/s)."""
    area = (root + tip) / 2 * span
    ar = span * span / area
    lam = tip / root
    tc = thickness / root
    return a * math.sqrt(g_pa * tc ** 3 * (ar + 2) / (FLUTTER_K * ar ** 3 * (lam + 1) * p))


def shear_modulus_pa(material: str | None, home=None) -> tuple[float, str]:
    """(G in Pa, the note saying where it came from) from the sim materials table: G = E / (2
    (1 + nu)), the in-plane shear modulus (sim/materials.py: no FDM sheet states G)."""
    from ..sim.materials import get_material
    m = get_material(material or "PLA", home)
    g = m.e_mpa / (2 * (1 + m.nu)) * 1e6
    return g, (f"{m.name}: G = E / (2 (1 + nu)) = {m.e_mpa:g} / (2 x {1 + m.nu:.2f}) = "
               f"{g / 1e6:.0f} MPa ({m.source})")
