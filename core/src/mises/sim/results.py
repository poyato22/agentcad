"""CalculiX result parsing, peak values, error band and the von Mises heatmap GLB."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from .mesh import Mesh


def von_mises(s: list[float]) -> float:
    """s = [sxx, syy, szz, sxy, syz, szx] (CalculiX .frd STRESS order)."""
    a, b, c, d, e, f = s[:6]
    return math.sqrt(max(0.0, 0.5 * ((a - b) ** 2 + (b - c) ** 2 + (c - a) ** 2)
                         + 3 * (d * d + e * e + f * f)))


def parse_frd(path: Path) -> dict[str, dict[int, list[float]]]:
    """Nodal result blocks of an ASCII .frd: {"DISP": {node: [ux,uy,uz]}, "STRESS": {...}, ...}.
    Data lines are fixed width: ' -1', node id (I10), then values (E12.5) that may touch."""
    out: dict[str, dict[int, list[float]]] = {}
    cur: dict[int, list[float]] | None = None
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(" -4"):
                cur = out.setdefault(line.split()[1], {})
            elif line.startswith(" -3"):
                cur = None
            elif cur is not None and line.startswith(" -1"):
                body = line.rstrip("\n")
                n = (len(body) - 13) // 12
                cur[int(body[3:13])] = [float(body[13 + 12 * k:25 + 12 * k]) for k in range(n)]
    return out


def error_band(*, coarse: float, fine: float) -> float | None:
    """|fine - coarse| / fine on the peak stress: the mesh-convergence error estimate."""
    if fine <= 0:
        return None
    return abs(fine - coarse) / fine


def peaks(mesh: Mesh, frd: dict, exclude: set[int]) -> dict:
    """Peak von Mises (MPa) away from the fixture, the peak at the fixture (constraint
    singularity, reported separately), and peak displacement (mm), with locations."""
    vm = {n: von_mises(s) for n, s in frd.get("STRESS", {}).items()}
    if not vm:
        raise ValueError("no stresses in the CalculiX results")
    away = {n: v for n, v in vm.items() if n not in exclude} or vm
    n_peak = max(away, key=away.get)
    n_fix = max((n for n in vm if n in exclude), key=vm.get, default=None)
    disp = {n: math.sqrt(sum(x * x for x in d[:3])) for n, d in frd.get("DISP", {}).items()}
    n_disp = max(disp, key=disp.get) if disp else None
    return {"vm": vm, "max_von_mises_mpa": away[n_peak],
            "max_von_mises_at": list(mesh.nodes.get(n_peak, (0, 0, 0))),
            "max_von_mises_at_fixture_mpa": vm[n_fix] if n_fix is not None else None,
            "max_displacement_mm": disp[n_disp] if n_disp is not None else 0.0,
            "max_displacement_at": list(mesh.nodes.get(n_disp, (0, 0, 0))) if n_disp else None}


# blue -> cyan -> green -> yellow -> red (sRGB stops)
_STOPS = [(0.0, (0.05, 0.25, 0.95)), (0.25, (0.0, 0.8, 0.95)), (0.5, (0.1, 0.8, 0.2)),
          (0.75, (0.98, 0.85, 0.1)), (1.0, (0.9, 0.12, 0.1))]


def colormap(t: float) -> tuple[float, float, float]:
    """Linear-light RGB (glTF COLOR_0 is linear) for t in [0, 1]."""
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            srgb = [c0[i] + (c1[i] - c0[i]) * u for i in range(3)]
            return tuple(x ** 2.2 for x in srgb)  # type: ignore[return-value]
    return tuple(x ** 2.2 for x in _STOPS[-1][1])  # type: ignore[return-value]


def write_result_glb(path: Path, mesh: Mesh, vm: dict[int, float], *, vmax: float,
                     extras: dict) -> None:
    """Boundary surface of the mesh, coloured per vertex by von Mises (0..vmax MPa, values above
    vmax are clipped). Same frame as the part's model.glb: metres, Z-up rotated to glTF Y-up."""
    index: dict[int, int] = {}
    pos: list[float] = []
    col: list[float] = []
    idx: list[int] = []

    def vid(n: int) -> int:
        i = index.get(n)
        if i is None:
            i = index[n] = len(index)
            pos.extend(c / 1000.0 for c in mesh.nodes[n])
            col.extend(colormap(vm.get(n, 0.0) / vmax if vmax > 0 else 0.0))
        return i

    for s in mesh.surfaces:
        for a, b, c, ab, bc, ca in s.tris:  # one quadratic triangle -> four linear ones
            for tri in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)):
                idx.extend(vid(n) for n in tri)
    nv = len(index)
    pos_b = struct.pack(f"<{len(pos)}f", *pos)
    col_b = struct.pack(f"<{len(col)}f", *col)
    idx_b = struct.pack(f"<{len(idx)}I", *idx)
    blob = pos_b + col_b + idx_b
    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    gltf = {
        "asset": {"version": "2.0", "generator": "mises.sim",
                  "extras": {"mises_sim": {**extras, "field": "von_mises", "units": "MPa",
                                           "min": 0.0, "max": vmax,
                                           "colormap": "blue-cyan-green-yellow-red"}}},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)]}],
        "materials": [{"name": "von_mises", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                                "metallicFactor": 0, "roughnessFactor": 1},
                       "extensions": {"KHR_materials_unlit": {}}}],
        "meshes": [{"name": "von_mises", "primitives": [{
            "attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2, "material": 0}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_b), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_b), "byteLength": len(col_b), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_b) + len(col_b), "byteLength": len(idx_b),
             "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": nv, "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5126, "count": nv, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    data = (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\0") + blob)
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def read_glb(path: Path) -> tuple[dict, list[tuple[float, ...]]]:
    """(gltf json, per-vertex COLOR_0) of a GLB written by write_result_glb."""
    data = Path(path).read_bytes()
    jlen = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + jlen])
    bin_start = 20 + jlen + 8
    acc = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    off = bin_start + view.get("byteOffset", 0)
    vals = struct.unpack(f"<{acc['count'] * 3}f", data[off:off + acc["count"] * 12])
    return gltf, [tuple(vals[i:i + 3]) for i in range(0, len(vals), 3)]
