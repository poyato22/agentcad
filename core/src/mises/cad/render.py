"""Offscreen rendering for the render-and-critique loop (G1).

Ruling (recorded in the task report): renders are produced on the host by a pure-numpy
z-buffer rasterizer that reads the already-built, trusted tessellated mesh (model.stl) of a
part. That mesh is the output of the sandboxed build worker, so rasterizing it executes no
untrusted code -- it is pure arithmetic with no file, network or process access -- which is why
it does not itself need the sandbox. On macOS arm64 inside the deny-fork, no-display sandbox a
pure rasterizer is more robust than spawning an OCCT offscreen view or pyrender/osmesa, and it
adds no system dependencies (numpy + Pillow only, both already required).

The rasterizer is vectorised (see "rasterization" below): a 150k-triangle mesh renders four
512 px views in well under 2 s.

The public surface:
- `load_stl(bytes)` -> triangles (N, 3, 3)
- `Mesh` -> a named, coloured, triangulated solid
- `render_views(meshes, ...)` -> {view_name: PNG bytes}
- `render_mask(meshes, view, ...)` -> the view's silhouette (bool HxW)
- `reference_mask(png_or_jpeg_bytes)` -> the foreground silhouette of a reference image
- `mask_iou(a, b)` / `align_and_iou(render, reference)` -> silhouette match scores
- `fit_png(png, max_edge)` -> a PNG downscaled to at most max_edge px (for model input)
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

import numpy as np

# The four standard views: a unit "forward" (the direction the camera looks, from eye to the
# model) and an "up". Matches the third-angle projection directions used by the 2D drawing.
VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),    # look +Y, Z up
    "top": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),     # look down -Z, Y up
    "side": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),    # look -X (from +X), Z up
    "iso": ((-1.0, 1.0, -1.0), (0.0, 0.0, 1.0)),    # three-quarter
}
DEFAULT_VIEWS = ("front", "top", "side", "iso")

_LIGHT = np.array([0.3, -0.5, 0.8])      # from the camera's upper area, world space
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)
_AMBIENT = 0.35
_BG = (238, 238, 240)
_EDGE = (40, 40, 45)
_DEFAULT_COLOR = (0.62, 0.66, 0.72)


@dataclass
class Mesh:
    """A triangulated solid in world mm. `tris` is (N, 3, 3): N triangles, 3 vertices, xyz."""
    tris: np.ndarray
    color: tuple[float, float, float] = _DEFAULT_COLOR
    opacity: float = 1.0
    name: str = ""

    def __post_init__(self) -> None:
        self.tris = np.asarray(self.tris, dtype=np.float64).reshape(-1, 3, 3)

    def centroid(self) -> np.ndarray:
        if len(self.tris) == 0:
            return np.zeros(3)
        return self.tris.reshape(-1, 3).mean(axis=0)


# ---- mesh loading (binary or ASCII STL)

def load_stl(data: bytes) -> np.ndarray:
    """Triangles (N, 3, 3) from STL bytes. Binary and ASCII are both accepted; the real build
    worker writes binary STL, but a hand-written ASCII solid loads too."""
    if _looks_ascii_stl(data):
        tris = _load_ascii_stl(data)
        if len(tris):
            return tris
    return _load_binary_stl(data)


def _looks_ascii_stl(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"solid") and b"facet" in data[:2048].lower()


def _load_binary_stl(data: bytes) -> np.ndarray:
    if len(data) < 84:
        raise ValueError("STL too short for a binary header")
    (n,) = struct.unpack_from("<I", data, 80)
    expected = 84 + n * 50
    if n == 0 or len(data) < expected:
        raise ValueError("binary STL triangle count does not match its length")
    # 50-byte records: normal (3 f32), 3 vertices (9 f32), attribute (u16); vertices only
    rec = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return np.frombuffer(data, dtype=rec, count=n, offset=84)["v"].astype(np.float64)


def _load_ascii_stl(data: bytes) -> np.ndarray:
    verts: list[list[float]] = []
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
    if len(verts) < 3 or len(verts) % 3 != 0:
        return np.empty((0, 3, 3))
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


# ---- geometry helpers

def bbox(meshes: list[Mesh]) -> tuple[np.ndarray, np.ndarray]:
    pts = [m.tris.reshape(-1, 3) for m in meshes if len(m.tris)]
    if not pts:
        return np.zeros(3), np.ones(3)
    allpts = np.concatenate(pts, axis=0)
    return allpts.min(axis=0), allpts.max(axis=0)


def _camera_basis(view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fwd_raw, up_raw = VIEWS[view]
    f = np.array(fwd_raw, dtype=np.float64)
    f = f / np.linalg.norm(f)
    up = np.array(up_raw, dtype=np.float64)
    right = np.cross(f, up)
    nr = np.linalg.norm(right)
    if nr < 1e-9:                       # up parallel to forward: pick any perpendicular
        right = np.cross(f, np.array([1.0, 0.0, 0.0]))
        nr = np.linalg.norm(right)
        if nr < 1e-9:
            right = np.cross(f, np.array([0.0, 1.0, 0.0]))
            nr = np.linalg.norm(right)
    right = right / nr
    true_up = np.cross(right, f)
    return f, right, true_up


def explode_meshes(meshes: list[Mesh], factor: float) -> list[Mesh]:
    """Move each mesh outward from the group's centre by `factor` x the group size, so an
    exploded view separates the parts. factor 0 is unchanged."""
    if factor <= 0 or len(meshes) < 2:
        return meshes
    lo, hi = bbox(meshes)
    center = (lo + hi) / 2.0
    scale = float(np.linalg.norm(hi - lo)) or 1.0
    out = []
    for m in meshes:
        d = m.centroid() - center
        n = np.linalg.norm(d)
        shift = (d / n) * scale * factor if n > 1e-9 else np.zeros(3)
        out.append(Mesh(m.tris + shift, color=m.color, opacity=m.opacity, name=m.name))
    return out


def section_mesh(tris: np.ndarray, origin, normal) -> np.ndarray:
    """Clip triangles to the half-space on the negative side of the plane (origin, normal),
    so the +normal half is cut away and the interior shows. Whole triangles are kept or dropped
    in one vectorised pass; only the few that cross the plane are re-triangulated
    (Sutherland-Hodgman on the kept side)."""
    if len(tris) == 0:
        return tris
    o = np.asarray(origin, dtype=np.float64)
    n = np.asarray(normal, dtype=np.float64)
    nn = np.linalg.norm(n)
    if nn < 1e-9:
        return tris
    n = n / nn
    d = (tris - o) @ n                          # (N, 3) signed distance per vertex
    k = (d <= 1e-9).sum(axis=1)
    whole = tris[k == 3]
    cross = (k > 0) & (k < 3)
    out: list[np.ndarray] = []
    for tri, dist in zip(tris[cross], d[cross]):
        poly = _clip_triangle(tri, dist)
        for j in range(1, len(poly) - 1):      # fan-triangulate the clipped polygon
            out.append(np.array([poly[0], poly[j], poly[j + 1]]))
    if out:
        whole = np.concatenate([whole, np.array(out, dtype=np.float64)], axis=0)
    return whole if len(whole) else np.empty((0, 3, 3))


def _clip_triangle(tri: np.ndarray, d: np.ndarray) -> list[np.ndarray]:
    """Vertices of the part of `tri` with signed distance d <= 0, inserting edge crossings."""
    poly: list[np.ndarray] = []
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        da, db = d[i], d[(i + 1) % 3]
        if da <= 1e-9:
            poly.append(a)
        if (da < 0) != (db < 0) and abs(da - db) > 1e-12:
            t = da / (da - db)
            poly.append(a + t * (b - a))
    return poly


# ---- rasterization (vectorised)
#
# Every triangle of every mesh is projected at once. Triangles are bucketed by the size of their
# pixel bounding box (next power of two), and each bucket is rasterised as one numpy batch: the
# candidate pixel centres of all its triangles are tested with barycentric coordinates together.
# The fragments (pixel, depth, triangle) are then resolved by a single sort: the nearest fragment
# per pixel wins (z-buffer); for x-ray they are composited front to back with per-triangle
# opacity.

_FRAG_CHUNK = 2_000_000                       # candidate pixels per batch, bounds peak memory
# Triangles whose pixel bbox is wider/taller than this go through the scanline path instead of
# the box batch: a long thin triangle (typical of a planar face's fan triangulation) has a huge
# bbox but little area, and testing every pixel of its box is what made such meshes slow.
_BOX_MAX = 8
_SCAN_CHUNK = 4_000_000                       # pixels per scanline batch


def _scan_fragments(fr: _Frame, ids, x0, x1, y0, y1, denom, size: int,
                    pix_out: list, dep_out: list, tri_out: list) -> None:
    """Scanline fragments of the triangles `ids`, all at once: for every (triangle, pixel row)
    the row centre's crossing with the three edges gives an x span, and only the pixels in that
    span are generated, so the work is the triangle's area, not its bounding box."""
    px, py, pz = fr.px, fr.py, fr.pz
    h = y1[ids] - y0[ids] + 1
    # rows in batches so a very large mesh cannot blow up memory
    cum = np.cumsum(h)
    starts = np.searchsorted(cum, np.arange(0, cum[-1], _SCAN_CHUNK), side="right")
    bounds = list(starts) + [len(ids)]
    for lo_i, hi_i in zip(bounds[:-1], bounds[1:]):
        if hi_i <= lo_i:
            continue
        t_ids = ids[lo_i:hi_i]
        hh = h[lo_i:hi_i]
        rep = np.repeat(np.arange(len(t_ids)), hh)
        first_row = np.repeat(np.cumsum(hh) - hh, hh)
        tri = t_ids[rep]
        ry = y0[tri] + (np.arange(len(rep)) - first_row)
        yc = ry + 0.5
        xl = np.full(len(rep), np.inf)
        xr = np.full(len(rep), -np.inf)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            yi, yj, xi, xj = py[tri, i], py[tri, j], px[tri, i], px[tri, j]
            dy = yj - yi
            hit = (np.minimum(yi, yj) <= yc) & (yc <= np.maximum(yi, yj)) & (np.abs(dy) > 1e-12)
            with np.errstate(divide="ignore", invalid="ignore"):
                x = np.where(hit, xi + (yc - yi) * (xj - xi) / np.where(hit, dy, 1.0), 0.0)
            xl = np.where(hit, np.minimum(xl, x), xl)
            xr = np.where(hit, np.maximum(xr, x), xr)
        good = np.isfinite(xl) & np.isfinite(xr)
        cx0 = np.where(good, np.ceil(xl - 0.5), 0).astype(np.int64)
        cx1 = np.where(good, np.floor(xr - 0.5), -1).astype(np.int64)
        cx0 = np.maximum(cx0, x0[tri])
        cx1 = np.minimum(cx1, x1[tri])
        n = np.maximum(cx1 - cx0 + 1, 0)
        if n.sum() == 0:
            continue
        rep2 = np.repeat(np.arange(len(rep)), n)
        first_px = np.repeat(np.cumsum(n) - n, n)
        X = cx0[rep2] + (np.arange(len(rep2)) - first_px)
        Y = ry[rep2]
        t = tri[rep2]
        gx, gy = X + 0.5, Y + 0.5
        tx, ty, dn = px[t], py[t], denom[t]
        a = ((ty[:, 1] - ty[:, 2]) * (gx - tx[:, 2]) + (tx[:, 2] - tx[:, 1]) * (gy - ty[:, 2])) / dn
        b = ((ty[:, 2] - ty[:, 0]) * (gx - tx[:, 2]) + (tx[:, 0] - tx[:, 2]) * (gy - ty[:, 2])) / dn
        z = pz[t]
        pix_out.append(Y * size + X)
        dep_out.append(a * z[:, 0] + b * z[:, 1] + (1.0 - a - b) * z[:, 2])
        tri_out.append(t)


