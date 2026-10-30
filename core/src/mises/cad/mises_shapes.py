"""mises_shapes: curved-geometry recipes for part scripts (Plan 8 G2).

Pre-imported into every sandboxed part script as `mises_shapes` (alias `ms`), next to build123d,
`bd` and `math`. Self-contained like worker.py: stdlib + build123d/OCP only, never mises imports.

Conventions (aircraft recipes): millimetres; X runs aft (leading edge at the smallest X), Y is
span (right wing +Y), Z is up. Airfoil point lists are unit-chord (x, y) tuples ordered from the
trailing edge over the upper surface to the leading edge and back along the lower surface (the
Selig order). Every recipe that returns a solid checks it first (valid, closed, positive volume)
and raises ValueError with a plain reason instead of returning broken geometry.

The host reads this file with `ast` (recipes_list / recipe_doc tools) and never imports it, so the
first docstring line of each name in __all__ is its one-line index entry.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import build123d as bd

__all__ = [
    "naca4", "naca5", "airfoil_from_points", "airfoil_wire", "wing", "blended_wing_body",
    "fuselage", "sweep_profile", "smooth_loft", "spline_surface", "thicken", "blend_edges",
    "variable_fillet", "enclosure", "standoffs", "vent_slots", "cable_path", "check_solid",
]

Pt2 = tuple[float, float]
Pt3 = tuple[float, float, float]


# ---------------------------------------------------------------- validation

def check_solid(shape, what: str = "shape", *, self_intersection: bool = False):
    """Validate a solid (is_valid, closed, volume > 0) and return it; raises ValueError otherwise.

    `self_intersection=True` also runs OCCT's argument analyzer (slower; use on final shapes).
    Every recipe calls this on its result, so a recipe never hands back broken geometry."""
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

    shape = shape.part if hasattr(shape, "part") else shape
    if shape is None or not hasattr(shape, "wrapped") or shape.wrapped is None:
        raise ValueError(f"{what}: no shape was produced")
    solids = shape.solids()
    if not solids:
        raise ValueError(f"{what}: result has no solid (an open surface or empty shape)")
    if not shape.is_valid:
        raise ValueError(f"{what}: OCCT reports the shape as invalid; try fewer or smoother "
                         "sections, a smaller radius, or a larger wall")
    vol = shape.volume
    if not (math.isfinite(vol) and vol > 0):
        raise ValueError(f"{what}: zero or negative volume ({vol})")
    fb = ShapeAnalysis_FreeBounds(shape.wrapped, False, True)
    for comp in (fb.GetOpenWires(), fb.GetClosedWires()):
        if not comp.IsNull() and bd.Compound(comp).edges():
            raise ValueError(f"{what}: the surface is not closed (free edges found)")
    if self_intersection:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Check

        if not BRepAlgoAPI_Check(shape.wrapped, True, True).IsValid():
            raise ValueError(f"{what}: the shape intersects itself")
    return shape


def _as_shape(obj):
    """build123d Part/BuildPart/Solid/Compound -> a Shape."""
    return obj.part if hasattr(obj, "part") else obj


def _downcast(topods):
    from build123d.topology.shape_core import downcast

    s = downcast(topods)
    if isinstance(s, bd.Shape):
        return s
    for cls in (bd.Solid, bd.Compound, bd.Shell, bd.Face):
        try:
            return cls(s)
        except Exception:  # noqa: BLE001
            continue
    return bd.Compound(s)


# ---------------------------------------------------------------- airfoils

def _cosine_x(n: int) -> list[float]:
    return [0.5 * (1.0 - math.cos(math.pi * i / n)) for i in range(n + 1)]


def _naca_thickness(x: float, t: float, closed_te: bool) -> float:
    a4 = -0.1036 if closed_te else -0.1015
    return 5.0 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2
                      + 0.2843 * x ** 3 + a4 * x ** 4)


def _from_camber(xs, yt_fn, camber_fn) -> list[Pt2]:
    upper, lower = [], []
    for x in xs:
        yt = yt_fn(x)
        yc, dyc = camber_fn(x)
        th = math.atan(dyc)
        upper.append((x - yt * math.sin(th), yc + yt * math.cos(th)))
        lower.append((x + yt * math.sin(th), yc - yt * math.cos(th)))
    return list(reversed(upper)) + lower[1:]


def naca4(code: str = "2412", n: int = 60, closed_te: bool = False) -> list[Pt2]:
    """NACA 4-digit airfoil as unit-chord (x, y) points (TE -> upper -> LE -> lower -> TE).

    `code` like "2412" (2% camber at 40% chord, 12% thick) or "0012". `n` points per surface,
    cosine-spaced. `closed_te=True` uses the -0.1036 coefficient (sharp trailing edge); the
    default keeps the published -0.1015 (a thin, finite trailing edge, which lofts more reliably)."""
    code = str(code).strip()
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"naca4: code must be 4 digits like '2412', got {code!r}")
    m, p, t = int(code[0]) / 100.0, int(code[1]) / 10.0, int(code[2:]) / 100.0
    if t <= 0:
        raise ValueError("naca4: thickness digits must be > 00")
    if m > 0 and not 0 < p < 1:
        raise ValueError("naca4: a cambered airfoil needs a camber position digit 1-9")

    def camber(x):
        if m == 0:
            return 0.0, 0.0
        if x < p:
            return m / p ** 2 * (2 * p * x - x * x), 2 * m / p ** 2 * (p - x)
        return (m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x * x),
                2 * m / (1 - p) ** 2 * (p - x))

    return _from_camber(_cosine_x(n), lambda x: _naca_thickness(x, t, closed_te), camber)


# standard (non-reflexed) 5-digit mean lines, design cl 0.3: position digit -> (r, k1)
_NACA5 = {1: (0.0580, 361.400), 2: (0.1260, 51.640), 3: (0.2025, 15.957),
          4: (0.2900, 6.643), 5: (0.3910, 3.230)}


def naca5(code: str = "23012", n: int = 60, closed_te: bool = False) -> list[Pt2]:
    """NACA 5-digit airfoil (standard, non-reflexed mean line), e.g. "23012", as unit-chord points.

    Digit 1 x 0.15 is the design lift coefficient, digit 2 x 5% the max-camber position (1-5),
    digit 3 must be 0 (reflexed lines are not supported), the last two the thickness in %."""
    code = str(code).strip()
    if len(code) != 5 or not code.isdigit():
        raise ValueError(f"naca5: code must be 5 digits like '23012', got {code!r}")
    cl, pos, q, t = int(code[0]) * 0.15, int(code[1]), int(code[2]), int(code[3:]) / 100.0
    if q != 0:
        raise ValueError("naca5: reflexed mean lines (third digit 1) are not supported")
    if pos not in _NACA5:
        raise ValueError("naca5: the camber position digit must be 1-5")
    if t <= 0:
        raise ValueError("naca5: thickness digits must be > 00")
    r, k1 = _NACA5[pos]
    k1 *= cl / 0.3

    def camber(x):
        if x < r:
            return (k1 / 6 * (x ** 3 - 3 * r * x * x + r * r * (3 - r) * x),
                    k1 / 6 * (3 * x * x - 6 * r * x + r * r * (3 - r)))
        return k1 * r ** 3 / 6 * (1 - x), -k1 * r ** 3 / 6

    return _from_camber(_cosine_x(n), lambda x: _naca_thickness(x, t, closed_te), camber)


def airfoil_from_points(points: Sequence[Sequence[float]], normalize: bool = True) -> list[Pt2]:
    """Clean a custom airfoil (e.g. pasted Selig/.dat coordinates) into unit-chord points.

    Accepts TE -> upper -> LE -> lower -> TE order (Lednicer two-block order is re-joined when
    it starts at the LE). Drops duplicate points; with `normalize` scales/shifts so the leading
    edge is at x=0 and chord is 1. Raises ValueError for fewer than 8 points."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 8:
        raise ValueError("airfoil_from_points: need at least 8 (x, y) points")
    # Lednicer order (upper LE->TE, then lower LE->TE): starts at the LE
    if pts[0][0] < pts[len(pts) // 2][0] and min(range(len(pts)), key=lambda i: pts[i][0]) == 0:
        half = next((i for i in range(1, len(pts)) if pts[i][0] < pts[i - 1][0]), None)
        if half is not None:
            pts = list(reversed(pts[:half])) + pts[half:]
    clean = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, clean[-1]) > 1e-9:
            clean.append(p)
    if normalize:
        x0 = min(p[0] for p in clean)
        x1 = max(p[0] for p in clean)
        if x1 - x0 <= 0:
            raise ValueError("airfoil_from_points: zero chord")
        i_le = min(range(len(clean)), key=lambda i: clean[i][0])
        y0 = clean[i_le][1]
        c = x1 - x0
        clean = [((x - x0) / c, (y - y0) / c) for x, y in clean]
    return clean


