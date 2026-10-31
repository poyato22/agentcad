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
from dataclasses import dataclass, replace

import numpy as np

try:                                   # inside the package, or as the worker's sibling module
    from . import render_shade as shade
except ImportError:                    # pragma: no cover - the sandboxed worker
    import render_shade as shade  # type: ignore[no-redef]

# The four standard views: a unit "forward" (the direction the camera looks, from eye to the
# model) and an "up". Matches the third-angle projection directions used by the 2D drawing.
VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),    # look +Y, Z up
    "top": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),     # look down -Z, Y up
    "side": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),    # look -X (from +X), Z up
    "iso": ((-1.0, 1.0, -1.0), (0.0, 0.0, 1.0)),    # three-quarter
}
DEFAULT_VIEWS = ("front", "top", "side", "iso")
# Where each standard view's camera sits, by axis (Z up): "front" looks along +Y from the -Y
# side, "side" looks along -X from the +X side. Shown next to the view names so a model whose
# nose points along X is not misread ("front" is then a side-on view).
VIEW_AXES = {"front": "camera on -Y, looking +Y", "top": "camera on +Z, looking down -Z",
             "side": "camera on +X, looking -X",
             "iso": "camera at (+X, -Y, +Z), looking at the centre"}

# Lighting: a key light from the camera's side (slightly above and to the right of the eye, in
# camera space) so whatever a view looks at is lit, whichever way the model faces; plus a fixed
# world fill from above-front so form still reads, and an ambient floor. Double-sided Lambert.
_KEY_DIR = (-1.0, 0.30, 0.45)            # (along forward, along right, along up)
_KEY = 0.55
_FILL_DIR = np.array([0.3, -0.5, 0.8])
_FILL_DIR = _FILL_DIR / np.linalg.norm(_FILL_DIR)
_FILL = 0.25
_AMBIENT = 0.30
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
    # CMF (appearance): 0..1 roughness and metalness drive the specular and reflection of the
    # shaded style; the defaults are the drafting look (matte plastic)
    roughness: float = 0.9
    metalness: float = 0.0
    role: str = ""                     # "figure": the sketch's human-scale figure

    def __post_init__(self) -> None:
        self.tris = np.asarray(self.tris, dtype=np.float64).reshape(-1, 3, 3)

    def centroid(self) -> np.ndarray:
        if len(self.tris) == 0:
            return np.zeros(3)
        return self.tris.reshape(-1, 3).mean(axis=0)


# ---- mesh loading (binary or ASCII STL)
#
# The STL is written by the sandboxed build, so it is attacker-shaped input: its size, its
# triangle count and its coordinates are all bounded here before anything is rasterised.

MAX_STL_BYTES = 50 * 1024 * 1024
MAX_TRIANGLES = 2_000_000
# a vertex further than this from the origin, or more than OUTLIER_FACTOR x the 90th percentile
# distance from the mesh's median, is an outlier: it would set the framing and shrink the model
# to a dot (a long legitimate spar is still well inside 50x the 90th percentile)
MAX_COORD_MM = 1e6
OUTLIER_FACTOR = 50.0


class RenderError(ValueError):
    """The mesh cannot be rendered within the renderer's bounds (too big, too complex, empty)."""


def load_stl(data: bytes) -> np.ndarray:
    """Triangles (N, 3, 3) from STL bytes. Binary and ASCII are both accepted; the real build
    worker writes binary STL, but a hand-written ASCII solid loads too. Refuses files over
    MAX_STL_BYTES or MAX_TRIANGLES (RenderError)."""
    if len(data) > MAX_STL_BYTES:
        raise RenderError(f"the mesh is {len(data) / 2**20:.0f} MB, over the "
                          f"{MAX_STL_BYTES // 2**20} MB render limit")
    if _looks_ascii_stl(data):
        tris = _load_ascii_stl(data)
        if len(tris):
            return tris
    return _load_binary_stl(data)


def clean_triangles(tris: np.ndarray) -> tuple[np.ndarray, int]:
    """(triangles with every non-finite or far-outlier vertex dropped, how many were dropped).
    One NaN or 1e38 vertex would otherwise blank the whole render silently."""
    tris = np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)
    if len(tris) == 0:
        return tris, 0
    # chunked, and no copy when nothing is dropped: the mesh may be MAX_TRIANGLES big and the
    # worker's memory budget is fixed
    step = 250_000
    with np.errstate(invalid="ignore", over="ignore"):
        ok = np.empty(len(tris), dtype=bool)
        for i in range(0, len(tris), step):
            t = tris[i:i + step]
            ok[i:i + step] = (np.isfinite(t).all(axis=(1, 2))
                              & (np.abs(t) < MAX_COORD_MM).all(axis=(1, 2)))
        kept = tris if ok.all() else tris[ok]
        if len(kept):
            center = np.array([np.median(kept[:, :, ax]) for ax in range(3)])
            dist = np.empty(len(kept))
            for i in range(0, len(kept), step):
                d = kept[i:i + step] - center
                dist[i:i + step] = np.sqrt((d * d).sum(axis=2)).max(axis=1)
            limit = max(float(np.percentile(dist, 90)) * OUTLIER_FACTOR, 1e-6)
            keep = dist <= limit
            if not keep.all():
                kept = kept[keep]
    return kept, int(len(tris) - len(kept))


def _looks_ascii_stl(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"solid") and b"facet" in data[:2048].lower()


def _load_binary_stl(data: bytes) -> np.ndarray:
    if len(data) < 84:
        raise ValueError("STL too short for a binary header")
    (n,) = struct.unpack_from("<I", data, 80)
    if n > MAX_TRIANGLES:
        raise RenderError(f"the mesh has {n} triangles, over the {MAX_TRIANGLES} render limit")
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
            try:
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
            except ValueError:
                continue
            if len(verts) > 3 * MAX_TRIANGLES:
                raise RenderError(f"the mesh has over {MAX_TRIANGLES} triangles, over the "
                                  "render limit")
    if len(verts) < 3 or len(verts) % 3 != 0:
        return np.empty((0, 3, 3))
    return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)


# ---- geometry helpers

def bbox(meshes: list[Mesh]) -> tuple[np.ndarray, np.ndarray]:
    """(min, max) corner over every mesh's vertices (per mesh: no concatenated copy)."""
    live = [m.tris.reshape(-1, 3) for m in meshes if len(m.tris)]
    if not live:
        return np.zeros(3), np.ones(3)
    return (np.min([p.min(axis=0) for p in live], axis=0),
            np.max([p.max(axis=0) for p in live], axis=0))


def _camera(view: str, cameras: dict | None = None):
    """(forward, up) of a named view: a standard view, or one from `cameras` (name -> (forward,
    up), finite 3-vectors; e.g. aircraft-named views computed by the caller)."""
    if cameras and view in cameras:
        fwd, up = cameras[view]
        f, u = np.asarray(fwd, dtype=np.float64), np.asarray(up, dtype=np.float64)
        if (f.shape != (3,) or u.shape != (3,) or not np.isfinite(f).all()
                or not np.isfinite(u).all() or np.linalg.norm(f) < 1e-9):
            raise ValueError(f"camera {view!r} needs finite 3-vectors forward and up")
        return tuple(f), tuple(u)
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; known: {', '.join(VIEWS)}")
    return VIEWS[view]


