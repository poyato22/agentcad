"""Worker side of layout skeletons and internals packing (Plan 8 G3).

cad/worker.py loads this file from its own directory (the worker runs `python -I`, which puts no
script dir on sys.path and must not import mises). Stdlib only at import time: build123d is
imported inside the functions that need it, so the host can import the pure box packer too.

Skeleton: the host writes the build's skeleton snapshot into the part's host-only meta dir (the
sandbox may read the parts tree but write only the part dir), and passes its path in
MISES_SKELETON. `install` puts read-only accessors into the part script's namespace:
`skeleton(name)`, `datum(name)`, `curve(name)`, `envelope()` and `inner_volume(part_id)`; each
returns a fresh value, so a script can never change the skeleton other parts see. Every access is
recorded and returned in result.json as `skeleton_refs`, which the host stores per build for
dependency regeneration (untrusted: the host validates it).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

_EPS = 1e-6
MAX_PACK = 200

_USED: dict = {"params": set(), "datums": set(), "curves": set(), "envelope": False,
               "parts": set()}


def used_refs() -> dict:
    """The skeleton names (and other parts) this build read, for result.json."""
    return {"params": sorted(_USED["params"]), "datums": sorted(_USED["datums"]),
            "curves": sorted(_USED["curves"]), "envelope": bool(_USED["envelope"]),
            "parts": sorted(_USED["parts"])}


def _load_snapshot(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _no_skeleton(what: str) -> ValueError:
    return ValueError(f"{what}: this build has no layout skeleton yet — create it with "
                      "skeleton_set, or pass default= for a value the part can live without")


def install(ns: dict, snapshot_path: str | None, part_dir: Path) -> None:
    """Inject the skeleton accessors into a part script's namespace."""
    data = _load_snapshot(snapshot_path)
    params = dict((data or {}).get("params") or {})
    datums = dict((data or {}).get("datums") or {})
    curves = dict((data or {}).get("curves") or {})
    env = (data or {}).get("envelope")
    root = Path((data or {}).get("parts_root") or Path(part_dir).resolve().parent)
    missing = object()

    def skeleton(name: str, default=missing) -> float:
        """A named skeleton parameter (mm, deg or a plain number), e.g. skeleton("wing_span")."""
        _USED["params"].add(str(name))
        if name in params:
            return float(params[name])
        if default is not missing:
            return default
        if data is None:
            raise _no_skeleton(f"skeleton({name!r})")
        raise KeyError(f"skeleton({name!r}): no such parameter; the skeleton has "
                       f"{sorted(params) or 'none'}")

    def datum(name: str):
        """A named datum: a build123d Plane, Axis or Vector (point), fresh on every call."""
        import build123d as bd
        _USED["datums"].add(str(name))
        d = datums.get(name)
        if d is None:
            if data is None:
                raise _no_skeleton(f"datum({name!r})")
            raise KeyError(f"datum({name!r}): no such datum; the skeleton has "
                           f"{sorted(datums) or 'none'}")
        origin = tuple(d.get("origin") or (0, 0, 0))
        if d["kind"] == "plane":
            x = d.get("x_dir")
            return bd.Plane(origin=origin, x_dir=tuple(x) if x else None,
                            z_dir=tuple(d["normal"]))
        if d["kind"] == "axis":
            return bd.Axis(origin, tuple(d["direction"]))
        return bd.Vector(*origin)

    def curve(name: str):
        """A named reference curve: a Wire (polyline) or an Edge (spline)."""
        import build123d as bd
        _USED["curves"].add(str(name))
        c = curves.get(name)
        if c is None:
            if data is None:
                raise _no_skeleton(f"curve({name!r})")
            raise KeyError(f"curve({name!r}): no such curve; the skeleton has "
                           f"{sorted(curves) or 'none'}")
        pts = [bd.Vector(*p) for p in c["points"]]
        closed = bool(c.get("closed"))
        if c["kind"] == "spline":
            return bd.Edge.make_spline(pts + ([pts[0]] if closed else []), periodic=False)
        return bd.Wire.make_polygon(pts, close=closed)

    def envelope():
        """The skeleton's envelope as a box Solid (the space the whole design must fit)."""
        import build123d as bd
        _USED["envelope"] = True
        if not env:
            if data is None:
                raise _no_skeleton("envelope()")
            raise KeyError("envelope(): the skeleton has no envelope")
        lo, hi = env["min"], env["max"]
        return bd.Solid.make_box(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]).moved(
            bd.Location(tuple(lo)))

    def inner_volume(part_id: str):
        """The cavity solid of another built part (a case, a shell), for internals."""
        import build123d as bd
        if not isinstance(part_id, str) or not part_id.replace("_", "a").isalnum():
            raise ValueError(f"inner_volume({part_id!r}): not a part id")
        _USED["parts"].add(part_id)
        step = root / part_id / "model.step"
        if not step.is_file():
            raise FileNotFoundError(f"inner_volume({part_id!r}): that part is not built")
        return cavity(bd.import_step(str(step)))

    bd_pack = ns.get("pack")

    def pack_any(components, *args, **kw):
        """pack(components, cavity, clearance_mm, strategy, keep_outs) for internals; build123d's
        own pack(objects, padding, align_z), which this name shadows, still works with shapes."""
        items = list(components)
        if items and not isinstance(items[0], dict) and callable(bd_pack):
            return bd_pack(items, *args, **kw)
        return pack(items, *args, **kw)

    ns.update({"skeleton": skeleton, "datum": datum, "curve": curve, "envelope": envelope,
               "inner_volume": inner_volume, "pack": pack_any})


