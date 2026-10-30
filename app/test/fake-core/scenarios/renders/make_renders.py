"""Regenerates the G4 fixture renders (critique-round before/after thumbnails, a view thumbnail).

    python3 app/test/fake-core/scenarios/renders/make_renders.py

Stand-ins for the core's render_views PNGs (G1): top views of a flying wing getting better each
round, drawn as flat grey silhouettes. No dependencies (zlib + struct).
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent
W, H = 192, 128
BG = (232, 232, 230)
INK = (74, 74, 74)
EDGE = (30, 30, 30)


def png(path: Path, px) -> None:
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def inside(poly, x, y) -> bool:
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def draw(polys, ellipses=()):
    px = [[BG] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if any(inside(p, x + .5, y + .5) for p in polys) or any(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1 for cx, cy, rx, ry in ellipses):
                px[y][x] = INK
    # a 1px darker outline where ink meets paper
    out = [row[:] for row in px]
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if px[y][x] == INK and BG in (px[y - 1][x], px[y + 1][x], px[y][x - 1], px[y][x + 1]):
                out[y][x] = EDGE
    return out


def wing(saw: int, blend: float):
    """A flying wing seen from above: swept leading edge, sawtooth trailing edge."""
    nose = (96, 22)
    tipl, tipr = (8, 92), (184, 92)
    pts = [nose, tipr]
    # trailing edge, right tip → left tip, `saw` teeth
    for i in range(1, 2 * saw):
        t = i / (2 * saw)
        x = tipr[0] + (tipl[0] - tipr[0]) * t
        y = 92 + (14 if i % 2 else 0) * (1 if saw else 0) - 10 * blend * math.sin(math.pi * t)
        pts.append((x, y))
    pts.append(tipl)
    return pts


def main() -> None:
    # round 1 before: a flat delta with an oval cockpit (the reported failure)
    png(OUT / "crit_r1_before.png", draw([[(96, 20), (180, 96), (12, 96)]], [(96, 52, 10, 18)]))
    # round 1 after / round 2 before: a swept wing with a plain trailing edge
    png(OUT / "crit_r1_after.png", draw([wing(0, 0.0)]))
    # round 2 after: sawtooth trailing edge and a blended center body
    png(OUT / "crit_r2_after.png", draw([wing(3, 1.0)], [(96, 50, 16, 24)]))
    # a view card's static thumbnail: the vented case from the iso corner
    case = [(48, 50), (120, 30), (168, 56), (96, 80)]
    side = [(48, 50), (96, 80), (96, 112), (48, 84)]
    front = [(96, 80), (168, 56), (168, 88), (96, 112)]
    png(OUT / "view_case_iso.png", draw([case, side, front]))


if __name__ == "__main__":
    main()
