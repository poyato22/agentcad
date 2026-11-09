"""Tests for the CADPrompt metric re-implementation and the BENCH4 task split
(run: cd core && .venv/bin/python -m pytest evals/bench/test_cadprompt.py -q)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from evals.bench import cadprompt_metric as cm


def _box(sx: float, sy: float, sz: float) -> np.ndarray:
    """A closed axis-aligned box as a triangle soup (12 triangles)."""
    v = np.array([[x, y, z] for x in (0, sx) for y in (0, sy) for z in (0, sz)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, c, d in quads:
        tris += [[v[a], v[b], v[c]], [v[a], v[c], v[d]]]
    return np.array(tris)


def test_failure_scores_the_papers_worst_case():
    r = cm.evaluate(None, _box(1, 1, 1))
    assert r == {"pcd": math.sqrt(3), "hausdorff": math.sqrt(3), "iogt": 0.0, "compiled": False}


def test_identical_shape_scores_near_zero_distance_and_full_overlap():
    r = cm.evaluate(_box(2, 3, 1), _box(2, 3, 1))
    # two independent 1,000-point samples of the same surface: mean NN distance ~ 0.02
    assert r["pcd"] < 0.04
    assert r["iogt"] > 0.95


def test_scale_and_translation_do_not_matter():
    a = cm.evaluate(_box(2, 3, 1) * 7.5 + 40.0, _box(2, 3, 1))
    b = cm.evaluate(_box(2, 3, 1), _box(2, 3, 1))
    assert abs(a["pcd"] - b["pcd"]) < 1e-6


def test_icp_recovers_a_small_rotation():
    th = math.radians(8)
    r = np.array([[math.cos(th), -math.sin(th), 0], [math.sin(th), math.cos(th), 0], [0, 0, 1]])
    q = cm.unit_cube(cm.sample_points(_box(2, 3, 1), seed=0))
    p = q @ r.T + 0.05
    moved = cm.icp(p, q)
    assert cm.point_cloud_distance(moved, q) < 1e-3
    assert cm.point_cloud_distance(p, q) > 0.03


def test_wrong_shape_is_worse_than_right_shape():
    gt = _box(4, 1, 1)
    good = cm.evaluate(_box(4, 1, 1), gt)
    bad = cm.evaluate(_box(1, 1, 1), gt)   # a cube for a long bar
    assert bad["pcd"] > good["pcd"] + 0.05
    # IoGT only measures box overlap over the ground truth's box, so a box that encloses the
    # ground truth scores 1.0 however wrong it is (the known weakness of IoGT)
    assert bad["iogt"] == pytest.approx(1.0)


def test_iogt_is_bounding_box_overlap_over_ground_truth():
    q = np.array([[0, 0, 0], [1, 1, 1.0]])
    p = np.array([[0.5, 0, 0], [1.5, 1, 1.0]])
    assert cm.iogt(p, q) == pytest.approx(0.5)
    assert cm.iogt(q, q) == pytest.approx(1.0)


def test_hausdorff_known_value():
    p = np.array([[0, 0, 0.0], [1, 0, 0]])
    q = np.array([[0, 0, 0.0], [3, 0, 0]])
    assert cm.hausdorff(p, q) == pytest.approx(2.0)
    assert cm.point_cloud_distance(p, q) == pytest.approx(0.5 * 0.5 + 0.5 * 1.0)


def _cadprompt_cached() -> bool:
    from evals.bench.common import CACHE
    return (CACHE / "cadprompt_raw" / "CAD_Code_Generation-main" / "CADPrompt").is_dir()


@pytest.mark.skipif(not _cadprompt_cached(), reason="CADPrompt not in MISES_BENCH_CACHE")
def test_bench4_split_is_disjoint_from_cadtestbench_runs_and_ground_truth_self_scores_low():
    from pathlib import Path

    from evals.bench import geometry as geo
    from evals.bench.datasets import fetch_cadprompt
    fresh, seen = fetch_cadprompt(), fetch_cadprompt(split="seen")
    assert len(fresh) == 100 and len(seen) == 100
    assert not {t.id for t in fresh} & {t.id for t in seen}
    assert all("cadquery" not in t.prompt.lower() for t in fresh)
    for t in fresh[:10]:
        gt = geo.load_stl_bytes(Path(t.meta["gt_stl"]).read_bytes())
        assert cm.evaluate(gt, gt)["pcd"] < 0.05     # the metric's own sampling noise
