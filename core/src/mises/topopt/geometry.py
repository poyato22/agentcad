"""Density field -> organic solid.

1. nodal_field: element densities to grid-node values, with the field shaped so the later
   booleans are transversal (research §2): material touching the design-space boundary is
   carried one element past it, and every keep-out shrinks by one element.
2. surface_nets: one vertex per sign-changing cell, one quad per sign-changing edge;
   watertight, manifold and far better shaped than marching tetrahedra.
3. taubin: volume-preserving smoothing (lambda | mu = 0.5 | -0.53).
4. decimate: quadric edge collapse with link-condition, normal-flip and quality checks, to keep
   the STEP small (one planar face per triangle).
5. mesh_to_solid: one shared TopoDS vertex / edge per mesh vertex / edge, one planar face per
   triangle: a valid OCCT solid with no sewing pass.
6. reimpose: common with the exact design space, cut the exact keep-outs.

numpy / scipy only, plus OCP / build123d for the B-rep (imported lazily: the mesh half runs
anywhere).
"""
from __future__ import annotations

import heapq
import json
import math
import struct
from pathlib import Path

import numpy as np
import scipy.sparse as sp

TAUBIN_LAMBDA, TAUBIN_MU = 0.5, -0.53
ISO = 0.5
ISO_GUARD = 0.02          # node values this close to the iso level are pushed off it


# ---- field

def element_grid3(x: np.ndarray, n: tuple[int, int, int]) -> np.ndarray:
    """Flat element array (fe.py order) -> (nx, ny, nz)."""
    nx, ny, nz = n
    return np.asarray(x).reshape(nz, ny, nx).transpose(2, 1, 0)


def nodal_field(x: np.ndarray, n: tuple[int, int, int], outside: np.ndarray,
                grow_from: np.ndarray | None = None) -> np.ndarray:
    """Element densities -> node values on a grid whose node 0 sits one element before the
    element grid's origin (node origin = grid origin - h): one extra element layer, then a void
    shell, so the surface always closes. `outside` (bool, flat): elements outside the design
    space or inside a keep-out. Each takes the max of its in-space 3x3x3 neighbours, so material
    crosses the design-space boundary by one element and keep-outs shrink by one element: the
    exact `common` / `cut` that follow then intersect transversally (no coplanar booleans).
    With `grow_from` (bool, flat) only those elements cross: a mount keeps its exact face, and
    a strut that merely reaches the boundary closes in a rounded surface of its own instead of
    being sliced flat by the design space's walls."""
    from scipy.ndimage import maximum_filter
    e = element_grid3(x, n).astype(float).copy()
    out = element_grid3(outside, n)
    e[out] = 0.0
    src = e if grow_from is None else e * element_grid3(grow_from, n)
    grown = maximum_filter(src, size=3, mode="constant")
    e[out] = grown[out]
    p = np.pad(e, 1, mode="edge")
    p = np.pad(p, 1)                      # the void shell
    nx, ny, nz = n
    F = sum(p[a:a + nx + 3, b:b + ny + 3, c:c + nz + 3]
            for a in (0, 1) for b in (0, 1) for c in (0, 1)) / 8.0
    # the outer node layer averages the void shell: at most 0.5, but 0.5000000000000001 in
    # floating point would open the surface there; it is void by construction
    F[0], F[-1], F[:, 0], F[:, -1], F[:, :, 0], F[:, :, -1] = (0.0,) * 6
    return F


# ---- iso-surface

_CUBE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
_CUBE_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8)
               if sum(abs(p - q) for p, q in zip(_CUBE[a], _CUBE[b])) == 1]


def _components(mask: int, inside: bool) -> int:
    nodes = [c for c in range(8) if bool(mask >> c & 1) == inside]
    seen, comps = set(), 0
    for s in nodes:
        if s in seen:
            continue
        comps += 1
        stack = [s]
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            stack += [b if a == v else a for a, b in _CUBE_EDGES
                      if v in (a, b) and (b if a == v else a) in nodes]
    return comps


