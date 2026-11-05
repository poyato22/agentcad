"""Independent physics for the AgentCAD-Physics graders: mass properties, tipping, a Barrowman
rocket model with a 1-D flight simulation. Nothing here is shared with AgentCAD's own code."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

G0 = 9.80665


@dataclass
class MassProps:
    mass_kg: float
    com_mm: np.ndarray

    def __add__(self, o: "MassProps") -> "MassProps":
        m = self.mass_kg + o.mass_kg
        return MassProps(m, (self.com_mm * self.mass_kg + o.com_mm * o.mass_kg) / m if m else
                         self.com_mm)


def mass_props(shape, rho_g_cm3: float) -> MassProps:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, g)
    c = g.CentreOfMass()
    return MassProps(abs(g.Mass()) * rho_g_cm3 * 1e-6, np.array([c.X(), c.Y(), c.Z()]))


def inertia_about(shape, rho_g_cm3: float, point, axis) -> float:
    """Moment of inertia (kg m^2) about the axis through point (mm)."""
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, g)
    I = g.MomentOfInertia(gp_Ax1(gp_Pnt(*map(float, point)), gp_Dir(*map(float, axis))))
    return abs(I) * rho_g_cm3 * 1e-6 * 1e-6        # mm^5 * kg/mm^3 -> kg mm^2 -> kg m^2


def point_mass(m_kg: float, xyz_mm) -> MassProps:
    return MassProps(m_kg, np.asarray(xyz_mm, float))


# ---------------------------------------------------------------------------- tipping
def contact_hull(shape, tol: float = 0.05) -> np.ndarray:
    """2-D convex hull (x, y) of the shape's points touching its lowest plane (the floor)."""
    from scipy.spatial import ConvexHull
    v, _ = shape.tessellate(0.05, 0.2)
    pts = np.array([tuple(p) for p in v])
    zmin = pts[:, 2].min()
    base = pts[pts[:, 2] <= zmin + tol][:, :2]
    if len(base) < 3:
        raise RuntimeError("fewer than 3 floor contact points")
    h = ConvexHull(base)
    return base[h.vertices]


def tipping_angle_deg(hull_xy: np.ndarray, com_mm, z_floor: float) -> float:
    """The smallest tilt (any direction) at which the centre of mass passes over a hull edge:
    atan(distance from the COM's floor projection to the nearest edge / COM height)."""
    c = np.asarray(com_mm, float)
    h = c[2] - z_floor
    p = c[:2]
    n = len(hull_xy)
    dmin = math.inf
    inside = True
    for i in range(n):
        a, b = hull_xy[i], hull_xy[(i + 1) % n]
        e = b - a
        nrm = np.array([e[1], -e[0]]) / np.linalg.norm(e)
        # signed distance with the hull counter-clockwise (scipy order): inside is negative
        d = float((p - a) @ nrm)
        if d > 0:
            inside = False
        dmin = min(dmin, abs(d))
    if not inside or h <= 0:
        return 0.0
    return math.degrees(math.atan2(dmin, h))


# ---------------------------------------------------------------------------- rocket
# Thrust curves (t s, F N), scaled to the published total impulse.
MOTORS = {
    "C6-5": {"curve": [(0, 0), (0.05, 4.0), (0.15, 12.5), (0.22, 14.1), (0.30, 8.0), (0.40, 5.5),
                       (1.70, 4.6), (1.86, 0)], "impulse": 10.0, "mass": 0.0241,
             "prop": 0.0125, "len": 70.0, "dia": 18.0},
    "B6-4": {"curve": [(0, 0), (0.05, 4.0), (0.12, 11.5), (0.21, 12.0), (0.30, 6.5), (0.40, 5.0),
                       (0.75, 4.5), (0.86, 0)], "impulse": 5.0, "mass": 0.0198,
             "prop": 0.0062, "len": 70.0, "dia": 18.0},
}


def thrust(motor: str, t: float) -> float:
    m = MOTORS[motor]
    pts = m["curve"]
    raw = sum(0.5 * (pts[i][1] + pts[i + 1][1]) * (pts[i + 1][0] - pts[i][0])
              for i in range(len(pts) - 1))
    k = m["impulse"] / raw
    if t <= pts[0][0] or t >= pts[-1][0]:
        return 0.0
    for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return k * (f0 + (f1 - f0) * (t - t0) / (t1 - t0))
    return 0.0


def apogee_m(motor: str, dry_mass_kg: float, diameter_mm: float, cd: float = 0.5,
             rho_air: float = 1.225, dt: float = 0.001) -> float:
    """Vertical flight from rest: thrust, gravity, quadratic drag on the body cross-section;
    propellant mass burns in proportion to impulse delivered. Returns the apogee (m)."""
    m = MOTORS[motor]
    area = math.pi * (diameter_mm / 2000.0) ** 2
    t, v, h, imp = 0.0, 0.0, 0.0, 0.0
    burn_end = m["curve"][-1][0]
    while True:
        F = thrust(motor, t)
        mass = dry_mass_kg + m["mass"] - m["prop"] * min(imp / m["impulse"], 1.0)
        drag = 0.5 * rho_air * cd * area * v * abs(v)
        a = (F - drag) / mass - G0
        if t < burn_end and F > 0 and v <= 0 and a < 0:
            a = 0.0                                  # still on the pad
        v += a * dt
        h += v * dt
        imp += F * dt
        t += dt
        if t > burn_end and v <= 0:
            return h
        if t > 60:
            return h


def barrowman_cp(nose_len: float, nose_vol: float, body_r: float, fins: dict) -> tuple[float, float]:
    """Centre of pressure (mm from the nose tip) and total normal-force slope. Nose: CN = 2 at
    the slender-body CP (L - V/A_base, exact for any nose shape). Fins: Barrowman with the
    body interference factor; fins = {n, cr, ct, s, xr (LE sweep), xb (root LE from tip)}."""
    a_ref = math.pi * body_r ** 2
    xn = nose_len - nose_vol / a_ref
    cn_n = 2.0
    n, cr, ct, s, xr, xb = (fins[k] for k in ("n", "cr", "ct", "s", "xr", "xb"))
    d = 2 * body_r
    lm = math.hypot(s, xr + ct / 2 - cr / 2)
    cn_f = (1 + body_r / (s + body_r)) * (4 * n * (s / d) ** 2) / (
        1 + math.sqrt(1 + (2 * lm / (cr + ct)) ** 2))
    xf = xb + xr / 3 * (cr + 2 * ct) / (cr + ct) + (cr + ct - cr * ct / (cr + ct)) / 6
    return (cn_n * xn + cn_f * xf) / (cn_n + cn_f), cn_n + cn_f