def _resolve_airfoil(airfoil) -> list[Pt2]:
    if isinstance(airfoil, str):
        a = airfoil.upper().replace("NACA", "").strip()
        return naca5(a) if len(a) == 5 else naca4(a)
    return airfoil_from_points(airfoil)


def _wire_through(pts3: list[Pt3]) -> bd.Wire:
    """A closed wire: one spline TE->LE->TE plus a straight TE closure when the TE is open.

    The spline is parametrised by point index, not chord length: sections built from the same
    point count then share one parametrisation, so a loft through them matches LE to LE and
    does not ripple between sections (cosine spacing makes the index a smooth angle)."""
    vecs = [bd.Vector(*p) for p in pts3]
    params = [i / (len(vecs) - 1) for i in range(len(vecs))]
    spline = bd.Edge.make_spline(vecs, parameters=params)
    if math.dist(pts3[0], pts3[-1]) < 1e-6:
        return bd.Wire([spline])
    return bd.Wire([spline, bd.Edge.make_line(vecs[-1], vecs[0])])


def airfoil_wire(airfoil="2412", chord: float = 100.0, le: Sequence[float] = (0, 0, 0),
                 twist_deg: float = 0.0, pivot: float = 0.25) -> bd.Wire:
    """Place an airfoil as a closed 3D wire in the XZ plane: chord along +X from `le`, up = +Z.

    `airfoil` is a NACA code string or a point list; `twist_deg` > 0 is nose-up, about the
    `pivot` chord fraction (quarter chord). Use the wires with smooth_loft or Solid.make_loft."""
    pts = _resolve_airfoil(airfoil)
    return _wire_through(_place(pts, chord, le, twist_deg, pivot))