# ---- cavity

_SIDES = ("x-min", "x-max", "y-min", "y-max", "z-min", "z-max")


def _touched(sb, bb, tol: float) -> list[str]:
    lo, hi = (bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z)
    slo, shi = (sb.min.X, sb.min.Y, sb.min.Z), (sb.max.X, sb.max.Y, sb.max.Z)
    out = []
    for i in range(3):
        if abs(slo[i] - lo[i]) <= tol:
            out.append(_SIDES[2 * i])
        if abs(shi[i] - hi[i]) <= tol:
            out.append(_SIDES[2 * i + 1])
    return out


def cavity_parts(shape) -> list[tuple]:
    """The enclosed voids of a shape as [(solid, open sides)]. The void is the shape's bounding
    box minus the shape. A piece that touches no side of that box is closed inside it; one that
    touches the sides of only ONE axis (an open-top case, or a tube open at both ends) is an open
    cavity. Pieces touching sides on two or more axes are the space around the outside (the
    corners left by fillets or a non-box outline), never the inside."""
    import build123d as bd
    bb = shape.bounding_box()
    size = (bb.size.X, bb.size.Y, bb.size.Z)
    tol = max(1e-4, 1e-6 * max(size))
    box = bd.Solid.make_box(*size).moved(bd.Location((bb.min.X, bb.min.Y, bb.min.Z)))
    try:
        void = box - shape
        solids = list(void.solids())
    except Exception:  # noqa: BLE001 - a fully solid shape can leave nothing to cut
        solids = []
    out = []
    for s in solids:
        if float(s.volume) <= _EPS:
            continue
        sides = _touched(s.bounding_box(), bb, tol)
        if len({side[0] for side in sides}) <= 1:
            out.append((s, sides))
    return out


def cavity(shape):
    """The cavity solid of a case: its largest enclosed void, or a Compound of all of them."""
    import build123d as bd
    parts = cavity_parts(shape)
    if not parts:
        raise ValueError("no enclosed cavity found: the shape has no inside (shell or hollow "
                         "it first, e.g. with offset/shell, or give the cavity as a box)")
    if len(parts) == 1:
        return parts[0][0]
    return bd.Compound([s for s, _ in parts])