@dataclass
class _Frame:
    px: np.ndarray                            # (N, 3) pixel x of each vertex
    py: np.ndarray                            # (N, 3) pixel y (grows downward)
    pz: np.ndarray                            # (N, 3) camera depth (bigger = farther)
    shade: np.ndarray                         # (N, 3) rgb 0..1 per triangle
    alpha: np.ndarray                         # (N,) opacity per triangle


def _project(meshes: list[Mesh], view: str, size: int, framing) -> _Frame:
    f, right, up = _camera_basis(view)
    lo, hi = framing if framing is not None else bbox(meshes)
    center = (lo + hi) / 2.0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                        for z in (lo[2], hi[2])])
    cx = (corners - center) @ right
    cy = (corners - center) @ up
    half = max(cx.max() - cx.min(), cy.max() - cy.min(), 1e-6) / 2.0
    scale = (size / 2.0) * 0.90 / half
    live = [m for m in meshes if len(m.tris)]
    if not live:
        z = np.zeros((0, 3))
        return _Frame(z, z, z, z, np.zeros(0))
    tris = np.concatenate([m.tris for m in live], axis=0)
    color = np.concatenate([np.broadcast_to(np.asarray(m.color, dtype=np.float64),
                                            (len(m.tris), 3)) for m in live], axis=0)
    alpha = np.concatenate([np.full(len(m.tris), float(m.opacity)) for m in live])
    v = tris - center
    px = (v @ right) * scale + size / 2.0
    py = size / 2.0 - (v @ up) * scale
    pz = v @ f
    nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    lam = np.where(ln > 1e-12, np.abs(nrm @ _LIGHT) / np.maximum(ln, 1e-12), 0.0)
    intensity = _AMBIENT + (1.0 - _AMBIENT) * lam    # double-sided Lambert
    shade = np.clip(color * intensity[:, None], 0.0, 1.0)
    return _Frame(px, py, pz, shade, alpha)


