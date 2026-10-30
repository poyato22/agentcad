"""Sandboxed build worker. Self-contained: stdlib + build123d only (no mises imports).

Run as: python -I worker.py <part_dir> <vars.json>
Loads <part_dir>/part.py, calls build(vars), writes model.step/.stl/.glb and metrics.json
into <part_dir>. Outputs are written to temp names and renamed only after all succeed.
"""
from __future__ import annotations

import __future__
import json
import math
import os
import re
import sys
import types
from pathlib import Path

BUILD_SIGNATURE_HINT = ("part.py must define `def build(v: dict)` that returns a build123d "
                        "Part, Compound or Solid (and optionally VARS = {...}).")


def _hole_groups(shape) -> list[dict]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_REVERSED

    groups: dict[tuple, dict] = {}
    for face in shape.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        ax = cyl.Axis()
        d, loc, r = ax.Direction(), ax.Location(), cyl.Radius()
        direction = [d.X(), d.Y(), d.Z()]
        origin = [loc.X(), loc.Y(), loc.Z()]
        # Surface normal of a cylinder points away from its axis; a reversed face
        # (or left-handed cylinder) flips it. Material-outward normal toward the axis = hole.
        flips = (face.wrapped.Orientation() == TopAbs_REVERSED) ^ (not cyl.Direct())
        if not flips:
            continue
        # Canonical axis direction (first non-zero component positive).
        for c in direction:
            if abs(c) > 1e-9:
                if c < 0:
                    direction = [-x for x in direction]
                break
        # Extent along the axis from the face's vertices-bbox.
        bb = face.bounding_box()
        corners = [(x, y, z) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y)
                   for z in (bb.min.Z, bb.max.Z)]
        ts = [sum((p[i] - origin[i]) * direction[i] for i in range(3)) for p in corners]
        # Bbox corners overshoot for curved faces; tighten with the face's actual vertices
        # plus the bbox of the surface extents in the axial direction (u-v bounds).
        u0, u1, v0, v1 = surf.FirstUParameter(), surf.LastUParameter(), \
            surf.FirstVParameter(), surf.LastVParameter()
        t0, t1 = (v0, v1) if v1 - v0 < 1e8 else (min(ts), max(ts))
        # origin of gp_Cylinder is at v=0; project to canonical direction
        sign = 1.0 if (cyl.Axis().Direction().X() * direction[0]
                       + cyl.Axis().Direction().Y() * direction[1]
                       + cyl.Axis().Direction().Z() * direction[2]) > 0 else -1.0
        a0, a1 = sorted((sign * t0, sign * t1))
        # Point on axis nearest world origin-independent reference: shift origin to v=0.
        perp = [origin[i] - direction[i] * sum(origin[j] * direction[j] for j in range(3))
                for i in range(3)]
        key = (tuple(round(x, 4) for x in direction), round(2 * r, 4),
               tuple(round(x, 3) for x in perp), round(a0 + sum(origin[j] * direction[j] for j in range(3)), 2),
               round(a1 + sum(origin[j] * direction[j] for j in range(3)), 2))
        if key in groups:
            continue
        base = sum(origin[j] * direction[j] for j in range(3))
        mid = (a0 + a1) / 2 + base
        center = [perp[i] + direction[i] * mid for i in range(3)]
        groups[key] = {"diameter_mm": 2 * r, "axis": direction, "center": center,
                       "depth_mm": a1 - a0}
    return list(groups.values())


def _face_list(shape) -> list[dict]:
    """Face ids ("f0".. in shape.faces() order) with type, centre of mass, area and the
    outward normal at the centre, so sim fixtures/loads can name a face. Compact on purpose:
    it rides along in every mirror_measure result."""
    from build123d import CenterOf

    out = []
    for i, face in enumerate(shape.faces()):
        c = face.center(CenterOf.MASS)
        try:
            n = face.normal_at(c)
            normal = [round(n.X, 4) + 0.0, round(n.Y, 4) + 0.0, round(n.Z, 4) + 0.0]
        except Exception:  # noqa: BLE001 - degenerate/odd surfaces have no usable normal
            normal = None
        out.append({"id": f"f{i}", "type": str(face.geom_type).split(".")[-1].lower(),
                    "center": [round(c.X, 3) + 0.0, round(c.Y, 3) + 0.0, round(c.Z, 3) + 0.0],
                    "normal": normal, "area": round(float(face.area), 3)})
    return out


_TAG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
MAX_FACE_TAGS = 16


