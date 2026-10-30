"""DXF → DrawingSpec, from the entities the sandboxed parse worker extracted.

The worker's output is untrusted: every number is checked here before use. Geometry becomes
sketch strokes, and the closed loops are found with the Sketch page's own loop finder
(mises.sketch): the largest closed profile is the part's outline, round holes in it are holes,
other inner loops are through cut-outs. Loops on frame / title / dimension / centre-line layers
are left out. DIMENSION entities become stated dimensions, classified against the outline and
the holes (overall length / width, hole diameters, hole positions and spacings). A 2D DXF has no
thickness: it comes from the upload form, else a "THK 6"-style note, else DEFAULT_THICKNESS_MM
(reported as assumed).
"""
from __future__ import annotations

import math
import re
from types import SimpleNamespace

from ..sketch import Stroke, _inside, _loops, _reverse, _sample, _signed_area
from ..types import ErrorCode, MisesError
from .spec import DrawingSpec

# $INSUNITS → mm per drawing unit (0 = unitless: taken as mm)
UNIT_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 25.4e-6, 9: 0.0254,
           10: 914.4, 13: 0.001, 14: 100.0}
UNIT_NAME = {0: "unitless (taken as mm)", 1: "inch", 2: "foot", 4: "mm", 5: "cm", 6: "m",
             8: "microinch", 9: "mil", 10: "yard", 13: "micron", 14: "decimetre"}
IGNORE_LAYER_RE = re.compile(r"border|frame|title|sheet|defpoints|dim|text|anno|hatch|cent(er|re)"
                             r"|hidden|constr|viewport|grid", re.I)
DEFAULT_THICKNESS_MM = 5.0
MAX_PIECES = 2_000               # open pieces fed to the loop finder (it is quadratic in ends)
MAX_SPAN_MM = 100_000.0
_CHUNK = 1_000                   # points per stroke when a long polyline is split
# "THK 6" / "THICKNESS: 6" / "T=6" first, then "6 THK" / "6MM THK"
_THK_RES = (re.compile(r"(?:\bTHK\.?|\bTHICK(?:NESS)?\.?|\bT\s*=)\s*[:=]?\s*(\d+(?:[.,]\d+)?)", re.I),
            re.compile(r"(?<![\d.])(\d+(?:[.,]\d+)?) ?(?:MM)? ?THK\b", re.I))
_NUM_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)")
_TOL_RE = re.compile(r"(?:±|%%[pP]|\+/-)\s*(\d+(?:\.\d+)?)")
_QTY_RE = re.compile(r"\b\d+\s*[xX]\s*")      # "4X Ø6": the 4 is a count, not the size


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _f(v) -> float:
    x = float(v)
    if not math.isfinite(x):
        raise ValueError("not finite")
    return x


def _pt(v) -> tuple[float, float]:
    return (_f(v[0]), _f(v[1]))


# ---- raw entities → mm geometry

def _geometry(raw: dict, k: float) -> list[dict]:
    """The geometry entities in mm (not yet moved), each checked."""
    out = []
    for e in raw.get("entities") or []:
        if not isinstance(e, dict):
            continue
        try:
            t, layer = e.get("t"), str(e.get("layer", ""))[:64]
            if t == "line":
                out.append({"t": t, "layer": layer, "a": _sc(_pt(e["a"]), k), "b": _sc(_pt(e["b"]), k)})
            elif t in ("circle", "arc"):
                r = _f(e["r"]) * k
                if r <= 0:
                    continue
                item = {"t": t, "layer": layer, "c": _sc(_pt(e["c"]), k), "r": r}
                if t == "arc":
                    item.update(a0=_f(e["a0"]), a1=_f(e["a1"]))
                out.append(item)
            elif t == "poly":
                pts = [(_f(p[0]) * k, _f(p[1]) * k, _f(p[2]) if len(p) > 2 else 0.0)
                       for p in e["pts"]][:20_000]
                if len(pts) >= 2:
                    out.append({"t": t, "layer": layer, "pts": pts, "closed": bool(e.get("closed"))})
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out


def _sc(p, k):
    return (p[0] * k, p[1] * k)


