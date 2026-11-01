"""Mass properties for dynamics (Plan 10 C3).

The build worker writes each part's B-rep mass properties into metrics.json (`inertia`:
{mass_kg, com_mm, inertia_kgm2} at the density it built with; cad/worker.py `_inertia`). The
mass that counts is the part's tree mass (mises.mass: volume x ITS material), so the tensor is
scaled to it (inertia is linear in density). A part without B-rep inertia (an old build, a
catalog envelope) is a solid box of its bounding box with that mass: approximate, and marked.

Units here: kg, m, kg m2. Transforms are Mises 4x4 row-major matrices in mm.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MassProps:
    mass: float                 # kg
    com: list[float]            # m
    inertia: list[list[float]]  # kg m2 about the com
    source: str                 # "brep" | "box" | "sum"


def _box(mass: float, size_mm) -> np.ndarray:
    x, y, z = (float(s) / 1000.0 for s in size_mm)
    return np.diag([mass * (y * y + z * z) / 12, mass * (x * x + z * z) / 12,
                    mass * (x * x + y * y) / 12])


def of_part(metrics: dict, mass_kg: float) -> MassProps:
    """The part's own mass properties (part coordinates) scaled to `mass_kg`."""
    i = metrics.get("inertia") if isinstance(metrics, dict) else None
    if isinstance(i, dict) and i.get("mass_kg") and i.get("inertia_kgm2"):
        k = mass_kg / float(i["mass_kg"])
        return MassProps(mass_kg, [float(c) / 1000.0 for c in i["com_mm"]],
                         (np.array(i["inertia_kgm2"], dtype=float) * k).tolist(), "brep")
    bb = (metrics or {}).get("bbox") or {}
    lo, hi = bb.get("min") or [0, 0, 0], bb.get("max") or [0, 0, 0]
    size = bb.get("size") or [h - l for l, h in zip(lo, hi)]
    com = [(float(a) + float(b)) / 2000.0 for a, b in zip(lo, hi)]
    return MassProps(mass_kg, com, _box(mass_kg, size).tolist(), "box")


def to_world(p: MassProps, transform) -> MassProps:
    t = np.array(transform, dtype=float)
    r = t[:3, :3]
    com = r @ np.array(p.com) + t[:3, 3] / 1000.0
    return MassProps(p.mass, com.tolist(), (r @ np.array(p.inertia) @ r.T).tolist(), p.source)


def combine(parts: list[MassProps]) -> MassProps:
    """The rigid sum (same frame): total mass, com, inertia about the new com."""
    m = sum(p.mass for p in parts)
    if m <= 0:
        return MassProps(0.0, [0.0, 0.0, 0.0], np.zeros((3, 3)).tolist(), "sum")
    c = sum(np.array(p.com) * p.mass for p in parts) / m
    total = np.zeros((3, 3))
    for p in parts:
        d = np.array(p.com) - c
        total += np.array(p.inertia) + p.mass * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    src = "brep" if all(p.source == "brep" for p in parts) else "box" if any(
        p.source == "box" for p in parts) else "sum"
    return MassProps(m, c.tolist(), total.tolist(), src)