def _face_tags(mod, shape, face_list: list[dict]) -> tuple[dict, list[str]]:
    """Named faces a part script declares: module-level FACES = {"motor_mount": fn, ...}, each fn
    taking the built shape and returning a Face or a list of faces. Re-run on every build, so a
    tag follows the geometry across variants (unlike a face id). Returns ({name: [face ids]},
    errors). Untrusted output: the host only ever looks the ids up in face_list."""
    faces_decl = getattr(mod, "FACES", None)
    if not isinstance(faces_decl, dict):
        return {}, []
    from build123d import CenterOf
    all_faces = list(shape.faces())
    tags: dict[str, list[str]] = {}
    errors: list[str] = []
    for name, fn in list(faces_decl.items())[:MAX_FACE_TAGS]:
        if not (isinstance(name, str) and _TAG_NAME_RE.match(name)) or not callable(fn):
            errors.append(f"FACES[{name!r}]: name must match [a-z][a-z0-9_]* and map to a function")
            continue
        try:
            sel = fn(shape)
            sel = [sel] if hasattr(sel, "geom_type") else list(sel or [])
            ids = []
            for f in sel:
                idx = next((i for i, g in enumerate(all_faces) if g.is_same(f)), None)
                if idx is None:  # a copy, not the same TopoDS face: match by centre
                    c = f.center(CenterOf.MASS)
                    idx = min(range(len(face_list)), default=None, key=lambda i: math.dist(
                        face_list[i]["center"], [c.X, c.Y, c.Z]))
                if idx is not None and f"f{idx}" not in ids:
                    ids.append(f"f{idx}")
            if not ids:
                errors.append(f"FACES[{name!r}] selected no face")
                continue
            tags[name] = ids
        except Exception as e:  # noqa: BLE001 - a bad selector must not fail the build
            errors.append(f"FACES[{name!r}]: {type(e).__name__}: {str(e)[:200]}")
    return tags, errors


def _edge_adjacency(shape) -> dict:
    """edge -> outward normals (at their centres) of the faces that share it."""
    from build123d import CenterOf
    adj: dict = {}
    for f in shape.faces():
        try:
            n = _xyz(f.normal_at(f.center(CenterOf.MASS)))
        except Exception:  # noqa: BLE001
            continue
        for e in f.edges():
            adj.setdefault(e, []).append(n)
    return adj


def _edge_list(shape) -> list[dict]:
    """Edge ids ("e0".. in shape.edges() order) with type, length, midpoint, direction and the
    normals of the faces it joins, so a feature op (fillet/chamfer) can name an edge and later
    re-find it. Compact; it rides along in metrics like the face list."""
    adj = _edge_adjacency(shape)
    out = []
    for i, edge in enumerate(shape.edges()):
        try:
            mid = edge.position_at(0.5)
            center = [round(mid.X, 3) + 0.0, round(mid.Y, 3) + 0.0, round(mid.Z, 3) + 0.0]
            direction = [round(c, 4) + 0.0 for c in _canon(_xyz(edge.tangent_at(0.5)))]
        except Exception:  # noqa: BLE001 - degenerate edges have no usable parameterisation
            center = direction = None
        out.append({"id": f"e{i}", "type": _geom(edge), "length": round(float(edge.length), 3),
                    "center": center, "direction": direction,
                    "adjacent": [[round(c, 4) + 0.0 for c in n] for n in adj.get(edge, [])]})
    return out


# ---- stable op references (called from `# mises-op` blocks in part.py)
#
# An op records a signature of its target (type, normal/direction, centroid, area/length, and for
# edges the normals of the faces it joins) when it is added. At every regeneration these re-find
# that target in the CURRENT shape. The match is strict: same surface/curve type, normal or
# direction within 5 deg, size within a factor of 2, and a centroid within the target's own size
# of where it was. Anything else — no candidate, or more than one — fails the build with an error
# that names the op, so an op never silently moves to another face or edge.

_COS_5DEG = math.cos(math.radians(5.0))
_SIZE_RATIO = (0.5, 2.0)


def _xyz(v) -> list[float]:
    return [float(v.X), float(v.Y), float(v.Z)]


def _canon(d: list[float]) -> list[float]:
    for c in d:
        if abs(c) > 1e-9:
            return [-x for x in d] if c < 0 else list(d)
    return list(d)


def _geom(obj) -> str:
    return str(obj.geom_type).split(".")[-1].lower()


def _ratio_ok(now: float, then: float) -> bool:
    if then <= 0:
        return True
    r = now / then
    return _SIZE_RATIO[0] <= r <= _SIZE_RATIO[1]


# op label -> the signatures its targets resolved to in this build, in call order. Returned in
# result.json so the host can refresh the op's recorded reference (fix: small steps must not add
# up to a failure, since each match is checked against the previous build, not the first one).
_RESOLVED: dict[str, list] = {}


def _r(x: float, n: int = 4) -> float:
    return round(float(x), n) + 0.0


def _face_sig_now(f, hint) -> dict:
    from build123d import CenterOf
    c = f.center(CenterOf.MASS)
    return {"type": _geom(f), "normal": [_r(v) for v in _xyz(f.normal_at(c))],
            "center": [_r(v, 3) for v in _xyz(c)], "area": _r(f.area, 3), "hint": hint}


