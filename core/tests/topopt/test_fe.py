"""The structured-grid FE solver: element stiffness, assembly and MG-CG against a direct solve."""
import numpy as np
import pytest
import scipy.sparse.linalg as spla

from mises.topopt import fe


def _cantilever(n, h=1.0):
    nodes = fe.node_coords(n, h, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] < 1e-9] = True
    f = np.zeros(3 * len(nodes))
    tip = np.flatnonzero((nodes[:, 0] > n[0] * h - 1e-9) & (nodes[:, 2] > n[2] * h - 1e-9))
    f[3 * tip + 2] = -1.0 / len(tip)
    return fixed.ravel(), f


def test_hex8_is_symmetric_with_six_rigid_modes():
    K = fe.hex8_ke(0.3)
    assert np.allclose(K, K.T)
    w = np.linalg.eigvalsh(K)
    assert (np.abs(w) < 1e-10).sum() == 6 and (w > -1e-10).all()


def test_uniaxial_bar_matches_hooke():
    # a 4x1x1 bar of unit cubes, x=0 held in x only (plus enough to stop rigid motion),
    # pulled with 1 N: elongation = F L / (E A)
    n = (4, 2, 2)
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    at0 = nodes[:, 0] < 1e-9
    fixed[at0, 0] = True
    fixed[at0 & (nodes[:, 1] < 1e-9), 1] = True
    fixed[at0 & (nodes[:, 2] < 1e-9), 2] = True
    lev = fe.Level(n, 1.0, fe.hex8_ke(0.0), fixed.ravel())
    K = lev.assemble(np.full(16, 1000.0))
    f = np.zeros(lev.ndof)
    end = np.flatnonzero(nodes[:, 0] > 4 - 1e-9)
    # consistent nodal forces of a uniform traction (edge nodes half, corners a quarter)
    w = np.where(np.isin(nodes[end, 1], (0, 2)), 0.5, 1.0) * \
        np.where(np.isin(nodes[end, 2], (0, 2)), 0.5, 1.0)
    f[3 * end] = w / w.sum()
    u = spla.spsolve(K.tocsc(), f)
    assert u[3 * end].mean() == pytest.approx(1.0 * 4 / (1000 * 4), rel=1e-6)


@pytest.mark.parametrize("levels", [2, 3])
def test_mgcg_matches_direct_solve(levels):
    n = (8, 4, 4)
    fixed, f = _cantilever(n)
    rng = np.random.default_rng(0)
    Ee = 1e-4 + rng.random(np.prod(n)) ** 3
    mg = fe.MGCG(n, 1.0, 0.3, fixed, levels=levels)
    mg.set_density(Ee)
    u, it = mg.solve(f, tol=1e-10, maxit=400)
    ref = spla.spsolve(mg.A[0].tocsc(), f)
    assert it < 400
    assert np.allclose(u, ref, rtol=1e-6, atol=1e-8 * np.abs(ref).max())


def test_grid_must_divide_into_levels():
    with pytest.raises(ValueError):
        fe.MGCG((6, 4, 4), 1.0, 0.3, np.zeros(3 * 7 * 5 * 5, bool), levels=3)
    assert fe.max_levels((6, 4, 4)) == 2 and fe.max_levels((8, 8, 4)) == 2
    assert fe.max_levels((16, 8, 8)) == 3