# cell corner configurations whose inside or outside corners split into several pieces: a
# surface-nets cell vertex there would join two sheets (a non-manifold edge or vertex)
_AMBIGUOUS = np.array([_components(m, True) > 1 or _components(m, False) > 1
                       for m in range(256)])


def disambiguate(F: np.ndarray, iso: float = ISO, max_rounds: int = 50) -> tuple[np.ndarray, int]:
    """Fill every ambiguous cell (all eight corners pushed inside) until none is left, so
    surface nets gives a 2-manifold. Only ever adds material (at most a cell at a saddle), so
    it terminates. Returns (field, cells filled)."""
    F = F.copy()
    filled = 0
    Nx, Ny, Nz = F.shape
    outer = [{0: np.take(F, 0, ax).copy(), -1: np.take(F, -1, ax).copy()} for ax in range(3)]
    for _ in range(max_rounds):
        ins = F > iso
        cfg = np.zeros((Nx - 1, Ny - 1, Nz - 1), np.int64)
        for bit, (a, b, c) in enumerate(_CUBE):
            cfg |= ins[a:a + Nx - 1, b:b + Ny - 1, c:c + Nz - 1].astype(np.int64) << bit
        amb = _AMBIGUOUS[cfg]
        if not amb.any():
            break
        filled += int(amb.sum())
        for a, b, c in _CUBE:
            sl = (slice(a, a + Nx - 1), slice(b, b + Ny - 1), slice(c, c + Nz - 1))
            F[sl] = np.where(amb, np.maximum(F[sl], iso + 2 * ISO_GUARD), F[sl])
        # the outer node layer stays outside (the surface must stay closed): a boundary cell
        # is then filled on its inner face only, which is enough to make it unambiguous
        for ax in range(3):
            for end in (0, -1):
                idx = [slice(None)] * 3
                idx[ax] = end
                F[tuple(idx)] = outer[ax][end]
    return F, filled


def smooth_field(F: np.ndarray, sigma: float) -> np.ndarray:
    """A crisp (0/1) field smoothed by sigma voxels, so the iso-surface sits between nodes
    instead of on the voxel stairs. The void shell around the grid stays void."""
    from scipy.ndimage import gaussian_filter
    out = gaussian_filter(np.asarray(F, float), sigma, mode="constant")
    out[0], out[-1] = 0.0, 0.0
    out[:, 0], out[:, -1] = 0.0, 0.0
    out[:, :, 0], out[:, :, -1] = 0.0, 0.0
    return out


def drop_thin(x: np.ndarray, n: tuple[int, int, int], keep_in: np.ndarray,
              iso: float = 0.5) -> np.ndarray:
    """Remove what a 3-voxel cross cannot sweep: membranes and hairs one voxel thick, which the
    smoothed surface turns into ragged fins. Nothing under 3 voxels is trustworthy in the voxel
    model anyway (thin_member_pct); CalculiX verifies what stays."""
    from scipy.ndimage import binary_opening, generate_binary_structure
    grid = element_grid3(x, n)
    opened = binary_opening(grid > iso, structure=generate_binary_structure(3, 1))
    out = grid.copy()
    out[(grid > iso) & ~opened & ~element_grid3(keep_in, n)] = 0.0
    return np.ascontiguousarray(out.transpose(2, 1, 0)).ravel()


