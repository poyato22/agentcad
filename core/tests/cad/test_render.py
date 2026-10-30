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
    out = bytearray(b"\0" * 80)
    out += struct.pack("<I", len(tris))
    for tri in tris:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)
        for vert in tri:
            out += struct.pack("<3f", *vert)
        out += struct.pack("<H", 0)
    return bytes(out)


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


def test_transparent_render_is_valid_png():
    tris = _box_tris(30, 30, 30)
    imgs = render.render_views(render.Mesh(tris, opacity=0.4), views=("iso",),
                               size=128, transparent=True)
    assert _png_size(imgs["iso"]) == (128, 128)