# ---- packing (pure python; `inside` lets the worker test a real cavity solid)

def _sep(a_lo, a_sz, b_lo, b_sz, gap: float) -> bool:
    """True when the boxes are at least `gap` apart along some axis."""
    for i in range(3):
        if a_lo[i] + a_sz[i] + gap <= b_lo[i] + _EPS or b_lo[i] + b_sz[i] + gap <= a_lo[i] + _EPS:
            return True
    return False


def _orients(size, allow_rotate: bool) -> list[tuple[tuple, bool]]:
    s = tuple(float(x) for x in size)
    out = [(s, False)]
    if allow_rotate and abs(s[0] - s[1]) > _EPS:
        out.append(((s[1], s[0], s[2]), True))
    return out


def _r(v) -> list[float]:
    return [round(float(x), 4) + 0.0 for x in v]


def pack_boxes(region_min, region_max, components, clearance_mm: float = 2.0,
               strategy: str = "layered", keep_outs=(), inside=None,
               allow_rotate: bool = True) -> dict:
    """Greedy box placement inside a region (a cavity's bounding box).

    components: [{"name", "size": [x, y, z]}]. Each component keeps `clearance_mm` to the region
    walls, to every other component and to every keep-out box ({"min", "max"}). `inside(lo,
    size)`, when given, must also accept the clearance-inflated box (the worker tests it against
    the real cavity solid, so bosses and curved walls count). Strategies:
    - "layered": biggest first, each at the lowest, then front-most, then left-most free corner
      (extreme points), so components fill the floor and then stack in layers;
    - "grid": components in order into uniform cells sized by the largest one.
    The footprint may be turned 90 degrees about z when that is the only way it fits.
    Returns {"placements": [...], "misfits": [...], "fits": bool}."""
    lo = [float(x) for x in region_min]
    hi = [float(x) for x in region_max]
    c = max(0.0, float(clearance_mm))
    comps = [dict(x) for x in list(components)[:MAX_PACK]]
    keeps = [([float(v) for v in k["min"]],
              [float(b) - float(a) for a, b in zip(k["min"], k["max"])]) for k in keep_outs]
    avail = [hi[i] - lo[i] - 2 * c for i in range(3)]
    start = [lo[i] + c for i in range(3)]
    placed: list[tuple[list, tuple]] = []
    placements, misfits = [], []

    if strategy == "grid":
        pitch = [max((float(x["size"][i]) for x in comps), default=0.0) + c for i in range(3)]
        counts = [max(1, int((avail[i] + c + _EPS) // pitch[i])) if pitch[i] > 0 else 1
                  for i in range(3)]
        cells = [[start[0] + ix * pitch[0], start[1] + iy * pitch[1], start[2] + iz * pitch[2]]
                 for iz in range(counts[2]) for iy in range(counts[1]) for ix in range(counts[0])]
        order = comps
    else:
        cells = None
        order = sorted(comps, key=lambda x: -(float(x["size"][0]) * float(x["size"][1])
                                              * float(x["size"][2])))
    points = [list(start)]
    for k_lo, k_sz in keeps:
        for i in range(3):
            p = list(start)
            p[i] = k_lo[i] + k_sz[i] + c
            points.append(p)
    if inside is not None and cells is None:   # a real cavity: a coarse lattice as a fallback
        for fz in (0.0, 0.25, 0.5):
            for fy in (0.0, 0.25, 0.5, 0.75):
                for fx in (0.0, 0.25, 0.5, 0.75):
                    points.append([start[0] + fx * avail[0], start[1] + fy * avail[1],
                                   start[2] + fz * avail[2]])

    def free(p, sz) -> bool:
        if any(p[i] < start[i] - _EPS or p[i] + sz[i] > hi[i] - c + _EPS for i in range(3)):
            return False
        if not all(_sep(p, sz, q, qs, c) for q, qs in placed):
            return False
        if not all(_sep(p, sz, k, ks, c) for k, ks in keeps):
            return False
        return inside is None or bool(inside([p[i] - c for i in range(3)],
                                             [sz[i] + 2 * c for i in range(3)]))

    for comp in order:
        name = str(comp.get("name") or "component")
        size = [float(x) for x in comp["size"]]
        orients = _orients(size, allow_rotate)
        if all(any(o[i] > avail[i] + _EPS for i in range(3)) for o, _ in orients):
            misfits.append({"name": name, "size": _r(size), "reason":
                            f"larger than the cavity with {c:g} mm clearance (room "
                            f"{avail[0]:.1f} x {avail[1]:.1f} x {avail[2]:.1f} mm)"})
            continue
        cands = cells if cells is not None else sorted(points, key=lambda p: (p[2], p[1], p[0]))
        hit = None
        for p in cands:
            for o, rotated in orients:
                if free(p, o):
                    hit = (list(p), o, rotated)
                    break
            if hit:
                break
        if hit is None:
            misfits.append({"name": name, "size": _r(size),
                            "reason": f"no room left with {c:g} mm clearance"})
            continue
        p, o, rotated = hit
        placed.append((p, o))
        if cells is not None:
            cells = [q for q in cells if q != p]
        for i in range(3):
            q = list(p)
            q[i] = p[i] + o[i] + c
            points.append(q)
        out = {"name": name, "min": _r(p), "max": _r([p[i] + o[i] for i in range(3)]),
               "size": _r(o), "center": _r([p[i] + o[i] / 2 for i in range(3)]),
               "rotated": rotated}
        for key in ("part_id", "bbox_min", "bbox_max"):
            if key in comp:
                out[key] = comp[key]
        placements.append(out)
    return {"placements": placements, "misfits": misfits, "fits": not misfits}


def pack(components, cavity_solid=None, clearance_mm: float = 2.0, strategy: str = "layered",
         keep_outs=(), allow_rotate: bool = True, region=None) -> dict:
    """pack() for part scripts and the worker: place boxes in a cavity solid (e.g. from
    inner_volume) or a region {"min", "max"}; see pack_boxes. Called with build123d shapes
    (build123d's own `pack(objects, padding)`, which this name shadows), it delegates there."""
    components = list(components)
    if components and not isinstance(components[0], dict):
        raise TypeError("pack(): components are dicts {'name', 'size': [x, y, z]}")
    if cavity_solid is None and region is None:
        raise ValueError("pack(): give a cavity solid or region={'min': ..., 'max': ...}")
    inside = None
    if cavity_solid is not None:
        bb = cavity_solid.bounding_box()
        lo, hi = [bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]
        inside = _inside_fn(cavity_solid)
    else:
        lo, hi = list(region["min"]), list(region["max"])
    return pack_boxes(lo, hi, components, clearance_mm, strategy, keep_outs, inside,
                      allow_rotate)


def _inside_fn(cavity_solid):
    import build123d as bd

    def inside(lo, size) -> bool:
        box = bd.Solid.make_box(*size).moved(bd.Location(tuple(lo)))
        try:
            outside = float((box - cavity_solid).volume)
        except Exception:  # noqa: BLE001 - nothing left after the cut
            outside = 0.0
        return outside <= 1e-6 * float(box.volume) + 1e-6
    return inside


# ---- worker job: python -I worker.py --internals <job.json>

def _sections(job: dict) -> list[dict]:
    import build123d as bd

    from_job = []
    for it in job["instances"]:
        shape = bd.import_step(it["step"])
        m = it["transform"]
        from OCP.gp import gp_Trsf
        t = gp_Trsf()
        t.SetValues(m[0][0], m[0][1], m[0][2], m[0][3], m[1][0], m[1][1], m[1][2], m[1][3],
                    m[2][0], m[2][1], m[2][2], m[2][3])
        from_job.append((it["name"], shape.moved(bd.Location(t))))
    min_clear = float(job.get("min_clearance_mm", 1.0))
    threshold = float(job.get("threshold_mm2", 0.01))
    span = 1000.0
    for _n, s in from_job:
        bb = s.bounding_box()
        diag = math.dist((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
        span = max(span, 4 * diag)
    out = []
    for pl in job["planes"]:
        plane = bd.Plane(origin=tuple(pl["origin"]), z_dir=tuple(pl["normal"]))
        cut = bd.Face.make_rect(span, span, plane)
        secs = []
        for name, shape in from_job:
            try:
                faces = list((shape & cut).faces())
            except Exception:  # noqa: BLE001 - a plane that misses the solid
                faces = []
            secs.append((name, faces))
        sections = [{"instance": n, "area_mm2": round(sum(float(f.area) for f in fs), 4)}
                    for n, fs in secs]
        interferences, clearances = [], []
        for i in range(len(secs)):
            for j in range(i + 1, len(secs)):
                (na, fa), (nb, fb) = secs[i], secs[j]
                if not fa or not fb:
                    continue
                ca, cb = bd.Compound(fa), bd.Compound(fb)
                ba, bb_ = ca.bounding_box(), cb.bounding_box()
                gap = max(ba.min.X - bb_.max.X, bb_.min.X - ba.max.X, ba.min.Y - bb_.max.Y,
                          bb_.min.Y - ba.max.Y, ba.min.Z - bb_.max.Z, bb_.min.Z - ba.max.Z)
                if gap > min_clear:
                    continue
                try:
                    overlap = float((ca & cb).area)
                except Exception:  # noqa: BLE001 - disjoint faces can raise instead of empty
                    overlap = 0.0
                if overlap > threshold:
                    interferences.append({"a": na, "b": nb, "overlap_mm2": round(overlap, 4)})
                    continue
                dist = float(ca.distance_to(cb))
                if dist < min_clear:
                    clearances.append({"a": na, "b": nb, "clearance_mm": round(dist, 4)})
        out.append({"plane": {"label": pl.get("label"), "origin": pl["origin"],
                              "normal": pl["normal"]},
                    "sections": sections, "interferences": interferences,
                    "clearances": clearances})
    return out


def internals(job_path: Path, nonce: str) -> None:
    import build123d as bd

    job = json.loads(Path(job_path).read_text())
    out_dir = Path(job["out_dir"]).resolve()
    action = job["action"]
    result: dict = {"nonce": nonce, "action": action}
    if action == "section":
        result["planes"] = _sections(job)
    else:
        cav = None
        if job.get("step"):
            parts = cavity_parts(bd.import_step(job["step"]))
            if not parts:
                raise ValueError("no enclosed cavity found in that part: shell or hollow it "
                                 "first, or give the cavity as a box")
            cav = parts[0][0] if len(parts) == 1 else bd.Compound([s for s, _ in parts])
            bb = cav.bounding_box()
            result["cavity"] = {
                "volume_mm3": round(float(cav.volume), 4), "solids": len(parts),
                "open_sides": sorted({side for _, sides in parts for side in sides}),
                "bbox": {"min": _r((bb.min.X, bb.min.Y, bb.min.Z)),
                         "max": _r((bb.max.X, bb.max.Y, bb.max.Z)),
                         "size": _r((bb.size.X, bb.size.Y, bb.size.Z))}}
        if action == "pack":
            result.update(pack(job["components"], cav, float(job.get("clearance_mm", 2.0)),
                               job.get("strategy", "layered"), job.get("keep_outs") or (),
                               bool(job.get("allow_rotate", True)),
                               region=job.get("cavity_box")))
    tmp = out_dir / "new_result.json"
    tmp.write_text(json.dumps(result))
    os.replace(tmp, out_dir / "result.json")