def _points(g: dict) -> list[tuple[float, float]]:
    if g["t"] == "line":
        return [g["a"], g["b"]]
    if g["t"] in ("circle", "arc"):
        (cx, cy), r = g["c"], g["r"]
        return [(cx - r, cy - r), (cx + r, cy + r)]
    return [(p[0], p[1]) for p in g["pts"]]


def _bulge_mid(a, b, bulge: float):
    dx, dy = b[0] - a[0], b[1] - a[1]
    chord = math.hypot(dx, dy)
    s = bulge * chord / 2                     # sagitta; +bulge = counter-clockwise
    return ((a[0] + b[0]) / 2 + dy / chord * s, (a[1] + b[1]) / 2 - dx / chord * s)


def _strokes(geom: list[dict], ox: float, oy: float) -> list[Stroke]:
    def mv(p):
        return [p[0] - ox, p[1] - oy]

    out: list[Stroke] = []

    def add(kind: str, pts: list, **kw) -> None:
        out.append(Stroke(id=f"e{len(out)}", kind=kind, points=pts, **kw))

    for g in geom:
        if g["t"] == "line":
            if math.dist(g["a"], g["b"]) > 1e-9:
                add("line", [mv(g["a"]), mv(g["b"])])
        elif g["t"] == "circle":
            add("circle", [mv(g["c"])], center=mv(g["c"]), radius_mm=g["r"])
        elif g["t"] == "arc":
            (cx, cy), r = g["c"], g["r"]
            a0 = math.radians(g["a0"])
            sweep = math.radians((g["a1"] - g["a0"]) % 360 or 360)
            pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (a0, a0 + sweep / 2, a0 + sweep)]
            if sweep >= 2 * math.pi - 1e-9:
                add("circle", [mv(g["c"])], center=mv(g["c"]), radius_mm=r)
            else:
                add("arc", [mv(p) for p in pts])
        else:
            pts, closed = g["pts"], g["closed"]
            if closed and math.dist(pts[0][:2], pts[-1][:2]) < 1e-9:
                pts = pts[:-1]
            if not any(abs(p[2]) > 1e-12 for p in (pts if closed else pts[:-1])):
                xy = [mv(p) for p in pts]
                if closed and 2 < len(xy) <= _CHUNK:
                    add("polyline", xy, closed=True)
                    continue
                if closed:
                    xy.append(xy[0])
                for i in range(0, len(xy) - 1, _CHUNK):   # long chains: open pieces, rejoined
                    add("polyline", xy[i:i + _CHUNK + 1])
                continue
            n = len(pts)
            for i in range(n if closed else n - 1):
                a, b = pts[i], pts[(i + 1) % n]
                if math.dist(a[:2], b[:2]) < 1e-9:
                    continue
                if abs(a[2]) > 1e-12:
                    add("arc", [mv(a), mv(_bulge_mid(a, b, a[2])), mv(b)])
                else:
                    add("line", [mv(a), mv(b)])
    return out


def _profiles(strokes: list[Stroke]) -> list[dict]:
    """Closed profiles (outer loop + its holes), largest first, in exact (unrounded) segments.
    Same nesting rule as mises.sketch.closed_profiles."""
    loops = []
    for _ids, segs in _loops(SimpleNamespace(strokes=strokes)):
        area = _signed_area(segs)
        if abs(area) < 1e-9:
            continue
        if area < 0:
            segs, area = _reverse(segs), -area
        loops.append({"segs": segs, "area": area, "poly": _sample(segs)})
    loops.sort(key=lambda lp: -lp["area"])
    for i, lp in enumerate(loops):
        lp["parent"] = None
        for j in range(i - 1, -1, -1):
            if loops[j]["area"] > lp["area"] and _inside(lp["poly"][0], loops[j]["poly"]):
                lp["parent"] = j
                break
        lp["depth"] = 0 if lp["parent"] is None else loops[lp["parent"]]["depth"] + 1
    out = []
    for i, lp in enumerate(loops):
        if lp["depth"] % 2 == 0:
            out.append({"outer": lp["segs"], "area": lp["area"],
                        "holes": [h["segs"] for h in loops if h["parent"] == i]})
    return out