def key_light(f: np.ndarray, right: np.ndarray, up: np.ndarray) -> np.ndarray:
    """The key light direction (unit, toward the light) for a camera basis."""
    a, b, c = _KEY_DIR
    v = a * f + b * right + c * up
    return v / np.linalg.norm(v)


def _camera_basis(view: str, cameras: dict | None = None
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fwd_raw, up_raw = _camera(view, cameras)
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
        out.append(replace(m, tris=m.tris + shift))
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


# ---- rasterization (vectorised, streaming)
#
# Every triangle of every mesh is projected at once. Small triangles are rasterised in batches
# that test the candidate pixel centres of each triangle's (power-of-two) bounding box with
# barycentric coordinates; triangles with a large bounding box go through a vectorised
# scanline path instead (a planar face's long thin slivers would waste nearly all of a box
# test). Fragments are produced in bounded batches and folded into a running z-buffer batch by
# batch, so memory never depends on how many layers a hostile or dense mesh stacks per pixel;
# x-ray composites with weighted-blended order-independent transparency, also batch by batch.
# The total work is bounded up front by MAX_LAYERS (estimated covered area / frame area).

_FRAG_CHUNK = 250_000                         # fragments (or candidates) per batch (~50 MB)
# Triangles whose pixel bbox is wider/taller than this go through the scanline path.
_BOX_MAX = 8
_SCAN_ROWS = 250_000                          # (triangle, row) pairs per scanline batch
# Average layers per pixel a view may cost (sum of projected triangle areas / frame area). A
# closed solid is ~2; a dense assembly seen through its stack maybe 10-20. A stack of thousands
# of full-frame triangles (the hostile case) is refused before any rasterising.
MAX_LAYERS = 64


# 35 mm-equivalent lens across the frame width (36 mm film): the perspective of a product shot
_HALF_FOV = float(np.arctan(18.0 / 35.0))


@dataclass
class _Cam:
    """A view's camera on a size x size frame. Orthographic: screen coordinates are distances
    along right/up from the framing centre. Perspective: they are right/up over depth from an
    eye placed so the framing's bounding sphere fills a 35 mm-equivalent field of view. Either
    way pixel = size/2 + (screen - screen_centre) * k, y down."""
    f: np.ndarray
    right: np.ndarray
    up: np.ndarray
    persp: bool
    center: np.ndarray
    eye: np.ndarray
    k: float
    xs0: float
    ys0: float
    size: int
    reach: float                               # framing diagonal (mm)

    def screen(self, pts: np.ndarray):
        """(px, py, linear depth along f) of world points (..., 3)."""
        if self.persp:
            d = pts - self.eye
            z = d @ self.f
            zc = np.maximum(z, 1e-9)
            xs, ys = (d @ self.right) / zc, (d @ self.up) / zc
        else:
            d = pts - self.center
            z = d @ self.f
            xs, ys = d @ self.right, d @ self.up
        h = self.size / 2.0
        return h + (xs - self.xs0) * self.k, h - (ys - self.ys0) * self.k, z

    def rays(self, gx: np.ndarray, gy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(origins, unit directions) of the rays through pixel coordinates (gx, gy)."""
        h = self.size / 2.0
        xs = (gx - h) / self.k + self.xs0
        ys = (h - gy) / self.k + self.ys0
        if self.persp:
            d = self.f[None, :] + xs[:, None] * self.right + ys[:, None] * self.up
            d = d / np.linalg.norm(d, axis=1)[:, None]
            return np.broadcast_to(self.eye, d.shape), d
        o = (self.center + xs[:, None] * self.right + ys[:, None] * self.up
             - 4.0 * self.reach * self.f)
        return o, np.broadcast_to(self.f, o.shape)

    def scaled(self, size: int) -> "_Cam":
        """The same camera on a size x size frame."""
        return replace(self, size=size, k=self.k * size / self.size)


def _make_cam(view: str, size: int, framing, cameras: dict | None = None,
              perspective: bool = False, fill: float = 0.90) -> _Cam:
    f, right, up = _camera_basis(view, cameras)
    lo, hi = (np.asarray(framing[0], dtype=np.float64), np.asarray(framing[1], dtype=np.float64))
    center = (lo + hi) / 2.0
    reach = float(np.linalg.norm(hi - lo)) or 1e-6
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                        for z in (lo[2], hi[2])])
    eye = center - (reach / 2.0) / np.sin(_HALF_FOV) * f
    if perspective:
        d = corners - eye
        z = d @ f
        xs, ys = (d @ right) / z, (d @ up) / z
    else:
        xs, ys = (corners - center) @ right, (corners - center) @ up
    xs0, ys0 = (xs.max() + xs.min()) / 2.0, (ys.max() + ys.min()) / 2.0
    half = max(xs.max() - xs.min(), ys.max() - ys.min(), 1e-9) / 2.0
    return _Cam(f, right, up, bool(perspective), center, eye, (size / 2.0) * fill / half,
                float(xs0), float(ys0), int(size), reach)


@dataclass
class _Frame:
    px: np.ndarray                            # (N, 3) pixel x of each vertex
    py: np.ndarray                            # (N, 3) pixel y (grows downward)
    pz: np.ndarray                            # (N, 3) raster depth (bigger = farther)
    shade: np.ndarray                         # (N, 3) rgb 0..1 per triangle (flat)
    alpha: np.ndarray                         # (N,) opacity per triangle
    tris: np.ndarray | None = None            # (N, 3, 3) world triangles
    mesh_of: np.ndarray | None = None         # (N,) index into the live meshes
    zlin: np.ndarray | None = None            # (N, 3) linear camera depth (mm)
    live: list | None = None                  # the meshes with triangles, in order
    face_unit: np.ndarray | None = None       # (N, 3) unit face normals (float32)
    starts: np.ndarray | None = None          # lite frames: first global triangle of each mesh

    def rows(self, t: np.ndarray) -> np.ndarray:
        """World triangles (M, 3, 3) of global triangle indices `t`, gathered from the meshes
        (a lite frame keeps no concatenated copy)."""
        if self.starts is None:
            return self.tris[t]
        out = np.empty((len(t), 3, 3))
        which = np.searchsorted(self.starts, t, side="right") - 1
        for i in np.unique(which):
            sel = which == i
            out[sel] = self.live[i].tris[t[sel] - self.starts[i]]
        return out

    def count(self) -> int:
        return len(self.mesh_of) if self.mesh_of is not None else 0


def _project(meshes: list[Mesh], cam: _Cam, flat: bool = False) -> _Frame:
    """Project the meshes for `cam`. Per-triangle data stays lean (it scales with up to
    MAX_TRIANGLES): face normals float32, mesh ids int32, the linear depth shares the z-buffer
    depth when orthographic, and the flat per-triangle shade (x-ray only) only when asked."""
    live = [m for m in meshes if len(m.tris)]
    if not live:
        z = np.zeros((0, 3))
        return _Frame(z, z, z, z, np.zeros(0), np.zeros((0, 3, 3)), np.zeros(0, np.int32), z,
                      [], np.zeros((0, 3), dtype=np.float32))
    tris = live[0].tris if len(live) == 1 else np.concatenate([m.tris for m in live], axis=0)
    alpha = np.concatenate([np.full(len(m.tris), float(m.opacity), dtype=np.float32)
                            for m in live])
    mesh_of = np.concatenate([np.full(len(m.tris), i, dtype=np.int32)
                              for i, m in enumerate(live)])
    px, py, zl = cam.screen(tris)
    # perspective: 1/z interpolates linearly in screen space, so the z-buffer stores -1/z
    pz = -1.0 / np.maximum(zl, 1e-9) if cam.persp else zl
    unit = shade.unit_normals(tris)
    shaded = np.zeros((0, 3))
    if flat:
        color = np.concatenate([np.broadcast_to(np.asarray(m.color, dtype=np.float32),
                                                (len(m.tris), 3)) for m in live], axis=0)
        key = np.abs(unit @ key_light(cam.f, cam.right, cam.up).astype(np.float32))
        fill = np.abs(unit @ _FILL_DIR.astype(np.float32))
        lit = (np.abs(unit).sum(axis=1) > 0)                   # degenerate: ambient only
        intensity = np.where(lit, _AMBIENT + _KEY * key + _FILL * fill, _AMBIENT)
        shaded = np.clip(color * intensity[:, None], 0.0, 1.0)
    return _Frame(px, py, pz, shaded, alpha, tris, mesh_of, zl, live, unit)


def _setup(fr: _Frame, size: int):
    px, py = fr.px, fr.py
    x0 = np.maximum(np.ceil(px.min(axis=1) - 0.5), 0).astype(np.int64)
    x1 = np.minimum(np.floor(px.max(axis=1) - 0.5), size - 1).astype(np.int64)
    y0 = np.maximum(np.ceil(py.min(axis=1) - 0.5), 0).astype(np.int64)
    y1 = np.minimum(np.floor(py.max(axis=1) - 0.5), size - 1).astype(np.int64)
    denom = ((py[:, 1] - py[:, 2]) * (px[:, 0] - px[:, 2])
             + (px[:, 2] - px[:, 1]) * (py[:, 0] - py[:, 2]))
    live = np.nonzero((x1 >= x0) & (y1 >= y0) & (np.abs(denom) > 1e-12))[0]
    return x0, x1, y0, y1, denom, live


def _check_budget(denom, live, size: int) -> None:
    """Refuse before rasterising when the view would cost more than MAX_LAYERS per pixel."""
    area = 0.5 * float(np.abs(denom[live]).sum())
    layers = area / float(size * size)
    if layers > MAX_LAYERS:
        raise RenderError(f"the view is too complex to render: about {layers:.0f} layers per "
                          f"pixel (limit {MAX_LAYERS})")


def _box_batches(fr: _Frame, ids, x0, x1, y0, y1, denom, size: int):
    """Fragments (pix, depth, tri) of small triangles, in bounded batches."""
    px, py, pz = fr.px, fr.py, fr.pz
    ext = np.maximum(x1[ids] - x0[ids] + 1, y1[ids] - y0[ids] + 1)
    bucket = np.ceil(np.log2(ext)).astype(np.int64)
    for b in np.unique(bucket):
        k = 1 << int(b)
        sel = ids[bucket == b]
        oy, ox = np.divmod(np.arange(k * k, dtype=np.int64), k)
        step = max(1, _FRAG_CHUNK // (k * k))
        for s in range(0, len(sel), step):
            t = sel[s:s + step]
            X = x0[t, None] + ox[None, :]
            Y = y0[t, None] + oy[None, :]
            gx, gy = X + 0.5, Y + 0.5
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
            yield (Y[rows, cols] * size + X[rows, cols],
                   a[rows, cols] * z[:, 0] + bb[rows, cols] * z[:, 1] + c[rows, cols] * z[:, 2],
                   t[rows])


def _scan_batches(fr: _Frame, ids, x0, x1, y0, y1, denom, size: int):
    """Scanline fragments of large triangles: for every (triangle, pixel row) the row centre's
    crossing with the three edges gives an x span, and only the pixels in it are generated, so
    the work is the triangle's area, not its bounding box. Bounded batches."""
    px, py, pz = fr.px, fr.py, fr.pz
    h = y1[ids] - y0[ids] + 1
    cum = np.cumsum(h)
    cuts = np.searchsorted(cum, np.arange(0, cum[-1], _SCAN_ROWS), side="right")
    bounds = sorted(set([0, *cuts.tolist(), len(ids)]))
    for lo_i, hi_i in zip(bounds[:-1], bounds[1:]):
        t_ids, hh = ids[lo_i:hi_i], h[lo_i:hi_i]
        rep = np.repeat(np.arange(len(t_ids)), hh)
        tri = t_ids[rep]
        ry = y0[tri] + (np.arange(len(rep)) - np.repeat(np.cumsum(hh) - hh, hh))
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
        cx0 = np.maximum(np.where(good, np.ceil(xl - 0.5), 0).astype(np.int64), x0[tri])
        cx1 = np.minimum(np.where(good, np.floor(xr - 0.5), -1).astype(np.int64), x1[tri])
        n = np.maximum(cx1 - cx0 + 1, 0)
        # expand the spans to pixels in sub-batches of at most _FRAG_CHUNK pixels
        ncum = np.cumsum(n)
        if ncum[-1] == 0:
            continue
        rcuts = np.searchsorted(ncum, np.arange(0, ncum[-1], _FRAG_CHUNK), side="right")
        rb = sorted(set([0, *rcuts.tolist(), len(n)]))
        for r0, r1 in zip(rb[:-1], rb[1:]):
            nn = n[r0:r1]
            if nn.sum() == 0:
                continue
            rep2 = r0 + np.repeat(np.arange(r1 - r0), nn)
            X = cx0[rep2] + (np.arange(len(rep2)) - np.repeat(np.cumsum(nn) - nn, nn))
            Y = ry[rep2]
            t = tri[rep2]
            gx, gy = X + 0.5, Y + 0.5
            tx, ty, dn = px[t], py[t], denom[t]
            a = ((ty[:, 1] - ty[:, 2]) * (gx - tx[:, 2])
                 + (tx[:, 2] - tx[:, 1]) * (gy - ty[:, 2])) / dn
            b = ((ty[:, 2] - ty[:, 0]) * (gx - tx[:, 2])
                 + (tx[:, 0] - tx[:, 2]) * (gy - ty[:, 2])) / dn
            z = pz[t]
            yield Y * size + X, a * z[:, 0] + b * z[:, 1] + (1.0 - a - b) * z[:, 2], t


def _fragment_batches(fr: _Frame, size: int):
    """Every covered pixel centre of every triangle, as bounded (pix, depth, tri) batches."""
    if len(fr.px) == 0:
        return
    x0, x1, y0, y1, denom, live = _setup(fr, size)
    if len(live) == 0:
        return
    _check_budget(denom, live, size)
    ext = np.maximum(x1[live] - x0[live] + 1, y1[live] - y0[live] + 1)
    big, small = live[ext > _BOX_MAX], live[ext <= _BOX_MAX]
    if len(small):
        yield from _box_batches(fr, small, x0, x1, y0, y1, denom, size)
    if len(big):
        yield from _scan_batches(fr, big, x0, x1, y0, y1, denom, size)


def _nearest_per_pixel(pix, dep):
    """Index of the nearest fragment of each distinct pixel in one batch."""
    dmin = float(dep.min())
    span = (float(dep.max()) - dmin) or 1.0
    order = np.argsort(pix + (dep - dmin) / (span * 1.000001))
    pix_s = pix[order]
    first = np.ones(len(pix_s), dtype=bool)
    first[1:] = pix_s[1:] != pix_s[:-1]
    return order[first]


def _raster(fr: _Frame, size: int, transparent: bool):
    """(rgb float HxWx3 0..1, mask bool HxW, zbuf HxW) for one projected frame, streaming."""
    n_pix = size * size
    bg = np.array(_BG, dtype=np.float64) / 255.0
    zbuf = np.full(n_pix, np.inf)
    tri_at = np.full(n_pix, -1, dtype=np.int64)
    if transparent:
        acc = np.zeros((n_pix, 3))
        acc_a = np.zeros(n_pix)
        log_t = np.zeros(n_pix)
    for pix, dep, tri in _fragment_batches(fr, size):
        win = _nearest_per_pixel(pix, dep)
        p, d = pix[win], dep[win]
        closer = d < zbuf[p]
        zbuf[p[closer]] = d[closer]
        tri_at[p[closer]] = tri[win][closer]
        if transparent:
            # weighted-blended OIT (McGuire & Bavoil): order-independent, so it streams
            a = np.minimum(fr.alpha[tri], 0.999)
            for ch in range(3):
                acc[:, ch] += np.bincount(pix, weights=a * fr.shade[tri, ch], minlength=n_pix)
            acc_a += np.bincount(pix, weights=a, minlength=n_pix)
            log_t += np.bincount(pix, weights=np.log1p(-a), minlength=n_pix)
    mask = tri_at >= 0
    img = np.tile(bg, (n_pix, 1))
    if transparent:
        t = np.exp(log_t)
        avg = acc / np.maximum(acc_a, 1e-12)[:, None]
        img = avg * (1.0 - t)[:, None] + bg[None, :] * t[:, None]
    else:
        img[mask] = fr.shade[tri_at[mask]]
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
    by more than `thresh` from a foreground neighbour AND by more than `thresh` beyond the
    surface's own slope on either side. This is what makes an internal feature (e.g. a cavity
    revealed by a section) show up under flat shading, while a smooth surface seen at a grazing
    angle (a thin wing head-on: its depth changes fast but steadily) is not inked solid."""
    z = np.where(mask, zbuf, np.nan)
    edge = np.zeros_like(mask)
    with np.errstate(invalid="ignore"):
        for axis in (0, 1):
            d = np.diff(z, axis=axis)                 # the step between neighbouring pixels
            pad = np.full_like(np.take(d, [0], axis=axis), np.nan)
            n = d.shape[axis]
            prev = np.concatenate([pad, np.take(d, range(n - 1), axis=axis)], axis=axis)
            nxt = np.concatenate([np.take(d, range(1, n), axis=axis), pad], axis=axis)
            dev = np.fmin(np.abs(d - prev), np.abs(d - nxt))     # NaN only if neither exists
            dev = np.where(np.isnan(prev) & np.isnan(nxt), np.inf, dev)
            jump = (np.abs(d) > thresh) & (dev > thresh)          # NaN compares False
            if axis == 0:
                edge[1:, :] |= jump
                edge[:-1, :] |= jump
            else:
                edge[:, 1:] |= jump
                edge[:, :-1] |= jump
    return edge & mask


_RASTER_CHUNK = 250_000                    # triangles projected at once by _coverage


def _chunks(meshes: list[Mesh]):
    """(global index of the first triangle, triangles) slices of at most _RASTER_CHUNK
    triangles, over the meshes with triangles in _project's order."""
    at = 0
    for m in meshes:
        if not len(m.tris):
            continue
        for s in range(0, len(m.tris), _RASTER_CHUNK):
            yield at + s, m.tris[s:s + _RASTER_CHUNK]
        at += len(m.tris)


def _screen_frame(cam: _Cam, tris: np.ndarray) -> _Frame:
    px, py, zl = cam.screen(tris)
    pz = -1.0 / np.maximum(zl, 1e-9) if cam.persp else zl
    return _Frame(px, py, pz, np.zeros((0, 3)), np.zeros(0))


def _projected_layers(meshes: list[Mesh], cam: _Cam) -> float:
    """Average triangles per pixel (projected area / frame area), computed chunk by chunk."""
    area = 0.0
    for _at, tris in _chunks(meshes):
        px, py, _z = cam.screen(tris)
        area += 0.5 * float(np.abs((px[:, 1] - px[:, 0]) * (py[:, 2] - py[:, 0])
                                   - (px[:, 2] - px[:, 0]) * (py[:, 1] - py[:, 0])).sum())
    return area / float(cam.size * cam.size)


def _coverage(meshes: list[Mesh], cam: _Cam) -> tuple[np.ndarray, np.ndarray]:
    """The opaque z-buffer of `meshes` for `cam`: (triangle index per pixel in _project's
    order, -1 for background; raster depth), flat arrays. Triangles are projected and
    rasterised _RASTER_CHUNK at a time, so the per-triangle screen arrays never exist for the
    whole mesh at once; the MAX_LAYERS budget is checked over all chunks before any work."""
    size = cam.size
    layers = _projected_layers(meshes, cam)
    if layers > MAX_LAYERS:
        raise RenderError(f"the view is too complex to render: about {layers:.0f} layers per "
                          f"pixel (limit {MAX_LAYERS})")
    zbuf = np.full(size * size, np.inf, dtype=np.float32)
    tri_at = np.full(size * size, -1, dtype=np.int32)          # MAX_TRIANGLES fits int32
    for at, tris in _chunks(meshes):
        fr = _screen_frame(cam, tris)
        for pix, dep, tri in _fragment_batches(fr, size):
            win = _nearest_per_pixel(pix, dep)
            p, d = pix[win], dep[win]
            closer = d < zbuf[p]
            zbuf[p[closer]] = d[closer]
            tri_at[p[closer]] = tri[win][closer] + at
    return tri_at, zbuf


def _lite_frame(meshes: list[Mesh]) -> _Frame:
    """What per-pixel shading needs per triangle (world triangles, mesh ids, float32 face
    normals) without any screen arrays: those are recomputed for the visible triangles."""
    live = [m for m in meshes if len(m.tris)]
    if not live:
        return _Frame(*([np.zeros((0, 3))] * 4), np.zeros(0), np.zeros((0, 3, 3)),
                      np.zeros(0, np.int32), None, [], np.zeros((0, 3), dtype=np.float32))
    mesh_of = np.concatenate([np.full(len(m.tris), i, dtype=np.int32)
                              for i, m in enumerate(live)])
    unit = np.concatenate([shade.unit_normals(m.tris) for m in live], axis=0)
    starts = np.cumsum([0] + [len(m.tris) for m in live[:-1]])
    z = np.zeros((0, 3))
    return _Frame(z, z, z, z, np.zeros(0), None, mesh_of, None, live, unit, starts)


def _zbuffer(fr: _Frame, size: int) -> tuple[np.ndarray, np.ndarray]:
    """(triangle index per pixel, -1 for background; raster depth per pixel), flat arrays."""
    zbuf = np.full(size * size, np.inf)
    tri_at = np.full(size * size, -1, dtype=np.int64)
    for pix, dep, tri in _fragment_batches(fr, size):
        win = _nearest_per_pixel(pix, dep)
        p, d = pix[win], dep[win]
        closer = d < zbuf[p]
        zbuf[p[closer]] = d[closer]
        tri_at[p[closer]] = tri[win][closer]
    return tri_at, zbuf


# ---- styles (Plan 9 G-R): shaded (smooth, CMF, studio light, ground shadow), zebra, sketch
# (NPR line art on paper), silhouette (black fill). Opaque styles are supersampled 2x up to a
# 2048 px internal raster and box-filtered down, so edges are anti-aliased.

STYLES = ("shaded", "zebra", "sketch", "silhouette")
BACKGROUNDS = ("flat", "transparent")
MAX_SIZE = 2048
MAX_PNG_BYTES = 1024 * 1024 - 1          # every PNG stays under 1 MB (model image input)
_PAPER = (250, 249, 245)
_INK = (36, 38, 44)
_FIGURE_INK = 0.6                        # the scale figure is drawn lighter than the design
_LINE_CREASE_DEG = 35.0                   # normal step between neighbouring pixels: a line


_SS_MAX_LAYERS = 12                       # deeper stacks render without supersampling


def _supersample(size: int) -> int:
    return 2 if size * 2 <= MAX_SIZE else 1


def _pixel_attrs(fr: _Frame, cam: _Cam, tri_at: np.ndarray, pix: np.ndarray):
    """For the covered pixels `pix` (flat indices): (triangle, perspective-correct barycentric
    weights (M, 3) float32, linear camera depth (M,)). Screen coordinates are recomputed for
    just these triangles."""
    S = cam.size
    t = tri_at[pix]
    gy, gx = np.divmod(pix, S)
    gx = gx + 0.5
    gy = gy + 0.5
    tx, ty, tz = cam.screen(fr.rows(t))
    dn = ((ty[:, 1] - ty[:, 2]) * (tx[:, 0] - tx[:, 2])
          + (tx[:, 2] - tx[:, 1]) * (ty[:, 0] - ty[:, 2]))
    dn = np.where(np.abs(dn) > 1e-12, dn, 1e-12)
    a = ((ty[:, 1] - ty[:, 2]) * (gx - tx[:, 2]) + (tx[:, 2] - tx[:, 1]) * (gy - ty[:, 2])) / dn
    b = ((ty[:, 2] - ty[:, 0]) * (gx - tx[:, 2]) + (tx[:, 0] - tx[:, 2]) * (gy - ty[:, 2])) / dn
    w = np.clip(np.stack([a, b, 1.0 - a - b], axis=1), 0.0, 1.0)
    if cam.persp:
        w = w / np.maximum(tz, 1e-9)
    w = w / np.maximum(w.sum(axis=1), 1e-12)[:, None]
    return t, w.astype(np.float32), (w * tz).sum(axis=1)


_BAND_ROWS = 256                           # rows per band for full-frame line passes


def _banded(fn, arrays: tuple, halo: int):
    """Apply a per-pixel neighbourhood pass `fn(*arrays) -> bool masks` in row bands with
    `halo` rows of context, so its temporaries scale with a band, not the frame."""
    h = arrays[0].shape[0]
    outs = None
    for r0 in range(0, h, _BAND_ROWS):
        r1 = min(h, r0 + _BAND_ROWS)
        a, b = max(0, r0 - halo), min(h, r1 + halo)
        res = fn(*(x[a:b] for x in arrays))
        res = res if isinstance(res, tuple) else (res,)
        if outs is None:
            outs = [np.zeros((h,) + r.shape[1:], dtype=r.dtype) for r in res]
        for o, r in zip(outs, res):
            o[r0:r1] = r[r0 - a:r0 - a + (r1 - r0)]
    return tuple(outs) if len(outs) > 1 else outs[0]


def _neighbour_lines(img_n: np.ndarray, mid: np.ndarray, mask: np.ndarray, cos_t: float):
    """(part-boundary pixels, crease pixels): neighbouring foreground pixels of different
    meshes, or of one mesh whose normals differ by more than the line angle."""
    part = np.zeros_like(mask)
    crease = np.zeros_like(mask)
    for axis in (0, 1):
        sl0 = (slice(None, -1), slice(None)) if axis == 0 else (slice(None), slice(None, -1))
        sl1 = (slice(1, None), slice(None)) if axis == 0 else (slice(None), slice(1, None))
        both = mask[sl0] & mask[sl1]
        diff = both & (mid[sl0] != mid[sl1])
        part[sl0] |= diff
        dot = (img_n[sl0] * img_n[sl1]).sum(axis=-1)
        crease[sl0] |= both & ~diff & (dot < cos_t)
    return part, crease


def _line_px(size: int) -> float:
    """Output px per 'design px': line widths are specified at 1024 px and scale gently."""
    return max(0.6, (size / 1024.0) ** 0.5)


def _segment_mask(shape, p0, p1) -> np.ndarray:
    """Pixels along the 2D segment p0 -> p1 (pixel coordinates)."""
    h, w = shape
    m = np.zeros(shape, dtype=bool)
    n = int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1]), 1.0) * 2) + 1
    t = np.linspace(0.0, 1.0, n)
    x = np.floor(p0[0] + (p1[0] - p0[0]) * t).astype(np.int64)
    y = np.floor(p0[1] + (p1[1] - p0[1]) * t).astype(np.int64)
    ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    m[y[ok], x[ok]] = True
    return m


