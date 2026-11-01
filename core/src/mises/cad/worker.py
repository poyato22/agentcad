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
        row = {"id": f"f{i}", "type": str(face.geom_type).split(".")[-1].lower(),
               "center": [round(c.X, 3) + 0.0, round(c.Y, 3) + 0.0, round(c.Z, 3) + 0.0],
               "normal": normal, "area": round(float(face.area), 3)}
        r = _radius(face)
        if r is not None:
            row["radius"] = r
        out.append(row)
    return out


def _radius(obj) -> float | None:
    """A cylindrical face's or a circular edge's exact radius (the app's click label), else None."""
    try:
        if hasattr(obj, "geom_type") and _geom(obj) == "cylinder":
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            return round(float(BRepAdaptor_Surface(obj.wrapped).Cylinder().Radius()), 4) + 0.0
        if hasattr(obj, "geom_type") and _geom(obj) == "circle":
            return round(float(obj.radius), 4) + 0.0
    except Exception:  # noqa: BLE001 - an odd surface just has no label radius
        return None
    return None


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
        row = {"id": f"e{i}", "type": _geom(edge), "length": round(float(edge.length), 3),
               "center": center, "direction": direction,
               "adjacent": [[round(c, 4) + 0.0 for c in n] for n in adj.get(edge, [])]}
        r = _radius(edge)
        if r is not None:
            row["radius"] = r
        out.append(row)
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


def _precise_props(shape):
    """OCCT's adaptive BRepGProp volume integration at a tight tolerance (closed shells only),
    run once per build for both the volume and the inertia; None when unavailable."""
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape.wrapped, props, 1e-9, True)
        vol = float(props.Mass())
        if math.isfinite(vol) and vol > 0:
            return props
    except Exception:  # noqa: BLE001 - any OCP trouble: the default volume
        pass
    return None


def _precise_volume(shape, props=...) -> float:
    """The solid volume by OCCT's adaptive BRepGProp integration (closed shells only), for the
    part's mass (Plan 9 B2): build123d's default `volume` can be off by a few percent on
    lofted / B-spline surfaces. Falls back to `shape.volume` when the precise call is
    unavailable or finds no closed volume."""
    props = _precise_props(shape) if props is ... else props
    return float(props.Mass()) if props is not None else float(shape.volume)


def _inertia(shape, density: float, props=...) -> dict | None:
    """Mass properties from the B-rep (Plan 10 C3, for mises.dyn): the precise volume
    integration x density (g/cm3). {mass_kg, com_mm, inertia_kgm2 (3x3, about the centre of
    mass, part axes)}; None when OCCT finds no closed volume."""
    try:
        props = _precise_props(shape) if props is ... else props
        if props is None:
            return None
        vol = float(props.Mass())
        rho = density * 1e-6                 # g/cm3 -> kg/mm3
        c = props.CentreOfMass()
        mat = props.MatrixOfInertia()        # mm^5 (unit density), about the centre of mass
        inertia = [[mat.Value(i, j) * rho * 1e-6 for j in (1, 2, 3)] for i in (1, 2, 3)]
        if not all(math.isfinite(x) for row in inertia for x in row):
            return None
        return {"mass_kg": vol * rho, "com_mm": [c.X(), c.Y(), c.Z()],
                "inertia_kgm2": inertia, "density_g_cm3": density}
    except Exception:  # noqa: BLE001 - mass properties are best effort
        return None