def _fragments(fr: _Frame, size: int):
    """(pixel index, depth, triangle index) of every covered pixel centre, all triangles."""
    px, py, pz = fr.px, fr.py, fr.pz
    empty = (np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0, dtype=np.int64))
    if len(px) == 0:
        return empty
    x0 = np.maximum(np.ceil(px.min(axis=1) - 0.5), 0).astype(np.int64)
    x1 = np.minimum(np.floor(px.max(axis=1) - 0.5), size - 1).astype(np.int64)
    y0 = np.maximum(np.ceil(py.min(axis=1) - 0.5), 0).astype(np.int64)
    y1 = np.minimum(np.floor(py.max(axis=1) - 0.5), size - 1).astype(np.int64)
    denom = ((py[:, 1] - py[:, 2]) * (px[:, 0] - px[:, 2])
             + (px[:, 2] - px[:, 1]) * (py[:, 0] - py[:, 2]))
    live = np.nonzero((x1 >= x0) & (y1 >= y0) & (np.abs(denom) > 1e-12))[0]
    if len(live) == 0:
        return empty
    ext = np.maximum(x1[live] - x0[live] + 1, y1[live] - y0[live] + 1)
    pix_out, dep_out, tri_out = [], [], []
    big = live[ext > _BOX_MAX]
    if len(big):
        _scan_fragments(fr, big, x0, x1, y0, y1, denom, size, pix_out, dep_out, tri_out)
    live, ext = live[ext <= _BOX_MAX], ext[ext <= _BOX_MAX]
    bucket = np.ceil(np.log2(ext)).astype(np.int64) if len(live) else np.zeros(0, np.int64)
    for b in np.unique(bucket):
        k = 1 << int(b)
        ids = live[bucket == b]
        oy, ox = np.divmod(np.arange(k * k, dtype=np.int64), k)
        step = max(1, _FRAG_CHUNK // (k * k))
        for s in range(0, len(ids), step):
            t = ids[s:s + step]
            X = x0[t, None] + ox[None, :]
            Y = y0[t, None] + oy[None, :]
            gx = X + 0.5
            gy = Y + 0.5
            tx, ty, dn = px[t], py[t], denom[t, None]
            a = ((ty[:, 1, None] - ty[:, 2, None]) * (gx - tx[:, 2, None])
                 + (tx[:, 2, None] - tx[:, 1, None]) * (gy - ty[:, 2, None])) / dn
            bb = ((ty[:, 2, None] - ty[:, 0, None]) * (gx - tx[:, 2, None])
                  + (tx[:, 0, None] - tx[:, 2, None]) * (gy - ty[:, 2, None])) / dn
            c = 1.0 - a - bb
            inside = ((X <= x1[t, None]) & (Y <= y1[t, None])
                      & (a >= -1e-9) & (bb >= -1e-9) & (c >= -1e-9))
            rows, cols = np.nonzero(inside)
            if len(rows) == 0:
                continue
            z = pz[t[rows]]
            ar, br, cr = a[rows, cols], bb[rows, cols], c[rows, cols]
            pix_out.append(Y[rows, cols] * size + X[rows, cols])
            dep_out.append(ar * z[:, 0] + br * z[:, 1] + cr * z[:, 2])
            tri_out.append(t[rows])
    if not pix_out:
        return empty
    return np.concatenate(pix_out), np.concatenate(dep_out), np.concatenate(tri_out)


def _raster(fr: _Frame, size: int, transparent: bool):
    """(rgb float HxWx3 0..1, mask bool HxW, zbuf HxW) for one projected frame."""
    n_pix = size * size
    bg = np.array(_BG, dtype=np.float64) / 255.0
    img = np.tile(bg, (n_pix, 1))
    zbuf = np.full(n_pix, np.inf)
    mask = np.zeros(n_pix, dtype=bool)
    pix, dep, tri = _fragments(fr, size)
    if len(pix):
        # order by pixel, then nearest first: one argsort on a combined float key
        dmin = float(dep.min())
        span = (float(dep.max()) - dmin) or 1.0
        order = np.argsort(pix + (dep - dmin) / (span * 1.000001))
        pix_s, dep_s, tri_s = pix[order], dep[order], tri[order]
        first = np.ones(len(pix_s), dtype=bool)
        first[1:] = pix_s[1:] != pix_s[:-1]
        near = pix_s[first]
        zbuf[near] = dep_s[first]
        mask[near] = True
        if not transparent:
            img[near] = fr.shade[tri_s[first]]
        else:
            # front-to-back "over": C = sum(c_i a_i T_i) + bg T, T_i = prod_{j<i}(1 - a_j)
            a = np.minimum(fr.alpha[tri_s], 0.999)
            la = np.log1p(-a)
            cs = np.cumsum(la)
            start = np.maximum.accumulate(np.where(first, np.arange(len(pix_s)), 0))
            excl = cs - la - (cs[start] - la[start])
            w = a * np.exp(excl)
            acc = np.stack([np.bincount(pix_s, weights=w * fr.shade[tri_s, ch],
                                        minlength=n_pix) for ch in range(3)], axis=1)
            trans = np.exp(np.bincount(pix_s, weights=la, minlength=n_pix))
            img = acc + bg[None, :] * trans[:, None]
    return img.reshape(size, size, 3), mask.reshape(size, size), zbuf.reshape(size, size)


def _silhouette_edges(mask: np.ndarray) -> np.ndarray:
    """Boundary pixels of the foreground mask (foreground next to background)."""
    m = mask
    edge = np.zeros_like(m)
    edge[1:, :] |= m[1:, :] & ~m[:-1, :]
    edge[:-1, :] |= m[:-1, :] & ~m[1:, :]
    edge[:, 1:] |= m[:, 1:] & ~m[:, :-1]
    edge[:, :-1] |= m[:, :-1] & ~m[:, 1:]
    return edge


def _depth_edges(zbuf: np.ndarray, mask: np.ndarray, thresh: float) -> np.ndarray:
    """Interior creases and hidden-feature contours: foreground pixels whose camera depth jumps
    by more than `thresh` from a foreground neighbour. This is what makes an internal feature
    (e.g. a cavity revealed by a section) show up under flat shading."""
    z = np.where(mask, zbuf, np.nan)
    edge = np.zeros_like(mask)
    with np.errstate(invalid="ignore"):
        for dy, dx in ((1, 0), (0, 1)):
            a = z[dy:, dx:]
            b = z[:z.shape[0] - dy, :z.shape[1] - dx]
            diff = np.abs(a - b) > thresh            # NaN compares False
            edge[dy:, dx:] |= diff
            edge[:z.shape[0] - dy, :z.shape[1] - dx] |= diff
    return edge & mask


def _render_one(meshes: list[Mesh], view: str, size: int,
                framing, transparent: bool) -> tuple[np.ndarray, np.ndarray]:
    img, mask, zbuf = _raster(_project(meshes, view, size, framing), size, transparent)
    edge = _silhouette_edges(mask)
    if not transparent:
        lo, hi = framing
        diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) or 1.0
        edge |= _depth_edges(zbuf, mask, 0.01 * diag)
    img[edge] = np.array(_EDGE) / 255.0
    return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8), mask


