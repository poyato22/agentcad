"""Pure-Python thumbnail rendering: binary STL -> shaded orthographic PNG (stdlib only).

Its rasterizer was U1's thumbnail renderer (thumbnails now use Plan 8 G1's bounded render job,
cad/thumbs.py); the host still uses this module's PNG codec and compositor to
compose a project tile. No OCCT, no GL and no third-party deps: it reads the mesh the build worker
already wrote (model.stl) and software-rasterizes it, so it runs under the same sandbox profile as
the build (network denied, writes only its out dir, the Mises home unreadable). Nothing here
evaluates the input; an STL is pure geometry data.

PNGs are written with filter type 0 on every scanline, so decode_png (used only for the project
composite, on our own output) is a plain zlib.decompress plus a per-row filter-byte strip.
"""
from __future__ import annotations

import math
import struct
import zlib

Vec = tuple[float, float, float]
Tri = tuple[Vec, Vec, Vec]
Rgba = bytearray

# A fixed 3/4 view and a single key light in view space: lit top faces, darker sides, so the
# shape reads as a solid. Deterministic — the same mesh always renders byte-for-byte alike.
_AZIM = math.radians(35.0)
_ELEV = math.radians(25.0)
_LIGHT = (-0.35, 0.55, 0.75)
_AMBIENT = 0.32
_BASE = (168, 176, 186)          # a neutral metallic grey; alpha comes from coverage
_SS = 2                          # supersample factor, box-downsampled for anti-aliasing
_MARGIN = 0.08                   # fraction of the frame left as padding around the part


def parse_binary_stl(data: bytes) -> list[Tri]:
    """Triangles (three (x, y, z) vertices each) from a binary STL. The 50-byte facet record's
    normal is ignored — we recompute a face normal from the vertices, which is robust to the
    zero normals some exporters write."""
    if len(data) < 84:
        raise ValueError("STL too short: no header and triangle count")
    (count,) = struct.unpack_from("<I", data, 80)
    need = 84 + count * 50
    if count < 0 or len(data) < need:
        raise ValueError(f"STL truncated: need {need} bytes for {count} triangles, got {len(data)}")
    tris: list[Tri] = []
    off = 84
    for _ in range(count):
        vals = struct.unpack_from("<12f", data, off)     # 3 normal + 3*3 vertex floats
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        tris.append((v1, v2, v3))
        off += 50
    return tris


def _normal(a: Vec, b: Vec, c: Vec) -> Vec:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    m = math.sqrt(nx * nx + ny * ny + nz * nz)
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (nx / m, ny / m, nz / m)


def _rotate(p: Vec) -> Vec:
    """World -> view: spin about world Z (azimuth), then tip about view X (elevation). Screen
    uses the rotated (x, y); +y is up and +z points out of the screen (depth)."""
    ca, sa = math.cos(_AZIM), math.sin(_AZIM)
    ce, se = math.cos(_ELEV), math.sin(_ELEV)
    x, y, z = p
    x1, y1 = x * ca - y * sa, x * sa + y * ca
    y2, z2 = y1 * ce - z * se, y1 * se + z * ce
    return (x1, y2, z2)


def _shade(n: Vec) -> tuple[int, int, int]:
    nx, ny, nz = _rotate(n)                                # the normal lives in the same view space
    lm = math.sqrt(sum(c * c for c in _LIGHT))
    lx, ly, lz = (c / lm for c in _LIGHT)
    diff = max(0.0, nx * lx + ny * ly + nz * lz)
    f = _AMBIENT + (1.0 - _AMBIENT) * diff
    return tuple(max(0, min(255, round(c * f))) for c in _BASE)  # type: ignore[return-value]


def render_stl_rgba(stl: bytes, size: int) -> tuple[int, int, Rgba]:
    """Render a binary STL to a `size`x`size` RGBA buffer (transparent background). An empty
    mesh gives a fully transparent frame rather than an error."""
    if size < 1:
        raise ValueError("size must be positive")
    tris = parse_binary_stl(stl)
    ss = size * _SS
    big = bytearray(ss * ss * 4)                           # zero -> transparent
    if tris:
        _rasterize(tris, ss, big)
    return size, size, _downsample(big, ss, size)


def _rasterize(tris: list[Tri], n: int, buf: Rgba) -> None:
    rot = [(_rotate(t[0]), _rotate(t[1]), _rotate(t[2])) for t in tris]
    xs = [v[0] for t in rot for v in t]
    ys = [v[1] for t in rot for v in t]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny) or 1.0
    scale = (n * (1 - 2 * _MARGIN)) / span
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    def to_px(v: Vec) -> tuple[float, float]:
        px = n / 2 + (v[0] - cx) * scale
        py = n / 2 - (v[1] - cy) * scale                   # image y grows downward
        return px, py

    # Painter's algorithm: draw far triangles first (smallest view-space z), near ones over them.
    order = sorted(range(len(tris)), key=lambda i: (rot[i][0][2] + rot[i][1][2] + rot[i][2][2]))
    for i in order:
        a, b, c = rot[i]
        colour = _shade(_normal(tris[i][0], tris[i][1], tris[i][2]))
        _fill_triangle(buf, n, to_px(a), to_px(b), to_px(c), colour)


