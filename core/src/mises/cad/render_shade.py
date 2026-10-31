"""Per-pixel shading for the design renderer (Plan 9 G-R, research 2026-10-30 §4.3/§4.4).

render.py rasterises (which triangle covers each pixel, at what depth); this module turns that
into a picture. Pure numpy + Pillow, no mises imports: it runs inside the sandboxed render
worker next to render.py.

- `weld` / `corner_normals`: shared vertices and crease-split, area-weighted smooth normals, so a
  smooth B-rep reads as smooth (a loft of exact ellipses no longer renders as crumpled foil)
  while real creases (dihedral above the crease angle) stay crisp.
- `shade_cmf`: three-point studio light (key, fill, rim) plus a sky/ground ambient, Blinn
  specular and a horizon reflection driven by each part's roughness and metalness (CMF).
- `zebra`: stripes of n.d -- a kink in the stripes is a G1 join, a jump is G0.
- line helpers (`dilate`, `soft`, `warp`, `noise_field`) for the NPR sketch style: silhouette
  and crease lines with a low-frequency hand wobble, drawn twice ("over-sketch").
- `blur`: separable box blur (3 passes ~ Gaussian) for soft ground shadows.
"""
from __future__ import annotations

import numpy as np

CREASE_DEG = 30.0             # shaded / sketch: dihedral above this is a crease
ZEBRA_CREASE_DEG = 20.0       # zebra keeps smaller kinks visible (export tessellates at ~6 deg)
_MAX_VALENCE = 32             # corners per vertex for the exact pairwise crease test
_PAIR_CHUNK = 250_000         # corner pairs per batch
_TRI_CHUNK = 100_000          # triangles per batch for face normals and angles


# ---- welding and normals

def _weld_one(pts: np.ndarray) -> np.ndarray:
    """Vertex ids (int32) of one mesh's corner points (M, 3): one packed int64 key per point
    (21 bits per axis), sorted -- no float row compares, no stacked copies."""
    lo = pts.min(axis=0)
    span = float((pts.max(axis=0) - lo).max()) or 1.0
    q = max(1e-4, span / (2 ** 21 - 2))      # >= 1e-4 mm; ~1e-3 mm for a 2 m mesh (21 bits)
    key = np.zeros(len(pts), dtype=np.int64)
    for ax in range(3):
        k = np.rint((pts[:, ax] - lo[ax]) / q).astype(np.int64)
        key |= k << (21 * (2 - ax))
        del k
    order = np.argsort(key, kind="stable")
    sk = key[order]
    del key
    new = np.empty(len(sk), dtype=bool)
    new[0] = True
    np.not_equal(sk[1:], sk[:-1], out=new[1:])
    del sk
    vid = np.empty(len(order), dtype=np.int32)
    vid[order] = np.cumsum(new, dtype=np.int32) - 1
    return vid


def weld(tris: np.ndarray, mesh_of: np.ndarray | None = None) -> np.ndarray:
    """(N, 3) int32 vertex ids: corners at the same position (to ~1e-6 of the mesh size, at
    most 1e-4 mm apart) of the same mesh share an id. STL stores every triangle's corners
    separately."""
    n = len(tris)
    if n == 0:
        return np.zeros((0, 3), dtype=np.int32)
    pts = tris.reshape(-1, 3)
    if mesh_of is None:
        return _weld_one(pts).reshape(-1, 3)
    mesh_of = np.asarray(mesh_of)
    out = np.empty(n * 3, dtype=np.int32)
    base = 0
    for m in np.unique(mesh_of):
        sel = np.repeat(mesh_of == m, 3)
        v = _weld_one(pts[sel])
        out[sel] = v + base
        base += int(v.max()) + 1
    return out.reshape(-1, 3)


