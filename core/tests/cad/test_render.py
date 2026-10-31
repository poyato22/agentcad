"""Pure-numpy renderer: STL loading, views, section, silhouette IoU."""
from __future__ import annotations

import io
import struct

import numpy as np
import pytest

from mises.cad import render


def _box_tris(sx: float, sy: float, sz: float, center=(0.0, 0.0, 0.0)) -> np.ndarray:
    """12-triangle axis-aligned box centred at `center`."""
    cx, cy, cz = center
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    v = {
        0: (x0, y0, z0), 1: (x1, y0, z0), 2: (x1, y1, z0), 3: (x0, y1, z0),
        4: (x0, y0, z1), 5: (x1, y0, z1), 6: (x1, y1, z1), 7: (x0, y1, z1),
    }
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6),
             (1, 2, 6, 5), (0, 3, 7, 4)]
    tris = []
    for a, b, c, d in quads:
        tris.append([v[a], v[b], v[c]])
        tris.append([v[a], v[c], v[d]])
    return np.array(tris, dtype=np.float64)


def _binary_stl(tris: np.ndarray) -> bytes:
    rec = np.zeros(len(tris), dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["v"] = tris
    return b"\0" * 80 + struct.pack("<I", len(tris)) + rec.tobytes()


def _png_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def test_load_binary_stl_roundtrip():
    tris = _box_tris(10, 20, 30)
    loaded = render.load_stl(_binary_stl(tris))
    assert loaded.shape == (12, 3, 3)
    assert np.allclose(sorted(loaded.reshape(-1).tolist()), sorted(tris.reshape(-1).tolist()))


def test_load_ascii_stl():
    ascii_stl = b"""solid box
facet normal 0 0 0
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid box
"""
    loaded = render.load_stl(ascii_stl)
    assert loaded.shape == (1, 3, 3)


def test_render_views_four_pngs_correct_size():
    tris = _box_tris(40, 20, 10)
    imgs = render.render_views(tris, size=256)
    assert set(imgs) == {"front", "top", "side", "iso"}
    for name, data in imgs.items():
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert _png_size(data) == (256, 256), name


def test_render_covers_center_but_not_corner():
    # a box fills the middle of the frame; the corners stay background
    tris = _box_tris(40, 20, 30)
    mask = render.render_mask(tris, "front", size=200)
    assert mask[100, 100]            # centre is covered
    assert not mask[2, 2]            # a corner is background


def test_section_reveals_interior_of_a_shell():
    # a hollow box: outer shell with an inner cavity (inner faces wound the same way)
    outer = _box_tris(40, 40, 40)
    inner = _box_tris(20, 20, 20)
    shell = np.concatenate([outer, inner], axis=0)
    # the front view looks along +Y, so cutting away the near (-Y) half reveals the cavity
    full = render.render_views(shell, views=("front",), size=200)["front"]
    cut = render.render_views(shell, views=("front",), size=200,
                              section=((0, 0, 0), (0, -1, 0)))["front"]
    assert full != cut                               # the cutaway reveals the inner cavity
    clipped = render.section_mesh(shell, (0, 0, 0), (0, -1, 0))
    assert len(clipped) > 0
    assert (clipped.reshape(-1, 3)[:, 1] >= -1e-6).all()   # only the +Y half survives


def test_section_mesh_keeps_negative_half():
    tris = _box_tris(10, 10, 10)
    kept = render.section_mesh(tris, (0, 0, 0), (0, 0, 1))
    # every vertex of the kept geometry is on the negative (or on-plane) side
    assert (kept.reshape(-1, 3)[:, 2] <= 1e-6).all()


def test_iou_identical_and_offset():
    a = np.zeros((50, 50), dtype=bool)
    a[10:30, 10:30] = True
    b = a.copy()
    assert render.mask_iou(a, b) == pytest.approx(1.0)
    offset = np.zeros((50, 50), dtype=bool)
    offset[20:40, 20:40] = True          # shifted by 10 px
    iou = render.mask_iou(a, offset)
    assert iou < 0.5
    assert iou > 0.0


def test_align_and_iou_tolerates_offset_but_not_shape():
    square = np.zeros((80, 80), dtype=bool)
    square[10:30, 10:30] = True
    moved = np.zeros((80, 80), dtype=bool)
    moved[50:70, 50:70] = True           # same shape, different place
    assert render.align_and_iou(square, moved) == pytest.approx(1.0, abs=0.05)
    tall = np.zeros((80, 80), dtype=bool)
    tall[10:70, 35:45] = True            # a thin tall rectangle: different shape
    assert render.align_and_iou(square, tall) < 0.6


def test_reference_mask_from_white_background():
    from PIL import Image
    im = Image.new("RGB", (40, 40), (255, 255, 255))
    for y in range(10, 30):
        for x in range(10, 30):
            im.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    mask = render.reference_mask(buf.getvalue())
    assert mask[20, 20]
    assert not mask[2, 2]


def test_explode_separates_meshes():
    a = render.Mesh(_box_tris(10, 10, 10, center=(-5, 0, 0)))
    b = render.Mesh(_box_tris(10, 10, 10, center=(5, 0, 0)))
    exploded = render.explode_meshes([a, b], 1.0)
    # centres move further apart
    before = np.linalg.norm(a.centroid() - b.centroid())
    after = np.linalg.norm(exploded[0].centroid() - exploded[1].centroid())
    assert after > before


def blended_wing_tris(n_span: int = 300, n_around: int = 250) -> np.ndarray:
    """A G2-style blended-wing body: swept, tapering NACA-00xx sections whose thickness swells
    into a centre body. 2 * n_span * n_around triangles (300 x 250 -> 150k)."""
    s = np.linspace(-1.0, 1.0, n_span)
    th = np.linspace(0.0, 2 * np.pi, n_around, endpoint=False)
    xc = (1 + np.cos(th)) / 2                      # 1 -> 0 -> 1 around the section
    side = np.where(th < np.pi, 1.0, -1.0)
    chord = 40 * (1 - 0.75 * np.abs(s)) + 8
    x_le = 30 * np.abs(s)
    tc = 0.12 + 0.12 * (1 - np.abs(s)) ** 4
    yt = 5 * (0.2969 * np.sqrt(xc) - 0.126 * xc - 0.3516 * xc ** 2
              + 0.2843 * xc ** 3 - 0.1015 * xc ** 4)
    X = x_le[:, None] + xc[None, :] * chord[:, None]
    Y = np.repeat((s * 120)[:, None], n_around, axis=1)
    Z = side[None, :] * yt[None, :] * (tc * chord)[:, None]
    P = np.stack([X, Y, Z], axis=-1)              # (n_span, n_around, 3)
    a = P[:-1, :]
    b = P[1:, :]
    c = np.roll(P, -1, axis=1)[1:, :]
    d = np.roll(P, -1, axis=1)[:-1, :]
    t1 = np.stack([a, b, c], axis=2).reshape(-1, 3, 3)
    t2 = np.stack([a, c, d], axis=2).reshape(-1, 3, 3)
    return np.concatenate([t1, t2], axis=0)


def test_four_views_of_a_150k_triangle_wing_under_4s():
    """G1 speed budget: 4 views at 512 px of a realistic ~150k-triangle mesh in < 4 s (Plan 9
    G-R: was 2 s for flat shading; smooth normals, 2x supersampling and the shadow cost more).
    MISES_RENDER_BUDGET_S loosens it on a heavily loaded machine (it is never tightened)."""
    import os
    import time
    tris = blended_wing_tris()
    assert 140_000 <= len(tris) <= 160_000
    stl = _binary_stl(tris)
    best = float("inf")
    for _ in range(2):
        t0 = time.perf_counter()
        imgs = render.render_views(render.load_stl(stl), size=512)
        best = min(best, time.perf_counter() - t0)
    assert len(imgs) == 4
    mask = render.render_mask(tris, "top", size=512)
    assert mask.mean() > 0.05                      # the wing really is drawn
    budget = max(4.0, float(os.environ.get("MISES_RENDER_BUDGET_S", "4.0")))
    print(f"render 4x512 views, {len(tris)} tris: {best:.3f} s")
    assert best < budget, f"4 views took {best:.2f} s (budget {budget} s)"


def test_long_thin_triangles_render_fast_and_cover_their_area():
    """A planar face fan-triangulated into slivers (what export_stl makes of flat faces): every
    sliver spans the frame, so a bbox test would scan ~the whole image per triangle."""
    import time
    n = 4000
    ang = np.linspace(0, 2 * np.pi, n + 1)
    rim = np.stack([50 * np.cos(ang), 50 * np.sin(ang), np.zeros(n + 1)], axis=1)
    disk = np.stack([np.zeros((n, 3)), rim[:-1], rim[1:]], axis=1)   # a fan of 4000 slivers
    t0 = time.perf_counter()
    imgs = render.render_views(disk, size=512)
    assert time.perf_counter() - t0 < 4.0
    assert len(imgs) == 4
    mask = render.render_mask(disk, "top", size=512)
    # a disk filling 90% of the frame's width covers pi/4 * 0.9^2 of it, with no gaps
    assert mask.mean() == pytest.approx(np.pi / 4 * 0.9 ** 2, abs=0.02)
    assert mask[256, 256] and mask[256, 40] and not mask[5, 5]


def test_transparent_render_is_valid_png():
    tris = _box_tris(30, 30, 30)
    imgs = render.render_views(render.Mesh(tris, opacity=0.4), views=("iso",),
                               size=128, transparent=True)
    assert _png_size(imgs["iso"]) == (128, 128)


# ---- lighting and view naming (benchmark fix: the B-2's head-on view rendered nearly black)

def _fg_stats(png: bytes, mask: np.ndarray | None = None) -> tuple[float, float]:
    """(mean luminance 0..255 of the shaded interior; the fraction of the interior that is
    dark ink). The interior is the foreground eroded by 2 px, so the anti-aliased outline is
    not counted; None luminance when a view has almost no interior (an edge-on sliver)."""
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(float)
    fg = mask if mask is not None else np.abs(a - np.array(render._BG)).sum(axis=2) > 6
    inner = fg.copy()
    for ax in (0, 1):
        for sh in (-2, -1, 1, 2):
            inner &= np.roll(fg, sh, axis=ax)
    lum = a.mean(axis=2)
    if inner.sum() < 50:
        return None, 0.0
    return float(lum[inner].mean()), float((lum[inner] < 80).mean())


def _wing() -> np.ndarray:
    """A thin swept wing: lens-shaped section (smooth, curved top and bottom), 300 mm span."""
    span = np.linspace(-150, 150, 61)
    chord = np.linspace(0, 60, 41)
    thick = 4.0 * np.sin(np.pi * chord / 60)                   # 0 at both edges
    tris = []
    for sgn in (1, -1):
        for i in range(len(span) - 1):
            for j in range(len(chord) - 1):
                def p(a, b):
                    y = span[a]
                    return (chord[b] + 0.4 * abs(y), y, sgn * thick[b] / 2)
                tris += [[p(i, j), p(i + 1, j), p(i + 1, j + 1)],
                         [p(i, j), p(i + 1, j + 1), p(i, j + 1)]]
    return np.array(tris, dtype=np.float64)


def test_every_standard_view_of_a_thin_wing_is_lit_not_inked():
    # a thin curved wing seen edge-on: its smooth grazing surfaces must be shaded by a light
    # from the camera's side, not drawn solid with depth-edge ink
    wing = render.Mesh(_wing())
    measured = 0
    for view, png in render.render_views(wing, size=512).items():
        lum, ink = _fg_stats(png, render.render_mask(wing, view, size=512))
        assert lum is None or lum > 110, (view, lum)
        assert ink < 0.5, (view, ink)
        measured += lum is not None
    # only the true edge-on view (side: the 4 mm section seen end-on) may lack an interior
    assert measured >= 3, measured


@pytest.mark.parametrize("view", ["front", "top", "side"])
def test_key_light_comes_from_the_camera_side(view):
    # the face a standard view looks straight at is brightly lit, whichever axis it is on
    img = render.render_views(render.Mesh(_box_tris(20, 20, 20)), views=(view,), size=128)[view]
    from PIL import Image
    px = np.asarray(Image.open(io.BytesIO(img)).convert("RGB")).astype(float)[64, 64]
    assert px.mean() / 255.0 > 0.8 * np.mean(render._DEFAULT_COLOR), (view, px)


def test_custom_cameras_render_named_views_and_bad_ones_are_refused():
    tris = _box_tris(40, 10, 10)
    cams = {"nose": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))}
    assert set(render.render_views(render.Mesh(tris), views=("nose",), size=64,
                                   cameras=cams)) == {"nose"}
    ys, xs = np.where(render.render_mask(render.Mesh(tris), "nose", size=64, cameras=cams))
    assert abs((xs.max() - xs.min()) - (ys.max() - ys.min())) <= 2   # head-on: a square
    with pytest.raises(ValueError):
        render.render_views(render.Mesh(tris), views=("nose",),
                            cameras={"nose": ((float("nan"), 0, 0), (0, 0, 1))})
    with pytest.raises(ValueError):
        render.render_views(render.Mesh(tris), views=("nowhere",))


def test_the_scale_figure_height_is_a_single_shared_constant():
    # FW-core M-7: the renderer's scale figure and the concept massing figure stand the same
    # height, so the "human for scale" can never drift between the two pipelines.
    from mises.concept.sketch import FIGURE_MM, figure_tris
    assert FIGURE_MM == render.FIGURE_HEIGHT_MM
    # the concept figure is built to the shared height exactly
    tris = figure_tris(0.0, 0.0, 0.0)
    assert tris[..., 2].max() == pytest.approx(render.FIGURE_HEIGHT_MM)
    # the renderer's figure stands within a head's tolerance of the same height
    fig = render._figure_mesh([render.Mesh(_box_tris(300, 300, 300))],
                              np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    assert fig is not None
    height = float(fig.tris[..., 2].max() - fig.tris[..., 2].min())
    assert abs(height - render.FIGURE_HEIGHT_MM) < 120.0
