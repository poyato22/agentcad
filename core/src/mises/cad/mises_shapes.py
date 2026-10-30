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

def check_solid(shape, what: str = "shape", *, self_intersection: bool = False,
                hint: str = ""):
    """Validate a solid (is_valid, closed, volume > 0) and return it; raises ValueError otherwise.

    `self_intersection=True` also runs OCCT's self-intersection check (~0.3 s on a wing; the
    loft and sweep recipes always run it). Every recipe calls this on its result, so a recipe
    never hands back broken geometry. `hint` is appended to a self-intersection error."""
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
            raise ValueError(f"{what}: the shape intersects itself"
                             + (f" ({hint})" if hint else ""))
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
    it starts at the LE). Drops duplicate points; with `normalize` moves the leading edge (the
    point furthest from the TE) to the origin and rotates/scales so the trailing-edge midpoint
    is at (1, 0): an inclined chord line in a .dat file adds no hidden incidence. Raises
    ValueError for fewer than 8 points. Wings and airfoil_wire also resample every section to
    one common cosine spacing, so mixed sources loft cleanly."""
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
        te = ((clean[0][0] + clean[-1][0]) / 2, (clean[0][1] + clean[-1][1]) / 2)
        le = max(clean, key=lambda p: math.dist(p, te))
        c = math.dist(le, te)
        if c <= 0:
            raise ValueError("airfoil_from_points: zero chord")
        ux, uy = (te[0] - le[0]) / c, (te[1] - le[1]) / c
        clean = [(((x - le[0]) * ux + (y - le[1]) * uy) / c,
                  (-(x - le[0]) * uy + (y - le[1]) * ux) / c) for x, y in clean]
    return clean


N_SECTION = 60   # points per surface after resampling: every lofted airfoil shares this grid


def _pchip(xs: list[float], ys: list[float], xq: float) -> float:
    """Monotone cubic (Fritsch-Carlson) interpolation; clamps outside [xs[0], xs[-1]]."""
    n = len(xs)
    if xq <= xs[0]:
        return ys[0]
    if xq >= xs[-1]:
        return ys[-1]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    dl = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    d = [0.0] * n
    d[0], d[-1] = dl[0], dl[-1]
    for i in range(1, n - 1):
        if dl[i - 1] * dl[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            d[i] = (w1 + w2) / (w1 / dl[i - 1] + w2 / dl[i])
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= xq:
            lo = mid
        else:
            hi = mid
    hk = h[lo]
    t = (xq - xs[lo]) / hk
    h00, h10 = (1 + 2 * t) * (1 - t) ** 2, t * (1 - t) ** 2
    h01, h11 = t * t * (3 - 2 * t), t * t * (t - 1)
    return h00 * ys[lo] + h10 * hk * d[lo] + h01 * ys[hi] + h11 * hk * d[hi]


def _canonical(pts: list[Pt2], n: int = N_SECTION) -> list[Pt2]:
    """Resample a normalised airfoil onto the common grid: both surfaces at the same cosine
    stations (interpolated monotonically in sqrt(x), which keeps the round LE), upper first.
    Sections from any source (NACA, .dat, other point counts or spacing) then share one point
    count and parametrisation, which keeps a loft through them free of self-intersection."""
    i_le = min(range(len(pts)), key=lambda i: pts[i][0])
    a = list(reversed(pts[:i_le + 1]))          # LE -> TE
    b = pts[i_le:]                              # LE -> TE
    if len(a) < 3 or len(b) < 3:
        raise ValueError("airfoil: need points on both surfaces "
                         "(TE -> upper -> LE -> lower -> TE)")
    if sum(y for _, y in a) / len(a) < sum(y for _, y in b) / len(b):
        a, b = b, a                              # a is the upper surface

    def side(seq):
        xs, ys = [], []
        for x, y in seq:
            x = max(x, 0.0)
            if not xs or x > xs[-1] + 1e-12:
                xs.append(x)
                ys.append(y)
        return [math.sqrt(x) for x in xs], ys

    (su, yu), (sl, yl) = side(a), side(b)
    xc = _cosine_x(n)
    up = [(x, _pchip(su, yu, math.sqrt(x))) for x in xc]
    lo = [(x, _pchip(sl, yl, math.sqrt(x))) for x in xc]
    y_le = (up[0][1] + lo[0][1]) / 2
    up[0] = lo[0] = (0.0, y_le)
    return list(reversed(up)) + lo[1:]


def _resolve_airfoil(airfoil) -> list[Pt2]:
    """NACA code or point list -> normalised points on the common cosine grid."""
    if isinstance(airfoil, str):
        a = airfoil.upper().replace("NACA", "").strip()
        airfoil = naca5(a) if len(a) == 5 else naca4(a)
    return _canonical(airfoil_from_points(airfoil))


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
    else 0..span). The section blends linearly from `airfoil` (root) to `tip_airfoil` (NACA
    codes or point lists, e.g. a pasted .dat). `sections` overrides the planform with explicit
    stations, each a dict {"y", "chord", "x_le"?, "z"?, "twist"?, "airfoil"?} (missing keys
    come from the planform). Returns a validated, self-intersection-free Solid."""
    semi = span / 2.0 if symmetric else span
    if span <= 0 or root_chord <= 0 or tip_chord <= 0:
        raise ValueError("wing: span and chords must be > 0")
    tip_airfoil = airfoil if tip_airfoil is None else tip_airfoil

    def planform(y: float) -> dict:
        f = y / semi
        return {"y": y, "chord": root_chord + (tip_chord - root_chord) * f,
                "x_le": y * math.tan(math.radians(sweep_deg)),
                "z": y * math.tan(math.radians(dihedral_deg)), "twist": twist_deg * f}

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

    def section(st) -> list[Pt2]:
        if "airfoil" in st:
            return _resolve_airfoil(st["airfoil"])
        f = min(1.0, max(0.0, abs(st["y"]) / semi))
        return [((1 - f) * r[0] + f * t[0], (1 - f) * r[1] + f * t[1])
                for r, t in zip(root_pts, tip_pts)]

    def wires(sign: float):
        out = []
        for st in stations:
            pts = section(st)
            out.append(_wire_through(_place(pts, st["chord"], (st["x_le"], sign * st["y"],
                                                               st["z"]), st["twist"], 0.25)))
        return out

    half = bd.Solid.make_loft(wires(1.0), ruled=False)
    if not symmetric:
        return check_solid(half, "wing", self_intersection=True, hint=_LOFT_HINT)
    other = bd.Solid.make_loft(wires(-1.0), ruled=False)
    whole = half.fuse(other).clean()
    whole = whole.solids()[0] if len(whole.solids()) == 1 else whole
    return check_solid(whole, "wing", self_intersection=True, hint=_LOFT_HINT)


