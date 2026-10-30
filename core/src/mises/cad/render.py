"""Offscreen rendering for the render-and-critique loop (G1).

Ruling (recorded in the task report): renders are produced on the host by a pure-numpy
z-buffer rasterizer that reads the already-built, trusted tessellated mesh (model.stl) of a
part. That mesh is the output of the sandboxed build worker, so rasterizing it executes no
untrusted code -- it is pure arithmetic with no file, network or process access -- which is why
it does not itself need the sandbox. On macOS arm64 inside the deny-fork, no-display sandbox a
pure rasterizer is more robust than spawning an OCCT offscreen view or pyrender/osmesa, and it
adds no system dependencies (numpy + Pillow only, both already required).

The public surface:
- `load_stl(bytes)` -> triangles (N, 3, 3)
- `Mesh` -> a named, coloured, triangulated solid
- `render_views(meshes, ...)` -> {view_name: PNG bytes}
- `render_mask(meshes, view, ...)` -> the view's silhouette (bool HxW)
- `reference_mask(png_or_jpeg_bytes)` -> the foreground silhouette of a reference image
- `mask_iou(a, b)` / `align_and_iou(render, reference)` -> silhouette match scores
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field

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
    out = np.empty((n, 3, 3), dtype=np.float64)
    off = 84
    for i in range(n):
        # 12 floats: normal (3) then 3 vertices (9); we keep the vertices only.
        vals = struct.unpack_from("<12f", data, off)
        out[i] = np.array(vals[3:12], dtype=np.float64).reshape(3, 3)
        off += 50
    return out


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
    so the +normal half is cut away and the interior shows. Mixed triangles are re-triangulated
    (Sutherland-Hodgman on the kept side)."""
    if len(tris) == 0:
        return tris
    o = np.asarray(origin, dtype=np.float64)
    n = np.asarray(normal, dtype=np.float64)
    nn = np.linalg.norm(n)
    if nn < 1e-9:
        return tris
    n = n / nn
    out: list[np.ndarray] = []
    for tri in tris:
        d = (tri - o) @ n                       # signed distance of each vertex
        keep = d <= 1e-9
        k = int(keep.sum())
        if k == 3:
            out.append(tri)
        elif k == 0:
            continue
        else:
            poly = _clip_triangle(tri, d)
            for j in range(1, len(poly) - 1):   # fan-triangulate the clipped polygon
                out.append(np.array([poly[0], poly[j], poly[j + 1]]))
    if not out:
        return np.empty((0, 3, 3))
    return np.array(out, dtype=np.float64)


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


# ---- rasterization

@dataclass
class _Proj:
    px: np.ndarray                      # (N, 3) pixel x of each vertex
    py: np.ndarray                      # (N, 3) pixel y
    depth: np.ndarray                   # (N, 3) camera depth (bigger = farther)
    tris_world: np.ndarray
    color: np.ndarray
    opacity: float = 1.0


def _project(meshes: list[Mesh], view: str, size: int,
             framing: tuple[np.ndarray, np.ndarray] | None) -> list[_Proj]:
    f, right, up = _camera_basis(view)
    lo, hi = framing if framing is not None else bbox(meshes)
    center = (lo + hi) / 2.0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                        for z in (lo[2], hi[2])])
    cx = (corners - center) @ right
    cy = (corners - center) @ up
    half = max(cx.max() - cx.min(), cy.max() - cy.min(), 1e-6) / 2.0
    margin = 0.90
    scale = (size / 2.0) * margin / half
    projs: list[_Proj] = []
    for m in meshes:
        if len(m.tris) == 0:
            continue
        v = m.tris.reshape(-1, 3) - center
        sx = (v @ right) * scale + size / 2.0
        sy = size / 2.0 - (v @ up) * scale        # image y grows downward
        dz = v @ f
        projs.append(_Proj(
            px=sx.reshape(-1, 3), py=sy.reshape(-1, 3), depth=dz.reshape(-1, 3),
            tris_world=m.tris, color=np.array(m.color, dtype=np.float64), opacity=m.opacity))
    return projs


def _shade(tri_world: np.ndarray, color: np.ndarray) -> np.ndarray:
    e1 = tri_world[1] - tri_world[0]
    e2 = tri_world[2] - tri_world[0]
    n = np.cross(e1, e2)
    nn = np.linalg.norm(n)
    if nn < 1e-12:
        intensity = _AMBIENT
    else:
        n = n / nn
        intensity = _AMBIENT + (1.0 - _AMBIENT) * abs(float(n @ _LIGHT))
    return np.clip(color * intensity, 0.0, 1.0)


def _raster_opaque(projs: list[_Proj], size: int):
    """Z-buffered flat/Lambert fill. Returns (rgb float HxWx3 in 0..1, mask bool HxW, zbuf)."""
    img = np.ones((size, size, 3), dtype=np.float64)
    img[:] = np.array(_BG) / 255.0
    zbuf = np.full((size, size), np.inf)
    mask = np.zeros((size, size), dtype=bool)
    for p in projs:
        for i in range(len(p.px)):
            shade = _shade(p.tris_world[i], p.color)
            _fill_triangle(img, zbuf, mask, p.px[i], p.py[i], p.depth[i], shade, size)
    return img, mask, zbuf