def _shadow_light(cam: _Cam) -> np.ndarray:
    """Toward the shadow-casting light: from above, tilted toward the key light's side."""
    key = shade.lights(cam.f, cam.right, cam.up)["key"]
    hz = key - np.array([0.0, 0.0, key[2]])
    hn = np.linalg.norm(hz)
    lt = np.array([0.0, 0.0, 1.0]) + (0.35 * hz / hn if hn > 1e-6 else 0.0)
    return lt / np.linalg.norm(lt)


_SHADOW_MAP = 384
_RAY_CHUNK = 262_144               # ground rays per batch


def _ground_shadow(meshes: list[Mesh], cam: _Cam, framing) -> np.ndarray | None:
    """Soft ground-shadow strength 0..1 per pixel of `cam`'s frame on the plane under the model
    (z = framing min z): a wide penumbra of the whole model plus a darker contact shadow of what
    is near the ground. None when the camera cannot see the ground (side-on views)."""
    casting = [m for m in meshes if len(m.tris) and m.role != "figure"]
    if not casting:
        return None
    lo, hi = (np.asarray(framing[0], dtype=np.float64), np.asarray(framing[1], dtype=np.float64))
    z0 = float(lo[2])
    S = cam.size
    gy, gx = np.divmod(np.arange(S * S, dtype=np.int32), S)
    # can the camera see the ground anywhere? (the frame corners bound every ray)
    _o, dc = cam.rays(np.array([0.0, S, 0.0, S]), np.array([0.0, 0.0, S, S]))
    if not (dc[:, 2] < -1e-4).any():
        return None
    ls = _shadow_light(cam)
    hint = (0.0, 1.0, 0.0) if abs(ls[1]) < 0.9 else (1.0, 0.0, 0.0)
    ext = hi - lo
    margin = 0.6 * float(max(ext[0], ext[1], ext[2]))
    lframe = (lo - margin, hi + margin)
    lcam = _make_cam("__light", _SHADOW_MAP, lframe, {"__light": (tuple(-ls), hint)}, fill=1.0)
    full, _ = _coverage(casting, lcam)
    hc = z0 + 0.04 * float(max(ext[2], 1e-6))
    near = [replace(m, tris=section_mesh(m.tris, (0.0, 0.0, hc), (0.0, 0.0, 1.0)))
            for m in casting]
    contact, _ = _coverage(near, lcam)
    sz = (_SHADOW_MAP, _SHADOW_MAP)
    wide = shade.blur((full >= 0).reshape(sz).astype(np.float32), 0.03 * _SHADOW_MAP)
    tight = shade.blur((contact >= 0).reshape(sz).astype(np.float32), 0.008 * _SHADOW_MAP)
    occl = 1.0 - (1.0 - 0.30 * wide) * (1.0 - 0.60 * tight)
    out = np.zeros(S * S, dtype=np.float32)
    for r0 in range(0, S * S, _RAY_CHUNK):
        idx = np.arange(r0, min(S * S, r0 + _RAY_CHUNK))
        o, d = cam.rays(gx[idx] + 0.5, gy[idx] + 0.5)
        down = d[:, 2] < -1e-4
        t = (z0 - o[down, 2]) / d[down, 2]
        ahead = t > 0
        sel = idx[down][ahead]
        if len(sel) == 0:
            continue
        g = o[down][ahead] + t[ahead, None] * d[down][ahead]
        sx, sy, _z = lcam.screen(g)
        out[sel] = shade.bilinear(occl, sx, sy)
    return out.reshape(S, S)


