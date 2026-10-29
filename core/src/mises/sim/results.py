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


def parse_frd_blocks(path: Path) -> list[tuple[str, dict[int, list[float]]]]:
    """Every nodal result block in file order: [(name, {node: values})]. Modal and buckling
    results have one DISP block per mode."""
    out: list[tuple[str, dict[int, list[float]]]] = []
    cur: dict[int, list[float]] | None = None
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(" -4"):
                cur = {}
                out.append((line.split()[1], cur))
            elif line.startswith(" -3"):
                cur = None
            elif cur is not None and line.startswith(" -1"):
                body = line.rstrip("\n")
                n = (len(body) - 13) // 12
                cur[int(body[3:13])] = [float(body[13 + 12 * k:25 + 12 * k]) for k in range(n)]
    return out


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def parse_dat_table(path: Path, header: str, cols: int) -> list[list[float]]:
    """Rows of numbers following `header` in a CalculiX .dat (e.g. "E I G E N V A L U E   O U T
    P U T" with mode, eigenvalue, rad/time, cycles/time, imaginary; or "B U C K L I N G   F A C T
    O R   O U T P U T" with mode, factor). Stops at the first non-numeric line after data."""
    import re
    rows: list[list[float]] = []
    on = False
    pat = re.compile(r"^\s*(\d+)" + (r"\s+(" + _NUM + r")") * (cols - 1) + r"\s*$")
    with open(path, errors="replace") as fh:
        for line in fh:
            if header in line:
                on, rows = True, []
                continue
            if not on:
                continue
            m = pat.match(line)
            if m:
                rows.append([float(g) for g in m.groups()])
            elif rows and line.strip():
                on = False
    return rows


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
    away = {n: v for n, v in vm.items() if n not in exclude}
    excluded = bool(away) and len(away) < len(vm)
    if not away:  # every stressed node is on the fixed face: the peak cannot exclude it
        away = vm
    n_peak = max(away, key=away.get)
    n_fix = max((n for n in vm if n in exclude), key=vm.get, default=None)
    disp = {n: math.sqrt(sum(x * x for x in d[:3])) for n, d in frd.get("DISP", {}).items()}
    n_disp = max(disp, key=disp.get) if disp else None
    return {"vm": vm, "max_von_mises_mpa": away[n_peak], "excludes_fixture": excluded,
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
    node_ids, tris = boundary_triangles(mesh)
    pos = [c for n in node_ids for c in mesh.nodes[n]]
    write_glb(path, pos, tris, color=[vm.get(n, 0.0) for n in node_ids], vmin=0.0, vmax=vmax,
              field="von_mises", units="MPa", extras=extras)


def boundary_triangles(mesh: Mesh) -> tuple[list[int], list[int]]:
    """(node ids in vertex order, flat linear-triangle vertex indices) of the mesh boundary;
    a quadratic triangle becomes four linear ones."""
    index: dict[int, int] = {}
    idx: list[int] = []

    def vid(n: int) -> int:
        i = index.get(n)
        if i is None:
            i = index[n] = len(index)
        return i

    for s in mesh.surfaces:
        for t in s.tris:
            if len(t) == 6:
                a, b, c, ab, bc, ca = t
                parts = ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))
            else:
                parts = (tuple(t[:3]),)
            for tri in parts:
                idx.extend(vid(n) for n in tri)
    return list(index), idx


def write_glb(path: Path, pos_mm: list[float], idx: list[int], *, color: list[float],
              vmin: float, vmax: float, field: str, units: str, extras: dict,
              channels: list[dict] | None = None) -> None:
    """A result GLB: POSITION (metres, Z-up rotated to glTF Y-up, the part's model.glb frame),
    COLOR_0 (linear RGB of `color` mapped vmin..vmax, clipped) and, per extra channel, a custom
    float attribute `_<NAME>` (SCALAR or VEC3) listed in asset.extras.mises_sim.channels so the
    viewer can recolour (channel picker), deform (displacement x scale) or animate mode shapes
    without another download. channels: [{name, attribute, units, values (flat), kind:
    "scalar"|"vector", min, max}]."""
    nv = len(pos_mm) // 3
    span = vmax - vmin
    col: list[float] = []
    for v in color:
        col.extend(colormap((v - vmin) / span if span > 0 else 0.0))
    pos = [c / 1000.0 for c in pos_mm]
    views: list[bytes] = [struct.pack(f"<{len(pos)}f", *pos), struct.pack(f"<{len(col)}f", *col),
                          struct.pack(f"<{len(idx)}I", *idx)]
    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": nv, "type": "VEC3",
         "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
        {"bufferView": 1, "componentType": 5126, "count": nv, "type": "VEC3"},
        {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]
    attributes = {"POSITION": 0, "COLOR_0": 1}
    listed = []
    for ch in channels or []:
        vals = [float(x) for x in ch["values"]]
        kind = "VEC3" if ch.get("kind") == "vector" else "SCALAR"
        if len(vals) != nv * (3 if kind == "VEC3" else 1):
            continue
        views.append(struct.pack(f"<{len(vals)}f", *vals))
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": nv,
                          "type": kind})
        attributes[ch["attribute"]] = len(accessors) - 1
        listed.append({k: ch[k] for k in ("name", "attribute", "units", "kind", "min", "max",
                                          "freq_hz", "factor") if k in ch})
    offs, blob = [], b""
    for v in views:
        offs.append(len(blob))
        blob += v
    buffer_views = [{"buffer": 0, "byteOffset": o, "byteLength": len(v),
                     "target": 34963 if i == 2 else 34962}
                    for i, (o, v) in enumerate(zip(offs, views))]
    gltf = {
        "asset": {"version": "2.0", "generator": "mises.sim",
                  "extras": {"mises_sim": {**extras, "field": field, "units": units,
                                           "min": vmin, "max": vmax,
                                           "colormap": "blue-cyan-green-yellow-red",
                                           "channels": listed}}},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)]}],
        "materials": [{"name": field, "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                                "metallicFactor": 0, "roughnessFactor": 1},
                       "extensions": {"KHR_materials_unlit": {}}}],
        "meshes": [{"name": field, "primitives": [{
            "attributes": attributes, "indices": 2, "material": 0}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
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
