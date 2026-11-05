"""Unit tests for the benchmark scoring (run: cd core && uv run pytest evals/bench -q)."""
from __future__ import annotations

import numpy as np
import pytest

b3 = pytest.importorskip("build123d")

from evals.bench import geometry as g  # noqa: E402


def _tris(shape) -> np.ndarray:
    return g.shape_triangles(shape.wrapped)


def test_watertight_box_and_open_mesh():
    t = _tris(b3.Box(10, 20, 30))
    assert g.mesh_watertight(t)
    assert not g.mesh_watertight(t[:-1])


def test_identical_shapes_score_perfectly_even_rotated_and_scaled():
    part = b3.Box(10, 20, 30) - b3.Cylinder(4, 40)
    a = _tris(part)
    rot = _tris(part.rotate(b3.Axis.Z, 90).rotate(b3.Axis.X, 90).scale(3))
    m = g.compare(rot, a)
    assert m["iou"] > 0.95
    assert m["f1"] > 0.95
    assert m["chamfer"] < 0.01


def test_different_shapes_score_lower():
    a = _tris(b3.Box(10, 20, 30) - b3.Cylinder(4, 40))
    b = _tris(b3.Sphere(10))
    m = g.compare(b, a)
    assert m["iou"] < 0.8
    assert m["f1"] < 0.6


def test_voxel_iou_of_known_overlap():
    a = _tris(b3.Box(10, 10, 10))
    b = _tris(b3.Pos(5, 0, 0) * b3.Box(10, 10, 10))
    assert g.voxel_iou(a, b) == pytest.approx(1 / 3, abs=0.03)


def test_validity_of_a_step(tmp_path):
    p = tmp_path / "x.step"
    b3.export_step(b3.Box(5, 5, 5) + b3.Pos(20, 0, 0) * b3.Box(5, 5, 5), str(p))
    v, tris = g.validity(p)
    assert v.valid and v.n_solids == 2
    assert v.volume == pytest.approx(250, rel=1e-6)
    assert len(tris) > 0
