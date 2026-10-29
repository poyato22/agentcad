"""Wind study setup: read the part's STL, turn it into the wind frame (flow along +x, up along
+z), size the lattice from the body and the memory budget, and voxelize the body."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .solver import BYTES_PER_CELL, NS_PER_CELL_STEP

CELLS_ACROSS = {1: 8, 2: 12, 3: 16, 4: 24}
MIN_CELLS_ACROSS = 6
U_LATTICE = 0.1          # lattice inflow speed (Mach ~0.17: weakly compressible)
NU_MIN_LES = 1e-4        # smallest lattice viscosity with the LES model (tau ~ 0.5003)
NU_MIN_BGK = 5e-3        # plain BGK needs tau well above 0.5 to stay stable
FLOW_THROUGHS = 3.0      # lattice steps to run = FLOW_THROUGHS x domain length / u


def read_stl(path: Path) -> np.ndarray:
    """(n, 3, 3) triangle corners in mm, binary or ASCII STL."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                           ("a", "<u2")]), count=n)
            return rec["v"].astype(np.float64)
    pts = [list(map(float, ln.split()[1:4])) for ln in data.decode(errors="replace").splitlines()
           if ln.strip().startswith("vertex")]
    if not pts or len(pts) % 3:
        raise ValueError("not a valid STL file")
    return np.array(pts, dtype=np.float64).reshape(-1, 3, 3)


def wind_frame(direction, up) -> np.ndarray:
    """Rotation R (rows = wind-frame axes in part coordinates): x along the flow, z along `up`
    made normal to the flow, y = z x x."""
    ex = np.asarray(direction, dtype=float)
    ex /= np.linalg.norm(ex)
    ez = np.asarray(up, dtype=float)
    ez = ez - ez.dot(ex) * ex
    ez /= np.linalg.norm(ez)
    ey = np.cross(ez, ex)
    return np.stack([ex, ey, ez])


def yawed(direction, up, yaw_deg: float) -> np.ndarray:
    """The flow direction turned by yaw_deg about `up` (right-hand rule)."""
    d = np.asarray(direction, dtype=float)
    k = np.asarray(up, dtype=float)
    k = k / np.linalg.norm(k)
    a = math.radians(yaw_deg)
    return d * math.cos(a) + np.cross(k, d) * math.sin(a) + k * k.dot(d) * (1 - math.cos(a))


@dataclass
class Grid:
    shape: tuple[int, int, int]
    h_mm: float                     # cell size
    origin: np.ndarray              # wind-frame position (mm) of cell (0, 0, 0) centre
    cells_across: int
    d_ref_mm: float                 # frontal size (largest of the body's y / z extent)
    body_lo: np.ndarray
    body_hi: np.ndarray

    @property
    def cells(self) -> int:
        return int(np.prod(self.shape))


def plan_grid(lo: np.ndarray, hi: np.ndarray, cells_across: int, domain: float) -> Grid:
    """Body at 2.5 frontal sizes from the inlet, (domain + 1) behind it, domain sizes of
    cross-section (lateral blockage ~ 1/domain^2)."""
    ext = hi - lo
    d_ref = max(ext[1], ext[2], 1e-6)
    h = d_ref / cells_across
    up, down = 2.5 * d_ref, (domain + 1.0) * d_ref
    lat = max(domain * d_ref - ext[1], 2 * h), max(domain * d_ref - ext[2], 2 * h)
    nx = int(math.ceil((up + ext[0] + down) / h))
    ny = int(math.ceil((ext[1] + lat[0]) / h))
    nz = int(math.ceil((ext[2] + lat[1]) / h))
    origin = np.array([lo[0] - up, lo[1] - lat[0] / 2, lo[2] - lat[1] / 2]) + h / 2
    return Grid((nx, ny, nz), h, origin, cells_across, d_ref, lo, hi)


def fit_budget(lo, hi, refinement: int, domain: float, memory_mb: int) -> Grid | None:
    """The finest grid at or below `refinement` that fits memory_mb, or None."""
    n = CELLS_ACROSS[refinement]
    while n >= MIN_CELLS_ACROSS:
        g = plan_grid(lo, hi, n, domain)
        if g.cells * BYTES_PER_CELL <= memory_mb * 1e6:
            return g
        n = int(n * 0.8)
    return None


def estimate_seconds(grid: Grid, steps: int) -> float:
    return grid.cells * steps * NS_PER_CELL_STEP * 1e-9


def steps_needed(grid: Grid) -> int:
    return int(FLOW_THROUGHS * grid.shape[0] / U_LATTICE)


