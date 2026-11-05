"""Structured-grid linear elasticity: hex8 element stiffness, grid levels and a CG solver
preconditioned by a geometric multigrid V-cycle (Amir, Aage & Lazarov 2014).

Grid convention (used by every topopt module): nx * ny * nz cubic elements of side h; element
e = i + nx * (j + ny * k), so a flat element array reshapes to (nz, ny, nx); node
n = i + Nx * (j + Ny * k) with N = n + 1; dof = 3 * node + axis.

The coarse operators are Galerkin (P^T A P, summed per coarse element), smoothing is Chebyshev-
accelerated Jacobi (3 pre- and 3 post-sweeps of one polynomial, so the preconditioner is
symmetric and CG stays valid) and the coarsest grid is solved directly with SuperLU. Each level halves every axis that divides and keeps >= 2
elements; a thin axis stays (semi-coarsening: level_dims).
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
SWEEPS = 3
CHEB_RATIO = 4.0
POWER_ITERS = 8


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
    """One grid level over the elements that exist: `el` (the design space; a void element has
    no stiffness and a node touching none is inactive, an identity row). The matrix is block
    CSR (3x3 per node pair, 27 per node), with the pattern built from the node adjacency and
    each element's 64 node-pair blocks located once, so assembly needs no sort of the 576 x ne
    element entries (they took ~30 kB per element, and capped the grid at 80k elements)."""

    def __init__(self, n: tuple[int, int, int], h: float, ke1: np.ndarray,
                 fixed_dofs: np.ndarray, elements: np.ndarray | None = None):
        nx, ny, nz = n
        self.n = n
        self.h = h
        self.N = (nx + 1, ny + 1, nz + 1)
        self.nn = int(np.prod(self.N))
        self.ndof = 3 * self.nn
        Nx, Ny = self.N[0], self.N[1]
        ne = nx * ny * nz
        self.el = np.arange(ne) if elements is None else np.flatnonzero(elements)
        e = self.el
        i, j, k = e % nx, (e // nx) % ny, e // (nx * ny)
        nodes = np.stack([(i + c[0]) + Nx * ((j + c[1]) + Ny * (k + c[2])) for c in CORNERS],
                         1).astype(np.int32)
        self.nodes = nodes
        self.edof = (3 * nodes[:, :, None] + np.arange(3, dtype=np.int32)).reshape(-1, 24)
        self.ke = ke1 * h
        m = len(e)
        C = sp.csr_matrix((np.ones(8 * m, np.int8), nodes.ravel(),
                           np.arange(0, 8 * m + 1, 8, dtype=np.int64)), shape=(m, self.nn))
        self.active_nodes = np.bincount(nodes.ravel(), minlength=self.nn) > 0
        G = (C.T @ C).tocsr()
        G = (G + sp.eye(self.nn, format="csr", dtype=G.dtype)).tocsr()
        G.sort_indices()
        self.indptr = G.indptr.astype(np.int32)
        self.indices = G.indices.astype(np.int32)
        self.nblocks = len(self.indices)
        del C
        rows = np.repeat(np.arange(self.nn, dtype=np.int64), np.diff(self.indptr))
        keys = rows * self.nn + self.indices
        # pos[e, a, b]: the block of the element's node pair (a, b); a node pair class (a, b)
        # never repeats a block, so each class adds without a bincount
        self.pos = np.empty((m, 64), np.int32)
        for a in range(8):
            for b in range(8):
                self.pos[:, 8 * a + b] = np.searchsorted(
                    keys, nodes[:, a].astype(np.int64) * self.nn + nodes[:, b])
        # a fixed dof, and every dof of an inactive node, keeps only its identity diagonal
        self.fixed = np.asarray(fixed_dofs, bool) | np.repeat(~self.active_nodes, 3)
        self.free = ~self.fixed
        fx = self.fixed.reshape(-1, 3)
        touched = fx[rows].any(1) | fx[self.indices].any(1)
        tb = np.flatnonzero(touched)
        if len(tb):
            rr, cc = fx[rows[tb]], fx[self.indices[tb]]
            zero = rr[:, :, None] | cc[:, None, :]
            bi, ri, ci = np.nonzero(zero)
            self.zero_at = (tb[bi], ri, ci)
        else:
            self.zero_at = None
        dn, da = np.nonzero(fx)
        self.diag_block = np.searchsorted(keys, dn.astype(np.int64) * self.nn + dn)
        self.diag_axis = da
        self.diag_all = np.searchsorted(keys, np.arange(self.nn, dtype=np.int64) * self.nn
                                        + np.arange(self.nn))

    def _matrix(self, data: np.ndarray) -> sp.bsr_matrix:
        if self.zero_at is not None:
            data[self.zero_at] = 0.0
        data[self.diag_block, self.diag_axis, self.diag_axis] = 1.0
        # a coarse dof whose every fine neighbour is fixed has no stiffness left
        dd = data[self.diag_all]
        idx = np.arange(3)
        zn, za = np.nonzero(dd[:, idx, idx] <= 0)
        if len(zn):
            data[self.diag_all[zn], za, za] = 1.0
        return sp.bsr_matrix((data, self.indices, self.indptr), shape=(self.ndof, self.ndof),
                             blocksize=(3, 3), copy=False)

    def assemble(self, Ee: np.ndarray) -> sp.bsr_matrix:
        """K(E) with identity rows / columns on the fixed dofs; Ee is over every grid element
        of the level (void elements ignored)."""
        Ea = np.asarray(Ee)[self.el]
        data = np.zeros((self.nblocks, 3, 3))
        for a in range(8):
            for b in range(8):
                data[self.pos[:, 8 * a + b]] += (Ea[:, None, None]
                                                 * self.ke[3 * a:3 * a + 3, 3 * b:3 * b + 3])
        return self._matrix(data)

    def assemble_elements(self, Ke: np.ndarray) -> sp.bsr_matrix:
        """The same from one 24x24 stiffness per element of `el` (m, 576)."""
        Ke = Ke.reshape(-1, 24, 24)
        data = np.zeros((self.nblocks, 3, 3))
        for a in range(8):
            for b in range(8):
                data[self.pos[:, 8 * a + b]] += Ke[:, 3 * a:3 * a + 3, 3 * b:3 * b + 3]
        return self._matrix(data)

    def diagonal(self, A: sp.bsr_matrix) -> np.ndarray:
        return A.data[self.diag_all][:, np.arange(3), np.arange(3)].ravel()


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


def _coarse_fixed(fx: np.ndarray, nf: tuple[int, int, int], halved: list[bool]) -> np.ndarray:
    """A coarse dof is fixed when its coincident fine dof is."""
    g = fx.reshape(nf[2] + 1, nf[1] + 1, nf[0] + 1, 3)
    sl = tuple(slice(None, None, 2 if hv else 1) for hv in reversed(halved))
    return g[sl].ravel().astype(bool)


def _halve(d: int) -> bool:
    return d % 2 == 0 and d // 2 >= 2


def level_dims(n: tuple[int, int, int], levels: int) -> list[tuple[int, int, int]]:
    """Each level's grid. An axis halves while it divides and keeps >= 2 elements; a thin axis
    (a plate 4 voxels thick) stops there while the others go on halving (semi-coarsening),
    so a thin part's coarsest grid stays small. Raises when a level would halve no axis."""
    dims = [tuple(int(d) for d in n)]
    for _ in range(levels - 1):
        d = dims[-1]
        if not any(_halve(x) for x in d):
            raise ValueError(f"grid {n} cannot be halved into {levels} levels")
        dims.append(tuple(x // 2 if _halve(x) else x for x in d))
    return dims


def max_levels(n: tuple[int, int, int], cap: int = 3) -> int:
    """The most levels (up to cap) the grid can be halved into (level_dims): every axis that
    can halve does, and at least one must at each level."""
    lv, d = 1, tuple(n)
    while lv < cap and any(_halve(x) for x in d):
        d = tuple(x // 2 if _halve(x) else x for x in d)
        lv += 1
    return lv


def choose_levels(n: tuple[int, int, int], cap: int = 3) -> int:
    """Multigrid depth: as deep as the grid divides, but not so deep that the directly solved
    coarsest grid gets under MIN_COARSE_DOFS (a coarse grid much coarser than the members it
    should correct makes CG crawl: 70+ iterations instead of 10-20)."""
    lv = max_levels(n, cap)
    while lv > 2:
        if 3 * np.prod([d + 1 for d in level_dims(n, lv)[-1]]) >= MIN_COARSE_DOFS:
            break
        lv -= 1
    return lv


def _child_interpolation(m: tuple[int, int, int], l: tuple[int, int, int]) -> np.ndarray:
    """24x24: the fine element at position l inside its coarse element, trilinear."""
    Pl = np.zeros((24, 24))
    for a, ca in enumerate(CORNERS):
        xi = (np.array(l) + ca) / np.array(m, float)
        for b, cb in enumerate(CORNERS):
            w = np.prod(np.where(cb == 1, xi, 1 - xi))
            Pl[3 * a:3 * a + 3, 3 * b:3 * b + 3] = w * np.eye(3)
    return Pl


def child_stiffness(ke: np.ndarray, m: tuple[int, int, int]) -> np.ndarray:
    """The Galerkin stiffness of each fine element inside one coarse element: a coarse element
    holding m[0] x m[1] x m[2] fine ones interpolates them trilinearly, so P^T ke P is one fixed
    24x24 matrix per fine position (prod m, 24, 24), and P^T A P is their density-weighted sum,
    coarse element by coarse element (no sparse triple product over the fine matrix)."""
    mx, my, mz = m
    out = np.empty((mx * my * mz, 24, 24))
    for lk in range(mz):
        for lj in range(my):
            for li in range(mx):
                Pl = _child_interpolation(m, (li, lj, lk))
                out[li + mx * (lj + my * lk)] = Pl.T @ ke @ Pl
    return out


class MGCG:
    """CG on the fine grid, preconditioned by one multigrid V-cycle per iteration. `elements`
    (bool over the grid) lists the elements that exist: only they cost memory or time, so a
    skeletal design space inside a large grid solves at the price of its own elements."""

    def __init__(self, n: tuple[int, int, int], h: float, nu: float, fixed_dofs: np.ndarray,
                 levels: int = 3, elements: np.ndarray | None = None):
        dims = level_dims(n, levels)          # raises when the grid cannot take `levels`
        ke1 = hex8_ke(nu)
        self.levels: list[Level] = []
        self.P: list[sp.csr_matrix] = []
        self.fixed_c: list[np.ndarray] = []
        self.coarsen: list[tuple] = []
        nx, ny, nz = n
        exists = np.ones(nx * ny * nz, bool) if elements is None else np.asarray(elements, bool)
        fine_e = np.flatnonzero(exists)
        fi, fj, fk = fine_e % nx, (fine_e // nx) % ny, fine_e // (nx * ny)
        fine = Level(n, h, ke1, fixed_dofs, exists)
        self.levels.append(fine)
        fx = np.asarray(fixed_dofs, bool)
        m = np.ones(3, np.int64)
        for lv in range(1, levels):
            cn = dims[lv]
            halved = [c != f for c, f in zip(cn, dims[lv - 1])]
            m = m * np.array([2 if hv else 1 for hv in halved])
            ci, cj, ck = fi // m[0], fj // m[1], fk // m[2]
            cx, cy = cn[0], cn[1]
            ce = ci + cx * (cj + cy * ck)
            present = np.zeros(int(np.prod(cn)), bool)
            present[ce] = True
            fx = _coarse_fixed(fx, dims[lv - 1], halved)
            lev = Level(cn, h * 2 ** lv, ke1, fx, present)
            self.levels.append(lev)
            lid = np.full(int(np.prod(cn)), -1, np.int64)
            lid[lev.el] = np.arange(len(lev.el))
            local = (fi % m[0]) + m[0] * ((fj % m[1]) + m[1] * (fk % m[2]))
            T = child_stiffness(fine.ke, tuple(int(x) for x in m)).reshape(-1, 576)
            # (coarse elements x child positions) holding the children's moduli at set_density
            W = sp.csr_matrix((np.arange(1, len(fine_e) + 1, dtype=float),
                               (lid[ce], local)), shape=(len(lev.el), T.shape[0]))
            W.sort_indices()
            # a fine element with a fixed dof drops that row from its interpolation (exact
            # Galerkin at the fixture, which is what the whole structure hangs from): only
            # these few elements differ from the fixed child matrices
            hit = np.flatnonzero(fine.fixed[fine.edof].any(1))
            ft = tuple(int(x) for x in m)
            D = np.empty((len(hit), 576))
            for q, f_ in enumerate(hit):
                keep = (~fine.fixed[fine.edof[f_]]).astype(float)
                Pl = _child_interpolation(ft, (int(fi[f_] % m[0]), int(fj[f_] % m[1]),
                                               int(fk[f_] % m[2])))
                keb = fine.ke * keep[:, None] * keep[None, :]
                D[q] = (Pl.T @ keb @ Pl).ravel() - T[local[f_]]
            self.coarsen.append((W, W.data.astype(np.int64) - 1, T, hit, lid[ce[hit]], D))
            prev = self.levels[lv - 1]
            p1 = [prolong_1d(c) if hv else sp.eye(c + 1, format="csr")
                  for c, hv in zip(cn, halved)]
            Pn = sp.kron(p1[2], sp.kron(p1[1], p1[0]))
            Pd = sp.kron(Pn, sp.eye(3)).tocsr()
            Pd = (sp.diags(prev.free.astype(float)) @ Pd
                  @ sp.diags(lev.free.astype(float))).tocsr()
            Pd.eliminate_zeros()
            self.P.append(Pd)
            self.fixed_c.append(lev.fixed.astype(float))
            fx = lev.fixed & np.repeat(lev.active_nodes, 3)   # only the real ones go on down
        self.A: list[sp.bsr_matrix] = []
        self._power: dict[int, np.ndarray] = {}

    @property
    def fine(self) -> Level:
        return self.levels[0]

    def set_density(self, Ee: np.ndarray, refactor: bool = True) -> None:
        """Assemble the fine operator for element moduli Ee and the coarse ones (Galerkin, per
        coarse element: child_stiffness). refactor=False keeps the previous coarsest-grid LU (a
        slightly stale but fixed, SPD coarse solve: CG stays valid)."""
        fine = self.levels[0]
        self.A = [fine.assemble(Ee)]
        Ea = np.asarray(Ee)[fine.el]
        for lv, (W, order, T, hit, owner, D) in enumerate(self.coarsen, 1):
            W = W.copy()
            W.data = Ea[order]
            Ke = W @ T
            np.add.at(Ke, owner, Ea[hit][:, None] * D)
            self.A.append(self.levels[lv].assemble_elements(Ke))
        self.dinv = [1.0 / lev.diagonal(A) for lev, A in zip(self.levels, self.A)]
        self.lam = [self._lambda_max(lv) for lv in range(len(self.A) - 1)]
        if refactor or getattr(self, "coarse", None) is None:
            self.coarse = spla.splu(self.A[-1].tocsc())

    def _lambda_max(self, lv: int) -> float:
        """The largest eigenvalue of D^-1 A, by power iteration from the last estimate's
        vector. A cut-cell boundary pushes it to 4-5, where a fixed damped Jacobi with w = 0.6
        amplifies the high modes it should remove: CG took 75-300 iterations instead of 8."""
        v = self._power.get(lv)
        if v is None:
            v = np.random.default_rng(lv).normal(size=self.A[lv].shape[0])
        lam = 1.0
        A, dinv = self.A[lv], self.dinv[lv]
        for _ in range(POWER_ITERS):
            v = dinv * (A @ v)
            lam = float(np.linalg.norm(v))
            v /= lam
        self._power[lv] = v
        return lam

    def _smooth(self, lv: int, b: np.ndarray, x: np.ndarray | None) -> np.ndarray:
        """SWEEPS steps of Chebyshev-accelerated Jacobi on [lambda_max / CHEB_RATIO, 1.1
        lambda_max]; the same polynomial before and after, so the cycle stays symmetric."""
        A, dinv = self.A[lv], self.dinv[lv]
        hi = 1.1 * self.lam[lv]
        lo = self.lam[lv] / CHEB_RATIO
        theta, delta = (hi + lo) / 2, (hi - lo) / 2
        sigma = theta / delta
        rho = 1 / sigma
        if x is None:
            r = dinv * b
            x = np.zeros_like(b)
        else:
            r = dinv * (b - A @ x)
        d = r / theta
        for k in range(SWEEPS):
            x = x + d
            if k == SWEEPS - 1:
                break
            r = r - dinv * (A @ d)
            rho1 = 1 / (2 * sigma - rho)
            d = rho1 * rho * d + 2 * rho1 / delta * r
            rho = rho1
        return x

    def vcycle(self, lv: int, b: np.ndarray) -> np.ndarray:
        if lv == len(self.A) - 1:
            return self.coarse.solve(b)
        x = self._smooth(lv, b, None)
        r = b - self.A[lv] @ x
        x += self.P[lv] @ self.vcycle(lv + 1, self.P[lv].T @ r)
        return self._smooth(lv, b, x)

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
