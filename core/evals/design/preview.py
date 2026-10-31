"""A small smooth-shaded preview renderer for the design evals (Plan 9 G-B, D28).

The product renderer is `mises.cad.render` (G-R owns it). When it supports `style=` (G-R's
shaded / zebra / sketch / silhouette), the eval scripts use it. Until then this module draws
its own preview so the design proof can be judged by eye:
- welded vertices and area-weighted vertex normals (crease-split at 35 deg), interpolated per
  pixel (Phong), so smooth lofts look smooth and are not "dented" by flat triangles;
- a perspective camera, three-point light with a Blinn highlight per part finish (CMF role);
- a z-buffer rasterizer (numpy, fragments in bounded batches), 2x supersampled;
- thin part-boundary lines (panel gaps and joints read as feature lines) and a soft ground
  shadow; a zebra style (reflection stripes: G0 jumps, G1 kinks, G2 flows).

Runs in-process on geometry the eval scripts build themselves; never on untrusted meshes.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

#: CMF roles (mises_shapes archetypes tag each part) -> (rgb 0..1, gloss 0..1)
CMF = {
    "shell": ((0.92, 0.92, 0.91), 0.55),     # light glossy shells
    "joint": ((0.15, 0.16, 0.18), 0.30),     # matte graphite joints and soft goods
    "accent": ((0.04, 0.05, 0.07), 0.95),    # black glossy visor / glass
    "tire": ((0.09, 0.09, 0.10), 0.05),
    "metal": ((0.62, 0.64, 0.67), 0.80),
}
_BG = np.array([0.965, 0.965, 0.97])
VIEWS = {"hero": (-35.0, 14.0), "front": (0.0, 4.0), "side": (90.0, 4.0), "back": (180.0, 8.0),
         "top": (0.0, 85.0), "iso": (45.0, 30.0), "rear34": (145.0, 16.0),
         "low34": (-40.0, 4.0),
         "hero_x": (55.0, 14.0)}           # 3/4 front of a model facing +X (car, dog)


@dataclass
class Item:
    name: str
    shape: object                # a build123d Shape, or a triangles array (N x 3 x 3, mm)
    role: str = "shell"


def _tessellate(shape, tol: float):
    if isinstance(shape, np.ndarray):            # triangles, e.g. mises.cad.render.load_stl
        tris = shape.reshape(-1, 3, 3).astype(np.float64)
        return tris.reshape(-1, 3), np.arange(len(tris) * 3).reshape(-1, 3)
    verts, tris = shape.tessellate(tol, 0.15)
    v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=np.float64)
    f = np.array(tris, dtype=np.int64).reshape(-1, 3)
    return v, f


def _smooth_normals(v: np.ndarray, f: np.ndarray, crease_deg: float = 35.0) -> np.ndarray:
    """Per-corner normals (F x 3 x 3): area-weighted vertex normals over welded vertices, but a
    corner only averages faces within `crease_deg` of its own face (real creases stay crisp)."""
    key = np.round(v / 1e-3).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    fw = weld.reshape(-1)[f]
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    fn = np.cross(b - a, c - a)                     # area-weighted
    unit = fn / (np.linalg.norm(fn, axis=1, keepdims=True) + 1e-12)
    acc = np.zeros((fw.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, fw[:, k], fn)
    vn = acc / (np.linalg.norm(acc, axis=1, keepdims=True) + 1e-12)
    corner = vn[fw]
    cos = np.einsum("fkc,fc->fk", corner, unit)
    keep = cos > math.cos(math.radians(crease_deg))
    return np.where(keep[..., None], corner, unit[:, None, :])


def _camera(view: str, center: np.ndarray, radius: float, fov_deg: float):
    az, el = VIEWS[view]
    a, e = math.radians(az), math.radians(el)
    # azimuth from -Y toward +X, Z up (the app's camera convention)
    eye_dir = np.array([math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)])
    dist = radius / math.sin(math.radians(fov_deg) / 2) * 0.88
    eye = center + eye_dir * dist
    fwd = (center - eye) / np.linalg.norm(center - eye)
    right = np.cross(fwd, [0, 0, 1.0])
    right = right / (np.linalg.norm(right) + 1e-12)
    up = np.cross(right, fwd)
    return eye, fwd, right, up


def _rasterize(p2: np.ndarray, z: np.ndarray, W: int, H: int):
    """Z-buffer rasterize screen triangles p2 (F x 3 x 2) with per-corner depth z (F x 3).
    Returns (tri id per pixel, -1 = empty; barycentrics H x W x 3)."""
    zbuf = np.full(W * H, np.inf)
    tid = np.full(W * H, -1, dtype=np.int64)
    bary = np.zeros((W * H, 3))
    x0 = np.floor(p2[:, :, 0].min(1)).astype(np.int64)
    x1 = np.ceil(p2[:, :, 0].max(1)).astype(np.int64)
    y0 = np.floor(p2[:, :, 1].min(1)).astype(np.int64)
    y1 = np.ceil(p2[:, :, 1].max(1)).astype(np.int64)
    ok = (x1 >= 0) & (y1 >= 0) & (x0 < W) & (y0 < H) & (z.min(1) > 0)
    span = np.maximum(x1 - x0, y1 - y0) + 1
    for lo, hi in ((0, 2), (2, 4), (4, 8), (8, 16), (16, 32), (32, 64), (64, 128),
                   (128, 4096)):
        sel = np.nonzero(ok & (span > lo) & (span <= hi))[0]
        if not len(sel):
            continue
        B = int(min(hi, span[sel].max()))
        oy, ox = np.mgrid[0:B, 0:B]
        ox, oy = ox.ravel(), oy.ravel()
        per = max(1, 3_000_000 // (B * B))
        for s in range(0, len(sel), per):
            ids = sel[s:s + per]
            px = x0[ids][:, None] + ox[None, :]
            py = y0[ids][:, None] + oy[None, :]
            cx, cy = px + 0.5, py + 0.5
            t = p2[ids]
            ax, ay = t[:, 0, 0:1], t[:, 0, 1:2]
            bx, by = t[:, 1, 0:1], t[:, 1, 1:2]
            qx, qy = t[:, 2, 0:1], t[:, 2, 1:2]
            den = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            w0 = ((by - qy) * (cx - qx) + (qx - bx) * (cy - qy)) / den
            w1 = ((qy - ay) * (cx - qx) + (ax - qx) * (cy - qy)) / den
            w2 = 1 - w0 - w1
            inside = ((w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
                      & (px >= 0) & (px < W) & (py >= 0) & (py < H))
            if not inside.any():
                continue
            r, c = np.nonzero(inside)
            zz = z[ids]
            d = w0[r, c] * zz[r, 0] + w1[r, c] * zz[r, 1] + w2[r, c] * zz[r, 2]
            pix = py[r, c] * W + px[r, c]
            np.minimum.at(zbuf, pix, d)
            win = d <= zbuf[pix]
            pix = pix[win]
            tid[pix] = ids[r[win]]
            bary[pix] = np.stack([w0[r, c][win], w1[r, c][win], w2[r, c][win]], 1)
    return tid.reshape(H, W), bary.reshape(H, W, 3)


def render_preview(items: list[Item], view: str = "hero", size: int = 1024,
                   style: str = "shaded", tol: float = 0.3, fov_deg: float = 26.0,
                   aspect: float = 1.0, lines: bool = True) -> bytes:
    """PNG bytes of `items` from `view` (see VIEWS) in `style` (shaded | zebra | silhouette)."""
    ss = 2
    W, H = int(size * aspect) * ss, size * ss
    vs, fs, ns, roles, parts = [], [], [], [], []
    off = 0
    for pi, it in enumerate(items):
        v, f = _tessellate(it.shape, tol)
        if not len(f):
            continue
        vs.append(v)
        fs.append(f + off)
        ns.append(_smooth_normals(v, f))
        roles += [it.role] * len(f)
        parts.append(np.full(len(f), pi))
        off += len(v)
    V, F, CN = np.concatenate(vs), np.concatenate(fs), np.concatenate(ns)
    part = np.concatenate(parts)
    lo, hi = V.min(0), V.max(0)
    center = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) / 2
    eye, fwd, right, up = _camera(view, center, radius, fov_deg)
    focal = (H / 2) / math.tan(math.radians(fov_deg) / 2) / max(1.0, 1.0 / aspect)

    def project(p):
        d = p - eye
        zc = d @ fwd
        return np.stack([W / 2 + focal * (d @ right) / zc, H / 2 - focal * (d @ up) / zc], -1), zc

    p2, zc = project(V)
    tid, bary = _rasterize(p2[F], zc[F], W, H)
    hit = tid >= 0
    rgb = np.ones((H, W, 3)) * _BG

    if style != "silhouette":
        # soft ground shadow: the model flattened onto its floor, blurred
        g = V.copy()
        g[:, 0] += (g[:, 2] - lo[2]) * 0.12
        g[:, 1] += (g[:, 2] - lo[2]) * 0.22
        g[:, 2] = lo[2]
        gp, gz = project(g)
        gt, _ = _rasterize(gp[F], gz[F], W, H)
        sh = Image.fromarray(((gt >= 0) * 255).astype(np.uint8))
        sh = np.asarray(sh.filter(ImageFilter.GaussianBlur(14 * ss)), dtype=np.float64) / 255
        rgb = rgb * (1 - 0.42 * sh[..., None])

    t = tid[hit]
    b = bary[hit]
    n = np.einsum("pk,pkc->pc", b, CN[t])
    n = n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)
    pos = np.einsum("pk,pkc->pc", b, V[F[t]])
    view_v = eye - pos
    view_v = view_v / np.linalg.norm(view_v, axis=1, keepdims=True)
    n = np.where((np.einsum("pc,pc->p", n, view_v) < 0)[:, None], -n, n)   # double-sided
    if style == "silhouette":
        col = np.zeros((len(t), 3))
    elif style == "zebra":
        refl = 2 * np.einsum("pc,pc->p", n, view_v)[:, None] * n - view_v
        stripe = np.sin(np.arctan2(refl[:, 2], np.hypot(refl[:, 0], refl[:, 1])) * 24.0)
        col = np.where(stripe[:, None] > 0, 0.94, 0.10) * np.ones((1, 3))
    else:
        role_rgb = np.array([CMF.get(r, CMF["shell"])[0] for r in roles])
        role_gloss = np.array([CMF.get(r, CMF["shell"])[1] for r in roles])
        base, gloss = role_rgb[t], role_gloss[t]
        key = np.array([-0.45, -0.75, 0.55]); key /= np.linalg.norm(key)
        fill = np.array([0.8, -0.2, 0.25]); fill /= np.linalg.norm(fill)
        rim = np.array([0.15, 0.9, 0.45]); rim /= np.linalg.norm(rim)
        dk = np.clip(n @ key, 0, None)
        df = np.clip(n @ fill, 0, None)
        dr = np.clip(n @ rim, 0, None) ** 2
        hv = key[None, :] + view_v
        hv = hv / np.linalg.norm(hv, axis=1, keepdims=True)
        spec = np.clip(np.einsum("pc,pc->p", n, hv), 0, None) ** (10 + 140 * gloss)
        # a soft studio "sky": surfaces facing up pick up a little more light
        amb = 0.24 + 0.12 * np.clip(n[:, 2], 0, None)
        fres = (1 - np.clip(np.einsum("pc,pc->p", n, view_v), 0, 1)) ** 4
        lum = amb + 0.62 * dk + 0.20 * df + 0.30 * dr
        col = np.clip(base * lum[:, None] + ((0.10 + 0.7 * gloss) * spec)[:, None]
                      + (0.12 * gloss * fres)[:, None], 0, 1)
    img = rgb.reshape(-1, 3)
    img[hit.ravel()] = col
    img = img.reshape(H, W, 3)
    if lines and style != "silhouette":
        pid = np.where(hit, part[np.maximum(tid, 0)], -1)
        edge = np.zeros((H, W), bool)
        edge[:, 1:] |= pid[:, 1:] != pid[:, :-1]
        edge[1:, :] |= pid[1:, :] != pid[:-1, :]
        edge &= hit
        img[edge] = img[edge] * 0.45
    out = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).resize(
        (W // ss, H // ss), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def contact_sheet(pngs: list[tuple[str, bytes]], cols: int = 3, cell: int = 512) -> bytes:
    """One PNG grid of labelled images (label, png)."""
    rows = max(1, math.ceil(len(pngs) / cols))
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 28)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, (label, png) in enumerate(pngs):
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im.thumbnail((cell, cell))
        x, y = (i % cols) * cell, (i // cols) * (cell + 28)
        sheet.paste(im, (x + (cell - im.width) // 2, y))
        d.text((x + 8, y + cell + 6), label[:70], fill=(20, 20, 20))
    buf = io.BytesIO()
    sheet.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def product_renderer_styles() -> set[str]:
    """The styles mises.cad.render.render_views supports (G-R): empty until it takes `style`."""
    import inspect

    from mises.cad import render
    if "style" not in inspect.signature(render.render_views).parameters:
        return set()
    return {"shaded", "zebra", "sketch", "silhouette"}
