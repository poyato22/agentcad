"""The density filter chain: design variables x -> physical densities xPhys.

x --density filter--> [mirror symmetry] --> [extrusion] --> [projection] --> [AM overhang]
--> xPhys

Every stage is differentiable; `Chain.backward` maps a sensitivity with respect to xPhys back to
one with respect to x (the transpose of each stage, in reverse order). Flat element arrays use
the fe.py grid convention (reshape to (nz, ny, nx)).

The AM overhang filter is Langelaar's layer-wise printability filter (Langelaar, "An additive
manufacturing filter for topology optimization of print-ready designs", SMO 55, 2017,
doi:10.1007/s00158-016-1522-2), written from the paper; pyMOTO's implementation
(https://github.com/aatmdelissen/pyMOTO, MIT licence, Copyright (c) 2022 Arnoud Delissen) was
the reference for the 3D five-element support stencil and the smooth min/max parameters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

AXES = {"x": 0, "y": 1, "z": 2}
# Langelaar's smooth min / max parameters
OVERHANG_P = 40.0
OVERHANG_EPS = 1e-4
OVERHANG_X0 = 0.5
PROJECT_ETA = 0.5


def density_filter(n: tuple[int, int, int], rmin: float,
                   space: np.ndarray | None = None) -> tuple[sp.csr_matrix, np.ndarray]:
    """Linear cone filter of radius rmin (in elements): (H, Hs) with xf = H x / Hs. With
    `space`, only design-space elements contribute (the filter never pulls in the void
    padding around a non-box design space)."""
    nx, ny, nz = n
    R = int(math.ceil(rmin))
    e = np.arange(nx * ny * nz)
    i, j, k = e % nx, (e // nx) % ny, e // (nx * ny)
    I, J, V = [], [], []
    for di in range(-R, R + 1):
        for dj in range(-R, R + 1):
            for dk in range(-R, R + 1):
                d = math.sqrt(di * di + dj * dj + dk * dk)
                if d >= rmin:
                    continue
                ii, jj, kk = i + di, j + dj, k + dk
                ok = (ii >= 0) & (ii < nx) & (jj >= 0) & (jj < ny) & (kk >= 0) & (kk < nz)
                nb = ii + nx * (jj + ny * kk)
                if space is not None:
                    ok &= space[np.where(ok, nb, 0)]
                I.append(e[ok])
                J.append(nb[ok])
                V.append(np.full(int(ok.sum()), rmin - d))
    H = sp.csr_matrix((np.concatenate(V), (np.concatenate(I), np.concatenate(J))),
                      shape=(len(e), len(e)))
    Hs = np.asarray(H.sum(1)).ravel()
    Hs[Hs == 0] = 1.0
    return H, Hs


def mirror_index(n: tuple[int, int, int], axis: int) -> np.ndarray:
    """For every element, the flat index of its mirror image across the grid's mid-plane."""
    nx, ny, nz = n
    idx = np.arange(nx * ny * nz).reshape(nz, ny, nx)
    return np.flip(idx, axis=2 - axis).ravel()


