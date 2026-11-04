"""Geometry scoring for the CAD benchmarks: validity, alignment and shape metrics.

Everything works on triangle soups (N, 3, 3) float arrays so a STEP (ours) and an STL (a
dataset's ground truth) are scored the same way.

- validity(step): OCCT BRepCheck_Analyzer, solid count, volume > 0, and a watertight mesh
  (every welded edge shared by exactly two triangles).
- normalise(): the Text2CAD-Bench convention: centre the bounding box at the origin and scale
  its longest side to 1 (unit bounding box).
- align(): CADBench-style rigid alignment of a prediction onto the ground truth: each of the
  24 axis-permutation rotations (signed, det +1) as a start, refined by a few Procrustes
  (Kabsch + nearest-neighbour) steps, re-normalised to the unit box after each; the start with
  the lowest Chamfer distance wins.
- chamfer(), f1(): on 30k area-weighted surface samples, in unit-box units (CD reported is the
  mean of the two directed mean distances, not squared; the x1e3 form is given too).
- voxel_iou(): solid occupancy on a G^3 grid over the union bounding box, by ray parity along z.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

N_SAMPLES = 30_000
F1_TAU = 0.01            # 1 % of the unit bounding box
IOU_GRID = 96


# ---------------------------------------------------------------------------- loading
def read_step(path: str | Path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    r = STEPControl_Reader()
    if r.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError(f"could not read STEP {path}")
    r.TransferRoots()
    return r.OneShape()


def shape_triangles(shape, rel_deflection: float = 0.002) -> np.ndarray:
    """Tessellate an OCCT shape; deflection relative to its bounding-box diagonal."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return np.zeros((0, 3, 3))
    lo, hi = box.CornerMin(), box.CornerMax()
    diag = lo.Distance(hi) or 1.0
    BRepMesh_IncrementalMesh(shape, diag * rel_deflection, False, 0.3, True)
    out = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            nodes = np.array([[p.X(), p.Y(), p.Z()] for p in
                              (tri.Node(i).Transformed(trsf) for i in range(1, tri.NbNodes() + 1))])
            idx = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
            if face.Orientation() == TopAbs_REVERSED:
                idx = idx[:, [0, 2, 1]]
            out.append(nodes[idx])
        exp.Next()
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def load_stl_bytes(data: bytes) -> np.ndarray:
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        vals = [list(map(float, ln.split()[1:4])) for ln in data.decode(errors="ignore").splitlines()
                if ln.strip().startswith("vertex")]
        return np.array(vals, dtype=float).reshape(-1, 3, 3)
    n = int(np.frombuffer(data[80:84], "<u4")[0])
    rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return np.frombuffer(data[84:84 + n * 50], rec)["v"].astype(float)