def _edge_sig_now(e, adj: dict, hint) -> dict:
    return {"type": _geom(e), "direction": [_r(v) for v in _canon(_xyz(e.tangent_at(0.5)))],
            "center": [_r(v, 3) for v in _xyz(e.position_at(0.5))], "length": _r(e.length, 3),
            "adjacent": [[_r(v) for v in n] for n in adj.get(e, [])], "hint": hint}


def _record(label: str, sig: dict) -> None:
    _RESOLVED.setdefault(label, []).append(sig)


def _one(cands: list, label: str, what: str):
    if not cands:
        raise ValueError(f"{label}: target {what} not found after regeneration (nothing with the "
                         "same type, orientation and size near where it was) — remove or redo "
                         "the op")
    if len(cands) > 1:
        raise ValueError(f"{label}: target {what} is ambiguous after regeneration "
                         f"({len(cands)} matching candidates) — remove or redo the op")
    return cands[0]


def _mises_face(shape, sig: dict, label: str):
    from build123d import CenterOf
    n0, c0, a0 = sig["normal"], sig["center"], float(sig.get("area") or 0.0)
    # pick tolerance: half the face's own size; accept a centroid within 2x that
    tol = 2.0 * 0.5 * (math.sqrt(a0) if a0 > 0 else 1.0)
    cands = []
    for f in shape.faces():
        if _geom(f) != sig["type"] or not _ratio_ok(float(f.area), a0):
            continue
        c = f.center(CenterOf.MASS)
        try:
            n = _xyz(f.normal_at(c))
        except Exception:  # noqa: BLE001
            continue
        if sum(a * b for a, b in zip(n, n0)) < _COS_5DEG:
            continue
        if math.dist(_xyz(c), c0) <= tol:
            cands.append(f)
    found = _one(cands, label, "face")
    _record(label, _face_sig_now(found, sig.get("hint")))
    return found


def _same_normals(now: list, then: list) -> bool:
    if len(now) != len(then):
        return False
    left = list(now)
    for n0 in then:
        hit = next((n for n in left if sum(a * b for a, b in zip(n, n0)) >= _COS_5DEG), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def _mises_edge(shape, sig: dict, label: str, adj: dict | None = None):
    d0, m0, l0 = sig["direction"], sig["center"], float(sig.get("length") or 0.0)
    adj0 = sig.get("adjacent")
    adj = _edge_adjacency(shape) if adj is None and adj0 else adj
    tol = 2.0 * 0.5 * (l0 if l0 > 0 else 1.0)
    cands = []
    for e in shape.edges():
        if _geom(e) != sig["type"] or not _ratio_ok(float(e.length), l0):
            continue
        try:
            mid, d = _xyz(e.position_at(0.5)), _xyz(e.tangent_at(0.5))
        except Exception:  # noqa: BLE001
            continue
        if abs(sum(a * b for a, b in zip(d, d0))) < _COS_5DEG or math.dist(mid, m0) > tol:
            continue
        if adj0 and not _same_normals(adj.get(e, []), adj0):
            continue
        cands.append(e)
    found = _one(cands, label, "edge")
    _record(label, _edge_sig_now(found, adj if adj is not None else _edge_adjacency(shape),
                                 sig.get("hint")))
    return found


def _distinct(found: list, label: str, what: str) -> list:
    for i, a in enumerate(found):
        if any(a.is_same(b) for b in found[:i]):
            raise ValueError(f"{label}: two of its target {what}s resolve to the same {what} "
                             "after regeneration — remove or redo the op")
    return found


def _mises_faces(shape, sigs: list, label: str) -> list:
    return _distinct([_mises_face(shape, s, label) for s in sigs], label, "face")


def _mises_edges(shape, sigs: list, label: str) -> list:
    adj = _edge_adjacency(shape)
    return _distinct([_mises_edge(shape, s, label, adj) for s in sigs], label, "edge")


def _mises_on_face(face, point, label: str) -> None:
    """The recorded pick point (centroid + offset) must still lie on the matched face."""
    from build123d import Vector
    size = max(math.sqrt(float(face.area)), 1.0)
    if face.distance_to(Vector(*point) if isinstance(point, (list, tuple)) else point) > 1e-3 * size:
        raise ValueError(f"{label}: the picked point is no longer on its target face after "
                         "regeneration — remove or redo the op")


def _metrics(shape, density: float) -> dict:
    bb = shape.bounding_box()
    vol = float(shape.volume)
    com = shape.center()
    return {
        "valid": bool(shape.is_valid),
        "volume_mm3": vol,
        "area_mm2": float(shape.area),
        "mass_g": vol / 1000.0 * density,
        "bbox": {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z],
                 "size": [bb.size.X, bb.size.Y, bb.size.Z]},
        "center_of_mass": [com.X, com.Y, com.Z],
        "faces": len(shape.faces()),
        "face_list": _face_list(shape),
        "edges": len(shape.edges()),
        "edge_list": _edge_list(shape),
        "holes": _hole_groups(shape),
        "solids": len(shape.solids()),
    }