def _place(pts: list[Pt2], chord, le, twist_deg, pivot, zscale: float = 1.0) -> list[Pt3]:
    th = math.radians(twist_deg)
    c, s = math.cos(th), math.sin(th)
    out = []
    for u, v in pts:
        dx, dz = (u - pivot) * chord, v * chord * zscale
        out.append((le[0] + pivot * chord + dx * c + dz * s, le[1], le[2] + dz * c - dx * s))
    return out


# ---------------------------------------------------------------- wings and bodies

def wing(span: float = 1000.0, root_chord: float = 250.0, tip_chord: float = 100.0,
         sweep_deg: float = 20.0, dihedral_deg: float = 3.0, twist_deg: float = -2.0,
         airfoil="2412", tip_airfoil=None, sections: list[dict] | None = None,
         symmetric: bool = True, n_sections: int = 6):
    """Lofted wing from a planform: tapered, swept (leading edge), dihedral, washout twist.

    Root leading edge at the origin, chord along +X, span along Y (+-span/2 when `symmetric`,
    else 0..span). `sections` overrides the planform with explicit stations, each a dict
    {"y", "chord", "x_le"?, "z"?, "twist"?, "airfoil"?} (missing keys come from the planform).
    Returns a validated Solid."""
    semi = span / 2.0 if symmetric else span
    if span <= 0 or root_chord <= 0 or tip_chord <= 0:
        raise ValueError("wing: span and chords must be > 0")
    tip_airfoil = airfoil if tip_airfoil is None else tip_airfoil

    def planform(y: float) -> dict:
        f = y / semi
        return {"y": y, "chord": root_chord + (tip_chord - root_chord) * f,
                "x_le": y * math.tan(math.radians(sweep_deg)),
                "z": y * math.tan(math.radians(dihedral_deg)), "twist": twist_deg * f,
                "airfoil": airfoil if f < 0.5 else tip_airfoil}

    if sections:
        stations = []
        for s in sorted(sections, key=lambda d: d["y"]):
            st = planform(float(s["y"]))
            st.update(s)
            stations.append(st)
    else:
        n = max(2, int(n_sections))
        stations = [planform(semi * i / (n - 1)) for i in range(n)]
    if any(st["chord"] <= 0 for st in stations):
        raise ValueError("wing: every section chord must be > 0")
    root_pts, tip_pts = _resolve_airfoil(airfoil), _resolve_airfoil(tip_airfoil)

    def wires(sign: float):
        out = []
        for st in stations:
            pts = st["airfoil"]
            pts = (root_pts if pts is airfoil else tip_pts if pts is tip_airfoil
                   else _resolve_airfoil(pts))
            out.append(_wire_through(_place(pts, st["chord"], (st["x_le"], sign * st["y"],
                                                               st["z"]), st["twist"], 0.25)))
        return out

    half = bd.Solid.make_loft(wires(1.0), ruled=False)
    if not symmetric:
        return check_solid(half, "wing")
    other = bd.Solid.make_loft(wires(-1.0), ruled=False)
    whole = half.fuse(other).clean()
    whole = whole.solids()[0] if len(whole.solids()) == 1 else whole
    return check_solid(whole, "wing")


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _bwb_section(n: int, thick: float, upper_share: float) -> list[Pt2]:
    """Unit-chord flying-wing section: NACA-like thickness, fuller forward and thin aft (so a
    planform cut near the TE stays thin), with an asymmetric upper/lower split."""
    pts_u, pts_l = [], []
    for x in _cosine_x(n):
        yt = _naca_thickness(x, thick, True) * (1.0 - 0.55 * x * x) * 2.0
        pts_u.append((x, yt * upper_share))
        pts_l.append((x, -yt * (1.0 - upper_share)))
    return list(reversed(pts_u)) + pts_l[1:]