def _to_png(rgb: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    # compress_level 6 without `optimize`: optimize multiplies encode time for little gain
    Image.fromarray(rgb, "RGB").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def fit_png(png: bytes, max_edge: int = 512) -> bytes:
    """`png` downscaled (aspect kept) so its long edge is at most `max_edge` px; unchanged when
    it already fits. Images sent to a model are capped this way (context budget)."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        if max(im.size) <= max_edge:
            return png
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", compress_level=6)
        return buf.getvalue()


def _coerce(meshes) -> list[Mesh]:
    if isinstance(meshes, Mesh):
        return [meshes]
    if isinstance(meshes, np.ndarray):
        return [Mesh(meshes)]
    return [m if isinstance(m, Mesh) else Mesh(m) for m in meshes]


def render_views(meshes, *, views=DEFAULT_VIEWS, size: int = 512,
                 section: tuple | None = None, explode: float = 0.0,
                 transparent: bool = False) -> dict[str, bytes]:
    """Render `meshes` (a Mesh, a triangles array, or a list of them) from each named view.

    Returns {view_name: PNG bytes}. `section` is (origin, normal): the +normal half is cut away.
    `explode` > 0 separates multiple meshes. `transparent` composites with each mesh's opacity
    (x-ray)."""
    ms = _coerce(meshes)
    if explode > 0:
        ms = explode_meshes(ms, explode)
    if section is not None:
        o, n = section
        ms = [Mesh(section_mesh(m.tris, o, n), color=m.color, opacity=m.opacity, name=m.name)
              for m in ms]
    framing = bbox(ms)
    out: dict[str, bytes] = {}
    for view in views:
        if view not in VIEWS:
            raise ValueError(f"unknown view {view!r}; known: {', '.join(VIEWS)}")
        rgb, _mask = _render_one(ms, view, size, framing, transparent)
        out[view] = _to_png(rgb)
    return out


def render_mask(meshes, view: str, *, size: int = 512,
                section: tuple | None = None) -> np.ndarray:
    """The view's silhouette (bool HxW): the pixels the model covers. Used for IoU matching."""
    ms = _coerce(meshes)
    if section is not None:
        o, n = section
        ms = [Mesh(section_mesh(m.tris, o, n)) for m in ms]
    fr = _project(ms, view, size, bbox(ms))
    _img, mask, _z = _raster(fr, size, transparent=False)
    return mask


# ---- silhouette matching

def reference_mask(image_bytes: bytes, *, size: int | None = None) -> np.ndarray:
    """The foreground silhouette of a reference image: pixels that are not near-white
    background. If the image has alpha, the alpha channel decides instead."""
    from PIL import Image
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA")
    if size is not None:
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    arr = np.asarray(im).astype(np.float64)
    alpha = arr[:, :, 3]
    if (alpha < 250).any():
        return alpha > 32
    lum = arr[:, :, :3].mean(axis=2)
    return lum < 245


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection-over-union of two boolean masks (no alignment). 1.0 when identical."""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    if a.shape != b.shape:
        b = _resize_mask(b, a.shape)
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum()) / float(union)


def _resize_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    from PIL import Image
    im = Image.fromarray((mask.astype(np.uint8) * 255), "L").resize(
        (shape[1], shape[0]), Image.Resampling.NEAREST)
    return np.asarray(im) > 127


_ALIGN = 64


def _tight(mask: np.ndarray) -> np.ndarray:
    """Crop a mask to its foreground bounding box and paste it, scaled uniformly (aspect ratio
    kept) and centred, into a fixed square canvas. Two silhouettes are then compared by shape
    with their position and overall size aligned -- an offset or a uniform resize no longer
    matters, but a different aspect ratio or outline still does."""
    ys, xs = np.where(mask)
    canvas = np.zeros((_ALIGN, _ALIGN), dtype=bool)
    if len(xs) == 0:
        return canvas
    sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = sub.shape
    scale = _ALIGN / max(h, w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    resized = _resize_mask(sub, (nh, nw))
    y0 = (_ALIGN - nh) // 2
    x0 = (_ALIGN - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = resized
    return canvas


def align_and_iou(render: np.ndarray, reference: np.ndarray) -> float:
    """IoU after aligning each silhouette's bounding box and scale: a reference that is merely
    offset or differently scaled still scores high; a genuinely different shape scores low."""
    return mask_iou(_tight(render), _tight(reference))
