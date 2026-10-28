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


def _load_part(part_file: Path):
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

    try:
        mod = _load_part(part_dir / "part.py")
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
        json.dumps({"nonce": nonce, "metrics": metrics, "vars": merged}))
    os.replace(tmp / "new_result.json", part_dir / "result.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        part_file = Path(sys.argv[1]).resolve() / "part.py"
        sys.stderr.write(_report(e, part_file) + "\n")
        sys.exit(1)