def _fill_triangle(buf: Rgba, n: int, p0, p1, p2, colour) -> None:
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-9:
        return                                             # degenerate (edge-on) triangle
    lo_x = max(0, int(math.floor(min(x0, x1, x2))))
    hi_x = min(n - 1, int(math.ceil(max(x0, x1, x2))))
    lo_y = max(0, int(math.floor(min(y0, y1, y2))))
    hi_y = min(n - 1, int(math.ceil(max(y0, y1, y2))))
    r, g, b = colour
    inv = 1.0 / area
    for py in range(lo_y, hi_y + 1):
        sy = py + 0.5
        row = py * n * 4
        for px in range(lo_x, hi_x + 1):
            sx = px + 0.5
            w0 = ((x1 - sx) * (y2 - sy) - (x2 - sx) * (y1 - sy)) * inv
            w1 = ((x2 - sx) * (y0 - sy) - (x0 - sx) * (y2 - sy)) * inv
            w2 = 1.0 - w0 - w1
            if w0 < -1e-6 or w1 < -1e-6 or w2 < -1e-6:
                continue
            o = row + px * 4
            buf[o] = r
            buf[o + 1] = g
            buf[o + 2] = b
            buf[o + 3] = 255


def _downsample(big: Rgba, n: int, size: int) -> Rgba:
    """Box-downsample n x n (n = size * _SS) to size x size, averaging the _SS x _SS block and
    its alpha, so edges anti-alias instead of staircasing."""
    if n == size:
        return big
    f = _SS
    out = bytearray(size * size * 4)
    area = f * f
    for oy in range(size):
        for ox in range(size):
            r = g = b = a = 0
            for dy in range(f):
                base = ((oy * f + dy) * n + ox * f) * 4
                for dx in range(f):
                    o = base + dx * 4
                    r += big[o]
                    g += big[o + 1]
                    b += big[o + 2]
                    a += big[o + 3]
            oo = (oy * size + ox) * 4
            out[oo] = r // area
            out[oo + 1] = g // area
            out[oo + 2] = b // area
            out[oo + 3] = a // area
    return out


def compose_rgba(tiles: list[tuple[int, int, Rgba]], size: int) -> tuple[int, int, Rgba]:
    """Lay up to four part buffers into a 2x2 grid in a `size`x`size` frame; a missing tile
    leaves its quadrant transparent."""
    out = bytearray(size * size * 4)
    cell = size // 2
    spots = [(0, 0), (cell, 0), (0, cell), (cell, cell)]
    for (tw, th, rgba), (ox, oy) in zip(tiles[:4], spots):
        scaled = _resize(rgba, tw, th, cell, cell)
        for y in range(cell):
            src = y * cell * 4
            dst = ((oy + y) * size + ox) * 4
            out[dst:dst + cell * 4] = scaled[src:src + cell * 4]
    return size, size, out


def _resize(rgba: Rgba, w: int, h: int, nw: int, nh: int) -> Rgba:
    if (w, h) == (nw, nh):
        return rgba
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        sy = min(h - 1, y * h // nh)
        for x in range(nw):
            sx = min(w - 1, x * w // nw)
            s = (sy * w + sx) * 4
            d = (y * nw + x) * 4
            out[d:d + 4] = rgba[s:s + 4]
    return out


# ---- PNG (8-bit RGBA, no interlace, filter 0 on every row) -------------------------------------

def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def encode_png(w: int, h: int, rgba: bytes) -> bytes:
    if len(rgba) != w * h * 4:
        raise ValueError("rgba length does not match w*h*4")
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)                                      # filter type 0 (None)
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    sig = b"\x89PNG\r\n\x1a\n"
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + \
        _chunk(b"IEND", b"")


def _idat_payload(png: bytes) -> bytes:
    """The raw IDAT bytes (zlib stream) of a PNG we produced — a test and decode helper."""
    off = 8
    while off < len(png):
        (length,) = struct.unpack_from(">I", png, off)
        tag = png[off + 4:off + 8]
        if tag == b"IDAT":
            return png[off + 8:off + 8 + length]
        off += 12 + length
    raise ValueError("no IDAT chunk")


def decode_png(png: bytes) -> tuple[int, int, Rgba]:
    """Decode one of our own filter-0 RGBA PNGs. Not a general PNG decoder."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    w = h = 0
    idat = bytearray()
    off = 8
    while off < len(png):
        (length,) = struct.unpack_from(">I", png, off)
        tag = png[off + 4:off + 8]
        body = png[off + 8:off + 8 + length]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack_from(">IIBB", body, 0)
            if (depth, ctype) != (8, 6):
                raise ValueError("decode_png only handles 8-bit RGBA")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        off += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = w * 4
    out = bytearray(w * h * 4)
    for y in range(h):
        pos = y * (stride + 1)
        if raw[pos] != 0:
            raise ValueError("decode_png only handles filter type 0")
        out[y * stride:(y + 1) * stride] = raw[pos + 1:pos + 1 + stride]
    return w, h, out


def render_stl_png(stl: bytes, size: int = 256) -> bytes:
    w, h, rgba = render_stl_rgba(stl, size)
    return encode_png(w, h, rgba)