def _figure_mesh(meshes: list[Mesh], cam_f: np.ndarray, cam_right: np.ndarray) -> Mesh | None:
    """A 1.75 m human figure standing on the ground beside the model (for scale), facing the
    camera, made of elliptical-section tubes. None for a model with nothing to measure."""
    lo, hi = bbox(meshes)
    if not np.isfinite(lo).all():
        return None
    r = cam_right - np.array([0.0, 0.0, cam_right[2]])
    if np.linalg.norm(r) < 1e-6:
        r = np.array([1.0, 0.0, 0.0])
    r = r / np.linalg.norm(r)
    fwd = np.cross(np.array([0.0, 0.0, 1.0]), r)              # the figure's depth axis
    center = (lo + hi) / 2.0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                        for z in (lo[2], hi[2])])
    reach = float(((corners - center) @ r).max())
    base = center + r * (reach + 450.0)
    base[2] = lo[2]

    def tube(secs):            # secs: (z, half-width, half-depth, x offset)
        th = np.linspace(0.0, 2 * np.pi, 18, endpoint=False)
        rings = np.array([[base + r * (ox + a * np.cos(q)) + fwd * (b * np.sin(q))
                           + np.array([0.0, 0.0, z]) for q in th] for z, a, b, ox in secs])
        out = []
        n = len(th)
        for i in range(len(secs) - 1):
            for j in range(n):
                p, q2 = rings[i, j], rings[i, (j + 1) % n]
                s, u = rings[i + 1, j], rings[i + 1, (j + 1) % n]
                out += [[p, q2, u], [p, u, s]]
        for ring in (rings[0], rings[-1]):                   # caps
            c = ring.mean(axis=0)
            out += [[c, ring[j], ring[(j + 1) % n]] for j in range(n)]
        return out

    phi = np.linspace(np.pi, 0.0, 11)                                       # head
    tris = tube([(1645 + 105 * np.cos(q), max(1.0, 82 * np.sin(q)), max(1.0, 95 * np.sin(q)), 0)
                 for q in phi])
    tris += tube([(1450, 45, 45, 0), (1560, 45, 45, 0)])                     # neck
    tris += tube([(930, 150, 95, 0), (1100, 140, 90, 0), (1300, 175, 100, 0),
                  (1440, 200, 95, 0), (1490, 120, 70, 0)])                   # torso
    for sx in (-1.0, 1.0):
        tris += tube([(80, 55, 55, sx * 95), (470, 50, 55, sx * 95), (520, 55, 60, sx * 92),
                      (900, 80, 80, sx * 85)])                              # leg
        tris += tube([(0, 50, 120, sx * 95), (80, 50, 110, sx * 95)])       # foot
        tris += tube([(780, 32, 32, sx * 245), (1100, 38, 38, sx * 238),
                      (1440, 50, 50, sx * 222)])                            # arm
    return Mesh(np.array(tris, dtype=np.float64), color=(1.0, 1.0, 1.0), name="scale figure",
                role="figure")