def bridge_islands(x: np.ndarray, n: tuple[int, int, int], allowed: np.ndarray,
                   keep_in: np.ndarray, iso: float = 0.5, radius: float = 1.6,
                   sigma: float = 0.0, min_share: float = 0.01) -> tuple[np.ndarray, int]:
    """Elements > iso form bodies (after a Gaussian of sigma voxels, when the surface will be
    smoothed by it: a one-voxel link connects the voxels but not the final surface, and half a
    frame fell off that way). Every body holding a keep region (a mount, a load face) or
    min_share of the largest is joined to the largest along the path of highest density
    through `allowed` elements, thickened to `radius` voxels. Returns (densities, bridges).
    Smaller bodies are left for the speck filter: they carry nothing."""
    from scipy.ndimage import (binary_dilation, gaussian_filter, generate_binary_structure,
                               label)
    grid = element_grid3(x, n)
    ok = element_grid3(allowed, n)
    keep = element_grid3(keep_in, n)
    seen = grid * ok
    if sigma > 0:
        seen = gaussian_filter(seen.astype(float), sigma, mode="constant")
    solid = (seen > iso) | keep
    st = generate_binary_structure(3, 1 if sigma > 0 else 3)
    lab, count = label(solid, structure=st)
    if count <= 1:
        return x, 0
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    main = int(sizes.argmax())
    with_keep = set(np.unique(lab[keep]).tolist()) | set(
        np.flatnonzero(sizes >= min_share * sizes[main]).tolist())
    with_keep -= {0, main}
    if not with_keep:
        return x, 0
    out = grid.copy()
    bridges = 0
    connected = lab == main
    # Dijkstra over the allowed elements from the main body; a step into density d costs
    # (1 - d) + a little for length, so paths follow the struts that nearly formed
    cost = 1.02 - np.clip(grid, 0, 1)
    for body in sorted(with_keep, key=lambda b: -sizes[b]):
        target = lab == body
        path = _densest_path(connected, target, cost, ok)
        if path is None:
            continue
        mask = np.zeros(grid.shape, bool)
        mask[tuple(np.array(path).T)] = True
        r = int(np.ceil(radius))
        ax = np.arange(-r, r + 1)
        ball = (ax[:, None, None] ** 2 + ax[None, :, None] ** 2 + ax[None, None, :] ** 2
                <= radius * radius)
        mask = binary_dilation(mask, structure=ball) & ok
        out[mask] = 1.0
        connected |= target | mask
        bridges += 1
    return np.ascontiguousarray(out.transpose(2, 1, 0)).ravel(), bridges


def _densest_path(start: np.ndarray, target: np.ndarray, cost: np.ndarray,
                  ok: np.ndarray) -> list | None:
    nx, ny, nz = start.shape
    best = np.full(start.shape, np.inf)
    prev: dict[tuple, tuple] = {}
    # only the boundary of the start region seeds the search
    from scipy.ndimage import binary_erosion
    seeds = np.argwhere(start & ~binary_erosion(start))
    heap = [(0.0, *map(int, s)) for s in seeds]
    for s in seeds:
        best[tuple(s)] = 0.0
    heapq.heapify(heap)
    steps = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)
             if (a, b, c) != (0, 0, 0)]
    while heap:
        d, i, j, k = heapq.heappop(heap)
        if d > best[i, j, k]:
            continue
        if target[i, j, k]:
            path = [(i, j, k)]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path
        for a, b, c in steps:
            u, w, y = i + a, j + b, k + c
            if not (0 <= u < nx and 0 <= w < ny and 0 <= y < nz) or not ok[u, w, y]:
                continue
            nd = d + cost[u, w, y] * math.sqrt(a * a + b * b + c * c)
            if nd < best[u, w, y]:
                best[u, w, y] = nd
                prev[(u, w, y)] = (i, j, k)
                heapq.heappush(heap, (nd, u, w, y))
    return None


def mesh_area(V: np.ndarray, T: np.ndarray) -> float:
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)