def voxelize(tris: np.ndarray, grid: Grid) -> np.ndarray:
    """Solid cells: cell centres inside the closed triangle mesh (ray parity along x), plus
    the cells that thin walls pass through when no inside cell is next to them (a plate
    thinner than a cell would otherwise vanish)."""
    T = (tris - grid.origin) / grid.h_mm               # cell-centre index coordinates
    solid, shell = _cast(T, grid.shape, 0)
    for ax in (1, 2):
        perm = (ax, *[a for a in range(3) if a != ax])
        shp = tuple(grid.shape[a] for a in perm)
        _, sh = _cast(T[..., list(perm)], shp, ax)
        shell |= np.transpose(sh, np.argsort(perm))
    near = solid.copy()
    for ax in range(3):
        near |= np.roll(solid, 1, ax) | np.roll(solid, -1, ax)
    return solid | (shell & ~near)


def _cast(T: np.ndarray, shape, _axis) -> tuple[np.ndarray, np.ndarray]:
    """Rays along the first axis: every triangle adds a crossing to the columns its
    projection covers, at the first cell centre beyond it; a cumulative sum mod 2 marks the
    inside. Also returns the cells that contain a crossing (the surface shell)."""
    nx, ny, nz = shape
    T = T.copy()
    T[..., 1:] += 1e-7                                   # keep rays off shared edges
    cross = np.zeros((nx + 1, ny, nz), np.int32)
    shell = np.zeros(shape, bool)
    for a, b, c in T:
        ymin, ymax = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        zmin, zmax = min(a[2], b[2], c[2]), max(a[2], b[2], c[2])
        j0, j1 = max(0, math.ceil(ymin)), min(ny - 1, math.floor(ymax))
        k0, k1 = max(0, math.ceil(zmin)), min(nz - 1, math.floor(zmax))
        if j0 > j1 or k0 > k1:
            continue
        J, K = np.meshgrid(np.arange(j0, j1 + 1), np.arange(k0, k1 + 1), indexing="ij")
        J, K = J.ravel().astype(float), K.ravel().astype(float)
        # barycentric coordinates of (J, K) in the triangle's y-z projection
        d = (b[1] - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (b[2] - a[2])
        if abs(d) < 1e-12:
            continue
        l1 = ((J - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (K - a[2])) / d
        l2 = ((b[1] - a[1]) * (K - a[2]) - (J - a[1]) * (b[2] - a[2])) / d
        inside = (l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)
        if not inside.any():
            continue
        l1, l2 = l1[inside], l2[inside]
        x = a[0] + l1 * (b[0] - a[0]) + l2 * (c[0] - a[0])
        i = np.clip(np.ceil(x), 0, nx).astype(int)
        jj, kk = J[inside].astype(int), K[inside].astype(int)
        np.add.at(cross, (i, jj, kk), 1)
        r = np.rint(x).astype(int)
        ok = (r >= 0) & (r < nx)
        shell[r[ok], jj[ok], kk[ok]] = True
    return (np.cumsum(cross, axis=0)[:nx] % 2).astype(bool), shell


def frontal_area_mm2(tris_wind: np.ndarray, res: int = 256) -> float:
    """Projected area of the body on the y-z plane (rasterized at res x res)."""
    lo = tris_wind[..., 1:].reshape(-1, 2).min(0)
    hi = tris_wind[..., 1:].reshape(-1, 2).max(0)
    span = max(float((hi - lo).max()), 1e-9)
    h = span / res
    img = np.zeros((res + 1, res + 1), bool)
    P = (tris_wind[..., 1:] - lo) / h
    for a, b, c in P:
        j0, j1 = int(math.floor(min(a[0], b[0], c[0]))), int(math.ceil(max(a[0], b[0], c[0])))
        k0, k1 = int(math.floor(min(a[1], b[1], c[1]))), int(math.ceil(max(a[1], b[1], c[1])))
        J, K = np.meshgrid(np.arange(j0, j1 + 1) + 0.5, np.arange(k0, k1 + 1) + 0.5,
                           indexing="ij")
        d = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(d) < 1e-12:
            continue
        l1 = ((J - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (K - a[1])) / d
        l2 = ((b[0] - a[0]) * (K - a[1]) - (J - a[0]) * (b[1] - a[1])) / d
        m = (l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)
        jj = np.clip(J[m].astype(int), 0, res)
        kk = np.clip(K[m].astype(int), 0, res)
        img[jj, kk] = True
    return float(img.sum()) * h * h


def surface_area_mm2(tris: np.ndarray) -> float:
    return float(0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0],
                                               tris[:, 2] - tris[:, 0]), axis=1).sum())