# ---- 2D engineering drawing (python -I worker.py <part_dir> <args.json> --drawing)

SHEET_W, SHEET_H = 297.0, 210.0          # A4 landscape, mm
_TITLE_H = 34.0
_NICE = [10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]


def _xml(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fmt(v: float) -> str:
    return f"{v:.1f}" if abs(v) < 1000 else f"{v:.0f}"


def _scale_text(scale: float) -> str:
    return f"{scale:g}:1" if scale >= 1 else f"1:{1 / scale:g}"


def _hole_notes(holes, bbox_size: list[float]) -> list[str]:
    """'2x D6 THRU' lines; holes with the same diameter and depth are grouped."""
    groups: dict[tuple, int] = {}
    for h in holes if isinstance(holes, list) else []:
        d, depth = round(float(h["diameter_mm"]), 2), float(h.get("depth_mm", 0.0))
        axis = h.get("axis", [0, 0, 1])
        extent = sum(abs(axis[i]) * bbox_size[i] for i in range(3))
        key = (d, None if depth >= extent - 0.05 else round(depth, 1))
        groups[key] = groups.get(key, 0) + 1
    return [f"{n}x Ø{d:g} " + ("THRU" if dep is None else f"DEPTH {dep:g}")
            for (d, dep), n in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))]


