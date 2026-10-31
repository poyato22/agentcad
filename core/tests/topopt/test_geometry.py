"""Density field -> mesh -> B-rep: watertight surfaces, smoothing, decimation, a valid solid,
and booleans that re-impose exact planar and cylindrical faces."""
import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from mises.topopt import geometry as g


def _field(seed, shape=(18, 14, 12)):
    rng = np.random.default_rng(seed)
    F = gaussian_filter(rng.random(shape), 2.0)
    F = (F - F.min()) / (F.max() - F.min())
    F[0], F[-1], F[:, 0], F[:, -1], F[:, :, 0], F[:, :, -1] = 0, 0, 0, 0, 0, 0
    return F


def _blob():
    n = (16, 12, 10)
    i, j, k = np.meshgrid(*(np.arange(d) + 0.5 for d in n), indexing="ij")
    x = (((i - 8) / 6) ** 2 + ((j - 6) / 4.5) ** 2 + ((k - 5) / 3.5) ** 2 < 1).astype(float)
    flat = x.transpose(2, 1, 0).ravel()
    return n, flat


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_surface_nets_is_watertight_and_manifold(seed):
    V, T = g.surface_nets(_field(seed), 1.0, (0, 0, 0), iso=0.5)
    st = g.mesh_stats(V, T)
    assert st["tris"] > 0 and st["watertight"] and st["bad_edges"] == 0
    assert st["volume_mm3"] > 0          # outward oriented
    assert g.nonmanifold_vertices(T) == 0
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    assert np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).min() > 1e-4


def test_taubin_keeps_volume():
    V, T = g.surface_nets(_field(3), 1.0, (0, 0, 0))
    a = g.mesh_stats(V, T)["volume_mm3"]
    b = g.mesh_stats(g.taubin(V, T, 10), T)["volume_mm3"]
    assert b == pytest.approx(a, rel=0.03)


def test_nodal_field_extends_across_the_boundary_and_shrinks_keep_outs():
    n, x = _blob()
    outside = np.zeros_like(x, bool)
    F = g.nodal_field(np.ones_like(x), n, outside)
    assert F.shape == (n[0] + 3, n[1] + 3, n[2] + 3)
    assert F[0].max() <= 0.5 and F[1:-1, 1:-1, 1:-1].min() > 0.4
    # a keep-out column inside: the field still has material on its rim nodes
    ko = np.zeros(n[::-1], bool)
    ko[:, 5:7, 7:9] = True
    F2 = g.nodal_field(np.where(ko.ravel(), 0.0, 1.0), n, ko.ravel())
    assert F2[8, 7, 6] > 0.5


def test_decimate_keeps_a_closed_manifold_and_its_volume():
    n, x = _blob()
    F = g.nodal_field(x, n, np.zeros_like(x, bool))
    V, T = g.surface_nets(F, 1.0, (-1, -1, -1))
    V = g.taubin(V, T, 10)
    before = g.mesh_stats(V, T)
    V2, T2 = g.decimate(V, T, len(T) // 3, max_error=0.3)
    after = g.mesh_stats(V2, T2)
    assert after["tris"] <= 1.1 * (len(T) // 3)
    assert g.nonmanifold_vertices(T2) == 0
    assert after["watertight"] and after["volume_mm3"] == pytest.approx(before["volume_mm3"],
                                                                         rel=0.03)


@pytest.mark.timeout(180)
def test_mesh_to_solid_is_valid_and_volume_exact():
    from build123d import Solid
    n, x = _blob()
    V, T = g.surface_nets(g.nodal_field(x, n, np.zeros_like(x, bool)), 1.0, (-1, -1, -1))
    V = g.taubin(V, T, 10)
    sol, valid = g.mesh_to_solid(V, T)
    s = Solid(sol)
    assert valid and s.is_valid
    assert s.volume == pytest.approx(g.mesh_stats(V, T)["volume_mm3"], rel=1e-6)


@pytest.mark.timeout(180)
def test_reimpose_cuts_exact_planes_and_bores():
    from build123d import Align, Box, GeomType, Solid
    # a solid block field extended past a 12 x 10 x 8 box design space
    n = (12, 10, 8)
    x = np.ones(int(np.prod(n)))
    V, T = g.surface_nets(g.nodal_field(x, n, np.zeros_like(x, bool)), 1.0, (-1, -1, -1))
    V = g.taubin(V, T, 10)
    organic = Solid(g.mesh_to_solid(V, T)[0])
    space = Box(12, 10, 8, align=(Align.MIN,) * 3)
    part = g.bool_op("common", organic, [space])
    bore = g.keep_out_solid({"kind": "cylinder", "cylinder": {"center": [6, 5, 4],
                                                             "axis": [0, 0, 1], "radius": 2,
                                                             "length": 20}})
    part = g.bool_op("cut", part, [bore])
    part, dropped = g.largest_solid(part)
    assert part.is_valid and dropped == 0
    planes = [f for f in part.faces() if f.geom_type == GeomType.PLANE and f.area > 50]
    assert {round(f.center().Z, 6) for f in planes if abs(f.normal_at().Z) > 0.99} >= {0.0, 8.0}
    cyl = [f for f in part.faces() if f.geom_type == GeomType.CYLINDER]
    assert cyl and abs(sum(f.area for f in cyl) - 2 * np.pi * 2 * 8) < 0.5
