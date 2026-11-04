"""Geometry helpers for the Mises-Functional checkers (build123d / OCP on exported STEP)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import build123d as b3
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf


# ---------------------------------------------------------------------------- loading
def load_result(out: Path) -> tuple[b3.Shape | None, dict[str, b3.Shape]]:
    """The whole result and its named components (components/<name>.step)."""
    out = Path(out)
    whole = b3.import_step(str(out / "result.step")) if (out / "result.step").is_file() else None
    comps = {}
    cdir = out / "components"
    if cdir.is_dir():
        for p in sorted(cdir.glob("*.step")):
            try:
                comps[p.stem] = b3.import_step(str(p))
            except Exception:  # noqa: BLE001
                pass
    if whole is not None and not comps:
        solids = whole.solids()
        comps = {f"solid{i}": s for i, s in enumerate(solids)}
    return whole, comps


def find(comps: dict[str, b3.Shape], *keys: str) -> b3.Shape | None:
    """The component whose name contains one of keys (case-insensitive), else None."""
    for k in keys:
        for name, s in comps.items():
            if k.lower() in name.lower():
                return s
    return None


def solids_of(shape) -> list:
    return list(shape.solids()) if shape is not None else []


# ---------------------------------------------------------------------------- measures
def volume(shape) -> float:
    if shape is None:
        return 0.0
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, g)
    return abs(g.Mass())


def _vol_raw(s) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, g)
    return abs(g.Mass())


def common_volume(a, b) -> float:
    op = BRepAlgoAPI_Common(a.wrapped, b.wrapped)
    op.Build()
    return _vol_raw(op.Shape()) if op.IsDone() else float("nan")


def cut_volume(a, b) -> float:
    """Volume of a minus b."""
    op = BRepAlgoAPI_Cut(a.wrapped, b.wrapped)
    op.Build()
    return _vol_raw(op.Shape()) if op.IsDone() else float("nan")


def cut_shape(a, b):
    """a minus b as a build123d shape."""
    op = BRepAlgoAPI_Cut(a.wrapped, b.wrapped)
    op.Build()
    return b3.Compound(op.Shape())


def sym_diff_volume(a, b) -> float:
    return cut_volume(a, b) + cut_volume(b, a)


def bbox(shape) -> tuple[np.ndarray, np.ndarray]:
    bb = shape.bounding_box()
    return np.array(tuple(bb.min)), np.array(tuple(bb.max))


def dims_sorted(shape) -> list[float]:
    lo, hi = bbox(shape)
    return sorted((hi - lo).tolist())


@dataclass
class Cyl:
    radius: float
    point: np.ndarray        # a point on the axis
    axis: np.ndarray         # unit direction
    concave: bool            # True: a hole/bore surface (normal points to the axis)
    area: float
    length: float            # extent along the axis

    def same_axis(self, other: "Cyl", tol: float = 0.05) -> bool:
        if abs(abs(float(self.axis @ other.axis)) - 1) > 1e-4:
            return False
        d = other.point - self.point
        return float(np.linalg.norm(d - (d @ self.axis) * self.axis)) < tol


def cylinders(shape) -> list[Cyl]:
    out = []
    if shape is None:
        return out
    for f in shape.faces():
        if f.geom_type != b3.GeomType.CYLINDER:
            continue
        c = BRepAdaptor_Surface(f.wrapped).Cylinder()
        ax = c.Axis()
        p = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        pc = np.array(tuple(f.center()))
        n = np.array(tuple(f.normal_at(f.center())))
        radial = (pc - p) - ((pc - p) @ d) * d
        concave = bool(n @ radial < 0)
        vs = np.array([tuple(v) for v in f.vertices()]) if f.vertices() else pc[None]
        ts = (vs - p) @ d
        out.append(Cyl(c.Radius(), p, d, concave, f.area, float(ts.max() - ts.min())))
    return out


def holes(shape, r_lo: float, r_hi: float) -> list[Cyl]:
    """Concave cylinders with radius in [r_lo, r_hi], merged by axis (a hole split into
    two half-faces counts once)."""
    out: list[Cyl] = []
    for c in cylinders(shape):
        if c.concave and r_lo <= c.radius <= r_hi:
            if not any(o.same_axis(c) and abs(o.radius - c.radius) < 1e-3 for o in out):
                out.append(c)
    return out


def bosses(shape, r_lo: float, r_hi: float) -> list[Cyl]:
    out: list[Cyl] = []
    for c in cylinders(shape):
        if not c.concave and r_lo <= c.radius <= r_hi:
            if not any(o.same_axis(c) and abs(o.radius - c.radius) < 1e-3 for o in out):
                out.append(c)
    return out


def largest_cyl(shape, concave: bool | None = None) -> Cyl | None:
    cs = [c for c in cylinders(shape) if concave is None or c.concave == concave]
    return max(cs, key=lambda c: c.area) if cs else None


def min_distance(a, b) -> float:
    return float(a.distance_to(b))


def rotate(shape, point, axis, deg: float):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(*map(float, point)), gp_Dir(*map(float, axis))), math.radians(deg))
    return shape.moved(b3.Location(t))


def translate(shape, vec):
    return shape.moved(b3.Location(tuple(float(v) for v in vec)))


def inside(shape, pt) -> bool:
    return bool(shape.is_inside(b3.Vector(*map(float, pt))))


def count_teeth(gear, axis: Cyl, radius: float, n: int = 720) -> int:
    """Teeth = runs of material on a circle of `radius` around the gear axis at mid-face."""
    d = axis.axis
    ref = np.cross(d, [1, 0, 0])
    if np.linalg.norm(ref) < 1e-6:
        ref = np.cross(d, [0, 1, 0])
    u = ref / np.linalg.norm(ref)
    v = np.cross(d, u)
    lo, hi = bbox(gear)
    ts = [((np.array(c) - axis.point) @ d) for c in (lo, hi)]
    mid = axis.point + d * (sum(ts) / 2)
    occ = [inside(gear, mid + radius * (math.cos(a) * u + math.sin(a) * v))
           for a in np.linspace(0, 2 * math.pi, n, endpoint=False)]
    return sum(1 for i in range(n) if occ[i] and not occ[i - 1])


def tessellate(shape, tol: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    verts, tris = shape.tessellate(tol, 0.2)
    return np.array([tuple(v) for v in verts]), np.array(tris)


def facets(shape, tol: float = 0.05):
    """(centroids, normals, areas) of the outward-oriented tessellation."""
    v, t = tessellate(shape, tol)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cr = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cr, axis=1)
    keep = area > 1e-12
    n = cr[keep] / (2 * area[keep, None])
    return ((a + b + c) / 3)[keep], n, area[keep]


def wall_thickness_samples(shape, n: int = 300, seed: int = 0) -> np.ndarray:
    """Distance from area-uniform surface points to the opposite wall along the inward normal."""
    from OCP.gp import gp_Lin
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    v, t = tessellate(shape, 0.1)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cr = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cr, axis=1)
    keep = area > 1e-12
    a, b, c, cr, area = a[keep], b[keep], c[keep], cr[keep], area[keep]
    nor = cr / (2 * area[:, None])
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(a), n, p=area / area.sum())
    u, w = rng.random(n), rng.random(n)
    flip = u + w > 1
    u[flip], w[flip] = 1 - u[flip], 1 - w[flip]
    pts = a[idx] + u[:, None] * (b[idx] - a[idx]) + w[:, None] * (c[idx] - a[idx])
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape.wrapped, 1e-6)
    out = []
    for p, nn in zip(pts, nor[idx]):
        q, d = p - nn * 1e-4, -nn
        inter.Perform(gp_Lin(gp_Pnt(*q), gp_Dir(*d)), 1e-5, 1e6)
        ws = [inter.WParameter(k) for k in range(1, inter.NbPnt() + 1)] if inter.IsDone() else []
        ws = [x for x in ws if x > 1e-3]
        if ws:
            out.append(min(ws))
    return np.array(out)


def column_crossings(shape, axis: int = 2, g: int = 60) -> np.ndarray:
    """Per (u, v) column along `axis`: the number of material runs (0 = empty)."""
    from ..geometry import voxel_occupancy
    v, t = tessellate(shape, 0.05)
    tris = v[t]
    perm = [i for i in range(3) if i != axis] + [axis]
    tris = tris[:, :, perm]
    lo, hi = tris.reshape(-1, 3).min(0) - 1e-3, tris.reshape(-1, 3).max(0) + 1e-3
    occ = voxel_occupancy(tris, lo, hi, g)
    runs = (np.diff(occ.astype(int), axis=2) == 1).sum(2) + occ[:, :, 0]
    return runs
