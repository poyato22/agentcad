"""B-rep / triangles -> grid masks and boundary-condition nodes (runs in the worker).

The grid (fe.py convention) is centred on the design space's bounding box and padded to a
multiple of 4 elements per axis (three multigrid levels); a mirror-symmetry option then maps
element i to n - 1 - i exactly. Solid masks use ray parity of the element centres against the
shape's tessellation (the CFD voxelizer's ray caster). Faces are triangle soups; distances to a
face use exact point-to-triangle distances on a densified copy (KD-tree for candidates).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import fe


@dataclass
class Grid:
    n: tuple[int, int, int]
    h: float
    origin: np.ndarray            # corner of element (0, 0, 0)

    @property
    def ne(self) -> int:
        return int(np.prod(self.n))

    def centres(self) -> np.ndarray:
        i, j, k = fe.element_grid(self.n)
        return self.origin + (np.stack([i, j, k], 1) + 0.5) * self.h

    def nodes(self) -> np.ndarray:
        return fe.node_coords(self.n, self.h, self.origin)


def plan_grid(lo, hi, h: float, multiple: int = 4) -> Grid:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    ext = hi - lo
    n = tuple(int(max(multiple, multiple * math.ceil(math.ceil(e / h - 1e-6) / multiple)))
              for e in ext)
    centre = (lo + hi) / 2
    return Grid(n, h, centre - np.array(n) * h / 2)


def to_flat(a3: np.ndarray) -> np.ndarray:
    """(nx, ny, nz) -> flat element order."""
    return np.ascontiguousarray(a3.transpose(2, 1, 0)).ravel()


def tris_of(shape, tol: float) -> np.ndarray:
    """A build123d shape's tessellation as (m, 3, 3) triangles."""
    verts, tris = shape.tessellate(tol)
    V = np.array([[v.X, v.Y, v.Z] for v in verts], float)
    T = np.array(tris, np.int64).reshape(-1, 3)
    return V[T] if len(T) else np.zeros((0, 3, 3))


def solid_mask(tris: np.ndarray, grid: Grid) -> np.ndarray:
    """Elements whose centre is inside the closed triangle mesh (flat)."""
    from ..sim.cfd.setup import _cast
    if len(tris) == 0:
        return np.zeros(grid.ne, bool)
    T = (tris - (grid.origin + 0.5 * grid.h)) / grid.h
    inside, _ = _cast(T, grid.n, 0)
    return to_flat(inside)


def box_mask(points: np.ndarray, box) -> np.ndarray:
    x0, y0, z0, x1, y1, z1 = box
    lo = np.minimum([x0, y0, z0], [x1, y1, z1])
    hi = np.maximum([x0, y0, z0], [x1, y1, z1])
    return ((points >= lo) & (points <= hi)).all(1)


def cylinder_mask(points: np.ndarray, cyl: dict) -> np.ndarray:
    c = np.asarray(cyl["center"], float)
    a = np.asarray(cyl["axis"], float)
    a = a / np.linalg.norm(a)
    d = points - c
    t = d @ a
    r = np.linalg.norm(d - t[:, None] * a, axis=1)
    return (np.abs(t) <= cyl["length"] / 2) & (r <= cyl["radius"])


# ---- distances to faces

def densify(tris: np.ndarray, max_edge: float) -> np.ndarray:
    """Split triangles (4-way) until every edge is at most max_edge."""
    tris = np.asarray(tris, float).reshape(-1, 3, 3)
    for _ in range(12):
        e = np.linalg.norm(tris[:, [1, 2, 0]] - tris, axis=2).max(1)
        big = e > max_edge
        if not big.any():
            break
        t = tris[big]
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        split = np.concatenate([np.stack(s, 1) for s in ((a, ab, ca), (ab, b, bc), (ca, bc, c),
                                                         (ab, bc, ca))])
        tris = np.concatenate([tris[~big], split])
    return tris


def point_triangle_distance(p: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Exact distances from points p (m, 3) to triangles tri (m, 3, 3), pairwise (Ericson)."""
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    q = np.empty_like(p)
    done = np.zeros(len(p), bool)

    def put(mask, val):
        m = mask & ~done
        q[m] = val[m]
        done[m] = True

    put((d1 <= 0) & (d2 <= 0), a)
    put((d3 >= 0) & (d4 <= d3), b)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + v[:, None] * ab)
        put((d6 >= 0) & (d5 <= d6), c)
        w = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + w[:, None] * ac)
        w2 = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + w2[:, None] * (c - b))
        den = 1.0 / (va + vb + vc)
        v2, w3 = vb * den, vc * den
        put(np.ones(len(p), bool), a + v2[:, None] * ab + w3[:, None] * ac)
    return np.linalg.norm(p - q, axis=1)


class FaceDistance:
    """Distance from points to a triangle soup (a face, or several)."""

    def __init__(self, tris: np.ndarray, max_edge: float):
        from scipy.spatial import cKDTree
        self.tris = densify(tris, max_edge)
        self.tree = cKDTree(self.tris.mean(1))
        self.k = min(12, len(self.tris))

    def __call__(self, pts: np.ndarray, cutoff: float | None = None) -> np.ndarray:
        pts = np.asarray(pts, float).reshape(-1, 3)
        out = np.full(len(pts), np.inf)
        if len(pts) == 0 or len(self.tris) == 0:
            return out
        near, idx = self.tree.query(pts, k=self.k)
        near = near.reshape(len(pts), -1)
        idx = idx.reshape(len(pts), -1)
        # only points whose nearest centroid is within reach are worth an exact test
        reach = np.ones(len(pts), bool) if cutoff is None else near[:, 0] <= cutoff + \
            np.linalg.norm(self.tris[:, 1] - self.tris[:, 0], axis=1).max()
        sel = np.flatnonzero(reach)
        if len(sel) == 0:
            return out
        for j in range(idx.shape[1]):
            d = point_triangle_distance(pts[sel], self.tris[idx[sel, j]])
            out[sel] = np.minimum(out[sel], d)
        return out
