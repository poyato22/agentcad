"""CADPrompt's geometry metrics (Alrashedy et al., ICLR 2025, arXiv 2410.05340), re-implemented
from the paper's text because the authors did not publish their evaluation code.

What the paper states, and what is done here:
- each STL becomes a 1,000-point cloud (sampling method not stated; here area-weighted
  uniform surface samples with a fixed seed);
- each cloud is normalised to fit a unit cube (here: bounding box scaled so its longest side is
  1 and moved into [0, 1]^3);
- ICP rotates and translates the generated cloud onto the ground truth (settings not stated;
  here point-to-point ICP from the identity, at most 50 iterations, tolerance 1e-7);
- point-cloud distance (Eq. 8): 1/2 mean_p min_q |p-q| + 1/2 mean_q min_p |p-q| (plain L2,
  not squared);
- Hausdorff distance (Eq. 9): max of the two directed sup-inf distances;
- IoGT (Eq. 10): |P intersect Q| / |Q|; the text calls it the overlap of the bounding boxes,
  so here: volume of (bbox(P) intersect bbox(Q)) / volume of bbox(Q), after ICP;
- a failure (no code that compiles to a shape) scores point-cloud and Hausdorff distance
  sqrt(3), the largest distance in a unit cube, and IoGT 0;
- tables report the median with the IQR.

Because the settings that the paper leaves open are guesses, numbers from this module sit next
to the published table as indicative only. Paired comparisons run through this module are
like-for-like.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

N_POINTS = 1000
FAIL_DIST = math.sqrt(3.0)
ICP_ITERS = 50
ICP_TOL = 1e-7


def sample_points(tris: np.ndarray, n: int = N_POINTS, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if not np.isfinite(area).all() or area.sum() <= 0:
        return np.zeros((0, 3))
    idx = rng.choice(len(tris), n, p=area / area.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    return a[idx] + u[:, None] * (b[idx] - a[idx]) + v[:, None] * (c[idx] - a[idx])


def unit_cube(pts: np.ndarray) -> np.ndarray:
    lo, hi = pts.min(0), pts.max(0)
    s = float((hi - lo).max()) or 1.0
    return (pts - lo) / s


def _rigid(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cs, cd = src.mean(0), dst.mean(0)
    h = (src - cs).T @ (dst - cd)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, cd - r @ cs


def icp(src: np.ndarray, dst: np.ndarray, iters: int = ICP_ITERS, tol: float = ICP_TOL) -> np.ndarray:
    """Point-to-point ICP from the identity; returns src moved onto dst."""
    tree = cKDTree(dst)
    x, prev = src.copy(), math.inf
    for _ in range(iters):
        dist, j = tree.query(x)
        err = float(dist.mean())
        if prev - err < tol:
            break
        prev = err
        r, t = _rigid(x, dst[j])
        x = x @ r.T + t
    return x


def point_cloud_distance(p: np.ndarray, q: np.ndarray) -> float:
    return float(0.5 * cKDTree(q).query(p)[0].mean() + 0.5 * cKDTree(p).query(q)[0].mean())


def hausdorff(p: np.ndarray, q: np.ndarray) -> float:
    return float(max(cKDTree(q).query(p)[0].max(), cKDTree(p).query(q)[0].max()))


def iogt(p: np.ndarray, q: np.ndarray) -> float:
    """Bounding-box overlap of the generated cloud p with the ground truth q, over q's box."""
    lo, hi = np.maximum(p.min(0), q.min(0)), np.minimum(p.max(0), q.max(0))
    inter = float(np.prod(np.clip(hi - lo, 0.0, None)))
    gt = float(np.prod(q.max(0) - q.min(0)))
    return inter / gt if gt > 0 else 0.0


def failure() -> dict:
    return {"pcd": FAIL_DIST, "hausdorff": FAIL_DIST, "iogt": 0.0, "compiled": False}


def evaluate(pred_tris: np.ndarray | None, gt_tris: np.ndarray, seed: int = 0) -> dict:
    """All CADPrompt metrics for one generated mesh against its ground truth mesh."""
    if pred_tris is None or len(pred_tris) == 0:
        return failure()
    p = sample_points(np.asarray(pred_tris, float), seed=seed)
    q = sample_points(np.asarray(gt_tris, float), seed=seed + 1)
    if len(p) == 0 or len(q) == 0:
        return failure()
    q = unit_cube(q)
    p = icp(unit_cube(p), q)
    return {"pcd": point_cloud_distance(p, q), "hausdorff": hausdorff(p, q),
            "iogt": iogt(p, q), "compiled": True}
