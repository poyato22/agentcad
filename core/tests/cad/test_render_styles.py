"""Plan 9 G-R: the design renderer's styles, judged by tolerant image statistics (no golden
bytes): smooth shading has no faceting, zebra stripes are continuous across a G2 blend and
broken across a G0 kink, the sketch has ink on the silhouette over paper, the silhouette style
matches the mask; plus perspective, CMF, the ground shadow, sizes and the PNG budget."""
from __future__ import annotations

import io
import os
import time

import numpy as np
import pytest

from mises.cad import render, render_shade
from mises.cad.render import Mesh

from .test_render import _box_tris, blended_wing_tris


def _rgb(png: bytes) -> np.ndarray:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        return np.asarray(im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB"))


def _lum(png: bytes) -> np.ndarray:
    return _rgb(png)[..., :3].astype(float).mean(axis=2)


def _erode(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for ax in (0, 1):
        for s in range(-r, r + 1):
            if s:
                out &= np.roll(m, s, axis=ax)
    return out


def _grid_tris(P: np.ndarray, closed_v: bool = False) -> np.ndarray:
    """Triangles of a (nu, nv, 3) point grid (v optionally wrapping around)."""
    a = P[:-1, :-1] if not closed_v else P[:-1]
    b = P[1:, :-1] if not closed_v else P[1:]
    c = P[1:, 1:] if not closed_v else np.roll(P, -1, axis=1)[1:]
    d = P[:-1, 1:] if not closed_v else np.roll(P, -1, axis=1)[:-1]
    t1 = np.stack([a, b, c], axis=2).reshape(-1, 3, 3)
    t2 = np.stack([a, c, d], axis=2).reshape(-1, 3, 3)
    return np.concatenate([t1, t2])


def _ellipse_loft(n_len: int = 24, n_around: int = 40) -> np.ndarray:
    """A smooth lofted body of elliptical sections (tapering, closed by its tips), coarsely
    tessellated the way an STL export of a loft is: flat shading would show every facet."""
    s = np.linspace(0.02, 0.98, n_len)
    th = np.linspace(0, 2 * np.pi, n_around, endpoint=False)
    a = 40 * np.sin(np.pi * s) ** 0.7
    b = 25 * np.sin(np.pi * s) ** 0.7
    P = np.stack([a[:, None] * np.cos(th)[None, :], b[:, None] * np.sin(th)[None, :],
                  np.repeat((s * 200)[:, None], n_around, axis=1)], axis=-1)
    return _grid_tris(P, closed_v=True)


# ---- welding and normals

def test_weld_shares_corners_and_keeps_meshes_apart():
    tris = _box_tris(10, 10, 10)
    vid = render_shade.weld(tris, np.zeros(len(tris), dtype=np.int64))
    assert len(np.unique(vid)) == 8                       # a box has 8 corners
    two = np.concatenate([tris, tris])
    vid2 = render_shade.weld(two, np.r_[np.zeros(12), np.ones(12)].astype(np.int64))
    assert len(np.unique(vid2)) == 16                     # same place, other mesh: not welded


def test_crease_split_keeps_box_edges_sharp_and_smooths_curves():
    box = _box_tris(10, 10, 10)
    cn = render_shade.corner_normals(box, render_shade.weld(box, np.zeros(12, np.int64)))
    _fn, unit = render_shade.face_normals(box)
    assert np.allclose(cn, unit[:, None, :].repeat(3, axis=1), atol=1e-9)   # 90 deg: crisp
    body = _ellipse_loft()
    cn = render_shade.corner_normals(body, render_shade.weld(body, np.zeros(len(body),
                                                                            np.int64)))
    # every corner of a smooth closed body shares its normal with the coincident corners
    pts = body.reshape(-1, 3).round(6)
    flat = cn.reshape(-1, 3)
    _, inv = np.unique(pts, axis=0, return_inverse=True)
    spread = np.zeros(inv.max() + 1)
    for k in range(3):
        mx = np.full(inv.max() + 1, -np.inf)
        mn = np.full(inv.max() + 1, np.inf)
        np.maximum.at(mx, inv.ravel(), flat[:, k])
        np.minimum.at(mn, inv.ravel(), flat[:, k])
        spread = np.maximum(spread, mx - mn)
    assert np.median(spread) < 1e-9


def test_high_valence_hub_is_bounded_and_still_valid():
    n = 2000                                              # a fan: one vertex with 2000 faces
    ang = np.linspace(0, 2 * np.pi, n + 1)
    rim = np.stack([50 * np.cos(ang), 50 * np.sin(ang), np.zeros(n + 1)], axis=1)
    fan = np.stack([np.zeros((n, 3)), rim[:-1], rim[1:]], axis=1)
    cn = render_shade.corner_normals(fan, render_shade.weld(fan, np.zeros(n, np.int64)))
    assert np.allclose(np.abs(cn[..., 2]), 1.0)           # a flat disk: all normals +-Z


# ---- smooth shading: no faceting

def _facet_energy(png: bytes, mask: np.ndarray) -> float:
    """Mean |second difference| of luminance inside the body (outline excluded): facets are
    steps in brightness, so they light this metric up; a smooth gradient barely does."""
    lum = _lum(png)
    inner = _erode(mask, 4)
    d2x = np.abs(lum[:, 2:] - 2 * lum[:, 1:-1] + lum[:, :-2])
    d2y = np.abs(lum[2:, :] - 2 * lum[1:-1, :] + lum[:-2, :])
    return float(d2x[inner[:, 1:-1]].mean() + d2y[inner[1:-1, :]].mean())


def test_smooth_lofted_ellipse_shows_no_faceting(monkeypatch):
    body = Mesh(_ellipse_loft())
    mask = render.render_mask(body, "side", size=384)
    smooth = render.render_views(body, views=("side",), size=384, shadow=False)["side"]
    e_smooth = _facet_energy(smooth, mask)
    # the same body flat-shaded (every edge a crease) is the faceted reference
    monkeypatch.setattr(render_shade, "CREASE_DEG", 0.01)
    faceted = render.render_views(body, views=("side",), size=384, shadow=False)["side"]
    e_faceted = _facet_energy(faceted, mask)
    assert e_faceted > 1.0                               # the reference really is faceted
    assert e_smooth < 0.3 * e_faceted, (e_smooth, e_faceted)
    assert e_smooth < 1.0, e_smooth


# ---- zebra: continuity

def _surface(kind: str) -> np.ndarray:
    """A sheet z = h(x) + 0.004 y^2 over 100 x 100 mm: two planes (0 and 40 deg) meeting at
    x = 0 with a G2 blend (quintic slope ramp over |x| < 30) or a G0 kink."""
    x = np.linspace(-50, 50, 401)
    y = np.linspace(-50, 50, 101)
    k = np.tan(np.radians(40))
    if kind == "g0":
        h = np.where(x > 0, k * x, 0.0)
    else:
        u = np.clip((x + 30) / 60, 0, 1)
        slope = k * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)     # C2 ramp: curvature continuous
        h = np.concatenate([[0.0], np.cumsum(0.5 * (slope[1:] + slope[:-1]) * np.diff(x))])
    X, Y = np.meshgrid(x, y, indexing="ij")
    Z = h[:, None] + 0.004 * Y ** 2
    return _grid_tris(np.stack([X, Y, Z], axis=-1))


def _seam_mismatch(kind: str) -> float:
    """Fraction of rows whose zebra value differs just left and just right of x = 0."""
    tris = _surface(kind)
    size = 256
    png = render.render_views(Mesh(tris), views=("top",), size=size, style="zebra")["top"]
    z = _lum(png) > 128
    lo, hi = render.bbox([Mesh(tris)])
    cam = render._make_cam("top", size, (lo, hi))
    sx, _sy, _ = cam.screen(np.array([[0.0, 0.0, 0.0]]))
    c = int(sx[0])
    rows = slice(int(size * 0.2), int(size * 0.8))
    return float((z[rows, c - 1] != z[rows, c + 1]).mean())


def test_zebra_stripes_continuous_on_g2_blend_and_broken_on_g0_kink():
    g2, g0 = _seam_mismatch("g2"), _seam_mismatch("g0")
    assert g2 < 0.12, g2
    assert g0 > 0.3, g0


def test_zebra_is_black_and_white_stripes():
    png = render.render_views(Mesh(_ellipse_loft()), views=("iso",), size=256,
                              style="zebra")["iso"]
    lum = _lum(png)
    mask = _erode(render.render_mask(Mesh(_ellipse_loft()), "iso", size=256), 3)
    dark, light = (lum[mask] < 60).mean(), (lum[mask] > 200).mean()
    assert dark > 0.15 and light > 0.15 and dark + light > 0.85


# ---- sketch

def test_sketch_has_ink_on_the_silhouette_over_paper():
    body = Mesh(_ellipse_loft())
    size = 320
    png = render.render_views(body, views=("iso",), size=size, style="sketch")["iso"]
    rgb = _rgb(png)[..., :3].astype(int)
    lum = rgb.mean(axis=2)
    mask = render.render_mask(body, "iso", size=size)
    edge = mask & ~_erode(mask, 1)
    near = np.zeros_like(lum, dtype=bool)                 # ink within 3 px (hand wobble)
    dark = lum < 130
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            near |= np.roll(np.roll(dark, dy, axis=0), dx, axis=1)
    assert near[edge].mean() > 0.9
    assert np.abs(rgb[:12, :12] - np.array(render._PAPER)).max() <= 2   # paper background
    fill = lum[_erode(mask, 6)]
    assert np.median(fill) > 180                          # light toon fill, not black


def test_sketch_figure_and_ground_line_in_elevation():
    body = Mesh(_box_tris(400, 300, 900, center=(0, 0, 450)))
    plain = render.render_views(body, views=("front",), size=256, style="sketch")["front"]
    fig = render.render_views(body, views=("front",), size=256, style="sketch",
                              figure=True)["front"]
    assert plain != fig
    lum = _lum(plain)
    mask = render.render_mask(body, "front", size=256)
    base = np.flatnonzero(mask.any(axis=1)).max()
    cols = np.flatnonzero(mask.any(axis=0))
    band = lum[base - 3:base + 4]                         # the ground line runs past the body
    assert band[:, cols.min() - 15:cols.min() - 5].min() < 200
    assert band[:, cols.max() + 5:cols.max() + 15].min() < 200
    assert lum[:base - 10, :cols.min() - 10].min() > 240  # above it: clean paper


# ---- silhouette

def test_silhouette_matches_the_mask():
    body = Mesh(_ellipse_loft())
    png = render.render_views(body, views=("iso",), size=256, style="silhouette")["iso"]
    black = _lum(png) < 128
    mask = render.render_mask(body, "iso", size=256)
    assert render.mask_iou(black, mask) > 0.98
    assert _lum(png)[0, 0] > 250                         # white ground


# ---- perspective, CMF, shadow, background

def test_perspective_draws_the_nearer_of_two_equal_cubes_bigger():
    near = _box_tris(20, 20, 20, center=(0, -60, 0))      # front view looks +Y: -Y is nearer
    far = _box_tris(20, 20, 20, center=(0, 60, 0))
    both = [Mesh(near), Mesh(far)]
    ortho = render.render_mask(both, "front", size=200)
    assert ortho.sum() > 0
    mn = render.render_mask([Mesh(near), Mesh(far)], "front", size=200, perspective=True)
    m_near = render.render_mask([Mesh(near)], "front", size=200, perspective=True)
    assert mn.sum() >= m_near.sum()
    # with the framing of the pair, the near cube alone covers more than the far one
    lo, hi = render.bbox(both)
    cam = render._make_cam("front", 200, (lo, hi), perspective=True)
    pn, _, _ = cam.screen(near.reshape(-1, 3))
    pf, _, _ = cam.screen(far.reshape(-1, 3))
    assert np.ptp(pn) > 1.5 * np.ptp(pf)


def test_cmf_gloss_and_metal_change_the_look():
    body = _ellipse_loft()
    kw = dict(views=("front",), size=192, shadow=False)
    matte = _lum(render.render_views(Mesh(body, roughness=0.95), **kw)["front"])
    gloss = _lum(render.render_views(Mesh(body, roughness=0.1), **kw)["front"])
    metal = _rgb(render.render_views(Mesh(body, color=(0.8, 0.5, 0.2), roughness=0.3,
                                          metalness=1.0), **kw)["front"])
    mask = _erode(render.render_mask(Mesh(body), "front", size=192), 2)
    assert gloss[mask].max() > matte[mask].max() + 40     # a specular highlight
    r, g, b = (metal[..., i][mask].mean() for i in range(3))
    assert r > g > b                                      # a coloured metal keeps its tint


def test_part_colours_show_in_shaded():
    a = Mesh(_box_tris(20, 20, 20, center=(-15, 0, 0)), color=(0.9, 0.1, 0.1))
    b = Mesh(_box_tris(20, 20, 20, center=(15, 0, 0)), color=(0.1, 0.2, 0.9))
    rgb = _rgb(render.render_views([a, b], views=("front",), size=200)["front"])
    assert rgb[100, 60, 0] > rgb[100, 60, 2] and rgb[100, 140, 2] > rgb[100, 140, 0]


def test_ground_shadow_darkens_the_floor_and_can_be_transparent():
    body = Mesh(_box_tris(30, 30, 60, center=(0, 0, 30)))
    lit = _lum(render.render_views(body, views=("iso",), size=256)["iso"])
    off = _lum(render.render_views(body, views=("iso",), size=256, shadow=False)["iso"])
    mask = render.render_mask(body, "iso", size=256)
    floor = ~mask
    diff = (off - lit)[floor]
    assert diff.max() > 25                                # a visible shadow
    assert (diff >= -1).all()                             # it only ever darkens
    assert off[0, 0] == pytest.approx(np.mean(render._BG), abs=1)
    rgba = _rgb(render.render_views(body, views=("iso",), size=256,
                                    background="transparent")["iso"])
    assert rgba.shape[-1] == 4
    assert rgba[0, 0, 3] == 0                             # clear background
    assert rgba[..., 3][floor].max() > 40                 # the shadow lives in alpha
    assert (rgba[..., 3][_erode(mask, 2)] == 255).all()


def test_side_on_views_have_no_floor_shadow():
    body = Mesh(_box_tris(30, 30, 60, center=(0, 0, 30)))
    a = render.render_views(body, views=("front",), size=128)["front"]
    b = render.render_views(body, views=("front",), size=128, shadow=False)["front"]
    assert a == b


# ---- sizes and the PNG budget

def test_sizes_up_to_2048_and_bad_args_refused():
    body = Mesh(_box_tris(10, 10, 10))
    png = render.render_views(body, views=("iso",), size=2048, style="silhouette")["iso"]
    assert _rgb(png).shape[:2] == (2048, 2048)
    for bad in (dict(size=2049), dict(style="watercolor"), dict(background="plaid")):
        with pytest.raises(render.RenderError):
            render.render_views(body, views=("iso",), **{"size": 64, **bad})


def test_png_stays_under_1_mb():
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, (2048, 2048, 3), dtype=np.uint8)
    png = render._to_png(noise)
    assert len(png) < 1024 * 1024
    assert png.startswith(b"\x89PNG")
    rgba = np.concatenate([noise, rng.integers(0, 256, (2048, 2048, 1), dtype=np.uint8)], -1)
    assert len(render._to_png(rgba)) < 1024 * 1024
    small = render._to_png(np.zeros((64, 64, 3), dtype=np.uint8))
    assert _rgb(small).shape == (64, 64, 3)


# ---- the G-R speed budget

def test_1024px_shaded_render_of_a_200k_triangle_assembly_under_3s():
    """Plan 9 G-R: 1024 px shaded (smooth normals, CMF, shadow) of ~200k triangles in < 3 s.
    MISES_RENDER_BUDGET_S loosens it on a heavily loaded machine (it is never tightened)."""
    tris = blended_wing_tris(400, 250)
    parts = [Mesh(tris[i::4], color=render._DEFAULT_COLOR) for i in range(4)]
    assert 190_000 <= sum(len(p.tris) for p in parts) <= 210_000
    best = float("inf")
    for _ in range(2):
        t0 = time.perf_counter()
        render.render_views(parts, views=("iso",), size=1024, perspective=True)
        best = min(best, time.perf_counter() - t0)
    budget = max(3.0, float(os.environ.get("MISES_RENDER_BUDGET_S", "3.0")))
    print(f"1024 px shaded, 200k tris: {best:.3f} s")
    assert best < budget, f"took {best:.2f} s (budget {budget} s)"
