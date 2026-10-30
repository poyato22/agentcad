"""DrawingSpec: what a technical drawing says about a part, as data Mises can build and check.

A spec describes a flat (2.5D) part in its plan view: an outline loop, through cut-outs, round
holes (through or blind, drilled from the top face) and a thickness along +Z. Coordinates are
in the drawing's units (mm or inch), +x right, +y up; the origin can be anywhere (the built part
puts the outline's lower-left corner at the origin). `dimensions` are the values the drawing
states: they are what the verify step checks the built part against.

`part_script(spec)` turns a spec into a build123d part script that holds only numbers (nothing
from the drawing's text reaches the script). `verify(spec, drawing_info, metrics)` compares the
stated dimensions with Mises's own drawing of the built part (drawing.json: overall size, hole
callouts) and its measured holes (metrics.json), row by row.

Everything in a spec (titles, notes, dimension text) came from an untrusted file or a model that
read one: it is data, never instructions.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from ..sketch import Point, _arc_sweep, _sample

Size = Annotated[float, Field(allow_inf_nan=False, gt=0, le=10_000)]
Tol = Annotated[float, Field(allow_inf_nan=False, ge=0, le=100)]
Text = Annotated[str, Field(max_length=300)]
_ID = r"^[A-Za-z0-9_-]{1,32}$"

INCH_MM = 25.4
DEFAULT_TOL_MM = 0.05            # |measured - stated| when the drawing gives no tolerance
MAX_POINTS = 5_000
MAX_ATTEMPTS = 3

DIM_KINDS = ("length", "width", "thickness", "diameter", "radius", "position_x", "position_y",
             "hole_spacing_x", "hole_spacing_y", "angle", "other")


class ArcSeg(BaseModel):
    center: Point
    r: Size
    start: Point
    end: Point
    ccw: bool = True


class CircleSeg(BaseModel):
    center: Point
    r: Size


class Segment(BaseModel):
    """One piece of a loop: exactly one of a polyline (2+ points), an arc or a full circle."""
    polyline: list[Point] | None = Field(default=None, min_length=2, max_length=2000)
    arc: ArcSeg | None = None
    circle: CircleSeg | None = None

    @model_validator(mode="after")
    def _one(self) -> Segment:
        if sum(x is not None for x in (self.polyline, self.arc, self.circle)) != 1:
            raise ValueError("a segment is exactly one of polyline, arc or circle")
        return self


Loop = Annotated[list[Segment], Field(min_length=1, max_length=500)]


class SpecHole(BaseModel):
    """A round hole drilled from the top face: centre (x, y), diameter, depth (None = THRU)."""
    x: Annotated[float, Field(allow_inf_nan=False, ge=-100_000, le=100_000)]
    y: Annotated[float, Field(allow_inf_nan=False, ge=-100_000, le=100_000)]
    diameter: Size
    depth: Size | None = None


class SpecDim(BaseModel):
    """A dimension the drawing states. kind: length / width / thickness are the overall X / Y / Z
    sizes; diameter / radius of hole `hole`; position_x / position_y of hole `hole` from the
    outline's min (left / bottom) or max (right / top) edge; hole_spacing_x / _y between holes
    `hole` and `hole2`; angle and other are listed but not checked."""
    id: str = Field(pattern=_ID)
    kind: Literal[DIM_KINDS]  # type: ignore[valid-type]
    value: Annotated[float, Field(allow_inf_nan=False, ge=0, le=100_000)]
    hole: Annotated[int, Field(ge=0)] | None = None
    hole2: Annotated[int, Field(ge=0)] | None = None
    from_edge: Literal["min", "max"] = "min"
    tol_plus: Tol | None = None
    tol_minus: Tol | None = None
    text: str = Field(default="", max_length=64)


class DrawingSpec(BaseModel):
    units: Literal["mm", "in"] = "mm"
    title: str = Field(default="", max_length=120)
    material: str = Field(default="", max_length=60)
    thickness: Size
    outline: Loop
    cutouts: list[Loop] = Field(default_factory=list, max_length=50)
    holes: list[SpecHole] = Field(default_factory=list, max_length=200)
    dimensions: list[SpecDim] = Field(default_factory=list, max_length=200)
    notes: list[Text] = Field(default_factory=list, max_length=50)
    views: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _consistent(self) -> DrawingSpec:
        ids = [d.id for d in self.dimensions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate dimension id")
        for d in self.dimensions:
            for i in (d.hole, d.hole2):
                if i is not None and i >= len(self.holes):
                    raise ValueError(f"dimension {d.id}: no hole {i} (holes are numbered from 0)")
            if d.kind in ("diameter", "radius", "position_x", "position_y") and d.hole is None:
                raise ValueError(f"dimension {d.id}: {d.kind} needs `hole`")
            if d.kind.startswith("hole_spacing") and (d.hole is None or d.hole2 is None):
                raise ValueError(f"dimension {d.id}: {d.kind} needs `hole` and `hole2`")
        n = sum(_seg_points(s) for loop in (self.outline, *self.cutouts) for s in loop)
        if n > MAX_POINTS:
            raise ValueError(f"too many points (max {MAX_POINTS})")
        return self

    def to_mm(self) -> DrawingSpec:
        """The same spec in millimetres."""
        if self.units == "mm":
            return self
        k = INCH_MM

        def pt(p):
            return (p[0] * k, p[1] * k)

        def seg(s: Segment) -> dict:
            if s.polyline is not None:
                return {"polyline": [pt(p) for p in s.polyline]}
            if s.arc is not None:
                a = s.arc
                return {"arc": {"center": pt(a.center), "r": a.r * k, "start": pt(a.start),
                                "end": pt(a.end), "ccw": a.ccw}}
            return {"circle": {"center": pt(s.circle.center), "r": s.circle.r * k}}

        def scaled(v):
            return None if v is None else v * k

        data = self.model_dump()
        data.update(
            units="mm", thickness=self.thickness * k,
            outline=[seg(s) for s in self.outline],
            cutouts=[[seg(s) for s in loop] for loop in self.cutouts],
            holes=[{"x": h.x * k, "y": h.y * k, "diameter": h.diameter * k,
                    "depth": scaled(h.depth)} for h in self.holes],
            dimensions=[{**d.model_dump(), "value": d.value if d.kind == "angle" else d.value * k,
                         "tol_plus": None if d.kind == "angle" else scaled(d.tol_plus),
                         "tol_minus": None if d.kind == "angle" else scaled(d.tol_minus)}
                        for d in self.dimensions])
        return DrawingSpec.model_validate(data)


def _seg_points(s: Segment) -> int:
    return len(s.polyline) if s.polyline is not None else 3


# ---- loops as sketch segment tuples (shared geometry helpers in mises.sketch)

def _tuples(loop: list[Segment]) -> list:
    out: list = []
    for s in loop:
        if s.polyline is not None:
            pts = [tuple(p) for p in s.polyline]
            out += [("line", a, b) for a, b in zip(pts, pts[1:]) if math.dist(a, b) > 1e-9]
        elif s.arc is not None:
            a = s.arc
            out.append(("arc", tuple(a.center), a.r, tuple(a.start), tuple(a.end), a.ccw))
        else:
            out.append(("circle", tuple(s.circle.center), s.circle.r))
    return out


def outline_bbox(spec: DrawingSpec) -> tuple[float, float, float, float]:
    pts = _sample(_tuples(spec.outline))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _r(v: float) -> float:
    return round(v, 6)


def _script_loop(loop: list[Segment], ox: float, oy: float) -> list:
    """The loop as ("line", a, b) / ("arc", start, mid, end) / ("circle", c, r) tuples, moved by
    (-ox, -oy), with a closing line wherever two pieces (or the ends) do not meet."""
    def mv(p):
        return (_r(p[0] - ox), _r(p[1] - oy))

    raw = _tuples(loop)
    if len(raw) == 1 and raw[0][0] == "circle":
        return [("circle", mv(raw[0][1]), _r(raw[0][2]))]
    pieces: list = []
    for seg in raw:
        if seg[0] == "line":
            pieces.append(("line", mv(seg[1]), mv(seg[2])))
        elif seg[0] == "arc":
            c, s, e = seg[1], seg[3], seg[4]
            r = math.dist(c, s)
            a0 = math.atan2(s[1] - c[1], s[0] - c[0])
            half = a0 + _arc_sweep(seg) / 2
            mid = (c[0] + r * math.cos(half), c[1] + r * math.sin(half))
            pieces.append(("arc", mv(s), mv(mid), mv(e)))
    if not pieces:
        raise ValueError("the outline has no edges")
    out: list = []
    for i, p in enumerate(pieces):
        out.append(p)
        nxt = pieces[(i + 1) % len(pieces)]
        end, start = p[-1], nxt[1]
        if math.dist(end, start) > 1e-6:
            out.append(("line", end, start))
    return out


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(ord("a") + rem) + s
    return s


def _num(v: float) -> str:
    return repr(float(round(v, 6)))


def _slider(name: str, value: float, doc: str) -> str:
    lo, hi = max(0.1, round(value * 0.25, 2)), max(10.0, round(value * 4, 1))
    return (f'    "{name}": {_num(value)},  # mises: min={lo:g} max={hi:g} step=0.1 unit=mm '
            f'doc="{doc}"')


_SCRIPT_BODY = '''

def _edges(loop):
    out = []
    for s in loop:
        if s[0] == "line":
            out.append(Edge.make_line((s[1][0], s[1][1], 0), (s[2][0], s[2][1], 0)))
        elif s[0] == "arc":
            out.append(Edge.make_three_point_arc(*[(p[0], p[1], 0) for p in s[1:]]))
        else:
            out.append(Edge.make_circle(s[2], Plane.XY.move(Location((s[1][0], s[1][1], 0)))))
    return out


def _face(loop):
    return Face(Wire(_edges(loop)))


def build(v: dict):
    t = v["thickness"]
    face = _face(OUTLINE)
    for loop in CUTOUTS:
        face = face - _face(loop)
    part = extrude(face, t)
    for x, y, key, depth in HOLES:
        r = v[key] / 2
        if depth is None:   # through: a little longer than the part, so no face is coplanar
            tool = Cylinder(r, t + 2, align=(Align.CENTER, Align.CENTER, Align.MAX))
            part = part - tool.moved(Location((x, y, t + 1)))
        else:               # blind, drilled down from the top face
            tool = Cylinder(r, depth, align=(Align.CENTER, Align.CENTER, Align.MAX))
            part = part - tool.moved(Location((x, y, t)))
    return part
'''


def part_script(spec: DrawingSpec, *, source: str = "") -> tuple[str, dict[str, float]]:
    """A build123d script (and its VARS) for the spec, in mm with the outline's lower-left
    corner at the origin. `source` is a trusted id for the header comment (e.g. the import id)."""
    spec = spec.to_mm()
    ox, oy, _, _ = outline_bbox(spec)
    groups: dict[float, str] = {}
    for h in spec.holes:
        groups.setdefault(round(h.diameter, 6), f"hole_{_letters(len(groups))}_d")
    vars_ = {"thickness": round(spec.thickness, 6)}
    lines = [_slider("thickness", spec.thickness, "thickness")]
    for d, name in groups.items():
        vars_[name] = d
        lines.append(_slider(name, d, f"hole diameter (drawing: {d:g} mm)"))
    holes = [(_r(h.x - ox), _r(h.y - oy), groups[round(h.diameter, 6)],
              None if h.depth is None else _r(h.depth)) for h in spec.holes]
    cutouts = [_script_loop(c, ox, oy) for c in spec.cutouts]
    header = (f"# Built by Mises from a technical drawing{f' ({source})' if source else ''}.\n"
              "# Sizes in mm; the outline's lower-left corner is at the origin.\n")
    script = (header + "VARS = {\n" + "\n".join(lines) + "\n}\n\n"
              f"OUTLINE = {_script_loop(spec.outline, ox, oy)!r}\n"
              f"CUTOUTS = {cutouts!r}\n"
              f"HOLES = {holes!r}\n" + _SCRIPT_BODY)
    return script, vars_


# ---- verify: the stated dimensions against our own drawing of the built part

_NOTE_RE = re.compile(r"^(\d+)x Ø([0-9.]+) (THRU|DEPTH ([0-9.]+))$")


def expected_callouts(spec: DrawingSpec) -> list[str]:
    """The hole callouts Mises's drawing should print for this spec (same grouping and format as
    the drawing's own notes: '4x Ø6 THRU', '2x Ø3 DEPTH 4')."""
    spec = spec.to_mm()
    groups: Counter = Counter()
    for h in spec.holes:
        depth = None if h.depth is None or h.depth >= spec.thickness - 0.05 else round(h.depth, 1)
        groups[(round(h.diameter, 2), depth)] += 1
    return [f"{n}x Ø{d:g} " + ("THRU" if dep is None else f"DEPTH {dep:g}")
            for (d, dep), n in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))]


