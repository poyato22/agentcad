"""Structured-grid linear elasticity: hex8 element stiffness, grid levels and a CG solver
preconditioned by a geometric multigrid V-cycle (Amir, Aage & Lazarov 2014).

Grid convention (used by every topopt module): nx * ny * nz cubic elements of side h; element
e = i + nx * (j + ny * k), so a flat element array reshapes to (nz, ny, nx); node
n = i + Nx * (j + Ny * k) with N = n + 1; dof = 3 * node + axis.

The coarse operators are Galerkin (P^T A P), smoothing is symmetric damped Jacobi (3 pre- and 3
post-sweeps, so the preconditioner is symmetric and CG stays valid) and the coarsest grid is
solved directly with SuperLU. Each level halves the grid, so every dimension must be divisible
by 2 ** (levels - 1).
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
JACOBI_W = 0.6
SWEEPS = 3


def hex8_ke(nu: float) -> np.ndarray:
    """24x24 stiffness of a unit cube with E = 1 (2x2x2 Gauss). A cube of side h scales by h."""
    lam = nu / ((1 + nu) * (1 - 2 * nu))
    mu = 1 / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[np.arange(3), np.arange(3)] += 2 * mu
    D[3:, 3:] = np.eye(3) * mu
    s = 2 * CORNERS - 1
    K = np.zeros((24, 24))
    g = 1 / np.sqrt(3)
    for gp in np.array(np.meshgrid([-g, g], [-g, g], [-g, g])).reshape(3, -1).T:
        dN = np.empty((8, 3))
        for a in range(8):
            f = 1 + s[a] * gp
            dN[a] = [s[a, 0] * f[1] * f[2], f[0] * s[a, 1] * f[2], f[0] * f[1] * s[a, 2]]
        dNx = dN / 8 / 0.5          # d/dx = d/dxi / J with J = 1/2 for the unit cube
        B = np.zeros((6, 24))
        for a in range(8):
            x, y, z = dNx[a]
            B[:, 3 * a:3 * a + 3] = [[x, 0, 0], [0, y, 0], [0, 0, z],
                                     [y, x, 0], [0, z, y], [z, 0, x]]
        K += B.T @ D @ B * 0.125   # det J = 1/8
    return K


def element_grid(n: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(i, j, k) of every element in flat element order."""
    nx, ny, nz = n
    e = np.arange(nx * ny * nz)
    return e % nx, (e // nx) % ny, e // (nx * ny)


def node_coords(n: tuple[int, int, int], h: float, origin) -> np.ndarray:
    """(nn, 3) node positions in flat node order."""
    nx, ny, nz = n
    Nx, Ny, Nz = nx + 1, ny + 1, nz + 1
    m = np.arange(Nx * Ny * Nz)
    ijk = np.stack([m % Nx, (m // Nx) % Ny, m // (Nx * Ny)], 1)
    return np.asarray(origin, float) + ijk * h


class Level:
    """One grid level: element dofs and the CSR pattern, so assembly is one bincount."""

    def __init__(self, n: tuple[int, int, int], h: float, ke1: np.ndarray,
                 fixed_dofs: np.ndarray):
        nx, ny, nz = n
        self.n = n
        self.h = h
        self.N = (nx + 1, ny + 1, nz + 1)
        self.nn = int(np.prod(self.N))
        self.ndof = 3 * self.nn
        Nx, Ny = self.N[0], self.N[1]
        i, j, k = element_grid(n)
        nodes = np.stack([(i + c[0]) + Nx * ((j + c[1]) + Ny * (k + c[2])) for c in CORNERS], 1)
        self.edof = (3 * nodes[:, :, None] + np.arange(3)).reshape(-1, 24)
        self.ke = ke1 * h
        rows = np.repeat(self.edof, 24, axis=1).ravel()
        cols = np.tile(self.edof, (1, 24)).ravel()
        key = rows.astype(np.int64) * self.ndof + cols
        uk, self.inv = np.unique(key, return_inverse=True)
        r, c = uk // self.ndof, uk % self.ndof
        self.fixed = fixed_dofs
        self.free = ~fixed_dofs
        self.zero = fixed_dofs[r] | fixed_dofs[c]
        self.diag_fixed = np.flatnonzero(self.zero & (r == c))
        indptr = np.zeros(self.ndof + 1, np.int64)
        np.add.at(indptr, r + 1, 1)
        self.indptr = np.cumsum(indptr)
        self.indices = c.astype(np.int32)
        self.nnz = len(r)
        self.kev = self.ke.ravel()

    def assemble(self, Ee: np.ndarray) -> sp.csr_matrix:
        """K(E) with identity rows / columns on the fixed dofs."""
        data = np.bincount(self.inv, weights=(Ee[:, None] * self.kev[None, :]).ravel(),
                           minlength=self.nnz)
        data[self.zero] = 0.0
        data[self.diag_fixed] = 1.0
        return sp.csr_matrix((data, self.indices, self.indptr), shape=(self.ndof, self.ndof))


def prolong_1d(nc: int) -> sp.csr_matrix:
    """Linear interpolation from nc + 1 coarse nodes to 2 nc + 1 fine nodes."""
    nf = 2 * nc
    rows, cols, vals = [], [], []
    for i in range(nf + 1):
        if i % 2 == 0:
            rows.append(i), cols.append(i // 2), vals.append(1.0)
        else:
            rows += [i, i]
            cols += [i // 2, i // 2 + 1]
            vals += [0.5, 0.5]
    return sp.csr_matrix((vals, (rows, cols)), shape=(nf + 1, nc + 1))


MIN_COARSE_DOFS = 2000


def max_levels(n: tuple[int, int, int], cap: int = 3) -> int:
    """The most levels (up to cap) every dimension can be halved into, keeping >= 2 elements."""
    lv = 1
    while lv < cap and all(d % (2 ** lv) == 0 and d // (2 ** lv) >= 2 for d in n):
        lv += 1
    return lv


def choose_levels(n: tuple[int, int, int], cap: int = 3) -> int:
    """Multigrid depth: as deep as the grid divides, but not so deep that the directly solved
    coarsest grid gets under MIN_COARSE_DOFS (a coarse grid much coarser than the members it
    should correct makes CG crawl: 70+ iterations instead of 10-20)."""
    lv = max_levels(n, cap)
    while lv > 2:
        s = 2 ** (lv - 1)
        if 3 * np.prod([d // s + 1 for d in n]) >= MIN_COARSE_DOFS:
            break
        lv -= 1
    return lv


class MGCG:
    """CG on the fine grid, preconditioned by one multigrid V-cycle per iteration."""

    def __init__(self, n: tuple[int, int, int], h: float, nu: float, fixed_dofs: np.ndarray,
                 levels: int = 3):
        if any(d % (2 ** (levels - 1)) for d in n):
            raise ValueError(f"grid {n} is not divisible by 2^{levels - 1}")
        ke1 = hex8_ke(nu)
        self.levels: list[Level] = []
        self.P: list[sp.csr_matrix] = []
        self.fixed_c: list[np.ndarray] = []
        fx = fixed_dofs
        for lv in range(levels):
            s = 2 ** lv
            lev = Level(tuple(d // s for d in n), h * s, ke1, fx)
            self.levels.append(lev)
            if lv + 1 < levels:
                cn = tuple(d // (2 * s) for d in n)
                Pn = sp.kron(prolong_1d(cn[2]), sp.kron(prolong_1d(cn[1]), prolong_1d(cn[0])))
                Pd = sp.kron(Pn, sp.eye(3)).tocsr()
                Pd = sp.diags(lev.free.astype(float)) @ Pd
                # a coarse node is fixed when its coincident fine node is
                Nf = lev.N
                ii, jj, kk = np.meshgrid(*(np.arange(0, m, 2) for m in Nf), indexing="ij")
                fine = (ii + Nf[0] * (jj + Nf[1] * kk)).transpose(2, 1, 0).ravel()
                fx = fx[(3 * fine[:, None] + np.arange(3)).ravel()]
                Pd = Pd @ sp.diags((~fx).astype(float))
                self.P.append(Pd.tocsr())
                self.fixed_c.append(fx.astype(float))
        self.A: list[sp.csr_matrix] = []

    @property
    def fine(self) -> Level:
        return self.levels[0]

    def set_density(self, Ee: np.ndarray, refactor: bool = True) -> None:
        """Assemble the fine operator for element moduli Ee and the Galerkin coarse ones.
        refactor=False keeps the previous coarsest-grid LU (a slightly stale but fixed, SPD
        coarse solve: CG stays valid). The SuperLU factorization of a 2-level cycle's coarse
        grid is most of an iteration's cost, and the density moves little between iterations."""
        self.A = [self.levels[0].assemble(Ee)]
        for lv in range(1, len(self.levels)):
            P = self.P[lv - 1]
            self.A.append((P.T @ self.A[lv - 1] @ P + sp.diags(self.fixed_c[lv - 1])).tocsr())
        self.dinv = [1.0 / A.diagonal() for A in self.A]
        if refactor or getattr(self, "coarse", None) is None:
            self.coarse = spla.splu(self.A[-1].tocsc())

    def vcycle(self, lv: int, b: np.ndarray) -> np.ndarray:
        if lv == len(self.A) - 1:
            return self.coarse.solve(b)
        A, dinv, w = self.A[lv], self.dinv[lv], JACOBI_W
        x = w * dinv * b          # the first pre-sweep from x = 0
        for _ in range(SWEEPS - 1):
            x += w * dinv * (b - A @ x)
        r = b - A @ x
        x += self.P[lv] @ self.vcycle(lv + 1, self.P[lv].T @ r)
        for _ in range(SWEEPS):
            x += w * dinv * (b - A @ x)
        return x

    def solve(self, b: np.ndarray, x0: np.ndarray | None = None, tol: float = 1e-6,
              maxit: int = 200) -> tuple[np.ndarray, int]:
        """(u, CG iterations); stops at ||r|| < tol ||b||."""
        A = self.A[0]
        x = np.zeros_like(b) if x0 is None else x0.copy()
        r = b - A @ x
        nb = np.linalg.norm(b)
        if nb == 0:
            return np.zeros_like(b), 0
        if np.linalg.norm(r) < tol * nb:
            return x, 0
        z = self.vcycle(0, r)
        p = z.copy()
        rz = r @ z
        for it in range(maxit):
            Ap = A @ p
            a = rz / (p @ Ap)
            x += a * p
            r -= a * Ap
            if np.linalg.norm(r) < tol * nb:
                return x, it + 1
            z = self.vcycle(0, r)
            rz2 = r @ z
            p = z + (rz2 / rz) * p
            rz = rz2
        return x, maxit