_PIX_CHUNK = 200_000                      # covered pixels shaded per batch (bounds memory)


# Above this many triangles the shaded/zebra/sketch styles use flat face normals and skip
# supersampling: smooth normals cost ~120 bytes per triangle at peak, and meshes this fine are
# tessellated near pixel size anyway. Picked from measurement (see the G-R report): every mesh
# that rendered under the 1 GB worker cap before Plan 9 still does, in every style.
SMOOTH_MAX_TRIANGLES = 600_000


def _mesh_normals(meshes: list[Mesh], style: str) -> np.ndarray | None:
    """Corner normals (N, 3, 3) float32 of the meshes with triangles, in _project's order,
    welded and crease-split per mesh: view independent, so render_views computes them once for
    all its views. None means flat face normals: the silhouette style needs none, and meshes
    over SMOOTH_MAX_TRIANGLES in total are shaded flat to stay inside the memory budget."""
    if style == "silhouette":
        return None
    live = [m for m in meshes if len(m.tris)]
    n = sum(len(m.tris) for m in live)
    if n > SMOOTH_MAX_TRIANGLES:
        return None
    crease = shade.ZEBRA_CREASE_DEG if style == "zebra" else shade.CREASE_DEG
    out = np.empty((n, 3, 3), dtype=np.float32)
    at = 0
    for m in live:
        k = len(m.tris)
        out[at:at + k] = shade.corner_normals(m.tris, shade.weld(m.tris), crease)
        at += k
    return out