def _segments(segs: list) -> list[dict]:
    out: list[dict] = []
    for seg in segs:
        if seg[0] == "line":
            if out and "polyline" in out[-1] and math.dist(out[-1]["polyline"][-1], seg[1]) < 1e-9:
                out[-1]["polyline"].append(list(seg[2]))
            else:
                out.append({"polyline": [list(seg[1]), list(seg[2])]})
        elif seg[0] == "arc":
            _, c, r, a, b, ccw = seg
            out.append({"arc": {"center": list(c), "r": r, "start": list(a), "end": list(b),
                                "ccw": bool(ccw)}})
        else:
            out.append({"circle": {"center": list(seg[1]), "r": seg[2]}})
    return out


# ---- dimensions

def _stated(d: dict, k: float, kind_is_angle: bool) -> tuple[float | None, float | None, str]:
    """(stated value in mm or degrees, ± tolerance, printed text) of a DIMENSION entity."""
    text = str(d.get("text") or "").strip()[:64]
    m = d.get("measurement")
    try:
        value = _f(m) if m is not None else None
    except (TypeError, ValueError):
        value = None
    tol = None
    if text and "<>" not in text:
        nums = _NUM_RE.findall(_QTY_RE.sub(" ", _TOL_RE.sub(" ", text)))
        if nums:
            value = float(nums[0])           # an override states what the drawing says
        t = _TOL_RE.search(text)
        if t:
            tol = float(t.group(1))
    elif text:
        t = _TOL_RE.search(text)
        if t:
            tol = float(t.group(1))
    if value is None:
        return None, None, text
    value = round(value, 6)                   # float noise from the definition points
    if kind_is_angle:
        return value, tol, text
    return round(value * k, 6), None if tol is None else tol * k, text


def _near(v: float, w: float, tol: float) -> bool:
    return abs(v - w) <= tol