def _drawing(part_dir: Path, args: dict, nonce: str) -> None:
    import io

    import build123d as b3
    from build123d import Compound, Edge, ExportDXF, ExportSVG, LineType, Location, Polyline

    shape = b3.import_step(str(part_dir / "model.step"))
    metrics = json.loads((part_dir / "metrics.json").read_text())
    size = metrics["bbox"]["size"]
    w, d, h = size  # X, Y, Z
    label, material, date = args["label"], args["material"], args["date"]

    # third-angle projection: top above front, right of the part to the right of front
    cam = max(w, d, h) * 10 + 100
    specs = {  # name -> (viewport origin, up)
        "front": ((0, -cam, 0), (0, 0, 1)),
        "top": ((0, 0, cam), (0, 1, 0)),
        "right": ((cam, 0, 0), (0, 0, 1)),
        "iso": ((cam, -cam, cam), (0, 0, 1)),
    }
    proj: dict[str, tuple[list, list, tuple[float, float, float, float]]] = {}
    for name, (origin, up) in specs.items():
        vis, hid = shape.project_to_viewport(origin, up)
        vis, hid = list(vis), list(hid)
        if not vis:
            raise RuntimeError(f"projection produced no visible edges for the {name} view")
        bb = Compound(vis).bounding_box()
        proj[name] = (vis, hid, (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y))
    ext = {n: (p[2][2] - p[2][0], p[2][3] - p[2][1]) for n, p in proj.items()}

    margin, gap, dim_room = 10.0, 24.0, 16.0
    area_w, area_h = SHEET_W - 2 * margin, SHEET_H - 2 * margin - _TITLE_H
    col_w = [max(ext["top"][0], ext["front"][0]), max(ext["right"][0], ext["iso"][0])]
    row_h = [max(ext["top"][1], ext["iso"][1]), max(ext["front"][1], ext["right"][1])]
    fit = min((area_w - gap - 2 * dim_room) / (col_w[0] + col_w[1]),
              (area_h - gap - 2 * dim_room) / (row_h[0] + row_h[1]))
    scale = next((s for s in _NICE if s <= fit), _NICE[-1])
    cw = [c * scale for c in col_w]
    rh = [r * scale for r in row_h]
    x0 = margin + dim_room
    y_top = SHEET_H - margin - dim_room
    # lower-left of each view's cell and its (column, row)
    cells = {"top": (x0, y_top - rh[0], 0, 0), "iso": (x0 + cw[0] + gap, y_top - rh[0], 1, 0),
             "front": (x0, y_top - rh[0] - gap - rh[1], 0, 1),
             "right": (x0 + cw[0] + gap, y_top - rh[0] - gap - rh[1], 1, 1)}

    svg = ExportSVG(unit=b3.Unit.MM, scale=1, margin=0, fit_to_stroke=False, line_weight=0.35)
    dxf = ExportDXF()
    svg.add_layer("sheet", line_weight=0.5)
    dxf.add_layer("SHEET")
    svg.add_layer("dims", line_weight=0.18)
    dxf.add_layer("DIMS")
    border = Polyline((0, 0), (SHEET_W, 0), (SHEET_W, SHEET_H), (0, SHEET_H), (0, 0))
    inner = Polyline((margin, margin), (SHEET_W - margin, margin), (SHEET_W - margin, SHEET_H - margin),
                     (margin, SHEET_H - margin), (margin, margin))
    for shp in (border, inner):
        svg.add_shape(shp, layer="sheet")
        dxf.add_shape(shp, layer="SHEET")

    placed: dict[str, tuple[float, float, float, float]] = {}  # name -> sheet-space bbox
    for name, (vis, hid, bb) in proj.items():
        cx0, cy0, ci, ri = cells[name]
        vw, vh = ext[name][0] * scale, ext[name][1] * scale
        ox = cx0 + (cw[ci] - vw) / 2 - bb[0] * scale
        oy = cy0 + (rh[ri] - vh) / 2 - bb[1] * scale
        loc = Location((ox, oy, 0))

        def place(edges):
            return [(e.scale(scale) if scale != 1 else e).moved(loc) for e in edges]
        vis_s, hid_s = place(vis), place(hid)
        svg.add_layer(f"view-{name}", line_weight=0.35)
        svg.add_layer(f"view-{name}-hidden", line_weight=0.18, line_type=LineType.HIDDEN)
        dxf.add_layer(f"VIEW_{name.upper()}")
        dxf.add_layer(f"VIEW_{name.upper()}_HIDDEN", line_type=LineType.HIDDEN)
        svg.add_shape(vis_s, layer=f"view-{name}")
        dxf.add_shape(vis_s, layer=f"VIEW_{name.upper()}")
        if hid_s:
            svg.add_shape(hid_s, layer=f"view-{name}-hidden")
            dxf.add_shape(hid_s, layer=f"VIEW_{name.upper()}_HIDDEN")
        placed[name] = (ox + bb[0] * scale, oy + bb[1] * scale, ox + bb[2] * scale, oy + bb[3] * scale)

    texts: list[tuple[float, float, str, float, str, str]] = []  # x, y, text, size, anchor, rotate
    dim_lines: list = []

    def line(a, b):
        dim_lines.append(Edge.make_line((a[0], a[1], 0), (b[0], b[1], 0)))

    def hdim(x1, x2, y, value, out_y):
        """Horizontal dimension x1..x2 at out_y (below y), with extension lines and ticks."""
        line((x1, y - 1.0), (x1, out_y - 1.5))
        line((x2, y - 1.0), (x2, out_y - 1.5))
        line((x1, out_y), (x2, out_y))
        for xx, sg in ((x1, 1), (x2, -1)):
            line((xx, out_y), (xx + sg * 2.5, out_y + 0.9))
            line((xx, out_y), (xx + sg * 2.5, out_y - 0.9))
        texts.append(((x1 + x2) / 2, out_y + 1.2, _fmt(value), 3.0, "middle", ""))

    def vdim(y1, y2, x, value, out_x):
        """Vertical dimension y1..y2 at out_x (left of x)."""
        line((x - 1.0, y1), (out_x - 1.5, y1))
        line((x - 1.0, y2), (out_x - 1.5, y2))
        line((out_x, y1), (out_x, y2))
        for yy, sg in ((y1, 1), (y2, -1)):
            line((out_x, yy), (out_x + 0.9, yy + sg * 2.5))
            line((out_x, yy), (out_x - 0.9, yy + sg * 2.5))
        texts.append((out_x - 1.2, (y1 + y2) / 2, _fmt(value), 3.0, "middle", "-90"))

    fx0, fy0, fx1, fy1 = placed["front"]
    rx0, ry0, rx1, _ = placed["right"]
    hdim(fx0, fx1, fy0, w, fy0 - 7)             # overall width under the front view
    vdim(fy0, fy1, fx0, h, fx0 - 7)             # overall height left of the front view
    hdim(rx0, rx1, ry0, d, ry0 - 7)             # overall depth under the right view
    svg.add_shape(dim_lines, layer="dims")
    dxf.add_shape(dim_lines, layer="DIMS")

    for name, (bx0, _by0, bx1, by1) in placed.items():
        texts.append(((bx0 + bx1) / 2, by1 + 3.0, name.upper(), 2.6, "middle", ""))

    notes = _hole_notes(metrics.get("holes", []), size)
    ty = margin + _TITLE_H
    tb_w = 118.0
    tb_x = SHEET_W - margin - tb_w
    shapes = [Polyline((tb_x, margin), (SHEET_W - margin, margin), (SHEET_W - margin, ty),
                       (tb_x, ty), (tb_x, margin)),
              Polyline((tb_x, margin + 11.33), (SHEET_W - margin, margin + 11.33)),
              Polyline((tb_x, margin + 22.66), (SHEET_W - margin, margin + 22.66)),
              Polyline((tb_x + 59, margin), (tb_x + 59, margin + 22.66))]
    for shp in shapes:
        svg.add_shape(shp, layer="sheet")
        dxf.add_shape(shp, layer="SHEET")
    texts += [
        (tb_x + 3, ty - 8.0, f"PART: {label}", 4.2, "start", ""),
        (tb_x + 3, margin + 14.5, f"MATERIAL: {material}", 3.2, "start", ""),
        (tb_x + 62, margin + 14.5, f"DATE: {date}", 3.2, "start", ""),
        (tb_x + 3, margin + 4.0, f"SCALE {_scale_text(scale)}   UNITS mm", 3.0, "start", ""),
        (tb_x + 62, margin + 4.0, "Mises", 3.6, "start", ""),
        (margin + 3, margin + _TITLE_H - 6, "HOLES" if notes else "NO HOLES", 3.2, "start", ""),
        (margin + 3, margin + 3, f"OVERALL {_fmt(w)} x {_fmt(d)} x {_fmt(h)} mm", 3.0, "start", ""),
    ]
    for i, n in enumerate(notes[:5]):
        texts.append((margin + 3, margin + _TITLE_H - 12 - 5.0 * i, n, 3.0, "start", ""))

    tmp = part_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    buf = io.BytesIO()
    svg.write(buf)
    body = buf.getvalue().decode()
    # text goes inside ExportSVG's y-flipped group, so each label flips back upright
    ann = ['    <g id="annotations" fill="rgb(0,0,0)" font-family="Menlo, monospace">']
    for x, y, t, sz, anchor, rot in texts:
        rotate = f" rotate({rot})" if rot else ""
        ann.append(f'      <text transform="translate({x:.3f},{y:.3f}) scale(1,-1){rotate}" '
                   f'font-size="{sz}" text-anchor="{anchor}">{_xml(t)}</text>')
    ann.append("    </g>")
    marker = "\n  </g>\n</svg>"
    if marker not in body:
        raise RuntimeError("unexpected SVG layout from ExportSVG")
    body = body.replace(marker, "\n" + "\n".join(ann) + marker)
    (tmp / "new_drawing.svg").write_text(body)

    msp = dxf._modelspace  # ExportDXF has no public text API; this is its ezdxf modelspace
    for x, y, t, sz, _a, rot in texts:
        ent = msp.add_text(t, height=sz, dxfattribs={"layer": "TEXT",
                                                     "rotation": float(rot) if rot else 0.0})
        ent.set_placement((x, y))
    dxf.write(str(tmp / "new_drawing.dxf"))

    info = {"nonce": nonce, "sheet": [SHEET_W, SHEET_H], "scale": scale,
            "scale_text": _scale_text(scale), "views": list(specs), "overall_mm": size,
            "holes": notes, "label": label, "material": material, "date": date,
            "step_mtime_ns": (part_dir / "model.step").stat().st_mtime_ns}
    os.replace(tmp / "new_drawing.svg", part_dir / "drawing.svg")
    os.replace(tmp / "new_drawing.dxf", part_dir / "drawing.dxf")
    (tmp / "new_drawing.json").write_text(json.dumps(info))
    os.replace(tmp / "new_drawing.json", part_dir / "drawing.json")


