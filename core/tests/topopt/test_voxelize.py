"""Grid planning, solid masks and exact point-to-face distances."""
import numpy as np
import pytest

from mises.topopt import voxelize as vx


def test_plan_grid_is_centred_and_divisible_by_four():
    g = vx.plan_grid([0, 0, 0], [60, 40, 20], 1.0)
    assert g.n == (60, 40, 20) and np.allclose(g.origin, 0)
    g = vx.plan_grid([0, 0, 0], [10, 7, 3], 1.0)
    assert all(d % 4 == 0 for d in g.n) and g.n == (12, 8, 4)
    assert np.allclose(g.origin + np.array(g.n) * 0.5, [5, 3.5, 1.5])


@pytest.mark.timeout(120)
def test_solid_mask_of_a_block_with_a_bore():
    from build123d import Align, Box, Cylinder, Pos
    part = Box(20, 12, 8, align=(Align.MIN,) * 3) - Pos(10, 6, 4) * Cylinder(3, 20)
    g = vx.plan_grid([0, 0, 0], [20, 12, 8], 0.5)
    m = vx.solid_mask(vx.tris_of(part, 0.02), g)
    c = g.centres()
    expect = np.hypot(c[:, 0] - 10, c[:, 1] - 6) > 3
    expect &= (c >= 0).all(1) & (c <= [20, 12, 8]).all(1)
    assert (m != expect).mean() < 0.01
    assert m.sum() * 0.125 == pytest.approx(part.volume, rel=0.03)


def test_point_triangle_distance_matches_brute_force():
    rng = np.random.default_rng(0)
    tri = rng.random((200, 3, 3))
    p = rng.random((200, 3)) * 2 - 0.5
    d = vx.point_triangle_distance(p, tri)
    # brute force on a dense barycentric sample
    u, v = np.meshgrid(np.linspace(0, 1, 120), np.linspace(0, 1, 120))
    keep = (u + v) <= 1
    u, v = u[keep], v[keep]
    for i in range(0, 200, 25):
        a, b, c = tri[i]
        pts = a + u[:, None] * (b - a) + v[:, None] * (c - a)
        brute = np.linalg.norm(pts - p[i], axis=1).min()
        assert d[i] == pytest.approx(brute, abs=0.01)


def test_face_distance_to_a_plane_patch():
    patch = np.array([[[0, 0, 0], [10, 0, 0], [10, 10, 0]], [[0, 0, 0], [10, 10, 0], [0, 10, 0]]],
                     float)
    fd = vx.FaceDistance(patch, max_edge=1.0)
    pts = np.array([[5, 5, 0.3], [5, 5, -2], [12, 5, 0], [-1, -1, 0]])
    assert fd(pts) == pytest.approx([0.3, 2.0, 2.0, np.sqrt(2)], abs=1e-9)