def blended_wing_body(span: float = 524.0, length: float | None = None,
                      sweep_deg: float = 33.0, body_thickness: float = 0.17,
                      wing_thickness: float = 0.08, body_width: float = 0.28,
                      sawtooth: bool = True, notches: Sequence[float] = (0.24, 0.43, 0.62),
                      n_stations: int = 21):
    """B-2 style flying wing: swept straight LE, thick blended center body, sawtooth TE.

    Defaults are a ~1:100 B-2 (52.4 m span -> 524 mm). Nose at the origin, X aft, span on Y,
    Z up. `length` (nose to tail, default 0.4 x span) sets the center chord. Thickness ratios
    blend smoothly from `body_thickness` at the center to `wing_thickness` outboard of
    `body_width` (fraction of the semispan). With `sawtooth` the trailing edge is the double-W:
    segments parallel to the leading edges that break at `notches` (inner notch, aft point,
    outer notch, as semispan fractions); without it the TE is one smooth curve. The skin is one
    smooth loft through `n_stations` stations per side. Returns a validated Solid."""
    if span <= 0:
        raise ValueError("blended_wing_body: span must be > 0")
    s = span / 2.0
    L = 0.4 * span if length is None else float(length)
    k = math.tan(math.radians(sweep_deg))
    if not 0 < body_width < 1 or not 0 < wing_thickness <= body_thickness < 0.4:
        raise ValueError("blended_wing_body: need 0 < body_width < 1 and "
                         "0 < wing_thickness <= body_thickness < 0.4")
    y1, y2, y3 = (float(f) * s for f in notches)
    if not 0 < y1 < y2 < y3 < s:
        raise ValueError("blended_wing_body: notches must rise strictly inside (0, 1)")
    r_nose = 0.08 * s

    def x_le(y):  # straight swept LE with a softly rounded apex (smooth through y = 0)
        return k * (math.sqrt(y * y + r_nose * r_nose) - r_nose)

    def x_te_saw(y):  # the double-W polyline; every segment is parallel to a leading edge
        y = abs(y)
        a = L - k * min(y, y1)
        if y <= y1:
            return a
        b = a + k * (min(y, y2) - y1)
        if y <= y2:
            return b
        c = b - k * (min(y, y3) - y2)
        if y <= y3:
            return c
        return c + k * (y - y3)

    tip_chord = x_te_saw(s) - x_le(s)
    if tip_chord <= 0.02 * s or x_te_saw(y1) - x_le(y1) <= 0:
        raise ValueError("blended_wing_body: the planform closes before the tip; "
                         "increase `length` or move the notches inboard")

    def x_te_env(y):  # a smooth TE on or behind the sawtooth (the skin is cut back to it)
        y = abs(y)
        aft = max(x_te_saw(y2), x_te_saw(0))
        tip = x_te_saw(s)
        e = aft + (tip - aft) * _smoothstep(y2 * 0.6, s, y)
        e = max(e, x_te_saw(y))
        return e + 0.015 * s

    # evenly spaced stations: an uneven spacing makes the loft ripple where the body blends
    n = max(5, int(n_stations))
    fr = [i / (n - 1) for i in range(n)]
    ys = [f * s for f in fr]
    wires_ = []
    for y in [-v for v in reversed(ys[1:])] + ys:
        a = abs(y) / s
        blend = 1.0 - _smoothstep(0.0, body_width, a)
        thick = wing_thickness + (body_thickness - wing_thickness) * blend
        up = 0.52 + 0.16 * blend          # humped top over the body, flatter belly
        le = x_le(y)
        chord = (x_te_env(y) if sawtooth else _bwb_smooth_te(y, L, s, x_le)) - le
        pts = _bwb_section(48, thick, up)
        wires_.append(_wire_through(_place(pts, chord, (le, y, 0.0), 0.0, 0.25)))
    skin = bd.Solid.make_loft(wires_, ruled=False)
    if not sawtooth:
        return check_solid(skin, "blended_wing_body")
    # planform cutter: everything forward of the sawtooth TE, wider than the span
    far = -L
    ext = s * 1.2
    outline = [(far, -ext), (far, ext), (x_te_saw(s) + k * (ext - s), ext)]
    for yy in (y3, y2, y1, 0.0, -y1, -y2, -y3):
        outline.append((x_te_saw(yy), yy))
    outline.append((x_te_saw(s) + k * (ext - s), -ext))
    zmax = L
    cutter = bd.Solid.extrude(bd.Face(bd.Wire.make_polygon(
        [bd.Vector(x, y, -zmax) for x, y in outline], close=True)), bd.Vector(0, 0, 2 * zmax))
    body = skin.intersect(cutter)
    solids = body.solids()
    if len(solids) != 1:
        raise ValueError(f"blended_wing_body: the trailing-edge cut gave {len(solids)} pieces")
    return check_solid(solids[0], "blended_wing_body")


def _bwb_smooth_te(y, L, s, x_le):
    """Smooth TE for blended_wing_body(sawtooth=False): from the tail to a 0.24 x semispan tip."""
    y = abs(y)
    tip_chord = 0.12 * 2 * s
    tip = x_le(s) + tip_chord
    return L + (tip - L) * _smoothstep(0.0, s, y)