# ---- assembly geometry (python -I worker.py --assembly <job.json>)
#
# The job file lists instances, each a STEP path plus a 4x4 row-major world transform. We return
# pairwise interference volumes (OCCT common over a threshold) and/or write a combined GLB with
# exactly one node per instance (the transform rides as the glTF node matrix, not baked into the
# geometry, so the app can still address instances individually).


def _trsf(matrix):
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    m = matrix
    t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3],
                m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3])
    return t


def _assembly_interference(placed, threshold: float) -> list:
    """placed: list of (name, moved_shape). Pairwise OCCT common volume over the threshold."""
    out = []
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            (na, sa), (nb, sb) = placed[i], placed[j]
            try:
                vol = float((sa & sb).volume)
            except Exception:  # noqa: BLE001 - non-overlapping solids can raise instead of empty
                vol = 0.0
            if vol > threshold:
                out.append({"a": na, "b": nb, "volume_mm3": round(vol, 4)})
    return out


def _col_major(matrix) -> list:
    return [float(matrix[r][c]) for c in range(4) for r in range(4)]


def _assembly_glb(path: Path, items) -> None:
    """items: list of (name, vertices, triangles, matrix). One node + mesh per instance."""
    import struct

    buf = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []

    def _pad():
        while len(buf) % 4:
            buf.append(0)

    for name, verts, tris, matrix in items:
        idx = [i for tri in tris for i in tri]
        idx_off = len(buf)
        buf += struct.pack(f"<{len(idx)}I", *idx)
        views.append({"buffer": 0, "byteOffset": idx_off, "byteLength": len(idx) * 4,
                      "target": 34963})
        accessors.append({"bufferView": len(views) - 1, "componentType": 5125, "count": len(idx),
                          "type": "SCALAR"})
        idx_acc = len(accessors) - 1
        _pad()
        pos = [float(c) for v in verts for c in (v[0], v[1], v[2])]
        pos_off = len(buf)
        buf += struct.pack(f"<{len(pos)}f", *pos)
        views.append({"buffer": 0, "byteOffset": pos_off, "byteLength": len(pos) * 4,
                      "target": 34962})
        xs, ys, zs = [p[0] for p in verts], [p[1] for p in verts], [p[2] for p in verts]
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "count": len(verts),
                          "type": "VEC3", "min": [min(xs), min(ys), min(zs)],
                          "max": [max(xs), max(ys), max(zs)]})
        pos_acc = len(accessors) - 1
        _pad()
        meshes.append({"primitives": [{"attributes": {"POSITION": pos_acc}, "indices": idx_acc}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1, "matrix": _col_major(matrix)})

    gltf = {"asset": {"version": "2.0", "generator": "Mises assembly"},
            "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
            "nodes": nodes, "meshes": meshes, "accessors": accessors, "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}]}
    jsonb = json.dumps(gltf, separators=(",", ":")).encode()
    jsonb += b" " * ((4 - len(jsonb) % 4) % 4)
    while len(buf) % 4:
        buf.append(0)
    total = 12 + 8 + len(jsonb) + 8 + len(buf)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(jsonb), b"JSON") + jsonb
    out += struct.pack("<I4s", len(buf), b"BIN\x00") + buf
    path.write_bytes(out)


def _assembly(job_path: Path, nonce: str) -> None:
    import build123d as b3
    from build123d import Location

    job = json.loads(job_path.read_text())
    out_dir = Path(job["out_dir"]).resolve()
    want = job.get("want", "both")
    threshold = float(job.get("threshold", 0.01))
    tol = float(job.get("tolerance", 0.2))

    loaded = [(it["name"], b3.import_step(it["step"]), it["transform"]) for it in job["instances"]]
    result: dict = {"nonce": nonce}

    if want in ("both", "interference"):
        placed = [(name, shape.moved(Location(_trsf(m)))) for name, shape, m in loaded]
        result["interferences"] = _assembly_interference(placed, threshold)

    if want in ("both", "glb"):
        items = []
        for name, shape, m in loaded:
            verts, tris = shape.tessellate(tol)
            items.append((name, [(v.X, v.Y, v.Z) for v in verts], tris, m))
        _assembly_glb(out_dir / "new_assembly.glb", items)
        os.replace(out_dir / "new_assembly.glb", out_dir / "assembly.glb")
        result["glb"] = "assembly.glb"

    (out_dir / "new_result.json").write_text(json.dumps(result))
    os.replace(out_dir / "new_result.json", out_dir / "result.json")


def _layout():
    """cad/layout_worker.py (skeleton accessors, cavity, packing), loaded from this file's
    directory: `python -I` puts no script dir on sys.path, and the worker never imports mises."""
    mod = sys.modules.get("mises_layout_worker")
    if mod is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mises_layout_worker", Path(__file__).resolve().parent / "layout_worker.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["mises_layout_worker"] = mod
        spec.loader.exec_module(mod)
    return mod


def _load_part(part_file: Path, skeleton: str | None = None):
    """Execute part.py in a namespace that already has build123d in scope.

    Local models routinely write `-> build123d` annotations or call `Box(...)` /`bd.Box(...)`
    without importing anything. Annotations are never evaluated (the `annotations` future flag),
    and compiling with the real filename keeps tracebacks pointing at part.py line numbers."""
    import build123d

    mod = types.ModuleType("mises_part")
    mod.__file__ = str(part_file)
    ns = mod.__dict__
    ns.update({"math": math, "build123d": build123d, "bd": build123d})
    exec("from build123d import *", ns)  # noqa: S102 - fixed string
    ns["math"] = math  # `from build123d import *` must not shadow the stdlib module
    # resolvers the `# mises-op` blocks call to re-find their target in the current shape
    ns.update({"_mises_face": _mises_face, "_mises_edge": _mises_edge,
               "_mises_faces": _mises_faces, "_mises_edges": _mises_edges,
               "_mises_on_face": _mises_on_face})
    # Plan 8 G3: read-only layout skeleton accessors (skeleton, datum, curve, envelope, ...)
    _layout().install(ns, skeleton, part_file.parent)
    code = compile(part_file.read_text(), str(part_file), "exec",
                   flags=__future__.annotations.compiler_flag, dont_inherit=True)
    exec(code, ns)  # noqa: S102 - the sandboxed part script is the whole point
    return mod


def _hint(e: BaseException) -> str | None:
    if isinstance(e, NameError):
        return ("build123d names are pre-imported (Box, BuildPart, Hole, Locations, ..., plus "
                "`bd`, `build123d`, `math`); check the spelling.")
    if isinstance(e, _MissingBuild):
        return BUILD_SIGNATURE_HINT
    if isinstance(e, TypeError) and "unexpected keyword argument" in str(e):
        return ("build123d shapes take no position keywords (no pos=, offset=, locations=): "
                "inside `with BuildPart() as p:` wrap them in `with Locations((x, y), ...):`, "
                "e.g. `with Locations((-10, 0), (10, 0)): Hole(2.1)`; Hole takes a radius.")
    return None


class _MissingBuild(Exception):
    pass


def _report(e: BaseException, part_file: Path) -> str:
    """Short, agent-facing failure text: only the frames inside part.py, then a one-line hint."""
    import traceback

    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == str(part_file)]
    lines: list[str] = []
    if frames:
        lines.append("Traceback (most recent call last, part.py frames only):")
        lines += [ln.rstrip("\n") for ln in traceback.format_list(frames)]
    lines.append("".join(traceback.format_exception_only(type(e), e)).rstrip("\n"))
    lines.append(f"BUILD_ERROR: {type(e).__name__}: {e}")
    hint = _hint(e)
    if hint:
        lines.append(f"Hint: {hint}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[1] == "--assembly":
        nonce = os.environ.pop("MISES_NONCE", "")
        if not nonce:
            raise RuntimeError("missing build nonce")
        _assembly(Path(argv[2]), nonce)
        return 0
    if argv[1] == "--internals":
        nonce = os.environ.pop("MISES_NONCE", "")
        if not nonce:
            raise RuntimeError("missing build nonce")
        _layout().internals(Path(argv[2]), nonce)
        return 0
    part_dir = Path(argv[1]).resolve()
    if len(argv) > 3 and argv[3] == "--drawing":
        nonce = os.environ.pop("MISES_NONCE", "")
        if not nonce:
            raise RuntimeError("missing build nonce")
        _drawing(part_dir, json.loads(Path(argv[2]).read_text()), nonce)
        return 0
    vars_path = Path(argv[2])
    density = float(os.environ.get("MISES_DENSITY", "1.24"))
    # Read the nonce before the part script runs and keep it out of the environment.
    nonce = os.environ.pop("MISES_NONCE", "")
    if not nonce:
        raise RuntimeError("missing build nonce")
    variables = json.loads(vars_path.read_text())
    skeleton = os.environ.pop("MISES_SKELETON", None)

    try:
        mod = _load_part(part_dir / "part.py", skeleton)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, Exception):
            raise
        raise RuntimeError(f"part.py raised {type(e).__name__}: {e}") from None
    merged = dict(getattr(mod, "VARS", {}) or {})
    merged.update(variables)
    if hasattr(mod, "VARS"):  # scripts may read module-level VARS inside build()
        mod.VARS.clear()
        mod.VARS.update(merged)
    if not callable(getattr(mod, "build", None)):
        raise _MissingBuild("part.py does not define a callable build(v)")
    try:
        shape = mod.build(dict(merged))
    except BaseException as e:  # noqa: BLE001  (SystemExit/KeyboardInterrupt from user code)
        if isinstance(e, Exception):
            raise
        raise RuntimeError(f"build() raised {type(e).__name__}: {e}") from None

    import build123d as b3
    if not hasattr(shape, "volume"):
        raise TypeError("build() must return a build123d Part or Compound")
    shape = shape.part if hasattr(shape, "part") else shape
    metrics = _metrics(shape, density)
    if not (math.isfinite(metrics["volume_mm3"]) and metrics["volume_mm3"] > 0):
        raise ValueError("build() produced an empty or zero-volume shape")
    tags, tag_errors = _face_tags(mod, shape, metrics["face_list"])
    if tags:
        metrics["face_tags"] = tags
    if tag_errors:
        metrics["face_tag_errors"] = tag_errors[:MAX_FACE_TAGS]

    tmp = part_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    def export_glb(s, path):  # binary=True: one self-contained .glb, no external .bin
        b3.export_gltf(s, path, binary=True)
    outs = {"model.step": b3.export_step, "model.stl": b3.export_stl, "model.glb": export_glb}
    for name, fn in outs.items():
        fn(shape, str(tmp / ("new_" + name)))
    (tmp / "new_metrics.json").write_text(json.dumps(metrics))
    for name in list(outs) + ["metrics.json"]:
        os.replace(tmp / ("new_" + name), part_dir / name)
    # Very last step: the result marker the runner trusts (carries the nonce).
    (tmp / "new_result.json").write_text(
        json.dumps({"nonce": nonce, "metrics": metrics, "vars": merged, "op_refs": _RESOLVED,
                    "skeleton_refs": _layout().used_refs()}))
    os.replace(tmp / "new_result.json", part_dir / "result.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        part_file = Path(sys.argv[1]).resolve() / "part.py"
        sys.stderr.write(_report(e, part_file) + "\n")
        sys.exit(1)
