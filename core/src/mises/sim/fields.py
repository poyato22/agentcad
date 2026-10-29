"""Result fields kept next to a study (fields.npz) for the viewer's probe, section plane and
charts. Two kinds:
- "fem":  nodal values on the fine mesh (node coordinates + corner tetrahedra); a probe is the
  linear (barycentric) interpolation inside the tetrahedron that contains the point.
- "grid": values on the CFD lattice (origin, spacing, shape); a probe is trilinear.
Coordinates are part coordinates in mm.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

FIELDS_FILE = "fields.npz"
MAX_SECTION = 64  # samples per side of a section plane
MAX_SECTION_WORK = 2_000_000  # tet-sample pairs one section may test


def principal_stresses(S: np.ndarray) -> np.ndarray:
    """S: (n, 6) [sxx, syy, szz, sxy, syz, szx] -> (n, 3) principal stresses, s1 >= s2 >= s3."""
    m = np.empty((len(S), 3, 3))
    m[:, 0, 0], m[:, 1, 1], m[:, 2, 2] = S[:, 0], S[:, 1], S[:, 2]
    m[:, 0, 1] = m[:, 1, 0] = S[:, 3]
    m[:, 1, 2] = m[:, 2, 1] = S[:, 4]
    m[:, 0, 2] = m[:, 2, 0] = S[:, 5]
    return np.linalg.eigvalsh(m)[:, ::-1]


def von_mises_np(S: np.ndarray) -> np.ndarray:
    a, b, c, d, e, f = (S[:, i] for i in range(6))
    return np.sqrt(np.maximum(0.0, 0.5 * ((a - b) ** 2 + (b - c) ** 2 + (c - a) ** 2)
                              + 3 * (d * d + e * e + f * f)))


def save_fem(path: Path, coords: np.ndarray, tets: np.ndarray, channels: dict[str, np.ndarray],
             units: dict[str, str]) -> None:
    arrays = {"kind": np.array("fem"), "coords": coords.astype(np.float32),
              "tets": tets.astype(np.int32), "units": np.array(repr(units))}
    arrays.update({f"ch_{k}": np.asarray(v, dtype=np.float32) for k, v in channels.items()})
    _save(path, arrays)


def save_grid(path: Path, origin, spacing: float, channels: dict[str, np.ndarray],
              units: dict[str, str], solid: np.ndarray | None = None, rot=None) -> None:
    """rot: rows = the grid frame's axes in part coordinates (the wind frame); a probe at a
    part-coordinate point p samples the grid at rot @ p."""
    arrays = {"kind": np.array("grid"), "origin": np.asarray(origin, dtype=np.float64),
              "spacing": np.array(float(spacing)), "units": np.array(repr(units)),
              "rot": np.asarray(rot if rot is not None else np.eye(3), dtype=np.float64)}
    if solid is not None:
        arrays["solid"] = solid.astype(bool)
    arrays.update({f"ch_{k}": np.asarray(v, dtype=np.float32) for k, v in channels.items()})
    _save(path, arrays)


def _save(path: Path, arrays: dict) -> None:
    tmp = Path(str(path) + ".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)


class Fields:
    def __init__(self, path: Path | bytes):
        """`path`: a host-made file, or the npz bytes (read from a part dir via safe_io)."""
        import ast
        import io
        src = io.BytesIO(path) if isinstance(path, (bytes, bytearray)) else path
        with np.load(src, allow_pickle=False) as z:
            self.kind = str(z["kind"])
            self.units: dict = ast.literal_eval(str(z["units"]))
            self.ch = {k[3:]: z[k] for k in z.files if k.startswith("ch_")}
            if self.kind == "fem":
                self.coords = z["coords"].astype(np.float64)
                self.tets = z["tets"]
            else:
                self.origin = z["origin"]
                self.h = float(z["spacing"])
                self.solid = z["solid"] if "solid" in z.files else None
                self.rot = z["rot"] if "rot" in z.files else np.eye(3)
                self.shape = next(iter(self.ch.values())).shape[-3:] if self.ch else (0, 0, 0)

    def channels(self) -> list[str]:
        return list(self.ch)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if self.kind == "fem":
            return self.coords.min(0), self.coords.max(0)
        lo = self.origin
        return lo, lo + self.h * (np.array(self.shape) - 1)

    # ---- point location
    def _bary(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(tet index or -1, barycentric weights (n, 4)) for points (n, 3)."""
        X = self.coords[self.tets[:, :4]]                      # (m, 4, 3)
        lo, hi = X.min(1), X.max(1)
        T = np.transpose(X[:, 1:] - X[:, :1], (0, 2, 1))        # (m, 3, 3)
        det = np.linalg.det(T)
        good = np.abs(det) > 1e-18
        Tinv = np.zeros_like(T)
        Tinv[good] = np.linalg.inv(T[good])
        out_t = np.full(len(pts), -1)
        out_w = np.zeros((len(pts), 4))
        eps = 1e-9
        for i, p in enumerate(pts):
            cand = np.nonzero(good & np.all(lo <= p + 1e-6, 1) & np.all(hi >= p - 1e-6, 1))[0]
            if not len(cand):
                continue
            lam = np.einsum("mij,mj->mi", Tinv[cand], p - X[cand, 0])
            w = np.concatenate([1 - lam.sum(1, keepdims=True), lam], 1)
            inside = np.nonzero(np.all(w >= -eps, 1))[0]
            if len(inside):
                k = inside[0]
                out_t[i], out_w[i] = cand[k], w[k]
        return out_t, out_w

    def _locate(self, cand: np.ndarray, p: np.ndarray):
        """(tet index, barycentric weights) of the candidate tet containing p, or None."""
        X = self.coords[self.tets[cand, :4]]
        T = np.transpose(X[:, 1:] - X[:, :1], (0, 2, 1))
        det = np.linalg.det(T)
        good = np.abs(det) > 1e-18
        if not good.any():
            return None
        lam = np.linalg.solve(T[good], (p - X[good, 0])[..., None])[..., 0]
        w = np.concatenate([1 - lam.sum(1, keepdims=True), lam], 1)
        inside = np.nonzero(np.all(w >= -1e-9, 1))[0]
        if not len(inside):
            return None
        return int(cand[good][inside[0]]), w[inside[0]]

    def probe(self, pts) -> list[dict]:
        pts = np.atleast_2d(np.asarray(pts, dtype=np.float64))
        res = []
        if self.kind == "fem":
            t, w = self._bary(pts)
            for i in range(len(pts)):
                if t[i] < 0:
                    res.append({"inside": False, "values": {}})
                    continue
                nodes = self.tets[t[i], :4]
                vals = {k: _round(np.tensordot(w[i], v[nodes], axes=(0, 0)))
                        for k, v in self.ch.items()}
                res.append({"inside": True, "values": vals})
            return res
        for p in pts @ self.rot.T:
            g = (p - self.origin) / self.h
            if np.any(g < 0) or np.any(g > np.array(self.shape) - 1):
                res.append({"inside": False, "values": {}})
                continue
            i0 = np.minimum(np.floor(g).astype(int), np.array(self.shape) - 2)
            f = g - i0
            if self.solid is not None and self.solid[tuple(np.round(g).astype(int))]:
                res.append({"inside": False, "solid": True, "values": {}})
                continue
            vals = {}
            for k, v in self.ch.items():
                acc = 0.0
                for dx in (0, 1):
                    for dy in (0, 1):
                        for dz in (0, 1):
                            wt = ((f[0] if dx else 1 - f[0]) * (f[1] if dy else 1 - f[1])
                                  * (f[2] if dz else 1 - f[2]))
                            acc = acc + wt * v[..., i0[0] + dx, i0[1] + dy, i0[2] + dz]
                vals[k] = _round(acc)
            res.append({"inside": True, "values": vals})
        return res

    def section(self, axis: int, offset: float, channel: str, n: int = 48) -> dict:
        """Values of one channel sampled on an n x n grid of the plane axis=offset (mm)."""
        n = max(4, min(MAX_SECTION, int(n)))
        lo, hi = self.bounds()
        u, v = [a for a in range(3) if a != axis]
        us = np.linspace(lo[u], hi[u], n)
        vs = np.linspace(lo[v], hi[v], n)
        if channel not in self.ch:
            raise KeyError(channel)
        data = self.ch[channel]
        if self.kind == "grid":
            k = int(round((offset - self.origin[axis]) / self.h))
            k = min(max(k, 0), self.shape[axis] - 1)
            sl = [slice(None)] * 3
            sl[axis] = k
            arr = data[(Ellipsis, *sl)]
            mask = None if self.solid is None else ~self.solid[tuple(sl)]
            vals = np.where(mask, arr, np.nan) if mask is not None else arr
            uu = self.origin[u] + self.h * np.arange(arr.shape[-2])
            vv = self.origin[v] + self.h * np.arange(arr.shape[-1])
            return {"axis": "xyz"[axis], "offset": float(self.origin[axis] + k * self.h),
                    "u": "xyz"[u], "v": "xyz"[v], "u_values": _list(uu), "v_values": _list(vv),
                    "channel": channel, "units": self.units.get(channel, ""),
                    "values": _list(vals)}
        out = np.full((n * n,) + data.shape[1:], np.nan)
        # only tetrahedra that cross the plane can contain a sample, and each sample only
        # tests the ones whose in-plane bounding box covers it (a bucket per grid point)
        tets = self.tets[:, :4]
        X = self.coords[tets]                                        # (m, 4, 3)
        keep = np.nonzero((X[..., axis].min(1) <= offset + 1e-9)
                          & (X[..., axis].max(1) >= offset - 1e-9))[0]
        du = (us[-1] - us[0]) / (n - 1) or 1.0
        dv = (vs[-1] - vs[0]) / (n - 1) or 1.0
        Xk = X[keep]
        i0 = np.clip(np.ceil((Xk[..., u].min(1) - us[0]) / du - 1e-9), 0, n - 1).astype(int)
        i1 = np.clip(np.floor((Xk[..., u].max(1) - us[0]) / du + 1e-9), -1, n - 1).astype(int)
        j0 = np.clip(np.ceil((Xk[..., v].min(1) - vs[0]) / dv - 1e-9), 0, n - 1).astype(int)
        j1 = np.clip(np.floor((Xk[..., v].max(1) - vs[0]) / dv + 1e-9), -1, n - 1).astype(int)
        entries = int(np.maximum(0, (i1 - i0 + 1)).astype(np.int64)
                      @ np.maximum(0, (j1 - j0 + 1)).astype(np.int64)) if len(keep) else 0
        if entries > MAX_SECTION_WORK:
            raise ValueError(f"section too large ({entries} tet-sample pairs); use a smaller n")
        buckets: dict[int, list[int]] = {}
        for k in range(len(keep)):
            for i in range(i0[k], i1[k] + 1):
                for j in range(j0[k], j1[k] + 1):
                    buckets.setdefault(i * n + j, []).append(k)
        for idx, cand in buckets.items():
            i, j = divmod(idx, n)
            p = np.zeros(3)
            p[u], p[v], p[axis] = us[i], vs[j], offset
            hit = self._locate(keep[np.array(cand)], p)
            if hit is not None:
                t, w = hit
                out[idx] = np.tensordot(w, data[tets[t]], axes=(0, 0))
        return {"axis": "xyz"[axis], "offset": float(offset), "u": "xyz"[u], "v": "xyz"[v],
                "u_values": _list(us), "v_values": _list(vs), "channel": channel,
                "units": self.units.get(channel, ""),
                "values": _list(out.reshape((n, n) + data.shape[1:]))}


def histogram(values: np.ndarray, bins: int = 20) -> dict:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if not len(v):
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(v, bins=bins)
    return {"edges": _list(edges), "counts": [int(c) for c in counts]}


def _round(x):
    a = np.asarray(x, dtype=float)

    def f(y):
        return float(y) if np.isfinite(y) else None
    return f(a) if a.ndim == 0 else [f(y) for y in a]


def _list(a) -> list:
    a = np.asarray(a, dtype=float)
    return [None if not np.isfinite(x) else float(round(x, 6)) for x in a.ravel()] \
        if a.ndim <= 1 else [_list(r) for r in a]