def _z_holes(metrics: dict) -> list[dict]:
    out = []
    for h in metrics.get("holes") or []:
        try:
            axis, c, d = h["axis"], h["center"], float(h["diameter_mm"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(abs(float(axis[2])) - 1) < 1e-3:
            out.append({"x": float(c[0]), "y": float(c[1]), "d": d})
    return out


def _match_holes(spec: DrawingSpec, built: list[dict], ox: float, oy: float) -> list[dict | None]:
    """For each spec hole, the built Z hole at its place (nearest centre, within a radius)."""
    out: list[dict | None] = []
    for h in spec.holes:
        x, y = h.x - ox, h.y - oy
        best = min(built, key=lambda b: math.hypot(b["x"] - x, b["y"] - y), default=None)
        if best is not None and math.hypot(best["x"] - x, best["y"] - y) <= max(0.5, h.diameter / 2):
            out.append(best)
        else:
            out.append(None)
    return out


def _row(d: SpecDim, measured: float | None, *, source: str) -> dict:
    tp = d.tol_plus if d.tol_plus is not None else DEFAULT_TOL_MM
    tm = d.tol_minus if d.tol_minus is not None else DEFAULT_TOL_MM
    if measured is None:
        status = "missing"
    else:
        diff = measured - d.value
        status = "match" if -tm - 1e-9 <= diff <= tp + 1e-9 else "mismatch"
    return {"id": d.id, "kind": d.kind, "text": d.text, "expected": round(d.value, 4),
            "measured": None if measured is None else round(measured, 4), "tol_plus": tp,
            "tol_minus": tm, "status": status, "source": source}


def verify(spec: DrawingSpec, drawing_info: dict, metrics: dict) -> dict:
    """The match table: one row per stated dimension (angle / other: "unchecked") plus one per
    expected hole callout. Values in mm. `ok` when every checked row matches."""
    spec = spec.to_mm()
    ox, oy, _, _ = outline_bbox(spec)
    size = [float(v) for v in drawing_info.get("overall_mm") or metrics["bbox"]["size"]]
    bmin = [float(v) for v in metrics["bbox"]["min"]]
    bmax = [float(v) for v in metrics["bbox"]["max"]]
    # the built part sits with the outline's lower-left at the origin: spec coords shift by it
    shift_x, shift_y = ox, oy
    found = _match_holes(spec, _z_holes(metrics), shift_x, shift_y)
    rows = []
    for d in spec.dimensions:
        m: float | None = None
        source = "model"
        if d.kind in ("length", "width", "thickness"):
            m, source = size[("length", "width", "thickness").index(d.kind)], "drawing"
        elif d.kind in ("diameter", "radius"):
            h = found[d.hole]
            m = None if h is None else (h["d"] if d.kind == "diameter" else h["d"] / 2)
        elif d.kind in ("position_x", "position_y"):
            h, ax = found[d.hole], 0 if d.kind == "position_x" else 1
            if h is not None:
                c = h["x"] if ax == 0 else h["y"]
                m = c - bmin[ax] if d.from_edge == "min" else bmax[ax] - c
        elif d.kind in ("hole_spacing_x", "hole_spacing_y"):
            a, b = found[d.hole], found[d.hole2]
            k = "x" if d.kind == "hole_spacing_x" else "y"
            m = None if a is None or b is None else abs(a[k] - b[k])
        else:
            rows.append({"id": d.id, "kind": d.kind, "text": d.text,
                         "expected": round(d.value, 4), "measured": None, "tol_plus": None,
                         "tol_minus": None, "status": "unchecked", "source": None})
            continue
        rows.append(_row(d, m, source=source))
    printed = [n for n in drawing_info.get("holes") or [] if isinstance(n, str)]
    for note in expected_callouts(spec):
        rows.append({"id": f"callout:{note}", "kind": "callout", "text": note, "expected": None,
                     "measured": None, "tol_plus": None, "tol_minus": None,
                     "status": "match" if note in printed else "mismatch", "source": "drawing"})
    counts = Counter(r["status"] for r in rows)
    checked = [r for r in rows if r["status"] != "unchecked"]
    return {"rows": rows, "matches": counts["match"], "mismatches": counts["mismatch"],
            "missing": counts["missing"], "unchecked": counts["unchecked"],
            "printed_callouts": printed,
            "ok": bool(checked) and all(r["status"] == "match" for r in checked)}


# ---- independent check: the numbers printed on a vector PDF (its text layer)

_TEXT_NUM_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)(?!\d)")
_TEXT_KINDS = ("length", "width", "thickness", "diameter", "radius", "position_x",
               "position_y", "hole_spacing_x", "hole_spacing_y", "angle")


def text_numbers(texts: list[str]) -> list[float]:
    """Every number printed in the pages' text ("Ø6", "4X", "100.5", "6,35")."""
    out = []
    for t in texts:
        for m in _TEXT_NUM_RE.finditer(t or ""):
            try:
                out.append(float(m.group(1).replace(",", ".")))
            except ValueError:
                continue
    return out[:5000]


def _printed(value: float, numbers: list[float]) -> bool:
    return any(abs(n - value) <= max(0.005, 1e-3 * abs(value)) for n in numbers)


def text_check(spec: DrawingSpec, table: dict, drawing_info: dict, metrics: dict,
               numbers: list[float]) -> dict:
    """Cross-check a model's reading against the numbers the drawing really prints (a vector
    PDF's text layer): each row's stated value (in the spec's units), and each built overall
    size and hole diameter, should appear somewhere in it. Marks each row's `evidence`
    ("printed" / "not printed") and returns the summary."""
    k = INCH_MM if spec.units == "in" else 1.0
    supported, unsupported = [], []
    for r in table["rows"]:
        if r["kind"] not in _TEXT_KINDS or r["expected"] is None:
            continue
        v = r["expected"] if r["kind"] == "angle" else r["expected"] / k
        if _printed(v, numbers):
            r["evidence"] = "printed"
            supported.append(r["id"])
        else:
            r["evidence"] = "not printed"
            unsupported.append(r["id"])
    built = []
    size = drawing_info.get("overall_mm") or (metrics.get("bbox") or {}).get("size") or []
    for name, v in zip(("length", "width", "thickness"), size):
        if not _printed(float(v) / k, numbers):
            built.append(f"overall {name} {round(float(v) / k, 3):g}")
    for d in sorted({round(h["d"], 3) for h in _z_holes(metrics)}):
        if not _printed(d / k, numbers):
            built.append(f"hole Ø{round(d / k, 3):g}")
    return {"available": True, "supported": supported, "unsupported": unsupported,
            "built_unsupported": built, "ok": not unsupported and not built}