_LOFT_HINT = ("neighbouring sections differ too much in shape, twist or point spacing; add "
              "intermediate sections or give point-list sections equal counts")


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def _bwb_section(n: int, thick: float, upper_share: float, sharp: bool = True) -> list[Pt2]:
    """Unit-chord flying-wing section: NACA-like thickness, fuller forward and thin aft (so a
    planform cut near the TE stays thin), with an asymmetric upper/lower split. `sharp` closes
    the TE (the sawtooth cut removes it); an uncut skin keeps a thin open TE, because a skin
    whose upper and lower surfaces meet tangentially fails the self-intersection check."""
    pts_u, pts_l = [], []
    for x in _cosine_x(n):
        yt = _naca_thickness(x, thick, sharp) * (1.0 - 0.55 * x * x) * 2.0
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
    `body_width` (fraction of the semispan); the default length grows with `sweep_deg` so the
    tip keeps a chord. With `sawtooth` the trailing edge is the double-W:
    segments parallel to the leading edges that break at `notches` (inner notch, aft point,
    outer notch, as semispan fractions); without it the TE is one smooth curve. The skin is one
    smooth loft through `n_stations` stations per side. Returns a validated Solid."""
    if span <= 0:
        raise ValueError("blended_wing_body: span must be > 0")
    s = span / 2.0
    k = math.tan(math.radians(sweep_deg))
    L = 0.4 * span if length is None else float(length)
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

    def min_chord():
        return min(x_te_saw(y) - x_le(y) for y in (y1, y3, s))

    if length is None and min_chord() < 0.12 * s:
        L += 0.12 * s - min_chord()     # e.g. sweep_deg=45: lengthen until the tip keeps a chord
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
        pts = _bwb_section(48, thick, up, sharp=sawtooth)
        wires_.append(_wire_through(_place(pts, chord, (le, y, 0.0), 0.0, 0.25)))
    skin = bd.Solid.make_loft(wires_, ruled=False)
    if not sawtooth:
        return check_solid(skin, "blended_wing_body", self_intersection=True,
                           hint=_LOFT_HINT)
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
    return check_solid(solids[0], "blended_wing_body", self_intersection=True,
                       hint=_LOFT_HINT)


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
        objs.append(bd.Wire([bd.Edge.make_spline(pts, periodic=True,
                                                 parameters=_periodic_params(n))]))
    return check_solid(bd.Solid.make_loft(objs, ruled=False), "fuselage",
                       self_intersection=True, hint=_LOFT_HINT)


def _periodic_params(n: int) -> list[float]:
    return [i / n for i in range(n + 1)]


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
        return bd.Wire([bd.Edge.make_spline(pts, periodic=True,
                                            parameters=_periodic_params(len(pts)))])
    return bd.Wire.make_polygon(pts, close=True)


def _frames(wire: bd.Wire, ts: list[float]) -> list[bd.Plane]:
    """Planes normal to the path at each t, with a twist-free x direction carried along by
    parallel transport (each x is the previous one projected onto the new normal plane)."""
    out: list[bd.Plane] = []
    x = None
    for t in ts:
        z = wire.tangent_at(t).normalized()
        if x is None:
            seed = bd.Vector(0, 0, 1) if abs(z.Z) < 0.9 else bd.Vector(1, 0, 0)
            x = seed - z * seed.dot(z)
        else:
            x = x - z * x.dot(z)
        x = x.normalized()
        out.append(bd.Plane(origin=wire.position_at(t), x_dir=x, z_dir=z))
    return out


def _frame(wire: bd.Wire, t: float) -> bd.Plane:
    return _frames(wire, [t])[0]


def _spun(pl: bd.Plane, deg: float) -> bd.Plane:
    """The plane turned by `deg` about its own normal (same origin and normal)."""
    a = math.radians(deg)
    x = pl.x_dir * math.cos(a) + pl.y_dir * math.sin(a)
    return bd.Plane(origin=pl.origin, x_dir=x, z_dir=pl.z_dir)


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
        return check_solid(solid, "sweep_profile", self_intersection=True, hint=_SWEEP_HINT)
    # twisted: a smooth loft through rotated profiles placed along the path (a multi-section
    # pipe shell self-intersects or refuses periodic profiles once the twist passes ~90 deg)
    steps = max(8, int(abs(twist_deg) // 10) + 2)
    ts = [i / (steps - 1) for i in range(steps)]
    profiles = [_profile_wire(profile, _spun(pl, twist_deg * t), smooth)
                for t, pl in zip(ts, _frames(wire, ts))]
    solid = bd.Solid.make_loft(profiles, ruled=False)
    return check_solid(solid, "sweep_profile", self_intersection=True, hint=_SWEEP_HINT)


_SWEEP_HINT = "a profile wider than the path's tightest bend radius; smooth the path"


def smooth_loft(sections: Sequence, ruled: bool = False):
    """Loft a solid through ordered sections: Wires/Faces/Sketches, or closed 3D point lists.

    A point list becomes a smooth closed (periodic) spline. The first/last item may be a single
    (x, y, z) point or a Vertex for a pointed end. Keep every section the same kind of outline
    (same edge count; point lists with equal counts starting at matching points) for a clean
    skin; airfoil_wire sections are already resampled to match. `ruled=True` gives straight
    panels between sections. Returns a validated, self-intersection-free Solid."""
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
                objs.append(bd.Wire([bd.Edge.make_spline(
                    [bd.Vector(*p) for p in seq], periodic=True,
                    parameters=_periodic_params(len(seq)))]))
    if sum(isinstance(o, bd.Wire) for o in objs) < 1 or len(objs) < 2:
        raise ValueError("smooth_loft: need at least 2 sections (one may be a point)")
    return check_solid(bd.Solid.make_loft(objs, ruled=ruled), "smooth_loft",
                       self_intersection=True, hint=_LOFT_HINT)


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
    the closed position, plus `.split_z`, `.bosses` [(x, y)] and `.boss_top` (top of the base
    bosses). `.compound(explode=0)` returns one shape for build(); a positive `explode` lifts
    the lid by that many mm. Returning the EnclosureParts itself from build() also works."""

    def __init__(self, base, lid, cavity, split_z: float, bosses=(), boss_top: float = 0.0):
        self.base, self.lid, self.cavity, self.split_z = base, lid, cavity, split_z
        self.bosses, self.boss_top = list(bosses), boss_top

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
    """Two-piece enclosure (base + lid: lip, screw bosses, ribs, snaps); `return e.compound()`.

    `outer`: (length, width, height) for a rounded box sitting on z=0 centred on XY, or any
    closed Solid (organic shells work too). `split_z` is the base/lid parting height (default
    75% of the height). `bosses`: "corners", [] or [(x, y), ...]; each gets a base boss with
    a `boss_hole` pilot and a matching lid boss (down to `clearance` above it) with a
    `lid_hole` clearance bore, so the screw clamps boss to boss. `ribs`: floor ribs across X. `snap_fits`:
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
            # lid bosses hang from the lid ceiling to just above the base bosses
            lid_boss_z = boss_top + clearance
            ceiling = bb.max.Z - wall + 0.01
            if ceiling - lid_boss_z > 0.5:
                hang = standoffs(bosses, ceiling - lid_boss_z, boss_od, 0, z=lid_boss_z)
                top = top + (hang & shell_outer)
            bore_z = min(lid_boss_z, split - lip) - 1
            top = top - standoffs(bosses, bb.max.Z + 1 - bore_z, lid_hole, 0, z=bore_z)
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
    return EnclosureParts(base_solid, lid_solid, cavity, split, bosses, boss_top)


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