def write_stl(tris: np.ndarray, path: str | Path) -> None:
    rec = np.zeros(len(tris), np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["v"] = tris
    with open(path, "wb") as f:
        f.write(b"mises-bench".ljust(80, b" "))
        f.write(np.uint32(len(tris)).tobytes())
        f.write(rec.tobytes())


# ---------------------------------------------------------------------------- validity
@dataclass
class Validity:
    readable: bool
    brep_valid: bool
    n_solids: int
    volume: float
    watertight: bool
    n_triangles: int
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.readable and self.brep_valid and self.n_solids > 0 and self.volume > 0 \
            and self.watertight

    def as_dict(self) -> dict:
        return {**self.__dict__, "valid": self.valid}


def mesh_watertight(tris: np.ndarray, tol: float = 1e-6) -> bool:
    """Every edge of the welded mesh is shared by exactly two triangles."""
    if len(tris) == 0:
        return False
    span = float(np.ptp(tris.reshape(-1, 3), axis=0).max()) or 1.0
    q = np.round(tris.reshape(-1, 3) / (span * tol)).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    f = inv.reshape(-1, 3)
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(len(counts)) and bool(np.all(counts == 2))


def validity(step_path: str | Path) -> tuple[Validity, np.ndarray]:
    try:
        shape = read_step(step_path)
    except Exception as e:  # noqa: BLE001
        return Validity(False, False, 0, 0.0, False, 0, str(e)), np.zeros((0, 3, 3))
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    n_solids, vol, solids_ok = 0, 0.0, True
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    tris_all = []
    while exp.More():
        s = TopoDS.Solid(exp.Current())
        n_solids += 1
        g = GProp_GProps()
        BRepGProp.VolumeProperties_s(s, g)
        vol += g.Mass()
        t = shape_triangles(s)
        solids_ok &= mesh_watertight(t)
        tris_all.append(t)
        exp.Next()
    tris = np.concatenate(tris_all) if tris_all else shape_triangles(shape)
    ok = bool(BRepCheck_Analyzer(shape).IsValid())
    return Validity(True, ok, n_solids, float(vol), bool(solids_ok and n_solids), len(tris)), tris


# ---------------------------------------------------------------------------- metrics
def sample_surface(tris: np.ndarray, n: int = N_SAMPLES, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if area.sum() <= 0:
        return np.zeros((0, 3))
    idx = rng.choice(len(tris), n, p=area / area.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    return a[idx] + u[:, None] * (b[idx] - a[idx]) + v[:, None] * (c[idx] - a[idx])


def normalise_params(pts: np.ndarray) -> tuple[np.ndarray, float]:
    lo, hi = pts.min(0), pts.max(0)
    return (lo + hi) / 2, float((hi - lo).max()) or 1.0


def normalise(tris: np.ndarray) -> np.ndarray:
    c, s = normalise_params(tris.reshape(-1, 3))
    return (tris - c) / s


def chamfer(p: np.ndarray, q: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    dpq = cKDTree(q).query(p)[0]
    dqp = cKDTree(p).query(q)[0]
    return 0.5 * (dpq.mean() + dqp.mean()), dpq, dqp


def f1_score(dpq: np.ndarray, dqp: np.ndarray, tau: float = F1_TAU) -> float:
    prec, rec = float((dpq < tau).mean()), float((dqp < tau).mean())
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def rotations24() -> list[np.ndarray]:
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for i, (j, s) in enumerate(zip(perm, signs)):
                m[i, j] = s
            if np.linalg.det(m) > 0:
                out.append(m)
    return out


def _kabsch(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    h = (src - src.mean(0)).T @ (dst - dst.mean(0))
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1, 1, d]) @ u.T


def align(pred: np.ndarray, gt: np.ndarray, iters: int = 6, n: int = 4000) -> np.ndarray:
    """Return the 3x3 rotation that best maps the (normalised) prediction onto the gt."""
    rng = np.random.default_rng(1)
    ps = pred[rng.choice(len(pred), min(n, len(pred)), replace=False)]
    gs = gt[rng.choice(len(gt), min(n, len(gt)), replace=False)]
    tree = cKDTree(gs)
    best, best_r = math.inf, np.eye(3)
    for r0 in rotations24():
        r = r0
        for _ in range(iters):
            x = ps @ r.T
            c, s = normalise_params(x)
            x = (x - c) / s
            nn = gs[tree.query(x)[1]]
            r = _kabsch(ps, nn * s + c)
        x = ps @ r.T
        c, s = normalise_params(x)
        x = (x - c) / s
        cd = 0.5 * (tree.query(x)[0].mean() + cKDTree(x).query(gs)[0].mean())
        if cd < best:
            best, best_r = cd, r
    return best_r


def voxel_occupancy(tris: np.ndarray, lo: np.ndarray, hi: np.ndarray, g: int = IOU_GRID) -> np.ndarray:
    """Inside/outside on g^3 cell centres by z-ray parity (watertight-ish meshes)."""
    step = (hi - lo) / g
    xs = lo[0] + (np.arange(g) + 0.5) * step[0]
    ys = lo[1] + (np.arange(g) + 0.5) * step[1]
    zs = lo[2] + (np.arange(g) + 0.5) * step[2]
    hits: list[list[float]] = [[] for _ in range(g * g)]
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    tmin, tmax = tris.min(1), tris.max(1)
    i0 = np.clip(np.ceil((tmin[:, 0] - lo[0]) / step[0] - 0.5), 0, g).astype(int)
    i1 = np.clip(np.floor((tmax[:, 0] - lo[0]) / step[0] - 0.5), -1, g - 1).astype(int)
    j0 = np.clip(np.ceil((tmin[:, 1] - lo[1]) / step[1] - 0.5), 0, g).astype(int)
    j1 = np.clip(np.floor((tmax[:, 1] - lo[1]) / step[1] - 0.5), -1, g - 1).astype(int)
    for t in np.nonzero((i1 >= i0) & (j1 >= j0))[0]:
        gx, gy = np.meshgrid(xs[i0[t]:i1[t] + 1], ys[j0[t]:j1[t] + 1], indexing="ij")
        px, py = gx.ravel(), gy.ravel()
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = a[t], b[t], c[t]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-15:
            continue
        w1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
        w2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
        w3 = 1 - w1 - w2
        ins = (w1 >= 0) & (w2 >= 0) & (w3 >= 0)
        if not ins.any():
            continue
        z = w1 * az + w2 * bz + w3 * cz
        ii = np.repeat(np.arange(i0[t], i1[t] + 1), j1[t] - j0[t] + 1)
        jj = np.tile(np.arange(j0[t], j1[t] + 1), i1[t] - i0[t] + 1)
        for i, j, zz in zip(ii[ins], jj[ins], z[ins]):
            hits[i * g + j].append(zz)
    occ = np.zeros((g, g, g), bool)
    for k, zl in enumerate(hits):
        if len(zl) < 2:
            continue
        zl = np.unique(np.round(np.array(zl), 9))
        cnt = np.searchsorted(zl, zs)          # number of crossings below each cell centre
        occ[k // g, k % g] = (cnt % 2) == 1
    return occ


def voxel_iou(pred: np.ndarray, gt: np.ndarray, g: int = IOU_GRID) -> float:
    allp = np.concatenate([pred.reshape(-1, 3), gt.reshape(-1, 3)])
    lo, hi = allp.min(0) - 1e-3, allp.max(0) + 1e-3
    a, b = voxel_occupancy(pred, lo, hi, g), voxel_occupancy(gt, lo, hi, g)
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


def compare(pred_tris: np.ndarray, gt_tris: np.ndarray) -> dict:
    """Normalise both to the unit box, align pred onto gt, then CD / F1 / IoU."""
    p, g = normalise(pred_tris), normalise(gt_tris)
    ps, gs = sample_surface(p), sample_surface(g, seed=1)
    r = align(ps, gs)
    pr = p @ r.T
    c, s = normalise_params(pr.reshape(-1, 3))
    pr = (pr - c) / s
    psr = sample_surface(pr)
    cd, dpq, dqp = chamfer(psr, gs)
    return {"chamfer": float(cd), "chamfer_x1e3": float(cd * 1e3),
            "f1": f1_score(dpq, dqp), "iou": voxel_iou(pr, g),
            "rotation": r.round(4).tolist()}


def bbox_dims(tris: np.ndarray) -> list[float]:
    pts = tris.reshape(-1, 3)
    return (pts.max(0) - pts.min(0)).round(4).tolist() if len(pts) else [0, 0, 0]