def _fill_triangle(img, zbuf, mask, px, py, pz, shade, size: int) -> None:
    minx = max(int(np.floor(px.min())), 0)
    maxx = min(int(np.ceil(px.max())), size - 1)
    miny = max(int(np.floor(py.min())), 0)
    maxy = min(int(np.ceil(py.max())), size - 1)
    if minx > maxx or miny > maxy:
        return
    x0, y0 = px[0], py[0]
    x1, y1 = px[1], py[1]
    x2, y2 = px[2], py[2]
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9:
        return
    xs = np.arange(minx, maxx + 1)
    ys = np.arange(miny, maxy + 1)
    gx, gy = np.meshgrid(xs + 0.5, ys + 0.5)
    a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / denom
    b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / denom
    c = 1.0 - a - b
    inside = (a >= 0) & (b >= 0) & (c >= 0)
    if not inside.any():
        return
    depth = a * pz[0] + b * pz[1] + c * pz[2]
    sub_z = zbuf[miny:maxy + 1, minx:maxx + 1]
    sub_img = img[miny:maxy + 1, minx:maxx + 1]
    sub_mask = mask[miny:maxy + 1, minx:maxx + 1]
    win = inside & (depth < sub_z)
    sub_z[win] = depth[win]
    sub_img[win] = shade
    sub_mask[win] = True


def _raster_transparent(projs: list[_Proj], size: int) -> tuple[np.ndarray, np.ndarray]:
    """Painter's algorithm with per-mesh opacity, for x-ray / transparency. Triangles are
    composited far-to-near so lower-opacity parts show what is behind them."""
    img = np.ones((size, size, 3), dtype=np.float64)
    img[:] = np.array(_BG) / 255.0
    mask = np.zeros((size, size), dtype=bool)
    items = []
    for p in projs:
        for i in range(len(p.px)):
            cz = float(p.depth[i].mean())
            items.append((cz, p, i))
    items.sort(key=lambda it: -it[0])             # far first
    for _cz, p, i in items:
        shade = _shade(p.tris_world[i], p.color)
        _blend_triangle(img, mask, p.px[i], p.py[i], shade, p.opacity, size)
    return img, mask


def _blend_triangle(img, mask, px, py, shade, opacity: float, size: int) -> None:
    minx = max(int(np.floor(px.min())), 0)
    maxx = min(int(np.ceil(px.max())), size - 1)
    miny = max(int(np.floor(py.min())), 0)
    maxy = min(int(np.ceil(py.max())), size - 1)
    if minx > maxx or miny > maxy:
        return
    x0, y0, x1, y1, x2, y2 = px[0], py[0], px[1], py[1], px[2], py[2]
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-9:
        return
    xs = np.arange(minx, maxx + 1)
    ys = np.arange(miny, maxy + 1)
    gx, gy = np.meshgrid(xs + 0.5, ys + 0.5)
    a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / denom
    b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / denom
    c = 1.0 - a - b
    inside = (a >= 0) & (b >= 0) & (c >= 0)
    if not inside.any():
        return
    al = max(0.0, min(1.0, opacity))
    sub_img = img[miny:maxy + 1, minx:maxx + 1]
    sub_mask = mask[miny:maxy + 1, minx:maxx + 1]
    sub_img[inside] = sub_img[inside] * (1.0 - al) + shade * al
    sub_mask[inside] = True


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
    for dy, dx in ((1, 0), (0, 1)):
        a = z[dy:, dx:]
        b = z[:z.shape[0] - dy, :z.shape[1] - dx]
        diff = np.abs(a - b) > thresh
        diff = np.nan_to_num(diff, nan=False).astype(bool)
        edge[dy:, dx:] |= diff
        edge[:z.shape[0] - dy, :z.shape[1] - dx] |= diff
    return edge & mask


def _render_one(meshes: list[Mesh], view: str, size: int,
                framing, transparent: bool) -> tuple[np.ndarray, np.ndarray]:
    projs = _project(meshes, view, size, framing)
    if transparent:
        img, mask = _raster_transparent(projs, size)
        edge = _silhouette_edges(mask)
    else:
        img, mask, zbuf = _raster_opaque(projs, size)
        lo, hi = framing
        diag = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) or 1.0
        edge = _silhouette_edges(mask) | _depth_edges(zbuf, mask, 0.01 * diag)
    img[edge] = np.array(_EDGE) / 255.0
    return (img * 255.0).astype(np.uint8), mask


def _to_png(rgb: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _coerce(meshes) -> list[Mesh]:
    if isinstance(meshes, Mesh):
        return [meshes]
    if isinstance(meshes, np.ndarray):
        return [Mesh(meshes)]
    out: list[Mesh] = []
    for m in meshes:
        out.append(m if isinstance(m, Mesh) else Mesh(m))
    return out


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
    _rgb, mask = _render_one(ms, view, size, bbox(ms), transparent=False)
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