def fuselage(stations: Sequence[Sequence[float]] | None = None, exponent: float = 2.0,
             n: int = 32):
    """Smooth fuselage/nacelle/pod: a loft through (x, width, height[, z]) cross-sections.

    Sections are superellipses in the YZ plane at each x (`exponent` 2 = ellipse, 3-4 = boxier).
    A width or height of 0 at the first/last station makes a pointed nose/tail. The default is a
    200 mm streamlined body. Returns a validated Solid."""
    if stations is None:
        stations = [(0, 0, 0), (12, 18, 16, 0), (40, 30, 30, 1), (100, 32, 32, 2),
                    (160, 20, 22, 4), (200, 0, 0, 6)]
    rows = [tuple(float(v) for v in st) + (0.0,) * (4 - len(st)) for st in stations]
    rows.sort(key=lambda r: r[0])
    if len(rows) < 2:
        raise ValueError("fuselage: need at least 2 stations")
    objs = []
    for i, (x, w, h, z) in enumerate(rows):
        if w <= 0 or h <= 0:
            if i not in (0, len(rows) - 1):
                raise ValueError("fuselage: only the first or last station may be a point")
            objs.append(bd.Vertex(x, 0, z))
            continue
        pts = []
        for j in range(n):
            t = 2 * math.pi * j / n
            c, s_ = math.cos(t), math.sin(t)
            pts.append(bd.Vector(x, w / 2 * math.copysign(abs(c) ** (2 / exponent), c),
                                 z + h / 2 * math.copysign(abs(s_) ** (2 / exponent), s_)))
        objs.append(bd.Wire([bd.Edge.make_spline(pts, periodic=True)]))
    return check_solid(bd.Solid.make_loft(objs, ruled=False), "fuselage")


# ---------------------------------------------------------------- sweeps, lofts, surfaces

def _path_edge(path) -> bd.Wire:
    if isinstance(path, bd.Wire):
        return path
    if isinstance(path, bd.Edge):
        return bd.Wire([path])
    pts = [bd.Vector(*p) for p in path]
    if len(pts) < 2:
        raise ValueError("path needs at least 2 points")
    return bd.Wire([bd.Edge.make_spline(pts) if len(pts) > 2 else bd.Edge.make_line(*pts)])


def _profile_wire(profile, plane: bd.Plane, smooth: bool = False) -> bd.Wire:
    if isinstance(profile, (int, float)):
        if profile <= 0:
            raise ValueError("profile radius must be > 0")
        return bd.Wire([bd.Edge.make_circle(float(profile), plane)])
    pts = [plane.from_local_coords(bd.Vector(float(p[0]), float(p[1]), 0)) for p in profile]
    if len(pts) < 3:
        raise ValueError("profile needs at least 3 points")
    if smooth:
        params = [i / len(pts) for i in range(len(pts) + 1)]
        return bd.Wire([bd.Edge.make_spline(pts, periodic=True, parameters=params)])
    return bd.Wire.make_polygon(pts, close=True)


def _frame(wire: bd.Wire, t: float) -> bd.Plane:
    """Plane normal to the path at t, with a twist-free (corrected) x direction."""
    loc_plane = bd.Plane(wire.location_at(t))
    return bd.Plane(origin=wire.position_at(t), x_dir=loc_plane.x_dir, z_dir=wire.tangent_at(t))


