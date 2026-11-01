"""The frame of a part's model.glb, and the fix for GLBs written before P11 F4.

A part's model.glb is metres and glTF Y-up; the app turns it back to the CAD frame (Z-up, mm)
with a +90° rotation about X (app ui/viewer/loadPart.ts). build123d's `export_gltf` maps Z-up
to Y-up by composing the rotation R = Rx(-90°) on the RIGHT of the shape's location L
(`location *= R`), so a part whose final shape carries a location (`Pos(0, 0, 360) * tube`, a
`.moved(...)`) was written as L·R instead of R·L: its translation stayed in the Z-up frame and
the app drew it rotated about the part's origin (z -> -y): the P11 F4 coupler, built at z
360-480, was drawn 360 mm off the rocket's axis while the core (OCCT geometry, health, renders)
had it in place. The worker now exports R·L (worker.py `_export_glb`, metrics `glb_frame` = 2);
`fix_stale_glb` turns an old file's L·R into R·L by rewriting its root nodes (M -> R·M·R⁻¹),
so models built before the fix draw where the core has them without a rebuild.
"""
from __future__ import annotations

import json
import math
import struct

GLB_FRAME = 2                     # metrics.json `glb_frame` of a model.glb written as R·L
# R = Rx(-90°) (Z-up -> Y-up) and its inverse, row-major 4x4
R = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
R_INV = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def node_matrix(node: dict) -> list[list[float]]:
    """A glTF node's local matrix (row-major), from `matrix` (column-major) or its TRS."""
    if "matrix" in node:
        m = node["matrix"]
        return [[float(m[c * 4 + r]) for c in range(4)] for r in range(4)]
    tx, ty, tz = node.get("translation") or (0.0, 0.0, 0.0)
    x, y, z, w = node.get("rotation") or (0.0, 0.0, 0.0, 1.0)
    sx, sy, sz = node.get("scale") or (1.0, 1.0, 1.0)
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    s = (sx, sy, sz)
    return [[rot[0][0] * s[0], rot[0][1] * s[1], rot[0][2] * s[2], float(tx)],
            [rot[1][0] * s[0], rot[1][1] * s[1], rot[1][2] * s[2], float(ty)],
            [rot[2][0] * s[0], rot[2][1] * s[1], rot[2][2] * s[2], float(tz)],
            [0.0, 0.0, 0.0, 1.0]]


def _col_major(m) -> list[float]:
    return [round(m[r][c], 12) + 0.0 for c in range(4) for r in range(4)]


def split_glb(data: bytes) -> tuple[dict, bytes]:
    """(the glTF JSON, the BIN chunk or b"") of a binary glTF."""
    if data[:4] != b"glTF" or len(data) < 20:
        raise ValueError("not a binary glTF")
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    if jtype != b"JSON":
        raise ValueError("binary glTF without a JSON chunk first")
    gltf = json.loads(data[20:20 + jlen])
    rest = data[20 + jlen:]
    if len(rest) >= 8:
        blen, btype = struct.unpack_from("<I4s", rest, 0)
        if btype == b"BIN\x00":
            return gltf, rest[8:8 + blen]
    return gltf, b""


def join_glb(gltf: dict, bin_chunk: bytes) -> bytes:
    body = json.dumps(gltf, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    out = struct.pack("<I4s", len(body), b"JSON") + body
    if bin_chunk:
        b = bin_chunk + b"\x00" * (-len(bin_chunk) % 4)
        out += struct.pack("<I4s", len(b), b"BIN\x00") + b
    return struct.pack("<4sII", b"glTF", 2, 12 + len(out)) + out


def fix_stale_glb(data: bytes) -> bytes:
    """An old model.glb (written L·R, see the module docstring) as R·L: each root node's
    matrix M becomes R·M·R⁻¹. A part whose shape had no location is unchanged by this."""
    gltf, bin_chunk = split_glb(data)
    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)] if gltf.get("scenes") else {}
    roots = scene.get("nodes") or []
    nodes = gltf.get("nodes") or []
    for i in roots:
        node = nodes[i]
        m = _mul(_mul(R, node_matrix(node)), R_INV)
        for k in ("matrix", "translation", "rotation", "scale"):
            node.pop(k, None)
        node["matrix"] = _col_major(m)
    return join_glb(gltf, bin_chunk)


def cad_bbox(data: bytes) -> tuple[list[float], list[float]] | None:
    """The model's bounding box in the CAD frame (mm, Z-up), as the app draws the GLB (Rx(+90°)
    after metres -> mm), from the nodes' transforms and the POSITION min/max. None without
    geometry."""
    gltf, _ = split_glb(data)
    nodes, meshes, acc = gltf.get("nodes") or [], gltf.get("meshes") or [], gltf.get("accessors") or []
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)] if gltf.get("scenes") else {}

    def walk(i: int, parent):
        node = nodes[i]
        m = _mul(parent, node_matrix(node))
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives") or []:
                a = acc[prim["attributes"]["POSITION"]]
                mn, mx = a.get("min"), a.get("max")
                if not mn or not mx:
                    continue
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            p = [sum(m[r][k] * v for k, v in enumerate((cx, cy, cz, 1.0)))
                                 for r in range(3)]
                            # glTF Y-up metres -> CAD Z-up mm: Rx(+90°) = (x, -z, y)
                            q = (p[0] * 1000, -p[2] * 1000, p[1] * 1000)
                            for k in range(3):
                                lo[k], hi[k] = min(lo[k], q[k]), max(hi[k], q[k])
        for c in node.get("children") or []:
            walk(c, m)

    ident = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for i in scene.get("nodes") or range(len(nodes)):
        walk(i, ident)
    return (lo, hi) if lo[0] != math.inf else None