def _dimensions(raw: dict, k: float, ox: float, oy: float, bbox, holes: list[dict]) -> list[dict]:
    x0, y0, x1, y1 = bbox
    tol = max(0.05, 0.002 * max(x1 - x0, y1 - y0))
    out: list[dict] = []

    def hole_at(p) -> int | None:
        best = min(range(len(holes)), key=lambda i: math.hypot(holes[i]["x"] - p[0], holes[i]["y"] - p[1]),
                   default=None)
        if best is None:
            return None
        h = holes[best]
        return best if math.hypot(h["x"] - p[0], h["y"] - p[1]) <= max(tol, h["diameter"] / 2) else None

    def hole_on_axis(p, ax: int) -> int | None:
        """A hole whose centre lies on this extension line (same x for ax=0)."""
        cands = [i for i, h in enumerate(holes) if _near((h["x"], h["y"])[ax], p[ax], tol)]
        if not cands:
            return None
        o = 1 - ax
        return min(cands, key=lambda i: abs((holes[i]["x"], holes[i]["y"])[o] - p[o]))

    for d in raw.get("entities") or []:
        if not isinstance(d, dict) or d.get("t") != "dim":
            continue
        try:
            dimtype = int(d.get("dimtype", -1))
            pts = {key: _sc(_pt(v), k) for key, v in (d.get("pts") or {}).items()
                   if key in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")}
            pts = {key: (p[0] - ox, p[1] - oy) for key, p in pts.items()}
            angle = _f(d.get("angle", 0.0))
        except (TypeError, ValueError, IndexError):
            continue
        is_angle = dimtype in (2, 5)
        value, tolv, text = _stated(d, k, is_angle)
        if value is None or value < 0:
            continue
        item = {"id": f"d{len(out) + 1}", "kind": "other", "value": value, "text": text}
        if tolv is not None:
            item.update(tol_plus=tolv, tol_minus=tolv)
        if is_angle:
            item["kind"] = "angle"
        elif dimtype in (0, 1) and "defpoint2" in pts and "defpoint3" in pts:
            p2, p3 = pts["defpoint2"], pts["defpoint3"]
            if dimtype == 1:
                angle = math.degrees(math.atan2(p3[1] - p2[1], p3[0] - p2[0]))
            a = angle % 180
            ax = 0 if min(a, 180 - a) < 1 else 1 if abs(a - 90) < 1 else None
            if ax is not None:
                lo, hi = sorted((p2, p3), key=lambda p: p[ax])
                emin, emax = (x0, x1) if ax == 0 else (y0, y1)
                at_min, at_max = _near(lo[ax], emin, tol), _near(hi[ax], emax, tol)
                h_lo, h_hi = hole_on_axis(lo, ax), hole_on_axis(hi, ax)
                axis = "x" if ax == 0 else "y"
                if at_min and at_max:
                    item["kind"] = "length" if ax == 0 else "width"
                elif at_min and h_hi is not None:
                    item.update(kind=f"position_{axis}", hole=h_hi, from_edge="min")
                elif at_max and h_lo is not None:
                    item.update(kind=f"position_{axis}", hole=h_lo, from_edge="max")
                elif h_lo is not None and h_hi is not None and h_lo != h_hi:
                    item.update(kind=f"hole_spacing_{axis}", hole=h_lo, hole2=h_hi)
        elif dimtype == 3 and "defpoint" in pts and "defpoint4" in pts:
            a, b = pts["defpoint"], pts["defpoint4"]
            i = hole_at(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            if i is not None:
                item.update(kind="diameter", hole=i)
        elif dimtype == 4 and "defpoint" in pts:
            i = hole_at(pts["defpoint"])
            if i is not None:
                item.update(kind="radius", hole=i)
        out.append(item)
        if len(out) >= 200:
            break
    return out


def _thickness(texts: list[str], k: float) -> float | None:
    for rx in _THK_RES:
        for t in texts:
            m = rx.search(t)
            if m:
                v = float(m.group(1).replace(",", "."))
                if v > 0:
                    return v * k
    return None


def spec_from_dxf(raw: dict, *, thickness_mm: float | None = None) -> tuple[DrawingSpec, dict]:
    """(spec in mm, facts for the report: units, thickness source, what was left out)."""
    try:
        units = int(raw.get("units", 0))
    except (TypeError, ValueError):
        units = 0
    notes: list[str] = []
    if units not in UNIT_MM:
        notes.append(f"DXF units code {units} is not supported; taken as mm")
        units = 0
    k = UNIT_MM[units]
    geom_all = _geometry(raw, k)
    geom = [g for g in geom_all if not IGNORE_LAYER_RE.search(g["layer"])] or geom_all
    if not geom:
        raise _bad("the DXF has no lines, arcs, circles or polylines to build from",
                   hint="export the part's outline (not only text or dimensions)")
    pts = [p for g in geom for p in _points(g)]
    ox, oy = min(p[0] for p in pts), min(p[1] for p in pts)
    if max(p[0] for p in pts) - ox > MAX_SPAN_MM or max(p[1] for p in pts) - oy > MAX_SPAN_MM:
        raise _bad("the drawing is larger than 100 m; check its units")
    strokes = _strokes(geom, ox, oy)
    if sum(1 for s in strokes if s.kind in ("line", "arc") or not s.closed) > MAX_PIECES:
        raise _bad("the DXF is too complex to read (too many separate lines)",
                   hint="export only the part's plan view, with closed polylines where possible")
    profiles = _profiles(strokes)
    if not profiles:
        raise _bad("no closed outline found in the DXF",
                   hint="the part's outline must be a closed loop (gaps under 0.01 mm are joined)")
    main = profiles[0]
    if len(profiles) > 1:
        notes.append(f"{len(profiles) - 1} other closed shape(s) left out (other views or a frame)")
    holes, cutouts = [], []
    for segs in main["holes"]:
        if len(segs) == 1 and segs[0][0] == "circle":
            holes.append({"x": segs[0][1][0], "y": segs[0][1][1], "diameter": 2 * segs[0][2]})
        else:
            cutouts.append(_segments(_reverse(segs)))
    poly = _sample(main["outer"])
    bbox = (min(p[0] for p in poly), min(p[1] for p in poly),
            max(p[0] for p in poly), max(p[1] for p in poly))
    texts = [str(t)[:300] for t in raw.get("texts") or [] if isinstance(t, str)]
    source = "form"
    t = thickness_mm
    if t is None:
        t, source = _thickness(texts, k), "note"
    if t is None:
        t, source = DEFAULT_THICKNESS_MM, "assumed"
        notes.append(f"the DXF gives no thickness; {DEFAULT_THICKNESS_MM:g} mm was assumed")
    dims = _dimensions(raw, k, ox, oy, bbox, holes)
    kinds = {d["kind"] for d in dims}
    for kind, value in (("length", bbox[2] - bbox[0]), ("width", bbox[3] - bbox[1])):
        if kind not in kinds:
            dims.append({"id": kind[0].upper(), "kind": kind, "value": value, "text": "(geometry)"})
    if source != "assumed" and "thickness" not in kinds:
        dims.append({"id": "T", "kind": "thickness", "value": t,
                     "text": "(upload form)" if source == "form" else "(note)"})
    if raw.get("truncated"):
        notes.append("the DXF has more entities than Mises reads; only the first ones were used")
    skipped = raw.get("skipped") if isinstance(raw.get("skipped"), dict) else {}
    spec = DrawingSpec.model_validate({
        "units": "mm", "thickness": t, "outline": _segments(main["outer"]), "cutouts": cutouts,
        "holes": holes, "dimensions": dims, "notes": texts[:50], "views": ["plan"]})
    facts = {"units": UNIT_NAME.get(units, "mm"), "thickness_source": source, "notes": notes,
             "skipped": {str(a)[:40]: int(b) for a, b in list(skipped.items())[:20]
                         if isinstance(b, int)}}
    return spec, facts


# ---- a picture of the original, for the side-by-side view

def _xml(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def preview_svg(raw: dict) -> str:
    """An SVG of the DXF's geometry and dimension values (numbers only, plus escaped text)."""
    try:
        k = UNIT_MM.get(int(raw.get("units", 0)), 1.0)
    except (TypeError, ValueError):
        k = 1.0
    geom = _geometry(raw, k)
    pts = [p for g in geom for p in _points(g)]
    if not pts:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>')
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    w, h = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
    pad = 0.06 * max(w, h)
    sw = max(w, h) / 400

    def P(p):
        return f"{p[0] - x0 + pad:.3f},{y1 - p[1] + pad:.3f}"

    body = []
    for g in geom[:5000]:
        if g["t"] == "line":
            body.append(f'<path d="M{P(g["a"])} L{P(g["b"])}"/>')
        elif g["t"] == "circle":
            c = P(g["c"]).split(",")
            body.append(f'<circle cx="{c[0]}" cy="{c[1]}" r="{g["r"]:.3f}"/>')
        elif g["t"] == "arc":
            (cx, cy), r = g["c"], g["r"]
            a0, a1 = math.radians(g["a0"]), math.radians(g["a1"])
            sweep = (a1 - a0) % (2 * math.pi)
            s, e = (cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))
            body.append(f'<path d="M{P(s)} A{r:.3f},{r:.3f} 0 {int(sweep > math.pi)} 0 {P(e)}"/>')
        else:
            d = " L".join(P(p) for p in g["pts"])
            body.append(f'<path d="M{d}{" Z" if g["closed"] else ""}"/>')
    labels = []
    for d in raw.get("entities") or []:
        if isinstance(d, dict) and d.get("t") == "dim":
            try:
                p = _sc(_pt((d.get("pts") or {}).get("text_midpoint") or d["pts"]["defpoint"]), k)
                v = _f(d["measurement"]) * (1 if int(d.get("dimtype", 0)) in (2, 5) else k)
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            q = P(p).split(",")
            labels.append(f'<text x="{q[0]}" y="{q[1]}">{_xml(f"{v:g}")}</text>')
    vw, vh = w + 2 * pad, h + 2 * pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw:.3f} {vh:.3f}">'
            f'<rect width="100%" height="100%" fill="white"/>'
            f'<g fill="none" stroke="black" stroke-width="{sw:.4f}">{"".join(body)}</g>'
            f'<g fill="#0057b8" font-family="Menlo, monospace" font-size="{max(w, h) / 40:.3f}" '
            f'text-anchor="middle">{"".join(labels[:400])}</g></svg>')