def column_groups(n: tuple[int, int, int], axis: int, space: np.ndarray) -> np.ndarray:
    """Element -> its column id along axis (extrusion), -1 outside the design space."""
    nx, ny, nz = n
    idx = np.arange(nx * ny * nz).reshape(nz, ny, nx)
    ijk = [idx % nx, (idx // nx) % ny, idx // (nx * ny)]
    others = [ijk[a] for a in range(3) if a != axis]
    dims = [n[a] for a in range(3) if a != axis]
    col = (others[0] + dims[0] * others[1]).ravel()
    return np.where(space, col, -1)


class Overhang:
    """Langelaar's AM filter along a build direction: an element can only be as dense as the
    smooth maximum of its five supports in the layer below (the element under it and that
    element's four in-layer neighbours), i.e. a 45 degree self-supporting overhang for cubes.
    The first layer sits on the build plate."""

    def __init__(self, n: tuple[int, int, int], build_dir: str):
        axis = AXES[build_dir.lstrip("+-")]
        self.n = n
        self.axis = axis
        self.flip = build_dir.startswith("-")
        self.Q = OVERHANG_P + math.log(5) / math.log(OVERHANG_X0)

    def _layers(self, x: np.ndarray) -> np.ndarray:
        """Flat elements -> (layers, a, b) with layer 0 on the plate."""
        g = x.reshape(self.n[2], self.n[1], self.n[0]).transpose(2, 1, 0)   # (nx, ny, nz)
        g = np.moveaxis(g, self.axis, 0)
        return g[::-1] if self.flip else g

    def _flat(self, L: np.ndarray) -> np.ndarray:
        g = L[::-1] if self.flip else L
        g = np.moveaxis(g, 0, self.axis)
        return np.ascontiguousarray(g.transpose(2, 1, 0)).ravel()

    @staticmethod
    def _supports(prev: np.ndarray) -> np.ndarray:
        p = np.pad(prev, 1)
        return np.stack([p[1:-1, 1:-1], p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])

    def forward(self, x: np.ndarray) -> np.ndarray:
        L = self._layers(x)
        out = np.empty_like(L)
        out[0] = L[0]
        self._cache = []
        P, Q, eps = OVERHANG_P, self.Q, OVERHANG_EPS
        for k in range(1, len(L)):
            S = self._supports(out[k - 1])
            sp_ = (S ** P).sum(0)
            xi = sp_ ** (1 / Q)
            d = L[k] - xi
            root = np.sqrt(d * d + eps)
            out[k] = 0.5 * (L[k] + xi - root + math.sqrt(eps))
            self._cache.append((S, sp_, d, root))
        return self._flat(out)

    def backward(self, g: np.ndarray) -> np.ndarray:
        """d/dx of a function whose gradient with respect to the output is g (call forward
        first, with the same x)."""
        G = self._layers(g).copy()
        dx = np.zeros_like(G)
        P, Q = OVERHANG_P, self.Q
        for k in range(len(G) - 1, 0, -1):
            S, sp_, d, root = self._cache[k - 1]
            lam = G[k]
            dx[k] = lam * 0.5 * (1 - d / root)
            mu = lam * 0.5 * (1 + d / root)
            with np.errstate(divide="ignore", invalid="ignore"):
                dxi = np.where(sp_ > 0, (1 / Q) * sp_ ** (1 / Q - 1) * P, 0.0)
            w = mu * dxi
            contrib = w[None] * np.where(S > 0, S ** (P - 1), 0.0)   # per support slot
            acc = np.zeros((G.shape[1] + 2, G.shape[2] + 2))
            acc[1:-1, 1:-1] += contrib[0]
            acc[:-2, 1:-1] += contrib[1]
            acc[2:, 1:-1] += contrib[2]
            acc[1:-1, :-2] += contrib[3]
            acc[1:-1, 2:] += contrib[4]
            G[k - 1] = G[k - 1] + acc[1:-1, 1:-1]
        dx[0] = G[0]
        return self._flat(dx)


@dataclass
class Chain:
    n: tuple[int, int, int]
    H: sp.csr_matrix
    Hs: np.ndarray
    mirrors: list[np.ndarray] = field(default_factory=list)
    columns: np.ndarray | None = None      # extrusion column ids (-1 outside)
    overhang: Overhang | None = None
    beta: float = 0.0                      # Heaviside projection sharpness (0: off)

    def _project(self, v: np.ndarray) -> np.ndarray:
        b = self.beta
        d = math.tanh(b * PROJECT_ETA) + math.tanh(b * (1 - PROJECT_ETA))
        return (math.tanh(b * PROJECT_ETA) + np.tanh(b * (v - PROJECT_ETA))) / d

    def _project_grad(self, v: np.ndarray) -> np.ndarray:
        b = self.beta
        d = math.tanh(b * PROJECT_ETA) + math.tanh(b * (1 - PROJECT_ETA))
        return b / np.cosh(np.clip(b * (v - PROJECT_ETA), -300, 300)) ** 2 / d

    def _extrude(self, v: np.ndarray) -> np.ndarray:
        c = self.columns
        inside = c >= 0
        sums = np.bincount(c[inside], weights=v[inside], minlength=int(c.max()) + 1)
        cnt = np.bincount(c[inside], minlength=int(c.max()) + 1)
        out = v.copy()
        out[inside] = (sums / np.maximum(cnt, 1))[c[inside]]
        return out

    def _linear(self, v: np.ndarray) -> np.ndarray:
        for m in self.mirrors:
            v = 0.5 * (v + v[m])
        if self.columns is not None:
            v = self._extrude(v)
        return v

    def forward(self, x: np.ndarray) -> np.ndarray:
        v = self._linear((self.H @ x) / self.Hs)
        if self.beta > 0:
            self._v = v
            v = self._project(v)
        if self.overhang is not None:
            v = self.overhang.forward(v)
        return v

    def backward(self, g: np.ndarray) -> np.ndarray:
        if self.overhang is not None:
            g = self.overhang.backward(g)
        if self.beta > 0:
            g = g * self._project_grad(self._v)
        # mirror averaging and column averaging are symmetric projections (self-adjoint);
        # applied in reverse order
        if self.columns is not None:
            g = self._extrude(g)
        for m in reversed(self.mirrors):
            g = 0.5 * (g + g[m])
        return self.H.T @ (g / self.Hs)