def _style_pixels(style: str, fr: _Frame, cam: _Cam, tri_at: np.ndarray, framing,
                  cn: np.ndarray | None = None):
    """The covered pixels' colour and the line masks for one opaque style, at the internal
    resolution. Returns (rgb HxWx3 float32 with the background colour, foreground mask,
    {line kind: bool mask}, figure-pixel mask). Pixels are shaded in bounded batches so a
    2048 px internal raster stays well inside the worker's memory budget."""
    S = cam.size
    mask = (tri_at >= 0).reshape(S, S)
    bg = np.array(_PAPER if style == "sketch" else (255, 255, 255) if style == "silhouette"
                  else _BG, dtype=np.float32) / 255.0
    rgb = np.empty((S * S, 3), dtype=np.float32)
    rgb[:] = bg
    covered = np.flatnonzero(tri_at >= 0)
    mid_flat = np.full(S * S, -1, dtype=np.int32)
    mid_flat[covered] = fr.mesh_of[tri_at[covered]]
    mid = mid_flat.reshape(S, S)
    fig_ids = [i for i, m in enumerate(fr.live) if m.role == "figure"]
    figure = np.isin(mid, fig_ids) if fig_ids else np.zeros_like(mask)
    lines: dict[str, np.ndarray] = {}
    if style == "silhouette":
        rgb[covered] = 0.0
        return rgb.reshape(S, S, 3), mask, lines, figure
    smooth = cn is not None and len(cn) == fr.count()        # else: flat face normals
    # per-pixel shading runs in float32 (twice the throughput, half the memory)
    rig = {k: d.astype(np.float32) for k, d in shade.lights(cam.f, cam.right, cam.up).items()}
    up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    f32 = {k: getattr(cam, k).astype(np.float32) for k in ("f", "right", "up", "eye")}
    live = fr.live
    base_of = np.array([m.color for m in live], dtype=np.float32)
    rough_of = np.array([m.roughness for m in live], dtype=np.float32)
    metal_of = np.array([m.metalness for m in live], dtype=np.float32)
    paper = np.array(_PAPER, dtype=np.float32) / 255.0
    img_n = np.zeros((S * S, 3), dtype=np.float32)
    zl = np.full(S * S, np.inf, dtype=np.float32)
    dark = np.zeros(S * S, dtype=bool) if style == "sketch" else None
    for c0 in range(0, len(covered), _PIX_CHUNK):
        pix = covered[c0:c0 + _PIX_CHUNK]
        t, w, depth = _pixel_attrs(fr, cam, tri_at, pix)
        if smooth:
            n = np.einsum("mi,mij->mj", w, cn[t])
            n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        else:
            n = fr.face_unit[t].copy()
        if cam.persp:
            v = (cam.eye[None, :] - np.einsum("mi,mij->mj", w, fr.rows(t))).astype(np.float32)
            v /= np.maximum(np.linalg.norm(v, axis=1), 1e-12)[:, None]
        else:
            v = np.broadcast_to(-f32["f"], n.shape).copy()
        fu = fr.face_unit[t]
        flip = np.einsum("ij,ij->i", fu, v) < 0               # double-sided: face the eye
        n[flip] = -n[flip]
        mi = fr.mesh_of[t]
        if style == "shaded":
            rgb[pix] = shade.shade_cmf(n, v, base_of[mi], rough_of[mi], metal_of[mi], rig, up)
        elif style == "zebra":
            rgb[pix] = (0.06 + 0.90 * shade.zebra(n, v, f32["right"], f32["up"]))[:, None]
        else:                                                  # sketch: 3-band toon fill
            lam = shade.lambert(n, rig)
            fig = np.isin(mi, fig_ids)
            tone = np.where(lam > 0.55, 0.975, np.where(lam > 0.25, 0.90, 0.80))
            tone[fig] = 1.0
            rgb[pix] = paper[None, :] * tone[:, None]
            dark[pix[(lam <= 0.25) & ~fig]] = True
        img_n[pix] = n
        zl[pix] = depth
    if dark is not None:
        lines["hatch"] = dark.reshape(S, S)
    cos_l = float(np.cos(np.radians(_LINE_CREASE_DEG)))
    part, cr = _banded(lambda n_, m_, k_: _neighbour_lines(n_, m_, k_, cos_l),
                       (img_n.reshape(S, S, 3), mid, mask), halo=2)
    del img_n
    lines["silhouette"] = _silhouette_edges(mask) | part
    lines["contour"] = _banded(lambda z_, m_: _depth_edges(z_, m_, 0.01 * cam.reach),
                               (zl.reshape(S, S), mask), halo=4)
    lines["crease"] = cr
    return rgb.reshape(S, S, 3), mask, lines, figure


