"""The density filter chain: values, symmetry, extrusion, the AM overhang filter, gradients."""
import numpy as np
import pytest

from mises.topopt import filters


N = (6, 5, 4)


def _grid(x):
    return x.reshape(N[2], N[1], N[0])


def test_density_filter_preserves_constants_and_respects_space():
    H, Hs = filters.density_filter(N, 1.8)
    x = np.full(np.prod(N), 0.4)
    assert np.allclose((H @ x) / Hs, 0.4)
    space = np.ones(np.prod(N), bool)
    space[:10] = False
    H2, Hs2 = filters.density_filter(N, 1.8, space)
    assert H2[:, :10].nnz == 0


def test_mirror_index_is_an_involution_across_the_mid_plane():
    for ax in range(3):
        m = filters.mirror_index(N, ax)
        assert (m[m] == np.arange(len(m))).all()
    g = _grid(np.arange(np.prod(N)))
    m = filters.mirror_index(N, 0)
    assert _grid(m)[0, 0, 0] == g[0, 0, -1]


def test_overhang_removes_unsupported_material_and_keeps_supported():
    oh = filters.Overhang(N, "z")
    x = np.zeros(np.prod(N))
    g = _grid(x)
    g[:, 2, 2] = 1.0             # a column standing on the plate: printable
    g[3, 0, 0] = 1.0             # a floating voxel at the top: not printable
    out = _grid(oh.forward(x))
    assert out[:, 2, 2].min() > 0.95
    assert out[3, 0, 0] < 0.05


def test_overhang_build_direction_flips():
    x = np.zeros(np.prod(N))
    _grid(x)[3, 2, 2] = 1.0      # on the top layer: supported only when printing from -z
    assert _grid(filters.Overhang(N, "-z").forward(x))[3, 2, 2] > 0.95
    assert _grid(filters.Overhang(N, "z").forward(x))[3, 2, 2] < 0.05


def test_chain_gradient_matches_finite_differences():
    rng = np.random.default_rng(1)
    space = np.ones(np.prod(N), bool)
    H, Hs = filters.density_filter(N, 1.5, space)
    chain = filters.Chain(N, H, Hs, mirrors=[filters.mirror_index(N, 1)],
                          columns=filters.column_groups(N, 0, space),
                          overhang=filters.Overhang(N, "z"))
    x = 0.2 + 0.6 * rng.random(np.prod(N))
    w = rng.random(np.prod(N))
    f = lambda v: float(w @ chain.forward(v) ** 2)  # noqa: E731
    chain.forward(x)
    grad = chain.backward(2 * w * chain.forward(x))
    for e in rng.choice(np.prod(N), 6, replace=False):
        d = np.zeros_like(x)
        d[e] = 1e-6
        fd = (f(x + d) - f(x - d)) / 2e-6
        assert grad[e] == pytest.approx(fd, rel=1e-3, abs=1e-7)


def test_extrusion_makes_columns_uniform():
    space = np.ones(np.prod(N), bool)
    H, Hs = filters.density_filter(N, 1.0, space)   # rmin 1: the identity
    chain = filters.Chain(N, H, Hs, columns=filters.column_groups(N, 2, space))
    out = _grid(chain.forward(np.random.default_rng(2).random(np.prod(N))))
    assert np.allclose(out, out[:1])