def surface_nets(F: np.ndarray, h: float, origin, iso: float = ISO) -> tuple[np.ndarray, np.ndarray]:
    """Watertight, 2-manifold, outward-oriented triangle mesh of {F > iso} on a regular grid
    with node (i, j, k) at origin + h (i, j, k). F must be below iso on its two outer node
    layers (nodal_field guarantees it)."""
    F, _ = disambiguate(F, iso)
    Nx, Ny, Nz = F.shape
    ins = F > iso
    F = np.where(np.abs(F - iso) < ISO_GUARD, np.where(ins, iso + ISO_GUARD, iso - ISO_GUARD), F)
    csum = np.zeros((Nx - 1, Ny - 1, Nz - 1, 3))
    ccnt = np.zeros((Nx - 1, Ny - 1, Nz - 1))
    I, J, K = np.meshgrid(np.arange(Nx), np.arange(Ny), np.arange(Nz), indexing="ij")
    G = np.stack([I, J, K], -1).astype(float)
    quads, outward = [], []
    dims = (Nx, Ny, Nz)

    def cid(i, j, k):
        return (i * (Ny - 1) + j) * (Nz - 1) + k

    for ax in range(3):
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[ax] = slice(0, -1)
        sl1[ax] = slice(1, None)
        f0, f1 = F[tuple(sl0)], F[tuple(sl1)]
        m = ins[tuple(sl0)] != ins[tuple(sl1)]
        t = np.where(m, np.clip((iso - f0) / np.where(m, f1 - f0, 1), 0.05, 0.95), 0)
        P = G[tuple(sl0)].copy()
        P[..., ax] += t
        o1, o2 = [a for a in range(3) if a != ax]
        for d1 in (0, 1):
            for d2 in (0, 1):
                cs = [slice(None)] * 3
                cs[o1] = slice(d1, d1 + dims[o1] - 1)
                cs[o2] = slice(d2, d2 + dims[o2] - 1)
                mm = m[tuple(cs)]
                csum += P[tuple(cs)] * mm[..., None]
                ccnt += mm
        e = np.argwhere(m)
        ok = ((e[:, o1] >= 1) & (e[:, o1] <= dims[o1] - 2)
              & (e[:, o2] >= 1) & (e[:, o2] <= dims[o2] - 2))
        e = e[ok]
        c = []
        for d1, d2 in ((1, 1), (0, 1), (0, 0), (1, 0)):
            q = e.copy()
            q[:, o1] -= d1
            q[:, o2] -= d2
            c.append(cid(q[:, 0], q[:, 1], q[:, 2]))
        quads.append(np.stack(c, 1))
        sgn = np.where(ins[e[:, 0], e[:, 1], e[:, 2]], 1.0, -1.0)   # inside at the low end
        dirv = np.zeros((len(e), 3))
        dirv[:, ax] = sgn
        outward.append(dirv)
    Q = np.concatenate(quads)
    D = np.concatenate(outward)
    if len(Q) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    active = np.flatnonzero(ccnt.ravel() > 0)
    remap = -np.ones(ccnt.size, np.int64)
    remap[active] = np.arange(len(active))
    V = (csum.reshape(-1, 3)[active] / ccnt.ravel()[active, None]) * h + np.asarray(origin, float)
    Q = remap[Q]
    a, b, c_, d = Q.T
    use_ac = np.linalg.norm(V[a] - V[c_], axis=1) <= np.linalg.norm(V[b] - V[d], axis=1)
    T1 = np.where(use_ac[:, None], np.stack([a, b, c_], 1), np.stack([a, b, d], 1))
    T2 = np.where(use_ac[:, None], np.stack([a, c_, d], 1), np.stack([b, c_, d], 1))
    T = np.concatenate([T1, T2])
    DD = np.concatenate([D, D])
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    flip = np.einsum("ij,ij->i", np.cross(v1 - v0, v2 - v0), DD) < 0
    T[flip] = T[flip][:, ::-1]
    return V, T


def _adjacency(V: np.ndarray, T: np.ndarray) -> sp.csr_matrix:
    n = len(V)
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    A = sp.coo_matrix((np.ones(2 * len(e)), (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])),
                      shape=(n, n)).tocsr()
    A.data[:] = 1.0
    return A