def face_normals(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(area-weighted normals (length = 2 x area), unit normals) per triangle."""
    fn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(fn, axis=1)
    return fn, fn / np.maximum(ln, 1e-30)[:, None]


def unit_normals(tris: np.ndarray) -> np.ndarray:
    """(N, 3) float32 unit face normals, computed in bounded batches."""
    out = np.empty((len(tris), 3), dtype=np.float32)
    for s in range(0, len(tris), _TRI_CHUNK):
        t = tris[s:s + _TRI_CHUNK]
        out[s:s + _TRI_CHUNK] = face_normals(t)[1]
    return out


def corner_angles(tris: np.ndarray) -> np.ndarray:
    """(N, 3) float32 interior angle (radians) of each triangle at each corner."""
    out = np.empty(tris.shape[:2], dtype=np.float32)
    for s in range(0, len(tris), _TRI_CHUNK):
        t = tris[s:s + _TRI_CHUNK]
        for k in range(3):
            a = t[:, (k + 1) % 3] - t[:, k]
            b = t[:, (k + 2) % 3] - t[:, k]
            na = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
            c = np.einsum("ij,ij->i", a, b) / np.maximum(na, 1e-30)
            out[s:s + _TRI_CHUNK, k] = np.arccos(np.clip(c, -1.0, 1.0))
    return out


def corner_normals(tris: np.ndarray, vid: np.ndarray, crease_deg: float = CREASE_DEG
                   ) -> np.ndarray:
    """(N, 3, 3) float32 unit normal per triangle corner: the angle-weighted mean of the
    normals of the faces around that vertex that are within `crease_deg` of this corner's own
    face. A vertex on a crease therefore carries one normal per side and the crease stays sharp.

    Exact for vertices with up to _MAX_VALENCE corners (all real tessellations); a hub with more
    (a fan centre, a hostile mesh) uses its one smooth normal where it is within the crease
    angle of the face, else the face normal -- so the work is bounded by _MAX_VALENCE x corners.
    Everything is float32 / int32 and batched: about 120 bytes per triangle at peak."""
    n = len(tris)
    unit = unit_normals(tris)
    if n == 0:
        return np.zeros((0, 3, 3), dtype=np.float32)
    out = np.repeat(unit, 3, axis=0)                          # (3N, 3): the face normal
    cos_t = np.float32(np.cos(np.radians(crease_deg)))
    # angle-weighted (Thurmer & Wuthrich): unlike area weights, independent of how the
    # tessellator happened to split a smooth patch, so irregular meshes do not look blotchy
    ang = corner_angles(tris).reshape(-1)
    order = np.argsort(vid.reshape(-1), kind="stable").astype(np.int32)
    sv = vid.reshape(-1)[order]
    starts = np.flatnonzero(np.r_[True, sv[1:] != sv[:-1]]).astype(np.int32)
    del sv
    counts = np.diff(np.r_[starts, 3 * n]).astype(np.int32)
    grp = np.repeat(np.arange(len(starts), dtype=np.int32), counts)   # group per sorted pos
    c_of = counts[grp]
    face_s = order // 3                                      # face of each sorted corner

    small = np.flatnonzero(c_of <= _MAX_VALENCE).astype(np.int32)
    if len(small):
        cum = np.cumsum(c_of[small], dtype=np.int64)
        cuts = np.searchsorted(cum, np.arange(0, cum[-1], _PAIR_CHUNK), side="right")
        bounds = sorted(set([0, *cuts.tolist(), len(small)]))
        for b0, b1 in zip(bounds[:-1], bounds[1:]):
            P = small[b0:b1]
            reps = c_of[P]
            local = np.repeat(np.arange(len(P), dtype=np.int32), reps)
            offs = (np.arange(len(local), dtype=np.int32)
                    - np.repeat(np.cumsum(reps, dtype=np.int32) - reps, reps))
            fi = face_s[P][local]
            cj = order[starts[grp[P]][local] + offs]
            fj = cj // 3
            w = ang[cj] * (np.einsum("ij,ij->i", unit[fi], unit[fj]) >= cos_t)
            acc = np.empty((len(P), 3), dtype=np.float32)
            for ax in range(3):
                acc[:, ax] = np.bincount(local, weights=unit[fj, ax] * w, minlength=len(P))
            ln = np.linalg.norm(acc, axis=1)
            good = ln > 1e-30
            out[order[P][good]] = acc[good] / ln[good, None]

    big = np.flatnonzero(c_of > _MAX_VALENCE)
    if len(big):
        g = grp[big]
        sums = np.zeros((len(starts), 3))
        for ax in range(3):
            sums[:, ax] = np.bincount(g, weights=unit[face_s[big], ax] * ang[order[big]],
                                      minlength=len(starts))
        nv = sums[g]
        nv = nv / np.maximum(np.linalg.norm(nv, axis=1), 1e-30)[:, None]
        use = np.einsum("ij,ij->i", nv, unit[face_s[big]]) >= cos_t
        out[order[big][use]] = nv[use]
    return out.reshape(n, 3, 3)


# ---- lighting

def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / (np.linalg.norm(v) or 1.0)


def lights(f: np.ndarray, right: np.ndarray, up: np.ndarray) -> dict[str, np.ndarray]:
    """Studio three-point rig in camera space, so every view is lit the same way: the key light
    above-right of the camera, a soft fill from the left, and a rim light from behind-above
    that separates the silhouette from the background."""
    return {"key": _unit(-0.85 * f + 0.55 * right + 0.65 * up),
            "fill": _unit(-0.7 * f - 0.8 * right - 0.25 * up),
            "rim": _unit(0.9 * f - 0.25 * right + 0.6 * up)}


def shade_cmf(n: np.ndarray, v: np.ndarray, base: np.ndarray, rough: np.ndarray,
              metal: np.ndarray, rig: dict[str, np.ndarray], world_up: np.ndarray
              ) -> np.ndarray:
    """(M, 3) rgb 0..1 of M pixels. n: unit normals facing the viewer, v: unit vectors toward
    the eye, base: rgb, rough/metal: 0..1 per pixel."""
    ndl = {k: np.clip(n @ d, 0.0, 1.0) for k, d in rig.items()}
    ndv = np.clip(np.einsum("ij,ij->i", n, v), 0.0, 1.0)
    up = n @ world_up
    # sky above, plus a soft bounce off the studio floor so undersides never go black
    ambient = 0.28 + 0.08 * up + 0.24 * np.clip(-up, 0.0, 1.0)
    rim = ndl["rim"] * (1.0 - ndv) ** 2
    diffuse = ambient + 0.68 * ndl["key"] + 0.22 * ndl["fill"] + 0.55 * rim
    r = np.clip(rough, 0.04, 1.0)
    shin = 4.0 + 260.0 * (1.0 - r) ** 3
    h = v + rig["key"]
    h = h / np.maximum(np.linalg.norm(h, axis=1), 1e-12)[:, None]
    nh = np.clip(np.einsum("ij,ij->i", n, h), 0.0, 1.0)
    spec_k = (0.03 + 0.55 * (1.0 - r) ** 2) * ndl["key"] ** 0.5
    spec = spec_k * nh ** shin
    # a soft studio horizon in the reflection: metals take their look from it
    refl = 2.0 * ndv[:, None] * n - v
    sky = 0.30 + 0.70 * np.clip((refl @ world_up + 0.25) / 0.85, 0.0, 1.0) ** 1.2
    fres = 0.04 + 0.96 * (1.0 - ndv) ** 5
    m = metal[:, None]
    dielectric = base * diffuse[:, None] + (spec + 0.35 * fres * (1.0 - r) * sky)[:, None]
    metallic = (base * (0.25 * diffuse[:, None]
                        + 0.85 * sky[:, None] * (0.6 + 0.4 * ndl["key"][:, None]))
                + (spec * 1.6)[:, None] * (0.4 + 0.6 * base))
    return np.clip((1.0 - m) * dielectric + m * metallic, 0.0, 1.0)


def lambert(n: np.ndarray, rig: dict[str, np.ndarray]) -> np.ndarray:
    """0..1 diffuse key + fill (for toon bands)."""
    return np.clip(0.8 * np.clip(n @ rig["key"], 0, 1) + 0.3 * np.clip(n @ rig["fill"], 0, 1),
                   0.0, 1.0)


def zebra(n: np.ndarray, v: np.ndarray, right: np.ndarray, up: np.ndarray,
          stripes: float = 15.0) -> np.ndarray:
    """0..1 zebra value: 0.5 + 0.5 sign(sin(k r.d)) of the view-reflected normal r = 2(n.v)n - v
    against a fixed stripe direction d in camera space (a striped light tunnel, as CAD zebra
    analysis does; reflecting doubles the angular sensitivity of plain n.d, so gently curved
    panels still carry several stripes). A stripe that bends smoothly across a join is G2, one
    with a kink is G1, one that jumps is G0."""
    r = 2.0 * np.einsum("ij,ij->i", n, v)[:, None] * n - v
    d = _unit(0.45 * right + 1.0 * up)
    return 0.5 + 0.5 * np.sign(np.sin(stripes * (r @ d)))


# ---- 2D helpers (images are float32 HxW unless noted)

def dilate(mask: np.ndarray, r: float) -> np.ndarray:
    """Boolean dilation by a disc of radius r px."""
    ri = int(np.floor(r))
    if ri <= 0:
        return mask.copy()
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-ri, ri + 1):
        for dx in range(-ri, ri + 1):
            if (dx or dy) and dx * dx + dy * dy <= r * r + 1e-9:
                ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else \
                    (slice(-dy, h), slice(0, h + dy))
                xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else \
                    (slice(-dx, w), slice(0, w + dx))
                out[yd, xd] |= mask[ys, xs]
    return out


def _box1(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Approximately Gaussian blur (three box passes per axis)."""
    if sigma <= 0.3:
        return a.astype(np.float32)
    r = max(1, int(round(np.sqrt(12.0 * sigma * sigma / 3.0 + 1.0) / 2.0)))
    out = a.astype(np.float32)
    for _ in range(3):
        out = _box1(_box1(out, r, 0), r, 1)
    return out


def tent(a: np.ndarray) -> np.ndarray:
    """3x3 [1 2 1]/4 separable smoothing (edge-replicated): a cheap anti-alias filter."""
    a = a.astype(np.float32)
    p = np.pad(a, 1, mode="edge")
    r = 0.25 * p[:, :-2] + 0.5 * p[:, 1:-1] + 0.25 * p[:, 2:]
    return 0.25 * r[:-2] + 0.5 * r[1:-1] + 0.25 * r[2:]


def soft(mask: np.ndarray, width: float) -> np.ndarray:
    """A boolean line mask as anti-aliased ink 0..1 of about `width` px."""
    if not mask.any():
        return np.zeros(mask.shape, dtype=np.float32)
    core = dilate(mask, max(0.0, width / 2.0 - 0.5))
    return np.clip(tent(core) * 1.25, 0.0, 1.0)


def noise_field(shape: tuple[int, int], cell: float, seed: int) -> np.ndarray:
    """Smooth deterministic noise -1..1 with features about `cell` px apart."""
    from PIL import Image
    h, w = shape
    gh, gw = max(2, int(np.ceil(h / cell)) + 2), max(2, int(np.ceil(w / cell)) + 2)
    rng = np.random.default_rng(seed)
    coarse = rng.uniform(-1.0, 1.0, (gh, gw)).astype(np.float32)
    im = Image.fromarray(coarse, "F").resize((int((gw - 1) * cell), int((gh - 1) * cell)),
                                            Image.Resampling.BICUBIC)
    arr = np.asarray(im, dtype=np.float32)[:h, :w]
    if arr.shape != (h, w):
        arr = np.pad(arr, ((0, h - arr.shape[0]), (0, w - arr.shape[1])), mode="edge")
    return np.clip(arr * 1.3, -1.0, 1.0)


_BAND = 256                   # image rows per batch in warp (bounds its temporaries)


def warp(img: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """img sampled at (x + dx, y + dy), bilinear, zero outside. Row bands bound memory."""
    h, w = img.shape
    out = np.empty((h, w), dtype=np.float32)
    xx = np.arange(w, dtype=np.float32)[None, :]
    for r0 in range(0, h, _BAND):
        r1 = min(h, r0 + _BAND)
        yy = np.arange(r0, r1, dtype=np.float32)[:, None]
        out[r0:r1] = bilinear(img, xx + dx[r0:r1] + 0.5, yy + dy[r0:r1] + 0.5)
    return out


def sketch_ink(lines: np.ndarray, px: float, seed: int = 7) -> np.ndarray:
    """Hand-drawn ink 0..1 from soft line ink: each line displaced along its normal by
    low-frequency noise (amplitude ~1 px, wavelength ~40 px at the output scale `px` internal
    px per output px) and drawn twice with a slight offset (over-sketch)."""
    if not lines.any():
        return np.zeros(lines.shape, dtype=np.float32)
    shape = lines.shape
    amp, cell = 1.1 * px, 40.0 * px
    a = warp(lines, amp * noise_field(shape, cell, seed), amp * noise_field(shape, cell, seed + 1))
    a *= -0.85
    a += 1.0
    b = warp(lines, amp * noise_field(shape, cell * 0.8, seed + 2) + 0.6 * px,
             amp * noise_field(shape, cell * 0.8, seed + 3) - 0.45 * px)
    b *= -0.55
    b += 1.0
    a *= b
    return 1.0 - a


def bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """img at float pixel coords (pixel centres at i + 0.5), zero outside."""
    h, w = img.shape
    sx, sy = x - 0.5, y - 0.5
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = sx - x0, sy - y0
    pad = np.pad(img, 1)
    def at(yy, xx):
        return pad[np.clip(yy + 1, 0, h + 1), np.clip(xx + 1, 0, w + 1)]
    return ((1 - fy) * ((1 - fx) * at(y0, x0) + fx * at(y0, x0 + 1))
            + fy * ((1 - fx) * at(y0 + 1, x0) + fx * at(y0 + 1, x0 + 1))).astype(np.float32)
