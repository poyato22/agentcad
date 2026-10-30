"""Pure-Python thumbnail renderer: binary STL -> shaded orthographic PNG (no OCCT, no GL)."""
import struct
import zlib

import pytest

from mises.cad import thumb_render as tr


def _binary_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    out = bytearray(b"\x00" * 80)
    out += struct.pack("<I", len(triangles))
    for tri in triangles:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)          # facet normal (ignored by the reader)
        for v in tri:
            out += struct.pack("<3f", *v)
        out += struct.pack("<H", 0)                        # attribute byte count
    return bytes(out)


# a cube (two triangles per face) — a small, recognizable solid
def _cube_stl(s: float = 20.0) -> bytes:
    c = [(x, y, z) for x in (0, s) for y in (0, s) for z in (0, s)]
    idx = [(0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7), (0, 4, 5), (0, 5, 1),
           (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return _binary_stl([tuple(c[i] for i in t) for t in idx])


def test_parse_binary_stl_counts_triangles():
    tris = tr.parse_binary_stl(_cube_stl())
    assert len(tris) == 12
    assert all(len(t) == 3 and len(t[0]) == 3 for t in tris)


def test_render_png_is_valid_and_sized():
    png = tr.render_stl_png(_cube_stl(), size=64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, rgba = tr.decode_png(png)
    assert (w, h) == (64, 64)
    assert len(rgba) == 64 * 64 * 4


def test_render_draws_pixels():
    # a cube fills a good part of the frame: many pixels must be opaque, not an empty image
    _, _, rgba = tr.decode_png(tr.render_stl_png(_cube_stl(), size=64))
    opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i] > 0)
    assert opaque > 64 * 64 * 0.15


def test_render_is_deterministic():
    a = tr.render_stl_png(_cube_stl(), size=48)
    b = tr.render_stl_png(_cube_stl(), size=48)
    assert a == b


def test_shading_has_lit_and_shadowed_faces():
    # a 3/4 view of a cube shows faces at different angles to the light -> a range of greys,
    # never one flat colour
    _, _, rgba = tr.decode_png(tr.render_stl_png(_cube_stl(), size=96))
    greys = {rgba[i] for i in range(0, len(rgba), 4) if rgba[i + 3] > 0}
    assert len(greys) >= 3


def test_empty_mesh_is_transparent_not_an_error():
    png = tr.render_stl_png(_binary_stl([]), size=32)
    _, _, rgba = tr.decode_png(png)
    assert all(rgba[i] == 0 for i in range(3, len(rgba), 4))


def test_rejects_truncated_stl():
    with pytest.raises(ValueError):
        tr.parse_binary_stl(b"\x00" * 40)


def test_decode_png_roundtrips_encode():
    rgba = bytearray(range(256)) * (8 * 8 * 4 // 256)
    png = tr.encode_png(8, 8, rgba)
    w, h, back = tr.decode_png(png)
    assert (w, h) == (8, 8)
    assert bytes(back) == bytes(rgba)
    assert zlib.decompress(tr._idat_payload(png))


def test_compose_tiles_into_one_image():
    tiles = [tr.render_stl_rgba(_cube_stl(), 32) for _ in range(3)]
    w, h, rgba = tr.compose_rgba(tiles, 64)
    assert (w, h) == (64, 64)
    assert any(rgba[i] > 0 for i in range(3, len(rgba), 4))