def taubin(V: np.ndarray, T: np.ndarray, iters: int = 10,
           mask: np.ndarray | None = None) -> np.ndarray:
    """Taubin smoothing; with `mask`, only those vertices move (a local fairing pass)."""
    A = _adjacency(V, T)
    deg = np.asarray(A.sum(1)).ravel()
    W = sp.diags(1 / np.maximum(deg, 1)) @ A
    V = V.copy()
    m = None if mask is None else np.asarray(mask, bool)[:, None]
    for _ in range(iters):
        for k in (TAUBIN_LAMBDA, TAUBIN_MU):
            step = k * (W @ V - V)
            V += step if m is None else np.where(m, step, 0.0)
    return V


def mesh_stats(V: np.ndarray, T: np.ndarray) -> dict:
    if len(T) == 0:
        return {"verts": 0, "tris": 0, "watertight": False, "bad_edges": 0, "volume_mm3": 0.0,
                "area_mm2": 0.0}
    e = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    _, cnt = np.unique(e[:, 0].astype(np.int64) * len(V) + e[:, 1], return_counts=True)
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    vol = float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6)
    area = float(np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).sum() / 2)
    return {"verts": int(len(V)), "tris": int(len(T)), "watertight": bool((cnt == 2).all()),
            "bad_edges": int((cnt != 2).sum()), "volume_mm3": vol, "area_mm2": area}


def components(V: np.ndarray, T: np.ndarray) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """The closed sheets of a mesh: [(V, T, signed volume)], largest |volume| first. A positive
    volume is a body (outward normals), a negative one a cavity inside a body."""
    from scipy.sparse.csgraph import connected_components
    if len(T) == 0:
        return []
    _, lab = connected_components(_adjacency(V, T), directed=False)
    out = []
    for c in np.unique(lab[T[:, 0]]):
        Tc = T[lab[T[:, 0]] == c]
        used = np.unique(Tc)
        remap = -np.ones(len(V), np.int64)
        remap[used] = np.arange(len(used))
        Vc, Tc = V[used], remap[Tc]
        out.append((Vc, Tc, mesh_stats(Vc, Tc)["volume_mm3"]))
    return sorted(out, key=lambda x: -abs(x[2]))


def nonmanifold_vertices(T: np.ndarray) -> int:
    """Vertices whose triangle fan is not one disk (their link is not a single cycle)."""
    link: dict[int, list[tuple[int, int]]] = {}
    for a, b, c in np.asarray(T).tolist():
        link.setdefault(a, []).append((b, c))
        link.setdefault(b, []).append((c, a))
        link.setdefault(c, []).append((a, b))
    bad = 0
    for edges in link.values():
        nxt = dict(edges)
        if len(nxt) != len(edges):
            bad += 1
            continue
        start = edges[0][0]
        v, steps = start, 0
        while True:
            v = nxt.get(v)
            steps += 1
            if v is None or v == start or steps > len(edges):
                break
        if v != start or steps != len(edges):
            bad += 1
    return bad


