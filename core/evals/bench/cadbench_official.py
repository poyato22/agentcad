"""CADBench's own geometry metric, so scores can sit next to the published ones.

    uv run --no-project --with trimesh --with manifold3d --with pymeshlab --with scipy \\
        python -I cadbench_official.py RESULT.stl GT.stl OUT.json

Port of CADBenchEval/CADBench/Eval/geometry/_utils.py and perform_evaluation in Eval/_main.py
(github.com/anniedoris/CADBench, MIT, Copyright (c) 2026 Annie Doris), same steps in the same
order: CGAL alpha wrap of both meshes (pymeshlab), ground truth and prediction normalised to
[-1, 1] on the longest side, then inertia-based alignment: centres of mass to the origin,
scale by sqrt(trace(I)/mass), the four SO(3) principal-axis pairings, keep the best exact
boolean IoU. Reported: aligned IoU, aligned Chamfer (sum of the two mean distances on 10k points
per shape), aligned surface IoU (threshold 0.02 in [-1, 1] units). The one departure: their
GPU Monte Carlo IoU fallback for a failed boolean uses their ray-casting module; here it is a
500k-point ray-parity estimate with trimesh. Any fallback is recorded in `iou_method`.
Runs in its own environment, never inside the product's.
"""
import json
import sys

import numpy as np
import pymeshlab as pml
import trimesh
from scipy.spatial import cKDTree


def alpha_wrap(mesh, alpha_fraction=1e-3, offset_fraction=1e-4):
    ms = pml.MeshSet()
    ms.add_mesh(pml.Mesh(vertex_matrix=np.ascontiguousarray(mesh.vertices, dtype=np.float64),
                         face_matrix=np.ascontiguousarray(mesh.faces, dtype=np.int32)))
    last = None
    for kw in ({"alpha_fraction": alpha_fraction, "offset_fraction": offset_fraction},
               {"alpha_relative": alpha_fraction, "offset_relative": offset_fraction}, {}):
        try:
            ms.apply_filter("generate_alpha_wrap", **kw)
            cm = ms.current_mesh()
            return trimesh.Trimesh(vertices=np.asarray(cm.vertex_matrix(), dtype=np.float64),
                                   faces=np.asarray(cm.face_matrix(), dtype=np.int64),
                                   process=False)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"alpha_wrap failed: {last}")


def mass_props(mesh):
    I = mesh.moment_inertia
    P = trimesh.inertia.principal_axis(I)[1].T[:, ::-1]
    return mesh.mass, mesh.center_mass, I, P


def normalise(shape):
    b = shape.bounds
    shape = shape.apply_translation(-(b[0] + b[1]) / 2)
    return shape.apply_scale(float(2 / (b[1] - b[0]).max()))


METHOD = {"fallback": False}


def _sampled_iou(a, b, n=500_000):
    METHOD["fallback"] = True
    rng = np.random.default_rng(42)
    lo = np.minimum(a.bounds[0], b.bounds[0])
    hi = np.maximum(a.bounds[1], b.bounds[1])
    pts = rng.random((n, 3)) * (hi - lo) + lo
    ia, ib = a.contains(pts), b.contains(pts)
    u = (ia | ib).sum()
    return float((ia & ib).sum() / u) if u else 0.0


def iou(a, b):
    try:
        return a.intersection(b).mass / a.union(b).mass
    except Exception:  # noqa: BLE001
        try:
            return _sampled_iou(a, b)
        except Exception:  # noqa: BLE001
            return 0.0


def align(source, target):
    m1, c1, mat1, v1 = mass_props(target)
    m2, c2, mat2, v2 = mass_props(source)
    s1, s2 = np.sqrt(np.trace(mat1) / m1), np.sqrt(np.trace(mat2) / m2)
    a = target.copy().apply_translation(-c1).apply_scale(1 / s1)
    b = source.copy().apply_translation(-c2).apply_scale(1 / s2)
    Rs = [v1 @ v2.T]
    for i in range(3):
        d = 1 - 2 * np.array([i > 0, (i + 1) % 2, i % 3 <= 1])
        Rs.append(v1 @ (d[None, :] * v2).T)
    best, best_t = iou(a, b), np.eye(4)
    for R in Rs:
        T = np.eye(4)
        T[:3, :3] = R
        v = iou(a, b.copy().apply_transform(T))
        if best < v <= 1.0:
            best, best_t = v, T
    out = b.apply_transform(best_t).apply_scale(s1).apply_translation(c1)
    return out, best


def _points(mesh, n=10000):
    return trimesh.sample.sample_surface(mesh, n, seed=0)[0]


def chamfer(a, b):
    p, q = _points(a), _points(b)
    return float(cKDTree(p).query(q)[0].mean() + cKDTree(q).query(p)[0].mean())


def surface_iou(a, b, thr=0.02):
    p, q = _points(a), _points(b)
    return float(((cKDTree(p).query(q)[0] < thr).sum() + (cKDTree(q).query(p)[0] < thr).sum())
                 / (2 * len(p)))


def main(pred_path, gt_path, out_path):
    pred = trimesh.load(pred_path, force="mesh")
    gt = trimesh.load(gt_path, force="mesh")
    pred, gt = alpha_wrap(pred), alpha_wrap(gt)
    gt = normalise(gt)
    pred = normalise(pred)
    naive = iou(pred, gt)
    aligned, a_iou = align(pred, gt)
    res = {"official_iou": float(a_iou), "official_naive_iou": float(naive),
           "official_chamfer": chamfer(aligned, gt), "official_siou": surface_iou(aligned, gt),
           "iou_method": "sampled" if METHOD["fallback"] else "boolean"}
    json.dump(res, open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
