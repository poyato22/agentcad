"""Skeletal, truly 3-D generative design: a tall design space inside a large grid, crisp struts,
one printed body, loads on boxes (a mount inside a design space has no face of its own)."""
import numpy as np
import pytest
import scipy.sparse.linalg as spla
from pydantic import ValidationError

from mises.topopt import fe, filters, geometry as geo, simp
from mises.topopt.jobs import _dir
from mises.topopt.spec import Fixture, TopoptLoad, TopoptRunArgs
from mises.topopt.verify import select_in_box
from mises.sim.mesh import Mesh, Surface


def _cantilever(n=(16, 8, 8)):
    nx, ny, nz = n
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] == 0] = True
    f = np.zeros(3 * len(nodes))
    tip = np.flatnonzero((nodes[:, 0] == nx) & (nodes[:, 1] == ny // 2) & (nodes[:, 2] == nz // 2))
    f[3 * tip + 2] = -1.0
    return nodes, fixed.ravel(), f


def _l_elements(n):
    """An L-shaped design space: a third of the grid has no elements at all."""
    i, j, k = fe.element_grid(n)
    return (k < n[2] // 2) | (i < n[0] // 4)


def test_only_the_design_space_is_assembled():
    n = (16, 8, 8)
    els = _l_elements(n)
    _, fixed, f = _cantilever(n)
    lev = fe.Level(n, 1.0, fe.hex8_ke(0.3), fixed, els)
    assert len(lev.el) == els.sum() and lev.pos.shape == (els.sum(), 64)
    # a node touching no element is an identity row
    assert lev.fixed[np.repeat(~lev.active_nodes, 3)].all()


def test_block_assembly_matches_the_element_sum():
    n = (4, 4, 4)
    fixed = np.zeros(3 * 125, bool)
    ke1 = fe.hex8_ke(0.3)
    lev = fe.Level(n, 1.0, ke1, fixed)
    E = np.random.default_rng(1).uniform(0.1, 1.0, 64)
    K = lev.assemble(E).toarray()
    ref = np.zeros_like(K)
    for e in range(64):
        d = lev.edof[e]
        ref[np.ix_(d, d)] += E[e] * ke1
    assert np.allclose(K, ref)


@pytest.mark.parametrize("levels", [2, 3])
def test_coarse_operators_are_the_galerkin_product(levels):
    """The per-coarse-element sum is exactly P^T A P, fixture included."""
    n = (8, 8, 8)
    nn = 9 ** 3
    fixed = np.zeros(3 * nn, bool)
    fixed[3 * 81:3 * 162] = True       # the whole z = 1 layer: no coarse node on it
    E = np.random.default_rng(2).uniform(0.1, 1.0, 512)
    mg = fe.MGCG(n, 1.0, 0.3, fixed, levels=levels)
    mg.set_density(E)
    for lv in range(1, levels):
        ref = (mg.P[lv - 1].T @ mg.A[lv - 1].tocsr() @ mg.P[lv - 1]).toarray()
        got = mg.A[lv].toarray()
        free = mg.levels[lv].free
        assert np.allclose(got[np.ix_(free, free)], ref[np.ix_(free, free)], atol=1e-9)


def test_solve_on_a_masked_design_space_matches_direct():
    n = (16, 8, 8)
    els = _l_elements(n)
    _, fixed, f = _cantilever(n)
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    f = np.zeros_like(f)
    tip = np.flatnonzero((nodes[:, 0] == 16) & (nodes[:, 2] == 2))
    f[3 * tip + 2] = -1.0
    mg = fe.MGCG(n, 1.0, 0.3, fixed, levels=3, elements=els)
    E = np.where(els, np.random.default_rng(0).uniform(1e-3, 1.0, els.size), 0.0)
    mg.set_density(E)
    u, its = mg.solve(f * mg.fine.free, tol=1e-9, maxit=300)
    ref = spla.spsolve(mg.A[0].tocsc(), f * mg.fine.free)
    assert its < 300 and np.linalg.norm(u - ref) / np.linalg.norm(ref) < 1e-6


def test_a_fixture_on_an_odd_layer_still_grounds_the_coarse_grid():
    n = (8, 8, 8)
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[(nodes[:, 2] == 1) & (abs(nodes[:, 0] - 4) <= 1) & (abs(nodes[:, 1] - 4) <= 1)] = True
    f = np.zeros(3 * len(nodes))
    f[3 * np.flatnonzero((nodes[:, 2] == 8))] = 1.0
    mg = fe.MGCG(n, 1.0, 0.3, fixed.ravel(), levels=3)
    mg.set_density(np.ones(512))
    u, its = mg.solve(f * mg.fine.free, tol=1e-6, maxit=300)
    assert np.isfinite(u).all() and its < 300


def test_projection_gradient_matches_finite_differences():
    n = (6, 4, 4)
    H, Hs = filters.density_filter(n, 1.5)
    chain = filters.Chain(n, H, Hs, beta=4.0)
    x = np.random.default_rng(3).uniform(0.2, 0.8, 96)
    g = np.random.default_rng(4).normal(size=96)
    chain.forward(x)
    an = chain.backward(g)
    d = np.random.default_rng(5).normal(size=96)
    eps = 1e-6
    num = (g @ chain.forward(x + eps * d) - g @ chain.forward(x - eps * d)) / (2 * eps)
    assert an @ d == pytest.approx(num, rel=1e-5)


def _problem(n=(24, 8, 8)):
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] == 0] = True
    f = np.zeros(3 * len(nodes))
    tip = np.flatnonzero((nodes[:, 0] == n[0]) & (nodes[:, 2] == 4))
    f[3 * tip + 2] = -1.0 / len(tip)
    ne = int(np.prod(n))
    return simp.Problem(n=n, h=1.0, space=np.ones(ne, bool), keep_in=np.zeros(ne, bool),
                        keep_out=np.zeros(ne, bool), fixed_dofs=fixed.ravel(),
                        loads=[(f, 1.0)], E=1000.0, nu=0.3)


def test_crisp_leaves_struts_not_a_gray_cloud():
    p = _problem()
    gray = simp.optimize(p, simp.Options(volume_fraction=0.15, filter_radius=1.5, max_iter=40))
    crisp = simp.optimize(p, simp.Options(volume_fraction=0.15, filter_radius=1.5, max_iter=40,
                                          crisp=True))

    def grayness(x):
        return float((4 * x * (1 - x)).mean())
    assert grayness(crisp.x) < 0.6 * grayness(gray.x)
    # the volume target still holds, on the projected densities
    assert crisp.x.sum() / crisp.x.size == pytest.approx(0.15, abs=0.01)


def test_a_floating_mount_is_bridged_to_the_body():
    n = (20, 8, 8)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    x = np.zeros(ne)
    x[(i < 6) & (j == 4) & (k == 4)] = 1.0      # the body
    mount = (i >= 15) & (j == 4) & (k == 4)
    x[mount] = 1.0
    x[(i >= 6) & (i < 15) & (j == 4) & (k == 4)] = 0.4   # a strut that nearly formed
    xs, bridges = geo.bridge_islands(x, n, np.ones(ne, bool), mount)
    assert bridges == 1
    from scipy.ndimage import generate_binary_structure, label
    _, count = label(geo.element_grid3(xs > 0.5, n), structure=generate_binary_structure(3, 3))
    assert count == 1
    # along the strut, not a new path
    assert (xs[(j == 4) & (k == 4) & (i >= 6) & (i < 15)] == 1.0).all()


def test_a_speck_without_a_mount_is_not_bridged():
    n = (20, 8, 8)
    ne = int(np.prod(n))
    i, j, k = fe.element_grid(n)
    x = np.zeros(ne)
    x[(i < 6) & (j == 4) & (k == 4)] = 1.0
    x[(i == 18) & (j == 4) & (k == 4)] = 1.0
    xs, bridges = geo.bridge_islands(x, n, np.ones(ne, bool), np.zeros(ne, bool))
    assert bridges == 0 and (xs == x).all()


def test_fixtures_and_loads_take_a_box_and_a_vector():
    Fixture(box=[0, 0, 0, 1, 1, 1])
    ld = TopoptLoad(box=[0, 0, 0, 1, 1, 1], newtons=5, direction=[1, 1, 0])
    assert _dir(ld.direction) == pytest.approx([2 ** -0.5, 2 ** -0.5, 0])
    assert _dir("-z") == [0, 0, -1]
    with pytest.raises(ValidationError):
        Fixture()
    with pytest.raises(ValidationError):
        Fixture(face="+x", box=[0, 0, 0, 1, 1, 1])
    with pytest.raises(ValidationError):
        Fixture(box=[1, 0, 0, 0, 1, 1])
    with pytest.raises(ValidationError):
        TopoptLoad(box=[0, 0, 0, 1, 1, 1], newtons=5, direction=[0, 0, 0])
    args = TopoptRunArgs(part_id="p", fixtures=[{"box": [0, 0, 0, 1, 1, 1]}],
                         load_cases=[{"loads": [{"box": [0, 0, 0, 1, 1, 1], "newtons": 1,
                                                 "direction": "+z"}]}])
    assert args.crisp is None and args.surface is None


def test_verify_finds_the_surface_inside_a_box():
    nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (5, 5, 5), 5: (6, 5, 5), 6: (5, 6, 5)}
    s = Surface(tag=1, center=[0, 0, 0], bbox=[0] * 6, area=0.0, tris=[[1, 2, 3], [4, 5, 6]])
    m = Mesh(nodes=nodes, tets=[], surfaces=[s], size_mm=1.0, gmsh_version="x", order=2)
    assert select_in_box(m, [-1, -1, -1, 2, 2, 1]) == [[1, 2, 3]]
    assert select_in_box(m, [20, 20, 20, 21, 21, 21]) == []