def weld(P: np.ndarray, decimals: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Triangle soup (m, 3, 3) -> indexed mesh, vertices merged at 10^-decimals mm, degenerate
    triangles dropped (OCCT's STL export can carry near-duplicates along boolean seams)."""
    pts = np.asarray(P, float).reshape(-1, 3)
    V, inv = np.unique(np.round(pts, decimals), axis=0, return_inverse=True)
    T = inv.reshape(-1, 3)
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    return V, T


# ---- decimation (Garland & Heckbert quadric error metric)

def _plane_quadrics(V: np.ndarray, T: np.ndarray) -> np.ndarray:
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.maximum(ln, 1e-30)
    q = np.concatenate([n, -np.einsum("ij,ij->i", n, v0)[:, None]], 1)
    K = q[:, :, None] * q[:, None, :]
    Qv = np.zeros((len(V), 4, 4))
    for c in range(3):
        np.add.at(Qv, T[:, c], K)
    return Qv


def _tri_quality(a, b, c) -> tuple[np.ndarray, float]:
    n = np.cross(b - a, c - a)
    area2 = float(np.linalg.norm(n))
    s = float(((b - a) ** 2).sum() + ((c - b) ** 2).sum() + ((a - c) ** 2).sum())
    return n, (2 * math.sqrt(3) * area2 / s if s > 0 else 0.0)


def decimate(V: np.ndarray, T: np.ndarray, target_tris: int, *, max_error: float,
             min_quality: float = 0.08) -> tuple[np.ndarray, np.ndarray]:
    """Collapse edges (cheapest quadric error first) until <= target_tris triangles or no
    collapse within max_error (mm) is left. Keeps a closed manifold closed and manifold (link
    condition), never flips a triangle and never makes one worse than min_quality
    (1 = equilateral)."""
    V = np.asarray(V, float).copy()
    T = np.asarray(T, np.int64).copy()
    if len(T) <= target_tris:
        return V, T
    Q = _plane_quadrics(V, T)
    vf: list[set[int]] = [set() for _ in range(len(V))]
    for f, tri in enumerate(T.tolist()):
        for v in tri:
            vf[v].add(f)
    alive = np.ones(len(T), bool)
    n_alive = len(T)
    stamp = [0] * len(V)
    dead = [False] * len(V)
    limit = max_error * max_error
    heap: list = []

    def neighbours(u: int) -> set[int]:
        out: set[int] = set()
        for f in vf[u]:
            out.update(T[f].tolist())
        out.discard(u)
        return out

    def best(u: int, v: int):
        Qs = Q[u] + Q[v]
        cands = [V[u], V[v], 0.5 * (V[u] + V[v])]
        A = Qs[:3, :3]
        try:
            if np.linalg.cond(A) < 1e5:
                p = np.linalg.solve(A, -Qs[:3, 3])
                if np.linalg.norm(p - cands[2]) <= np.linalg.norm(V[u] - V[v]):
                    cands.append(p)
        except np.linalg.LinAlgError:
            pass
        bestc, bestp = None, None
        for p in cands:
            ph = np.append(p, 1.0)
            c = float(ph @ Qs @ ph)
            if bestc is None or c < bestc:
                bestc, bestp = c, p
        return max(bestc, 0.0), bestp

    def push(u: int, v: int) -> None:
        if u > v:
            u, v = v, u
        c, p = best(u, v)
        if c <= limit:
            heapq.heappush(heap, (c, u, v, stamp[u], stamp[v], tuple(p)))

    edges = np.unique(np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1),
                      axis=0)
    for u, v in edges.tolist():
        push(u, v)
    while heap and n_alive > target_tris:
        c, u, v, su, sv, p = heapq.heappop(heap)
        if dead[u] or dead[v] or stamp[u] != su or stamp[v] != sv:
            continue
        shared = vf[u] & vf[v]
        if len(shared) != 2:
            continue
        opp = set()
        for f in shared:
            opp.update(T[f].tolist())
        opp -= {u, v}
        if neighbours(u) & neighbours(v) != opp or len(neighbours(u) | neighbours(v)) <= 3:
            continue
        # the edge half of the link condition: no edge in both links (a valence-3 opposite
        # vertex would leave two coincident, opposite triangles)
        lk_u = {frozenset(T[f].tolist()) - {u} for f in vf[u]}
        lk_v = {frozenset(T[f].tolist()) - {v} for f in vf[v]}
        if lk_u & lk_v:
            continue
        p = np.array(p)
        ok = True
        for f in (vf[u] | vf[v]) - shared:
            tri = T[f]
            a, b, cc = V[tri[0]], V[tri[1]], V[tri[2]]
            n_old, _ = _tri_quality(a, b, cc)
            new = [p if w in (u, v) else V[w] for w in tri.tolist()]
            n_new, qual = _tri_quality(*new)
            if qual < min_quality or float(n_old @ n_new) <= 0.2 * np.linalg.norm(n_old) * \
                    np.linalg.norm(n_new):
                ok = False
                break
        if not ok:
            continue
        V[u] = p
        Q[u] = Q[u] + Q[v]
        for f in shared:
            alive[f] = False
            n_alive -= 1
            for w in T[f].tolist():
                vf[w].discard(f)
        for f in vf[v]:
            T[f][T[f] == v] = u
            vf[u].add(f)
        vf[v] = set()
        dead[v] = True
        stamp[u] += 1
        for w in neighbours(u):
            push(u, w)
    T = T[alive]
    used = np.unique(T)
    remap = -np.ones(len(V), np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[T]


# ---- files

def write_stl(path: Path, V: np.ndarray, T: np.ndarray, header: bytes = b"mises topopt") -> None:
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    nrm = np.cross(v1 - v0, v2 - v0)
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-30
    rec = np.zeros(len(T), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = nrm
    rec["v"] = np.stack([v0, v1, v2], 1)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(header.ljust(80, b" ")[:80])
        fh.write(np.uint32(len(T)).tobytes())
        fh.write(rec.tobytes())
    tmp.replace(path)


def read_stl_tris(path: Path) -> np.ndarray:
    """Binary STL -> (m, 3, 3) triangles."""
    raw = Path(path).read_bytes()
    n = struct.unpack("<I", raw[80:84])[0]
    rec = np.frombuffer(raw[84:84 + 50 * n], dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                     ("a", "<u2")])
    return rec["v"].astype(float)


def write_glb(path: Path, V: np.ndarray, T: np.ndarray, *, color=(0.62, 0.66, 0.72),
              extras: dict | None = None) -> None:
    """A plain lit mesh GLB (POSITION + NORMAL + indices) in the part GLBs' frame: metres,
    Z-up rotated to glTF Y-up."""
    V = np.asarray(V, np.float32)
    T = np.asarray(T, np.uint32)
    vn = np.zeros_like(V)
    if len(T):
        fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
        for c in range(3):
            np.add.at(vn, T[:, c], fn)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-30
    pos = (V / 1000.0).astype(np.float32)
    blobs = [pos.tobytes(), vn.astype(np.float32).tobytes(), T.ravel().tobytes()]
    offs, blob = [], b""
    for b in blobs:
        offs.append(len(blob))
        blob += b + b"\0" * (-len(b) % 4)
    lo = pos.min(0).tolist() if len(pos) else [0, 0, 0]
    hi = pos.max(0).tolist() if len(pos) else [0, 0, 0]
    gltf = {
        "asset": {"version": "2.0", "generator": "mises.topopt",
                  "extras": {"mises_topopt": extras or {}}},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)]}],
        "materials": [{"name": "topopt", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [*color, 1],
                                                "metallicFactor": 0, "roughnessFactor": 0.6}}],
        "meshes": [{"name": "topopt", "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": offs[0], "byteLength": len(blobs[0]),
                         "target": 34962},
                        {"buffer": 0, "byteOffset": offs[1], "byteLength": len(blobs[1]),
                         "target": 34962},
                        {"buffer": 0, "byteOffset": offs[2], "byteLength": len(blobs[2]),
                         "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                       "min": lo, "max": hi},
                      {"bufferView": 1, "componentType": 5126, "count": len(pos), "type": "VEC3"},
                      {"bufferView": 2, "componentType": 5125, "count": int(T.size),
                       "type": "SCALAR"}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    data = (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\0") + blob)
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---- B-rep

def mesh_to_solid(V: np.ndarray, T: np.ndarray):
    """Outward-oriented watertight triangle mesh -> (TopoDS_Solid, valid). Vertices and edges
    are shared by construction (no sewing); each face is the plane of its triangle bounded by
    a wire running counter-clockwise around it."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.Geom import Geom_Plane
    from OCP.gp import gp_Dir, gp_Pnt
    from OCP.TopoDS import TopoDS_Face, TopoDS_Shell, TopoDS_Solid, TopoDS_Vertex, TopoDS_Wire
    B = BRep_Builder()
    verts = []
    for p in np.asarray(V, float).tolist():
        v = TopoDS_Vertex()
        B.MakeVertex(v, gp_Pnt(*p), 1e-7)
        verts.append(v)
    edges: dict = {}

    def edge(a: int, b: int):
        k = (a, b) if a < b else (b, a)
        e = edges.get(k)
        if e is None:
            e = BRepBuilderAPI_MakeEdge(verts[k[0]], verts[k[1]]).Edge()
            edges[k] = e
        return e if a < b else e.Reversed()

    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    N = np.cross(v1 - v0, v2 - v0)
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    shell = TopoDS_Shell()
    B.MakeShell(shell)
    for (a, b, c), p, nrm in zip(np.asarray(T).tolist(), v0.tolist(), N.tolist()):
        w = TopoDS_Wire()
        B.MakeWire(w)
        B.Add(w, edge(a, b))
        B.Add(w, edge(b, c))
        B.Add(w, edge(c, a))
        w.Closed(True)
        f = TopoDS_Face()
        B.MakeFace(f, Geom_Plane(gp_Pnt(*p), gp_Dir(*nrm)), 1e-7)
        B.Add(f, w)
        B.Add(shell, f)
    shell.Closed(True)
    sol = TopoDS_Solid()
    B.MakeSolid(sol)
    B.Add(sol, shell)
    return sol, bool(BRepCheck_Analyzer(sol).IsValid())


def bool_op(kind: str, a, tools: list):
    """OCCT boolean (parallel mode on; build123d's operators run serial). a / tools are
    build123d shapes; returns a build123d Shape."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
    from build123d import Shape
    op = {"common": BRepAlgoAPI_Common, "cut": BRepAlgoAPI_Cut, "fuse": BRepAlgoAPI_Fuse}[kind]()
    la, lt = TopTools_ListOfShape(), TopTools_ListOfShape()
    la.Append(a.wrapped)
    for t in tools:
        lt.Append(t.wrapped)
    op.SetArguments(la)
    op.SetTools(lt)
    op.SetRunParallel(True)
    op.Build()
    if not op.IsDone():
        raise RuntimeError(f"the {kind} boolean failed")
    return Shape.cast(op.Shape())


def keep_out_solid(region: dict):
    """A box / cylinder region dict (spec.Region) as a build123d Solid."""
    from build123d import Align, Box, Cylinder, Location, Plane, Pos, Vector
    if region["kind"] == "box":
        x0, y0, z0, x1, y1, z1 = region["box"]
        return Pos(min(x0, x1), min(y0, y1), min(z0, z1)) * Box(
            abs(x1 - x0), abs(y1 - y0), abs(z1 - z0), align=(Align.MIN, Align.MIN, Align.MIN))
    if region["kind"] == "cylinder":
        cy = region["cylinder"]
        c, ax = Vector(*cy["center"]), Vector(*cy["axis"]).normalized()
        plane = Plane(origin=c, z_dir=ax)
        return Location(plane) * Cylinder(cy["radius"], cy["length"])
    raise ValueError(f"no solid for region kind {region['kind']!r}")


def largest_solid(shape):
    """(the largest solid of a boolean result, the volume fraction left out)."""
    solids = sorted(shape.solids(), key=lambda s: s.volume, reverse=True)
    if not solids:
        raise RuntimeError("the boolean result has no solid")
    total = sum(s.volume for s in solids)
    return solids[0], (1 - solids[0].volume / total) if total > 0 else 0.0