def _over(rgb: np.ndarray, alpha: np.ndarray, color, ink: np.ndarray) -> None:
    """Composite ink (0..1) of `color` over rgb/alpha in place (straight colour)."""
    c = np.asarray(color, dtype=np.float32) / 255.0
    for ch in range(3):                         # per channel: no HxWx3 temporaries
        rgb[..., ch] += (c[ch] - rgb[..., ch]) * ink
    alpha += (1.0 - alpha) * ink


def _render_styled(meshes: list[Mesh], view: str, size: int, framing, cameras: dict | None,
                   style: str, perspective: bool, shadow: bool, background: str,
                   normals: np.ndarray | None = None) -> np.ndarray:
    """One opaque view as uint8 HxWx3 (or HxWx4 for a transparent background)."""
    ss = _supersample(size)
    cam = _make_cam(view, size * ss, framing, cameras, perspective)
    big = sum(len(m.tris) for m in meshes) > SMOOTH_MAX_TRIANGLES
    if ss > 1 and (big or _projected_layers(meshes, cam) > _SS_MAX_LAYERS):
        ss = 1                    # a huge mesh or a dense stack: anti-aliasing would 4x it
        cam = cam.scaled(size)
    S = size * ss
    tri_at, _z = _coverage(meshes, cam)
    fr = _lite_frame(meshes)
    rgb, mask, lines, figure = _style_pixels(style, fr, cam, tri_at, framing, normals)
    del fr, tri_at, _z                           # the raster is no longer needed: free it
    alpha = mask.astype(np.float32)
    lp = _line_px(size) * ss                     # internal px per design px
    if style in ("shaded", "sketch") and shadow:
        sh = _ground_shadow(meshes, cam.scaled(size), framing)
        if sh is not None:
            if style == "sketch":
                sh = sh * 0.6
            sh = np.repeat(np.repeat(sh, ss, axis=0), ss, axis=1)
            bgm = ~mask
            if background == "transparent":
                rgb[bgm] = 0.0
                alpha[bgm] = sh[bgm]
            else:
                rgb[bgm] *= (1.0 - sh[bgm])[:, None]
    if style == "shaded":
        strong = lines["silhouette"] | lines["contour"]
        ink = np.maximum(0.80 * shade.soft(strong, 1.0 * lp), 0.30 * shade.soft(lines["crease"],
                                                                              0.9 * lp))
        _over(rgb, alpha, _EDGE, ink * mask)
    elif style == "zebra":
        _over(rgb, alpha, _EDGE, 0.9 * shade.soft(lines["silhouette"], 1.0 * lp) * mask)
    elif style == "sketch":
        ink = shade.soft(lines["silhouette"] & ~figure, 2.5 * lp)
        np.maximum(ink, shade.soft((lines["contour"] | lines["crease"]) & ~figure, 1.5 * lp),
                   out=ink)
        # light diagonal hatching across the darkest tone band
        diag = np.add.outer(np.arange(S, dtype=np.int32), np.arange(S, dtype=np.int32))
        hatch = (diag % max(3, int(round(7 * lp)))) < max(1, int(round(0.7 * lp)))
        del diag
        np.maximum(ink, 0.30 * shade.soft(hatch & lines["hatch"], 0.8 * lp), out=ink)
        del hatch
        ground = np.zeros((S, S), dtype=bool)
        if abs(float(cam.f[2])) < 0.35:          # elevations: a ground line
            lo, hi = framing
            c = (np.asarray(lo) + np.asarray(hi)) / 2.0
            r = cam.right - np.array([0.0, 0.0, cam.right[2]])
            r = r / (np.linalg.norm(r) or 1.0)
            half = 0.65 * float(np.linalg.norm(np.asarray(hi)[:2] - np.asarray(lo)[:2]))
            a = np.array([c[0], c[1], lo[2]]) - r * half
            b = np.array([c[0], c[1], lo[2]]) + r * half
            (ax, bx), (ay, by), _zz = cam.screen(np.array([a, b]))
            ground = _segment_mask((S, S), (ax, ay), (bx, by)) & ~dilate_mask(mask, lp)
        np.maximum(ink, shade.soft(ground, 1.5 * lp), out=ink)
        ink = shade.sketch_ink(ink, lp)
        if figure.any():
            fig = shade.soft((lines["silhouette"] | lines["contour"]) & figure, 1.2 * lp)
            np.maximum(ink, _FIGURE_INK * shade.sketch_ink(fig, lp, seed=11), out=ink)
            del fig
        _over(rgb, alpha, _INK, ink)
    del lines
    if ss > 1:
        if background == "transparent":
            rgb *= alpha[..., None]                  # premultiply, then box-filter
        rgb = rgb.reshape(size, ss, size, ss, 3).mean(axis=(1, 3), dtype=np.float32)
        alpha = alpha.reshape(size, ss, size, ss).mean(axis=(1, 3), dtype=np.float32)
        if background == "transparent":
            rgb /= np.maximum(alpha, 1e-6)[..., None]
    if background == "transparent":
        bgc = np.array(_BG, dtype=np.float32) / 255.0
        rgb = np.where(alpha[..., None] > 1e-6, rgb, bgc)
        out = np.concatenate([rgb, alpha[..., None]], axis=-1)
    else:
        out = rgb
    return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def dilate_mask(mask: np.ndarray, r: float) -> np.ndarray:
    return shade.dilate(mask, r)