def _metrics(shape, density: float) -> dict:
    bb = shape.bounding_box()
    props = _precise_props(shape)
    vol = _precise_volume(shape, props)
    com = shape.center()
    return {
        "inertia": _inertia(shape, density, props),
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


# ---- measure (python -I worker.py <part_dir> <args.json> --measure)
#
# Exact B-rep measurements of picked faces / edges / vertices / points on the built part
# (model.step, never the part script). The app names faces and edges by their metrics ids; the
# STEP round trip may reorder topology (edges do), so each id is re-found by its metrics
# signature: same type, same length / area, and its recorded point on it. A point snaps to a
# vertex within _SNAP_MM, else to the closest point of the part's surface.

_SNAP_MM = 0.02
_PARALLEL_DEG = 0.01


def _vec(p) -> list[float]:
    return [float(p.X), float(p.Y), float(p.Z)]


def _pnt(p) -> list[float]:
    """An OCCT gp_Pnt as [x, y, z]."""
    return [float(p.X()), float(p.Y()), float(p.Z())]


def _round_list(v, n: int = 6) -> list[float]:
    return [round(float(c), n) + 0.0 for c in v]


def _measure_match_face(faces, row: dict):
    from build123d import CenterOf
    best, best_d = None, 0.01
    a0 = float(row["area"])
    for i, f in enumerate(faces):
        if _geom(f) != row["type"] or abs(float(f.area) - a0) > 0.002 + 1e-6 * a0:
            continue
        d = math.dist(_vec(f.center(CenterOf.MASS)), row["center"])
        if d <= best_d:
            best, best_d = (i, f), d
    return best


def _measure_match_edge(edges, row: dict):
    from build123d import Vector
    best, best_d = None, 0.01
    l0 = float(row["length"])
    if row.get("center") is None:
        return None
    p = Vector(*row["center"])
    for i, e in enumerate(edges):
        if _geom(e) != row["type"] or abs(float(e.length) - l0) > 0.002 + 1e-6 * l0:
            continue
        d = float(e.distance_to(p))
        if d <= best_d:
            best, best_d = (i, e), d
    return best


def _face_info(face, fid: str) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from build123d import CenterOf
    info: dict = {"kind": "face", "id": fid, "type": _geom(face), "area": float(face.area),
                  "center": _vec(face.center(CenterOf.MASS))}
    surf = BRepAdaptor_Surface(face.wrapped)
    if surf.GetType() == GeomAbs_Plane:
        info["normal"] = _xyz(face.normal_at(face.center(CenterOf.MASS)))
    elif surf.GetType() == GeomAbs_Cylinder:
        cyl = surf.Cylinder()
        ax = cyl.Axis()
        info["radius"] = float(cyl.Radius())
        info["axis"] = _canon([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        info["axis_point"] = [ax.Location().X(), ax.Location().Y(), ax.Location().Z()]
    return info


def _edge_info(edge, eid: str) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    info: dict = {"kind": "edge", "id": eid, "type": _geom(edge), "length": float(edge.length)}
    curve = BRepAdaptor_Curve(edge.wrapped)
    if curve.GetType() == GeomAbs_Line:
        info["start"] = _vec(edge.position_at(0))
        info["end"] = _vec(edge.position_at(1))
        info["direction"] = _canon(_xyz(edge.tangent_at(0.5)))
    elif curve.GetType() == GeomAbs_Circle:
        circ = curve.Circle()
        c, ax = circ.Location(), circ.Axis().Direction()
        info["radius"] = float(circ.Radius())
        info["center"] = [c.X(), c.Y(), c.Z()]
        info["axis"] = _canon([ax.X(), ax.Y(), ax.Z()])
        sweep = abs(curve.LastParameter() - curve.FirstParameter())
        info["arc_deg"] = min(360.0, math.degrees(sweep))
    return info


def _measure_resolve(shape, metrics: dict, ref: dict):
    """One ref -> (the shape distances are measured from, its info)."""
    from build123d import Vector, Vertex
    if not isinstance(ref, dict) or len(ref) != 1:
        raise ValueError("each ref is one of {face_id}, {edge_id}, {vertex_id} or {point}")
    (key, val), = ref.items()
    if key == "face_id":
        row = next((f for f in metrics.get("face_list") or [] if f.get("id") == val), None)
        hit = _measure_match_face(list(shape.faces()), row) if row else None
        if hit is None:
            raise LookupError(f"face {val!r} is not on this version of the part; pick again")
        return hit[1], _face_info(hit[1], str(val))
    if key == "edge_id":
        row = next((e for e in metrics.get("edge_list") or [] if e.get("id") == val), None)
        hit = _measure_match_edge(list(shape.edges()), row) if row else None
        if hit is None:
            raise LookupError(f"edge {val!r} is not on this version of the part; pick again")
        return hit[1], _edge_info(hit[1], str(val))
    verts = list(shape.vertices())
    if key == "vertex_id":
        m = re.fullmatch(r"v(\d+)", str(val))
        if not m or int(m.group(1)) >= len(verts):
            raise LookupError(f"vertex {val!r} is not on this version of the part; pick again")
        v = verts[int(m.group(1))]
        return v, {"kind": "vertex", "id": str(val), "point": _vec(v)}
    if key == "point":
        if (not isinstance(val, (list, tuple)) or len(val) != 3
                or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                           and math.isfinite(c) for c in val)):
            raise ValueError("point must be [x, y, z] in mm")
        p = Vector(*[float(c) for c in val])
        best = min(range(len(verts)), key=lambda i: (Vector(verts[i]) - p).length, default=None)
        if best is not None and (Vector(verts[best]) - p).length <= _SNAP_MM:
            v = verts[best]
            return v, {"kind": "vertex", "id": f"v{best}", "point": _vec(v)}
        # the closest point of the part's surface (a click lands on a tessellated facet)
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        ext = BRepExtrema_DistShapeShape(shape.wrapped, Vertex(p.X, p.Y, p.Z).wrapped)
        on = _pnt(ext.PointOnShape1(1)) if ext.IsDone() and ext.NbSolution() else _vec(p)
        return Vertex(*on), {"kind": "point", "point": on}
    raise ValueError(f"unknown ref {key!r}: use face_id, edge_id, vertex_id or point")


def _angle_deg(a, b) -> float:
    """Angle between two lines (or plane normals, cylinder axes): no sense, so in [0, 90]."""
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    c = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
    deg = math.degrees(math.acos(c))
    return min(deg, 180.0 - deg)


def _val(key: str, label: str, value: float, unit: str) -> dict:
    return {"key": key, "label": label, "value": round(float(value), 6) + 0.0, "unit": unit}


def _measure_one(info: dict) -> list[dict]:
    out: list[dict] = []
    if info["kind"] == "edge":
        if "radius" in info:
            out += [_val("diameter", "Diameter", 2 * info["radius"], "mm"),
                    _val("radius", "Radius", info["radius"], "mm")]
            if info["arc_deg"] < 359.999:
                out.append(_val("arc_angle", "Arc angle", info["arc_deg"], "°"))
        out.append(_val("length", "Length", info["length"], "mm"))
    elif info["kind"] == "face":
        if "radius" in info:
            out += [_val("diameter", "Diameter", 2 * info["radius"], "mm"),
                    _val("radius", "Radius", info["radius"], "mm")]
        out.append(_val("area", "Area", info["area"], "mm²"))
    else:
        p = info["point"]
        out += [_val("x", "X", p[0], "mm"), _val("y", "Y", p[1], "mm"), _val("z", "Z", p[2], "mm")]
    return out


def _direction(info: dict):
    """What an angle compares: a line edge's direction, a plane's normal, a cylinder's axis."""
    if info["kind"] == "edge":
        return info.get("direction")
    if info["kind"] == "face":
        return info.get("normal") or info.get("axis")
    return None


def _measure_two(sa, ia: dict, sb, ib: dict) -> list[dict]:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    ext = BRepExtrema_DistShapeShape(sa.wrapped, sb.wrapped)
    if not ext.IsDone() or ext.NbSolution() == 0:
        raise ValueError("could not measure the distance between these two")
    pa, pb = _pnt(ext.PointOnShape1(1)), _pnt(ext.PointOnShape2(1))
    out = [_val("distance", "Distance", ext.Value(), "mm"),
           _val("dx", "ΔX", abs(pb[0] - pa[0]), "mm"), _val("dy", "ΔY", abs(pb[1] - pa[1]), "mm"),
           _val("dz", "ΔZ", abs(pb[2] - pa[2]), "mm")]
    da, db = _direction(ia), _direction(ib)
    if da is not None and db is not None:
        ang = _angle_deg(da, db)
        # a line (an edge, or a cylinder's axis) against a plane: the angle to the plane, not to
        # its normal; a hole drilled straight through a plate is at 90°
        if ("normal" in ia) != ("normal" in ib):
            ang = 90.0 - ang
        out.append(_val("angle", "Angle", ang, "°"))
        if "normal" in ia and "normal" in ib and ang < _PARALLEL_DEG:
            n = ia["normal"]
            gap = abs(sum((ib["center"][i] - ia["center"][i]) * n[i] for i in range(3)))
            out.insert(0, _val("normal_distance", "Normal distance", gap, "mm"))
    if all(i["kind"] == "edge" and "radius" in i for i in (ia, ib)):
        out.append(_val("center_distance", "Center distance",
                        math.dist(ia["center"], ib["center"]), "mm"))
    if (all(i["kind"] == "face" and "axis" in i for i in (ia, ib))
            and _angle_deg(ia["axis"], ib["axis"]) < _PARALLEL_DEG):
        a, d = ia["axis_point"], ia["axis"]
        w = [ib["axis_point"][i] - a[i] for i in range(3)]
        t = sum(w[i] * d[i] for i in range(3))
        out.append(_val("center_distance", "Center distance",
                        math.sqrt(max(0.0, sum(x * x for x in w) - t * t)), "mm"))
    return out


def _measure_compute(shape, metrics: dict, refs, cache: dict | None = None) -> dict:
    """{items, values} for the picks, or {error: {code, message}}. `cache` (the serving worker's)
    keeps each resolved pick, so A then A+B then A+C re-finds A once."""
    import copy
    try:
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError("refs must list 1 to 8 picks")
        picked = []
        for r in refs:
            key = json.dumps(r, sort_keys=True)
            hit = cache.get(key) if cache is not None else None
            if hit is None:
                hit = _measure_resolve(shape, metrics, r)
                if cache is not None:
                    cache[key] = hit
            picked.append((hit[0], copy.deepcopy(hit[1])))
        infos = [i for _, i in picked]
        if len(picked) == 1:
            values = _measure_one(infos[0])
        elif len(picked) == 2:
            values = _measure_two(picked[0][0], infos[0], picked[1][0], infos[1])
        elif all(i["kind"] == "edge" for i in infos):
            values = [_val("total_length", "Total length", sum(i["length"] for i in infos), "mm")]
        elif all(i["kind"] == "face" for i in infos):
            values = [_val("total_area", "Total area", sum(i["area"] for i in infos), "mm²")]
        else:
            raise ValueError("pick one or two items (or several edges, or several faces)")
        for i in infos:
            for k in ("center", "normal", "axis", "axis_point", "start", "end", "direction", "point"):
                if k in i:
                    i[k] = _round_list(i[k])
            for k in ("area", "length", "radius", "arc_deg"):
                if k in i:
                    i[k] = round(float(i[k]), 6) + 0.0
        return {"items": infos, "values": values}
    except LookupError as e:
        return {"error": {"code": "not_found", "message": str(e)}}
    except ValueError as e:
        return {"error": {"code": "invalid_input", "message": str(e)}}


def _measure(part_dir: Path, args: dict, nonce: str) -> None:
    import build123d as b3

    metrics = json.loads((part_dir / "metrics.json").read_text())
    shape = b3.import_step(str(part_dir / "model.step"))
    result = {"nonce": nonce, **_measure_compute(shape, metrics, args.get("refs"))}
    tmp = part_dir / "tmp"
    (tmp / "new_measure.json").write_text(json.dumps(result))
    os.replace(tmp / "new_measure.json", tmp / "measure.json")


MEASURE_MARK = "MISES_MEASURE"


def _measure_serve(part_dir: Path, nonce: str) -> None:
    """The warm measure worker: load the shape once, then answer one JSON request per stdin line
    ({id, refs}) with one stdout line "MISES_MEASURE <nonce> {id, ...}" (other output, e.g. a
    library's warning, is never mistaken for an answer). Exits at EOF: the host closes stdin when
    it evicts the worker, and a dead host closes it too."""
    import build123d as b3

    metrics = json.loads((part_dir / "metrics.json").read_text())
    shape = b3.import_step(str(part_dir / "model.step"))
    cache: dict = {}
    out = sys.stdout
    out.write(f"{MEASURE_MARK} {nonce} " + json.dumps({"id": 0, "ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            req = json.loads(line)
            rid = req.get("id") if isinstance(req, dict) else None
            res = _measure_compute(shape, metrics, req.get("refs") if isinstance(req, dict) else None, cache)
        except Exception as e:  # noqa: BLE001 - one bad request never ends the worker
            rid, res = None, {"error": {"code": "tool_failed", "message": f"{type(e).__name__}: {e}"}}
        out.write(f"{MEASURE_MARK} {nonce} " + json.dumps({"id": rid, **res}) + "\n")
        out.flush()


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


def _arr1(a) -> list:
    """OCCT Array1 (TColgp_Array1OfPnt, TColStd_Array1OfReal, ...) -> a Python list.

    build123d's SVG/DXF exporters iterate these arrays directly (`for p in curve.Poles()`).
    OCP's pybind11 `__iter__` for an Array1 is pathologically slow — ~1 ms per element — so a
    drawing of a part with many hidden curved edges (every hole seen edge-on becomes a hidden
    B-spline, each broken into several Bézier arcs) spends tens of seconds iterating poles.
    Indexed `Value(k)` access over Lower()..Upper() is ~200x faster and returns the same points.
    """
    return [a.Value(k) for k in range(a.Lower(), a.Upper() + 1)]


#: The patch below is a verbatim copy of these build123d internals; any other version keeps
#: build123d's own (slow but correct) exporters rather than silently freezing old behaviour.
_FAST_EXPORT_FOR = "0.13."


def _patch_fast_bspline_export() -> bool:
    """Replace the two exporter methods that iterate OCCT pole/knot arrays with versions that
    index them instead. The geometry (and therefore the SVG path data and DXF spline) is
    byte-for-byte identical; only the hot Python-level iteration changes. Idempotent. Returns
    whether the fast path is active (False on a build123d version it was not written for)."""
    import build123d
    import build123d.exporters as ex

    if getattr(ex.ExportSVG, "_mises_fast_poles", False):
        return True
    if not str(getattr(build123d, "__version__", "")).startswith(_FAST_EXPORT_FOR):
        return False

    def _svg_bspline_segments(self, edge, reverse):
        edge = edge.to_splines()
        adaptor = edge.geom_adaptor()
        spline = adaptor.Curve().Curve().Copy()
        u1, u2 = adaptor.FirstParameter(), adaptor.LastParameter()
        if not edge or edge.location is None:
            raise ValueError(f"Edge is empty {edge}.")
        spline.Transform(edge.location.wrapped.Transformation())
        converter = ex.GeomConvert_BSplineCurveToBezierCurve(
            ex.tcast(ex.Geom_BSplineCurve, spline), u1, u2, ex.Export2D.PARAMETRIC_TOLERANCE)

        def make_segment(bezier, reverse):
            p = [self._path_point(x) for x in _arr1(bezier.Poles())]
            if reverse:
                p.reverse()
            if len(p) == 2:
                return ex.PT.Line(start=p[0], end=p[1])
            if len(p) == 3:
                return ex.PT.QuadraticBezier(start=p[0], control=p[1], end=p[2])
            if len(p) == 4:
                return ex.PT.CubicBezier(start=p[0], control1=p[1], control2=p[2], end=p[3])
            raise ValueError(f"Surprising Bézier of degree {bezier.Degree()}!")

        result = [make_segment(converter.Arc(i), reverse)
                  for i in range(1, converter.NbArcs() + 1)]
        if reverse:
            result.reverse()
        return result

    def _dxf_convert_bspline(self, edge, attribs):
        edge = edge.to_splines()
        adaptor = edge.geom_adaptor()
        curve = adaptor.Curve().Curve()
        u1, u2 = adaptor.FirstParameter(), adaptor.LastParameter()
        spline = ex.GeomConvert.SplitBSplineCurve_s(
            ex.tcast(ex.Geom_BSplineCurve, curve), u1, u2, ex.Export2D.PARAMETRIC_TOLERANCE)
        if not edge or edge.location is None:
            raise ValueError(f"Edge is empty {edge}.")
        spline.Transform(edge.location.wrapped.Transformation())
        order = spline.Degree() + 1
        knots = _arr1(spline.KnotSequence())
        poles = [self._convert_point(p) for p in _arr1(spline.Poles())]
        weights = ([spline.Weight(i) for i in range(1, spline.NbPoles() + 1)]
                   if spline.IsRational() else None)
        if spline.IsPeriodic():
            pad = spline.NbKnots() - spline.LastUKnotIndex()
            poles += poles[:pad]
        dxf_spline = ex.ezdxf.math.BSpline(poles, order, knots, weights)
        self._modelspace.add_spline(dxfattribs=attribs).apply_construction_tool(dxf_spline)

    # _other_* delegate to the bspline path in stock build123d, so repoint them too.
    def _svg_other_segments(self, edge, reverse):
        return _svg_bspline_segments(self, edge, reverse)

    def _svg_bspline_element(self, edge):
        path = ex.PT.Path(*_svg_bspline_segments(self, edge, False))
        return ex.ET.Element("path", {"d": path.d()})

    # The exporters dispatch each edge through a class-level {GeomType: method} table built at
    # import from the original functions; reassigning the attributes is not enough, so the
    # B-spline entries of those tables are repointed too. (target, key, is_table_entry, fast)
    bspline = ex.GeomType.BSPLINE
    swaps = [(ex.ExportSVG, "_bspline_segments", False, _svg_bspline_segments),
             (ex.ExportSVG, "_other_segments", False, _svg_other_segments),
             (ex.ExportSVG, "_bspline_element", False, _svg_bspline_element),
             (ex.ExportSVG, "_other_element", False, _svg_bspline_element),
             (ex.ExportDXF, "_convert_bspline", False, _dxf_convert_bspline),
             (ex.ExportDXF, "_convert_other", False, _dxf_convert_bspline),
             (ex.ExportSVG._SEGMENT_LOOKUP, bspline, True, _svg_bspline_segments),
             (ex.ExportSVG._ELEMENT_LOOKUP, bspline, True, _svg_bspline_element),
             (ex.ExportDXF._CONVERTER_LOOKUP, bspline, True, _dxf_convert_bspline)]
    # build123d's originals, so a test can put stock back and compare whole drawings
    ex.ExportSVG._mises_stock = [(t, k, item, t[k] if item else t.__dict__[k])
                                 for t, k, item, _ in swaps]
    for t, k, item, fast in swaps:
        if item:
            t[k] = fast
        else:
            setattr(t, k, fast)
    ex.ExportSVG._mises_fast_poles = True
    return True


def _drawing(part_dir: Path, args: dict, nonce: str) -> None:
    import io

    import build123d as b3
    from build123d import Compound, Edge, ExportDXF, ExportSVG, LineType, Location, Polyline

    _patch_fast_bspline_export()
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


def _rigid(m):
    """The 4x4 with its rotation re-orthonormalised (Gram-Schmidt on the columns): a solved
    pose carries float noise, and gp_Trsf takes a non-orthogonal matrix as a slight distortion."""
    cols = [[m[r][c] for r in range(3)] for c in range(3)]
    out = []
    for c in cols:
        for o in out:
            d = sum(c[k] * o[k] for k in range(3))
            c = [c[k] - d * o[k] for k in range(3)]
        n = sum(x * x for x in c) ** 0.5 or 1.0
        out.append([x / n for x in c])
    return [[out[0][r], out[1][r], out[2][r], m[r][3]] for r in range(3)] + [[0, 0, 0, 1]]


def _inv_rigid(m):
    r = [[m[c][r] for c in range(3)] for r in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [r[i] + [t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _apply(m, p):
    return [m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] for r in range(3)]


def _trsf(matrix):
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    m = _rigid(matrix)
    t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3],
                m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3])
    return t


def _world_box(shape, m):
    """The world AABB of a part-frame shape posed by `m` (its bbox corners, transformed)."""
    bb = shape.bounding_box()
    lo, hi = (bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z)
    pts = [_apply(m, [lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1],
                      lo[2] if i & 4 else hi[2]]) for i in range(8)]
    return ([min(p[a] for p in pts) for a in range(3)], [max(p[a] for p in pts) for a in range(3)])


def _assembly_interference(items, threshold: float) -> list:
    """items: list of (name, part-frame shape, world matrix, instance id). Pairwise OCCT common
    volume over the threshold. G2: the common is taken in the first part's own frame (the second
    moved by the pair's RELATIVE pose), so it depends only on how the two parts sit against each
    other, never on where the pair is in the world (in world space the same pair gave 0, 0.12,
    9.7 and 36.5 mm^3 on the ornicopter's four wing roots). World boxes prefilter the pairs.
    Each hit carries the instance ids and where the overlap is (world centre and box)."""
    from build123d import Location
    from OCP.gp import gp_Trsf

    def loc(m):
        return Location(_trsf(m)) if m is not None else Location(gp_Trsf())
    boxes = [_world_box(s, m) for _n, s, m, _i in items]
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (na, sa, ma, ia), (nb, sb, mb, ib) = items[i], items[j]
            (alo, ahi), (blo, bhi) = boxes[i], boxes[j]
            if any(ahi[k] < blo[k] or bhi[k] < alo[k] for k in range(3)):
                continue
            ra = _rigid(ma)
            try:
                common = sa & sb.moved(loc(_mul(_inv_rigid(ra), _rigid(mb))))
                vol = float(getattr(common, "volume", 0.0) or 0.0)
            except Exception:  # noqa: BLE001 - non-overlapping solids can raise instead of empty
                common, vol = None, 0.0
            if vol > threshold:
                hit = {"a": na, "b": nb, "volume_mm3": round(vol, 4)}
                if ia is not None:
                    hit.update(a_id=ia, b_id=ib)
                try:
                    lo, hi = _world_box(common, ra)
                    hit["bbox"] = {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]}
                    hit["center"] = [round((lo[k] + hi[k]) / 2, 3) for k in range(3)]
                except Exception:  # noqa: BLE001 - the volume stands without a location
                    pass
                out.append(hit)
    return out


def _sweep_pose(loaded, pose: dict, threshold: float, location) -> dict:
    """One sweep position: pairwise interference, and the smallest clearance between any pair
    (0 where they overlap) with that pair's names."""
    placed = [(name, shape.moved(location(_trsf(pose.get(name, m))))) for name, shape, m, _i in loaded]
    hits = _assembly_interference([(name, shape, pose.get(name, m), None)
                                   for name, shape, m, _i in loaded], threshold)
    overlap = {(h["a"], h["b"]) for h in hits}
    best = None
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            (na, sa), (nb, sb) = placed[i], placed[j]
            if (na, nb) in overlap:
                d = 0.0
            else:
                try:
                    d = float(sa.distance_to(sb))
                except Exception:  # noqa: BLE001 - an unmeasurable pair is skipped
                    continue
            if best is None or d < best[0]:
                best = (d, na, nb)
    return {"interferences": hits,
            "min_clearance_mm": None if best is None else round(best[0], 4),
            "closest": None if best is None else [best[1], best[2]]}


def _col_major(matrix) -> list:
    return [float(matrix[r][c]) for c in range(4) for r in range(4)]


# P11 F4 (mises.cad.glb_frame): model.glb is written as R·L (the shape's location L, then
# Z-up -> Y-up R = Rx(-90°)); build123d's export_gltf composes L·R (`location *= R`), which
# leaves a located shape's translation in the Z-up frame (the app then drew a `Pos(0,0,360)*tube`
# 360 mm off its axis). Exporting with the location conjugated, L' = R·L·R⁻¹, gives
# L'·R = R·L exactly, without touching the geometry.
GLB_FRAME = 2


def _export_glb(b3, shape, path) -> None:
    rot = b3.Location((0, 0, 0), (1, 0, 0), -90)
    loc = shape.location
    shape.location = rot * loc * rot.inverse()
    try:
        b3.export_gltf(shape, path, binary=True)
    finally:
        shape.location = loc


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


def _flat_step(b3, path: str):
    """A part's STEP as one plain compound of its solids. A library assembly (a bd_warehouse
    bearing: races + balls) comes back from STEP as nested located compounds that moving and
    the booleans place wrongly (Plan 10 C2: a 608 off the origin reported 107 mm3 of phantom
    overlap); its solids carry their placement, so a flat compound of them is the same part."""
    shape = b3.import_step(path)
    solids = shape.solids() if hasattr(shape, "solids") else []
    if len(solids) > 1:
        return b3.Compound(list(solids))
    return shape


def _assembly(job_path: Path, nonce: str) -> None:
    import build123d as b3
    from build123d import Location

    job = json.loads(job_path.read_text())
    out_dir = Path(job["out_dir"]).resolve()
    want = job.get("want", "both")
    threshold = float(job.get("threshold", 0.01))
    tol = float(job.get("tolerance", 0.2))

    loaded = [(it["name"], _flat_step(b3, it["step"]), it["transform"], it.get("id"))
              for it in job["instances"]]
    result: dict = {"nonce": nonce}

    if want == "sweep":
        # Plan 7 R19: one worker for a whole joint sweep; each STEP is read once. Every pose
        # maps instance name -> transform (missing names keep their job transform).
        result["positions"] = [_sweep_pose(loaded, pose, threshold, Location)
                               for pose in job.get("poses") or []]

    if want in ("both", "interference"):
        result["interferences"] = _assembly_interference(loaded, threshold)

    if want in ("both", "glb"):
        items = []
        for name, shape, m, _iid in loaded:
            verts, tris = shape.tessellate(tol)
            items.append((name, [(v.X, v.Y, v.Z) for v in verts], tris, m))
        _assembly_glb(out_dir / "new_assembly.glb", items)
        os.replace(out_dir / "new_assembly.glb", out_dir / "assembly.glb")
        result["glb"] = "assembly.glb"

    (out_dir / "new_result.json").write_text(json.dumps(result))
    os.replace(out_dir / "new_result.json", out_dir / "result.json")


def _mises_shapes():
    """The curved-geometry recipe module that sits next to this file (cad/mises_shapes.py).

    Loaded by path (the worker runs with -I, so its directory is not on sys.path) and registered
    as `mises_shapes`, so `import mises_shapes` in a part script works too."""
    import importlib.util

    if "mises_shapes" in sys.modules:
        return sys.modules["mises_shapes"]
    spec = importlib.util.spec_from_file_location(
        "mises_shapes", Path(__file__).resolve().parent / "mises_shapes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mises_shapes"] = mod
    spec.loader.exec_module(mod)
    return mod


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
    shapes = _mises_shapes()
    ns.update({"mises_shapes": shapes, "ms": shapes})
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
                "`bd`, `build123d`, `math`, and the curved-shape recipes as `ms` / "
                "`mises_shapes`); check the spelling.")
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
    if len(argv) > 2 and argv[2] == "--measure-serve":
        nonce = os.environ.pop("MISES_NONCE", "")
        if not nonce:
            raise RuntimeError("missing build nonce")
        _measure_serve(part_dir, nonce)
        return 0
    if len(argv) > 3 and argv[3] == "--measure":
        nonce = os.environ.pop("MISES_NONCE", "")
        if not nonce:
            raise RuntimeError("missing build nonce")
        _measure(part_dir, json.loads(Path(argv[2]).read_text()), nonce)
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
    if not hasattr(shape, "volume") and callable(getattr(shape, "compound", None)):
        shape = shape.compound()        # e.g. ms.enclosure(...) returned as-is
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
        _export_glb(b3, s, path)
    metrics["glb_frame"] = GLB_FRAME
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