def sweep_profile(path, profile=5.0, twist_deg: float = 0.0, smooth: bool = False,
                  frenet: bool = False):
    """Sweep a profile along a smooth path (spline through points), optionally twisting.

    `path`: list of (x, y, z) points (a spline through them) or an Edge/Wire. `profile`: a
    radius (circle) or the (u, v) corner points of a closed outline in the plane normal to the
    path start (straight sides; `smooth=True` runs a closed spline through them instead).
    `twist_deg` rotates the profile linearly to the end. Use for tubes, handles, ducts, horns,
    twisted blades and ribs. Returns a validated Solid."""
    wire = _path_edge(path)
    if not twist_deg:
        prof = _profile_wire(profile, _frame(wire, 0.0), smooth)
        solid = bd.Solid.sweep(bd.Face(prof), wire, is_frenet=frenet,
                               transition=bd.Transition.RIGHT)
        return check_solid(solid, "sweep_profile")
    steps = max(3, int(abs(twist_deg) // 30) + 2)
    profiles = [_profile_wire(profile, _frame(wire, i / (steps - 1)).rotated(
        (0, 0, twist_deg * i / (steps - 1))), smooth) for i in range(steps)]
    solid = bd.Solid.sweep_multi(profiles, wire, make_solid=True, is_frenet=frenet)
    return check_solid(solid, "sweep_profile")


def smooth_loft(sections: Sequence, ruled: bool = False):
    """Loft a solid through ordered sections: Wires/Faces/Sketches, or closed 3D point lists.

    A point list becomes a smooth closed (periodic) spline. The first/last item may be a single
    (x, y, z) point or a Vertex for a pointed end. Keep every section the same kind of outline
    (same edge count) for a clean skin; `ruled=True` gives straight panels between sections.
    Returns a validated Solid."""
    objs = []
    for sec in sections:
        if isinstance(sec, bd.Vertex):
            objs.append(sec)
        elif isinstance(sec, bd.Wire):
            objs.append(sec)
        elif isinstance(sec, (bd.Face, bd.Sketch)):
            face = sec if isinstance(sec, bd.Face) else sec.faces()[0]
            objs.append(face.outer_wire())
        elif isinstance(sec, bd.Edge):
            objs.append(bd.Wire([sec]))
        else:
            seq = list(sec)
            if seq and isinstance(seq[0], (int, float)):
                objs.append(bd.Vertex(*seq))
            else:
                objs.append(bd.Wire([bd.Edge.make_spline([bd.Vector(*p) for p in seq],
                                                         periodic=True)]))
    if sum(isinstance(o, bd.Wire) for o in objs) < 1 or len(objs) < 2:
        raise ValueError("smooth_loft: need at least 2 sections (one may be a point)")
    return check_solid(bd.Solid.make_loft(objs, ruled=ruled), "smooth_loft")


def spline_surface(points_grid: Sequence[Sequence[Sequence[float]]], tol: float = 0.01):
    """Smooth B-spline surface (a Face) through a rows x cols grid of (x, y, z) points.

    Rows must all have the same length (>= 2 x 2). Pass the face to thicken() for a solid
    panel, shell, canopy or hull skin. Returns a Face (not a solid)."""
    rows = [[bd.Vector(*p) for p in row] for row in points_grid]
    if len(rows) < 2 or len({len(r) for r in rows}) != 1 or len(rows[0]) < 2:
        raise ValueError("spline_surface: need an N x M grid (N, M >= 2) with equal row lengths")
    face = bd.Face.make_surface_from_array_of_points(rows, tol=tol)
    if not face.is_valid:
        raise ValueError("spline_surface: the surface is invalid; smooth the grid")
    return face


def thicken(surface, thickness: float = 2.0, both: bool = False):
    """Turn a surface (Face/Shell, e.g. from spline_surface) into a solid of `thickness`.

    The material goes along the face normal (negative thickness flips it); `both=True` centres
    it on the surface. Returns a validated Solid."""
    if not thickness:
        raise ValueError("thicken: thickness must be non-zero")
    if both:
        part = bd.thicken(surface, amount=abs(thickness) / 2.0, both=True)
    else:
        part = bd.Solid.thicken(surface, thickness)
    part = _as_shape(part)
    solids = part.solids()
    return check_solid(solids[0] if len(solids) == 1 else part, "thicken")


# ---------------------------------------------------------------- blends

def blend_edges(shape, radius: float, edges=None, min_radius: float | None = None):
    """Fillet edges (all by default) and, if OCCT refuses, retry with smaller radii.

    Stops at `min_radius` (default radius/8) and raises ValueError if even that fails. The
    radius used is printed (build output), so report it rather than the one you asked for.
    Fillets spread along tangent edge chains (OCCT behaviour). Returns a validated Solid."""
    shape = _as_shape(shape)
    edge_list = list(shape.edges() if edges is None else edges)
    if not edge_list:
        raise ValueError("blend_edges: no edges to blend")
    r = float(radius)
    floor = float(min_radius) if min_radius else r / 8.0
    last = None
    while r >= floor - 1e-12:
        try:
            out = check_solid(shape.fillet(r, edge_list), "blend_edges")
            if r != radius:
                print(f"blend_edges: radius reduced from {radius} to {r:.3g} mm")
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            r /= 2.0
    raise ValueError(f"blend_edges: no fillet down to {floor:.3g} mm worked ({last})")


def variable_fillet(shape, edges, r_start: float, r_end: float):
    """Fillet whose radius varies linearly from `r_start` to `r_end` along each edge.

    Good for blending a wing/fin/handle root into a body. Returns a validated Solid."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

    shape = _as_shape(shape)
    edges = [edges] if isinstance(edges, bd.Edge) else list(edges)
    if not edges or r_start <= 0 or r_end <= 0:
        raise ValueError("variable_fillet: need edges and radii > 0")
    mk = BRepFilletAPI_MakeFillet(shape.wrapped)
    for e in edges:
        mk.Add(float(r_start), float(r_end), e.wrapped)
    mk.Build()
    if not mk.IsDone():
        raise ValueError("variable_fillet: OCCT could not build the fillet; use smaller radii")
    return check_solid(_downcast(mk.Shape()), "variable_fillet")


# ---------------------------------------------------------------- enclosures

class EnclosureParts:
    """Result of enclosure(): `.base`, `.lid` (or None), `.cavity` (the inner volume), all in
    the closed position. `.compound(explode=0)` returns one shape for build(); a positive
    `explode` lifts the lid by that many mm."""

    def __init__(self, base, lid, cavity, split_z: float):
        self.base, self.lid, self.cavity, self.split_z = base, lid, cavity, split_z

    def compound(self, explode: float = 0.0):
        parts = [self.base]
        if self.lid is not None:
            parts.append(self.lid.moved(bd.Location((0, 0, explode))) if explode else self.lid)
        return bd.Compound(parts)


def _slab(bb, z0: float, z1: float):
    pad = 10.0
    return bd.Solid.make_box(bb.size.X + 2 * pad, bb.size.Y + 2 * pad, z1 - z0,
                             bd.Plane.XY.offset(z0).move(bd.Location(
                                 (bb.min.X - pad, bb.min.Y - pad, 0))))


def _box_at(cx, cy, z0, sx, sy, sz):
    return bd.Solid.make_box(sx, sy, sz, bd.Plane.XY.move(
        bd.Location((cx - sx / 2, cy - sy / 2, z0))))


def enclosure(outer=(80.0, 60.0, 30.0), wall: float = 2.0, split_z: float | None = None,
              corner_radius: float = 4.0, bosses="corners", boss_od: float = 6.0,
              boss_hole: float = 2.5, lid_hole: float = 2.9, ribs: int = 0, lid: bool = True,
              lip: float = 3.0, clearance: float = 0.2, snap_fits: int = 0):
    """Two-piece shelled enclosure: base + lid with a locating lip, screw bosses, ribs, snaps.

    `outer`: (length, width, height) for a rounded box sitting on z=0 centred on XY, or any
    closed Solid (organic shells work too). `split_z` is the base/lid parting height (default
    75% of the height). `bosses`: "corners", [] or [(x, y), ...]; each gets a `boss_hole`
    pilot and the lid a `lid_hole` clearance hole. `ribs`: floor ribs across X. `snap_fits`:
    bumps per long side on the lip with matching wall windows (box outer only). Returns an
    EnclosureParts (.base, .lid, .cavity, .compound())."""
    if isinstance(outer, (tuple, list)):
        lx, ly, lz = (float(v) for v in outer)
        if min(lx, ly, lz) <= 4 * wall:
            raise ValueError("enclosure: outer size must exceed 4 x wall in every direction")
        shell_outer = bd.Solid.make_box(lx, ly, lz, bd.Plane.XY.move(
            bd.Location((-lx / 2, -ly / 2, 0))))
        if corner_radius > 0:
            vert = shell_outer.edges().filter_by(bd.Axis.Z)
            shell_outer = shell_outer.fillet(min(corner_radius, min(lx, ly) / 2 - wall), vert)
        is_box = True
    else:
        shell_outer = _as_shape(outer)
        check_solid(shell_outer, "enclosure outer")
        is_box = False
    if wall <= 0:
        raise ValueError("enclosure: wall must be > 0")
    bb = shell_outer.bounding_box()
    cavity = shell_outer.offset_3d(None, -wall)
    check_solid(cavity, "enclosure cavity")
    split = bb.min.Z + 0.75 * bb.size.Z if split_z is None else float(split_z)
    if not bb.min.Z + 2 * wall < split < bb.max.Z - wall:
        raise ValueError("enclosure: split_z must leave at least a wall above and 2 below")
    shell = shell_outer - cavity
    base = shell & _slab(bb, bb.min.Z - 1, split)
    top = shell & _slab(bb, split, bb.max.Z + 1) if lid else None
    cbb = cavity.bounding_box()
    floor_z = cbb.min.Z

    if bosses == "corners":
        inset = wall + boss_od / 2 + (corner_radius * 0.3 if is_box else 0.5)
        bosses = [(sx * (bb.size.X / 2 - inset) + bb.center().X,
                   sy * (bb.size.Y / 2 - inset) + bb.center().Y)
                  for sx in (-1, 1) for sy in (-1, 1)]
    bosses = list(bosses or [])
    boss_top = split - (lip + clearance if lid else 0.0)
    if bosses and boss_top - floor_z > 1.0:
        posts = standoffs(bosses, boss_top - floor_z, boss_od, boss_hole, z=floor_z - 0.01)
        base = base + (posts & shell_outer)      # an organic outer may cut a corner post
    if ribs:
        rib_h = min(0.25 * (split - floor_z), 8.0)
        for i in range(ribs):
            x = cbb.min.X + cbb.size.X * (i + 1) / (ribs + 1)
            rib = _box_at(x, cbb.center().Y, floor_z - 0.01, wall, cbb.size.Y + 2 * wall, rib_h)
            base = base + (rib & (cavity + (shell & _slab(bb, bb.min.Z - 1, split))))
    if top is not None:
        # the lip hangs below the parting line inside the base wall (with clearance); its root
        # overlaps the lid wall a little above the parting line so the two fuse into one solid
        lip_outer = (shell_outer.offset_3d(None, -(wall + clearance))
                     & _slab(bb, split - lip, split + 0.5 * wall))
        lip_root = (shell_outer.offset_3d(None, -0.5 * wall)
                    & _slab(bb, split + 0.01, split + wall))
        lip_inner = (shell_outer.offset_3d(None, -(2 * wall + clearance))
                     & _slab(bb, split - lip - 1, split + wall + 1))
        ring = (lip_outer + lip_root) - lip_inner
        top = top + ring
        if bosses:
            top = top - standoffs(bosses, bb.max.Z - split + 2, lid_hole, 0, z=split - lip - 1)
        if snap_fits and is_box:
            bump_w, depth = 6.0, 0.6
            zc = split - lip / 2
            for i in range(snap_fits):
                x = -lx / 2 + lx * (i + 1) / (snap_fits + 1)
                for sy in (-1, 1):
                    y_face = sy * (ly / 2 - wall - clearance)
                    bump = _box_at(x, y_face + sy * depth / 2, zc - 0.6, bump_w, depth, 1.2)
                    window = _box_at(x, sy * (ly / 2 - wall / 2), zc - 0.6 - clearance,
                                     bump_w + 2 * clearance, wall + 0.02, 1.2 + 2 * clearance)
                    top = top + bump
                    base = base - window
    base_solid = _one_solid(base, "enclosure base")
    lid_solid = _one_solid(top, "enclosure lid") if top is not None else None
    return EnclosureParts(base_solid, lid_solid, cavity, split)


def _one_solid(shape, what):
    shape = _as_shape(shape)
    solids = shape.solids()
    if len(solids) != 1:
        raise ValueError(f"{what}: expected one solid, got {len(solids)}")
    return check_solid(solids[0], what)


def standoffs(points: Iterable[Sequence[float]], height: float = 6.0, od: float = 5.0,
              hole_d: float = 2.5, z: float = 0.0):
    """PCB standoffs/bosses: tubes of `od` x `height` at (x, y) points from z, with `hole_d` bores.

    `hole_d=0` gives solid pins (use as a drill tool to subtract). Returns one validated Solid,
    or the fused Compound of all posts."""
    pts = [tuple(float(v) for v in p[:2]) for p in points]
    if not pts or height <= 0 or od <= 0 or hole_d >= od:
        raise ValueError("standoffs: need points, height > 0 and hole_d < od")
    posts = []
    for x, y in pts:
        post = bd.Solid.make_cylinder(od / 2, height, bd.Plane.XY.move(bd.Location((x, y, z))))
        if hole_d > 0:
            post = post - bd.Solid.make_cylinder(hole_d / 2, height + 2, bd.Plane.XY.move(
                bd.Location((x, y, z - 1))))
        posts.append(_as_shape(post))
    for p in posts:
        check_solid(p, "standoffs")
    return posts[0] if len(posts) == 1 else posts[0].fuse(*posts[1:])


def vent_slots(center: Sequence[float] = (0, 0, 0), length: float = 20.0, width: float = 2.0,
               count: int = 5, pitch: float = 4.0, depth: float = 10.0, normal: str = "Z",
               angle_deg: float = 0.0):
    """Cutting tool for a row of rounded vent slots; subtract it: `part - ms.vent_slots(...)`.

    Slots run along local X, repeat along local Y every `pitch`, and are `depth` deep centred on
    `center`, through a wall whose normal is "X", "Y" or "Z". `angle_deg` rotates the row about
    that normal. Returns a validated Solid (all slots fused)."""
    if count < 1 or width <= 0 or length <= width or depth <= 0:
        raise ValueError("vent_slots: need count >= 1, width > 0, length > width, depth > 0")
    planes = {"Z": bd.Plane.XY, "Y": bd.Plane.XZ, "X": bd.Plane.YZ}
    if normal.upper() not in planes:
        raise ValueError("vent_slots: normal must be 'X', 'Y' or 'Z'")
    base = planes[normal.upper()]
    pl = bd.Plane(origin=bd.Vector(*center), x_dir=base.x_dir, z_dir=base.z_dir)
    pl = pl.rotated((0, 0, angle_deg)).offset(-depth / 2)
    slots = None
    for i in range(int(count)):
        off = (i - (count - 1) / 2) * pitch
        sk = bd.Plane(pl.from_local_coords((0, off, 0)), pl.x_dir, pl.z_dir) * \
            bd.SlotOverall(length, width)
        sl = bd.extrude(sk, amount=depth)
        slots = sl if slots is None else slots + sl
    shape = _as_shape(slots)
    return check_solid(shape, "vent_slots")


def cable_path(points: Sequence[Sequence[float]], diameter: float = 4.0):
    """A cable/hose/wire harness: a round tube swept along a smooth spline through 3D points.

    Use the solid as a modelled cable, or subtract it (larger diameter) to cut a routing
    channel. Keep bends gentler than ~1.5 x diameter. Returns a validated Solid."""
    if diameter <= 0:
        raise ValueError("cable_path: diameter must be > 0")
    return sweep_profile(points, diameter / 2.0)