def _render_one(meshes: list[Mesh], view: str, size: int,
                framing, transparent: bool,
                cameras: dict | None = None, *, style: str = "shaded",
                perspective: bool = False, shadow: bool = True,
                background: str = "flat", normals: np.ndarray | None = None) -> np.ndarray:
    if not transparent:
        return _render_styled(meshes, view, size, framing, cameras, style, perspective, shadow,
                              background, normals)
    # x-ray: weighted-blended transparency of flat-shaded triangles (unchanged)
    cam = _make_cam(view, size, framing, cameras, perspective)
    img, mask, _zbuf = _raster(_project(meshes, cam, flat=True), size, transparent)
    img[_silhouette_edges(mask)] = np.array(_EDGE) / 255.0
    return (np.clip(img, 0.0, 1.0) * 255.0).astype(np.uint8)


def _to_png(arr: np.ndarray, max_bytes: int = MAX_PNG_BYTES) -> bytes:
    """PNG of an RGB or RGBA uint8 image, kept under `max_bytes`: when the lossless encoding
    is too big it is palettised (256 colours, dithered), then, if still too big, scaled down."""
    from PIL import Image
    im = Image.fromarray(arr, "RGBA" if arr.shape[-1] == 4 else "RGB")

    def enc(img) -> bytes:
        buf = io.BytesIO()
        # compress_level 6 without `optimize`: optimize multiplies encode time for little gain
        img.save(buf, "PNG", compress_level=6)
        return buf.getvalue()
    data = enc(im)
    if len(data) <= max_bytes:
        return data
    method = Image.Quantize.FASTOCTREE if im.mode == "RGBA" else Image.Quantize.MEDIANCUT
    q = im.quantize(256, method=method, dither=Image.Dither.FLOYDSTEINBERG)
    data = enc(q)
    while len(data) > max_bytes and min(q.size) > 64:
        im = im.resize((int(im.size[0] * 0.85), int(im.size[1] * 0.85)),
                       Image.Resampling.LANCZOS)
        q = im.quantize(256, method=method, dither=Image.Dither.FLOYDSTEINBERG)
        data = enc(q)
    return data


def fit_png(png: bytes, max_edge: int = 512) -> bytes:
    """`png` downscaled (aspect kept) so its long edge is at most `max_edge` px; unchanged when
    it already fits. Images sent to a model are capped this way (context budget)."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        if max(im.size) <= max_edge:
            return png
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
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


def _check_style(style: str, size: int, background: str) -> None:
    if style not in STYLES:
        raise RenderError(f"unknown render style {style!r}; known: {', '.join(STYLES)}")
    if background not in BACKGROUNDS:
        raise RenderError(f"unknown background {background!r}; known: {', '.join(BACKGROUNDS)}")
    if not 1 <= int(size) <= MAX_SIZE:
        raise RenderError(f"render size {size} is outside 1..{MAX_SIZE} px")


def render_views(meshes, *, views=DEFAULT_VIEWS, size: int = 512,
                 section: tuple | None = None, explode: float = 0.0,
                 transparent: bool = False, cameras: dict | None = None,
                 style: str = "shaded", perspective: bool = False, shadow: bool = True,
                 background: str = "flat", figure: bool = False) -> dict[str, bytes]:
    """Render `meshes` (a Mesh, a triangles array, or a list of them) from each named view.

    Returns {view_name: PNG bytes}, each under 1 MB. `section` is (origin, normal): the +normal
    half is cut away. `explode` > 0 separates multiple meshes. `transparent` composites with
    each mesh's opacity (x-ray). `cameras` adds named views: {name: (forward, up)}.

    `style`: "shaded" (smooth crease-split normals, per-mesh colour/roughness/metalness, studio
    three-point light, soft ground shadow), "zebra" (surface-continuity stripes), "sketch" (NPR
    line art on paper: silhouette/crease/contour lines, toon fill, hand wobble, over-sketch, a
    ground line) or "silhouette" (black fill on white). `perspective`: a 35 mm-equivalent
    camera instead of orthographic. `shadow`: the ground shadow (shaded, sketch). `background`
    "transparent" gives RGBA (shadow in alpha). `figure`: a 1.75 m human beside the model for
    scale (sketch). `size` up to MAX_SIZE px."""
    _check_style(style, size, background)
    ms = _coerce(meshes)
    if explode > 0:
        ms = explode_meshes(ms, explode)
    if section is not None:
        o, n = section
        ms = [replace(m, tris=section_mesh(m.tris, o, n)) for m in ms]
    framing = bbox(ms)
    normals = None if transparent else _mesh_normals(ms, style)
    out: dict[str, bytes] = {}
    for view in views:
        _camera(view, cameras)                        # unknown or malformed: ValueError
        drawn, frame, cn = ms, framing, normals
        if figure and style == "sketch":
            f, right, _up = _camera_basis(view, cameras)
            fig = _figure_mesh(ms, f, right)
            if fig is not None:
                drawn = [*ms, fig]
                frame = bbox(drawn)
                cn = _mesh_normals(drawn, style)
        arr = _render_one(drawn, view, size, frame, transparent, cameras, style=style,
                          perspective=perspective, shadow=shadow, background=background,
                          normals=cn)
        out[view] = _to_png(arr)
    return out


def render_mask(meshes, view: str, *, size: int = 512,
                section: tuple | None = None, cameras: dict | None = None,
                perspective: bool = False) -> np.ndarray:
    """The view's silhouette (bool HxW): the pixels the model covers. Used for IoU matching."""
    ms = _coerce(meshes)
    if section is not None:
        o, n = section
        ms = [Mesh(section_mesh(m.tris, o, n)) for m in ms]
    cam = _make_cam(view, size, bbox(ms), cameras, perspective)
    tri_at, _z = _coverage(ms, cam)
    return (tri_at >= 0).reshape(size, size)


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
