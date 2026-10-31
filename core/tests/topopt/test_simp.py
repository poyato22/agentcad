"""SIMP on a small grid: converges, is deterministic, honours the volume and passive regions."""
import numpy as np
import pytest

from mises.topopt import fe, simp

N = (12, 8, 4)


def _problem(keep=False, two_cases=False, n=N):
    ne = int(np.prod(n))
    nodes = fe.node_coords(n, 1.0, (0, 0, 0))
    fixed = np.zeros((len(nodes), 3), bool)
    fixed[nodes[:, 0] < 1e-9] = True
    tip = np.flatnonzero((nodes[:, 0] > n[0] - 1e-9) & (nodes[:, 2] < 1e-9))
    f = np.zeros(3 * len(nodes))
    f[3 * tip + 2] = -10.0 / len(tip)
    loads = [(f, 1.0)]
    if two_cases:
        g = np.zeros_like(f)
        g[3 * tip + 1] = 5.0 / len(tip)
        loads.append((g, 0.5))
    space = np.ones(ne, bool)
    keep_in = np.zeros(ne, bool)
    keep_out = np.zeros(ne, bool)
    if keep:
        i, j, k = fe.element_grid(n)
        keep_in = i == 0                       # a mounting flange
        keep_out = (i >= 4) & (i <= 6) & (j >= 3) & (j <= 4)   # a slot
    return simp.Problem(n=n, h=1.0, space=space, keep_in=keep_in, keep_out=keep_out,
                        fixed_dofs=fixed.ravel(), loads=loads, E=2300.0, nu=0.35)


OPTS = simp.Options(volume_fraction=0.3, filter_radius=1.5, max_iter=60)


def test_small_grid_converges_and_beats_a_uniform_design():
    p = _problem()
    r = simp.optimize(p, OPTS)
    assert r.converged and r.iters < 60
    assert r.x.sum() == pytest.approx(0.3 * p.ne, rel=0.01)
    # the uniform design at the same volume (SIMP-penalized) is far softer
    uni = simp.optimize(p, simp.Options(volume_fraction=0.3, filter_radius=1.5, max_iter=1))
    assert r.compliance < 0.5 * uni.history[0]["c"]
    assert r.compliance_bw > 0 and r.deflection_bw[0] > 0
    assert 0.2 < r.vf_bw < 0.4


def test_deterministic():
    a = simp.optimize(_problem(), OPTS)
    b = simp.optimize(_problem(), OPTS)
    assert np.array_equal(a.x, b.x) and a.compliance == b.compliance


def test_keep_regions_are_passive():
    p = _problem(keep=True)
    r = simp.optimize(p, OPTS)
    assert (r.x[p.keep_in] == 1).all() and (r.x[p.keep_out] == 0).all()


def test_weighted_load_cases():
    r = simp.optimize(_problem(two_cases=True), OPTS)
    assert len(r.case_compliance_bw) == 2 and r.converged


def test_mirror_symmetry_gives_a_symmetric_design():
    r = simp.optimize(_problem(), simp.Options(volume_fraction=0.3, filter_radius=1.5,
                                               mirror_axes=(1,), max_iter=30))
    g = r.x.reshape(N[2], N[1], N[0])
    assert np.allclose(g, g[:, ::-1, :])


def test_keep_ins_over_the_volume_target_are_refused():
    p = _problem()
    p.keep_in[:] = True
    p.keep_in[:5] = False
    with pytest.raises(simp.InfeasibleProblem):
        simp.optimize(p, OPTS)


def test_progress_callback_sees_every_iteration():
    seen = []
    r = simp.optimize(_problem(), simp.Options(volume_fraction=0.3, max_iter=5),
                      on_iter=lambda it, row, x: seen.append((it, row["c"], x.shape)))
    assert [s[0] for s in seen] == list(range(r.iters)) and seen[0][2] == (int(np.prod(N)),)
