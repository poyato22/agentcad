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
    # Plan 9 G-B: skin over skeleton, and archetype starters for design-heavy builds
    "squircle", "station_loft", "skin_shell", "tapered_capsule", "joint_cowl", "panel_split",
    "clamshell", "cover_for", "trust", "humanoid", "quadruped", "car_body",
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


# ---------------------------------------------------------------- skin over skeleton (Plan 9 G-B)
#
# Design-class builds put mechanism (actuators, boards, batteries) at skeleton hardpoints and
# cover it with separate shell parts: lofts through matched sections with an inset-loft cavity
# (never offset_3d on a loft: it reports valid with a wrong or negative volume), joint cowls,
# capsule links instead of bars, and panel gaps as the feature lines.

def _occ_volume(shape, eps: float = 1e-6) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(_as_shape(shape).wrapped, props, eps)
    return float(props.Mass())


def _mesh_volume(shape, tol: float | None = None) -> float:
    """Volume of a tessellation: 0.05 mm, coarser for big parts (1e-4 of the diagonal), but
    never coarser than a tenth of the wall (2 V / A estimates a thin shell's thickness)."""
    import numpy as np   # a build123d dependency, so always present in the worker

    shape = _as_shape(shape)
    if tol is None:
        area = float(shape.area) or 1.0
        wall_est = 2.0 * abs(float(shape.volume)) / area
        tol = max(0.03, min(max(0.05, 1e-4 * shape.bounding_box().diagonal), 0.1 * wall_est))
    # OCCT's mesher can fail on one big freeform face at one deflection and succeed at the
    # next: retry finer, then coarser, before giving up
    for t in (tol, tol / 3, tol * 3):
        try:
            verts, tris = shape.tessellate(t, 0.1)
            break
        except AttributeError:          # BRepMesh left a face without a triangulation
            continue
    else:
        raise ValueError("the shape could not be meshed at any tolerance")
    v = np.array([(p.X, p.Y, p.Z) for p in verts], dtype=np.float64)
    f = np.array(tris, dtype=np.int64).reshape(-1, 3)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)


def trust(shape, what: str = "shape", rel: float = 0.03):
    """Check a freeform solid harder than check_solid: valid AND two volume methods agree.

    `is_valid` misses bad offsets and booleans on lofts (an offset_3d shell can be "valid" with
    a volume 18% off, or negative). trust() also requires the precise OCCT volume (eps 1e-6) to
    be positive and within `rel` of the volume of a 0.05 mm tessellation. Use it on every shell
    and cover. Returns the shape; raises ValueError naming `what` otherwise."""
    shape = check_solid(_as_shape(shape), what)
    vo, vm = _occ_volume(shape), _mesh_volume(shape)
    if vo > 0 and abs(vo - vm) > rel * max(vo, vm):
        # OCCT's mesher can mis-triangulate one big freeform face at one deflection; a real
        # defect disagrees at every deflection, so re-mesh finer before giving up
        area = float(shape.area) or 1.0
        fine = max(0.01, 0.02 * 2.0 * vo / area)
        vm = _mesh_volume(shape, tol=fine)
    if not (vo > 0 and abs(vo - vm) <= rel * max(vo, vm)):
        raise ValueError(f"{what}: untrustworthy solid (precise volume {vo:.0f} mm3, mesh "
                         f"volume {vm:.0f} mm3); rebuild it as outer loft minus inset loft")
    return shape


def squircle(a: float, b: float, n: float = 2.6, plane: bd.Plane = bd.Plane.XY,
             seam_deg: float = 225.0, k: int = 96, tol: float = 0.05):
    """Closed smooth section (a Wire): superellipse |x/a|^n + |y/b|^n = 1 on `plane`.

    `a`, `b` are the half-width (local X) and half-depth (local Y). n=2 is an ellipse, 2.4-3 a
    soft "muscle" section, 3-4 a soft box (use it instead of RectangleRounded, which has 8 edges,
    is only G1 and twists lofts). The wire is one fitted B-spline (`k` samples, `tol` mm), so
    every section of a loft has the same topology. Keep the seam (`seam_deg`, from local +X
    toward +Y) off any plane you later split on and on a hidden side."""
    if a <= 0 or b <= 0 or n < 1.2:
        raise ValueError("squircle: need a > 0, b > 0 and n >= 1.2")
    pts = []
    for i in range(int(k)):
        t = math.radians(seam_deg) + 2 * math.pi * i / k
        c, s = math.cos(t), math.sin(t)
        pts.append(plane.from_local_coords((a * math.copysign(abs(c) ** (2 / n), c),
                                            b * math.copysign(abs(s) ** (2 / n), s))))
    return bd.Wire([bd.Edge.make_spline_approx(pts + [pts[0]], tol=tol, max_deg=5)])


def _stations(stations) -> list[tuple[float, float, float, float, float, float]]:
    rows = []
    for st in stations:
        st = tuple(float(v) for v in st)
        if not 3 <= len(st) <= 6:
            raise ValueError("station_loft: a station is (z, half_w, half_d[, n, dx, dy])")
        rows.append(st + (2.6, 0.0, 0.0)[len(st) - 3:])
    if len(rows) < 2:
        raise ValueError("station_loft: need at least 2 stations")
    steps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
    if not (all(s > 0 for s in steps) or all(s < 0 for s in steps)):
        raise ValueError("station_loft: station z values must be strictly monotonic")
    return rows


def _station_wire(row, inset: float, seam_deg: float):
    z, a, b, n, dx, dy = row
    if a - inset <= 0 or b - inset <= 0:
        raise ValueError(f"station_loft: the section at z={z:g} is thinner than the wall")
    return squircle(a - inset, b - inset, n, bd.Plane(origin=(dx, dy, z)), seam_deg=seam_deg)


def station_loft(stations, inset: float = 0.0, seam_deg: float = 225.0):
    """A body or limb lofted through squircle stations along Z: [(z, half_w, half_d, n, dx, dy)].

    Each station is a section at height `z` with half-width `half_w` (X), half-depth `half_d`
    (Y), exponent `n` (default 2.6) and centre offset (dx, dy) (default 0): a forward dy gives a
    belly or chest, a dx a lean. z must be monotonic (up or down). `inset` > 0 shrinks every
    section (the cavity of skin_shell). Smoothness comes from the loft: do not fillet it.
    Returns a validated Solid."""
    rows = _stations(stations)
    loft = bd.Solid.make_loft([_station_wire(r, inset, seam_deg) for r in rows])
    return check_solid(loft, "station_loft", hint=_LOFT_HINT)


def _end_plug(row, inset: float, outward: float, seam_deg: float):
    face = bd.Face(_station_wire(row, inset, seam_deg))
    return _as_shape(bd.extrude(face, amount=outward, dir=(0, 0, 1)))


def skin_shell(stations, wall: float = 2.5, open_ends=(True, True), seam_deg: float = 225.0):
    """A cover shell: the station_loft minus the same loft inset by `wall`, trust()-checked.

    `stations` as station_loft. `open_ends` (first, last) leaves that end open (the cavity runs
    out through it) so mechanism and harness pass in; a closed end keeps a cap `wall` thick.
    The wall is the section inset (exact for ellipses, close for squircles). This is the shell
    pattern to use on freeform covers instead of offset_3d. Returns a trusted Solid."""
    if wall <= 0:
        raise ValueError("skin_shell: wall must be > 0")
    rows = _stations(stations)
    sgn = 1.0 if rows[-1][0] > rows[0][0] else -1.0
    outer = bd.Solid.make_loft([_station_wire(r, 0.0, seam_deg) for r in rows])
    inner = list(rows)
    if not open_ends[0]:
        inner[0] = (rows[0][0] + sgn * wall,) + rows[0][1:]
    if not open_ends[1]:
        inner[-1] = (rows[-1][0] - sgn * wall,) + rows[-1][1:]
    cavity = _as_shape(bd.Solid.make_loft([_station_wire(r, wall, seam_deg) for r in inner]))
    if open_ends[0]:
        cavity = cavity + _end_plug(rows[0], wall, -sgn * 5.0, seam_deg)
    if open_ends[1]:
        cavity = cavity + _end_plug(rows[-1], wall, sgn * 5.0, seam_deg)
    shell = _as_shape(outer - cavity)
    solids = shell.solids()
    return trust(solids[0] if len(solids) == 1 else shell, "skin_shell")


def tapered_capsule(r1: float, r2: float, length: float | None = None, start=None, end=None):
    """A G1 link between two joint spheres (r1 at start, r2 at end): use it, never a bar.

    Use it for every visible link between joints. It is the convex hull of the two spheres (three faces, tangent everywhere). Give either
    `length` (from the origin up +Z) or `start` and `end` points (the joint centres). Returns a
    validated Solid."""
    s = e = None
    if start is not None and end is not None:
        s, e = bd.Vector(*start), bd.Vector(*end)
        length = (e - s).length
    if length is None or length <= 0 or r1 <= 0 or r2 <= 0:
        raise ValueError("tapered_capsule: need r1, r2 > 0 and a length (or start != end)")
    if abs(r1 - r2) >= 0.99 * length:
        raise ValueError("tapered_capsule: the radii differ by more than the length")
    L = float(length)
    a = math.asin((r1 - r2) / L)
    p1 = (r1 * math.cos(a), 0, r1 * math.sin(a))
    p2 = (r2 * math.cos(a), 0, L + r2 * math.sin(a))
    m1, m2 = (a - math.pi / 2) / 2, (a + math.pi / 2) / 2
    w = bd.Wire([bd.ThreePointArc((0, 0, -r1), (r1 * math.cos(m1), 0, r1 * math.sin(m1)), p1),
                 bd.Line(p1, p2),
                 bd.ThreePointArc(p2, (r2 * math.cos(m2), 0, L + r2 * math.sin(m2)),
                                  (0, 0, L + r2)),
                 bd.Line((0, 0, L + r2), (0, 0, -r1))])
    cap = _as_shape(bd.revolve(bd.Face(w), axis=bd.Axis.Z))
    if s is not None:
        cap = cap.moved(bd.Location(bd.Plane(origin=s, z_dir=(e - s).normalized())))
    return check_solid(cap, "tapered_capsule")


def joint_cowl(r_act: float, w_act: float, clearance: float = 2.0, wall: float = 2.5,
               rom_deg: float = 120.0, bulge: float = 3.0, bore: float | None = None):
    """A revolved joint cover around an actuator on the X axis, open for the link's swing.

    The actuator is a cylinder of radius `r_act` and width `w_act` centred on the origin, axis
    X. The cowl is a barrel lozenge (`bulge` mm proud at its middle) with `clearance` around the
    actuator and a `wall`-thick skin, end walls with an axle bore (`bore`, default 0.35 r_act;
    0 = closed ends, the clean look) and an open sector of `rom_deg` centred on -Z, between
    the end walls, that the link swings through (0 = closed). Hide the joint this way, or
    celebrate it as a deliberate machined ring; never half-expose it. Returns a validated
    Solid."""
    if r_act <= 0 or w_act <= 0 or clearance < 0 or wall <= 0 or not 0 <= rom_deg < 300:
        raise ValueError("joint_cowl: need r_act, w_act, wall > 0 and 0 <= rom_deg < 300")
    r_in = r_act + clearance
    inner_w = w_act + 2 * clearance
    half = inner_w / 2 + wall + 0.18 * r_in    # domed ends past the end walls
    top = r_in + wall + bulge
    p = 6.0                                    # superellipse profile: a crown with domed ends
    xs = [-half * math.cos(math.pi * i / 48) for i in range(49)]
    pts = [(x, 0, top * max(0.0, 1 - abs(x / half) ** p) ** (1 / p)) for x in xs]
    if min(r for x, _, r in pts if abs(x) <= inner_w / 2) < r_in + wall - 1e-6:
        raise ValueError("joint_cowl: the wall is too thin over the actuator; raise bulge")
    prof = bd.Edge.make_spline_approx(pts, tol=0.02, max_deg=5)
    lid = bd.Wire([prof, bd.Line(prof @ 1, prof @ 0)])
    cowl = _as_shape(bd.revolve(bd.Face(lid), axis=bd.Axis.X))
    cowl = _as_shape(cowl - bd.Solid.make_cylinder(r_in, inner_w, bd.Plane.YZ.offset(-inner_w / 2)))
    bore = 0.35 * r_act if bore is None else float(bore)
    if bore > 0:
        cowl = _as_shape(cowl - bd.Solid.make_cylinder(bore, 2 * half + 2,
                                                       bd.Plane.YZ.offset(-half - 1)))
    if rom_deg > 0:
        h = math.radians(rom_deg / 2)
        sector = bd.Solid.make_cylinder(top + 10, inner_w, bd.Plane(
            origin=(-inner_w / 2, 0, 0), x_dir=(0, -math.sin(h), -math.cos(h)),
            z_dir=(1, 0, 0)), angle=rom_deg)
        cowl = _as_shape(cowl - sector)
    solids = cowl.solids()
    if len(solids) != 1:
        raise ValueError("joint_cowl: the open sector splits the cowl; use a smaller rom_deg")
    return check_solid(solids[0], "joint_cowl")


def panel_split(shape, plane: bd.Plane = bd.Plane.XY, gap: float = 0.8):
    """Split a cover into two panels with a real `gap` (mm) between them: (positive, negative).

    The panel gap is the feature line, the way real products get crisp lines (an engraved
    groove is fragile on lofts). `plane` is the gap's mid-plane; the first part is on its +normal
    side. Keep a loft's section seams off this plane. Returns two validated shapes (each a Solid,
    or a Compound when the cut leaves several pieces)."""
    shape = _as_shape(shape)
    if gap < 0:
        raise ValueError("panel_split: gap must be >= 0")
    out = []
    for side, off, keep in (("positive", gap / 2, bd.Keep.TOP),
                            ("negative", -gap / 2, bd.Keep.BOTTOM)):
        solids = []
        for piece in (shape.solids() or [shape]):        # a compound splits solid by solid
            part = _as_shape(bd.split(piece, bisect_by=plane.offset(off), keep=keep))
            solids += part.solids() if part is not None else []
        if not solids:
            raise ValueError(f"panel_split: nothing on the {side} side of the plane")
        out.append(check_solid(solids[0] if len(solids) == 1 else bd.Compound(solids),
                               f"panel_split {side} panel"))
    return out[0], out[1]


def clamshell(shape, plane: bd.Plane = bd.Plane.XZ, gap: float = 0.3):
    """Molded halves of a cover split on its silhouette plane: (+normal half, -normal half).

    A panel_split with a tight default gap. The default XZ plane (normal -Y) puts the parting
    line on the side silhouette of a part facing -Y: the first half returned is the front (-Y),
    the second the back. Each half pulls along the plane normal: build draft into the
    sections. Returns two validated shapes."""
    return panel_split(shape, plane, gap)


def _envelope_box(env):
    if isinstance(env, bd.Shape):
        return env
    (cx, cy, cz), (sx, sy, sz) = env
    return bd.Solid.make_box(sx, sy, sz, bd.Plane.XY.move(
        bd.Location((cx - sx / 2, cy - sy / 2, cz - sz / 2))))


def cover_for(stations, components=(), wall: float = 2.5, clearance: float = 1.5,
              bulkheads=(), harness=(16.0, 10.0), open_ends=(True, True),
              seam_deg: float = 225.0):
    """A skin_shell over mechanism: asserts every component fits inside, adds bulkheads.

    `components` are Solids or boxes ((cx, cy, cz), (sx, sy, sz)) of the parts the cover hides
    (servo, board, battery); each, grown by `clearance`, must sit inside the cavity or this
    raises ValueError naming it. `bulkheads` are z heights of internal ribs (the outer loft's
    slab, `wall` thick) the mechanism bolts to, each with a `harness` (w, d) pass-through for
    cables. Returns a trusted Solid."""
    rows = _stations(stations)
    shell = skin_shell(rows, wall, open_ends, seam_deg)
    cavity = station_loft(rows, inset=wall, seam_deg=seam_deg)
    for i, comp in enumerate(components):
        bb = _envelope_box(comp).bounding_box()
        grown = bd.Solid.make_box(bb.size.X + 2 * clearance, bb.size.Y + 2 * clearance,
                                  bb.size.Z + 2 * clearance, bd.Plane.XY.move(bd.Location(
                                      (bb.min.X - clearance, bb.min.Y - clearance,
                                       bb.min.Z - clearance))))
        clash = _as_shape(grown - cavity)
        if clash.solids() and clash.volume > 1.0:
            raise ValueError(f"cover_for: component {i} does not fit inside the skin with "
                             f"{clearance} mm clearance; grow the stations around it")
    outer = station_loft(rows, seam_deg=seam_deg)
    hw, hd = harness
    for z in bulkheads:
        z = float(z)
        rib = _as_shape(outer & bd.Solid.make_box(1e4, 1e4, wall, bd.Plane.XY.move(
            bd.Location((-5e3, -5e3, z - wall / 2)))))
        if hw > 0 and hd > 0:
            rib = _as_shape(rib - bd.Solid.make_box(hw, hd, wall + 2, bd.Plane.XY.move(
                bd.Location((-hw / 2, -hd / 2, z - wall / 2 - 1)))))
        shell = _as_shape(shell + rib)
    solids = shell.solids()
    return trust(solids[0] if len(solids) == 1 else shell, "cover_for")


# ---------------------------------------------------------------- archetype starters (Plan 9 G-B)
#
# Stage-2 massing and stage-4 covers for design-heavy archetypes, driven by proportion units.
# Each returns an Archetype: named cover parts (separate solids: shells, cowls, panels), each
# with a CMF role, plus the skeleton (hardpoints, joint axes, actuator envelopes) the
# mechanism is placed on. Start from one, then edit its stations: do not start from boxes.

class Archetype:
    """Result of humanoid() / quadruped() / car_body().

    `.parts` {name: Solid} are the visible covers; `.roles` {name: "shell"|"joint"|"accent"|
    "tire"} their CMF role (at most 3 materials: light shells, dark joints, one accent);
    `.hardpoints` {name: (x, y, z)}; `.axes` {name: ((x, y, z), (dx, dy, dz))} joint axes;
    `.envelopes` {name: Solid} the actuator/component volumes the covers must clear;
    `.units` the proportion read (e.g. head heights). `.compound(roles=None)` returns one shape
    for build() (only the given roles when set). Make each part its own Mises part for real
    builds; `.part(name)` returns one."""

    def __init__(self, kind: str, units: dict):
        self.kind, self.units = kind, dict(units)
        self.parts: dict = {}
        self.roles: dict[str, str] = {}
        self.hardpoints: dict[str, tuple] = {}
        self.axes: dict[str, tuple] = {}
        self.envelopes: dict = {}
        self.hulls: dict = {}        # {name: solid outer volume of a shelled part} for checks

    def add(self, name: str, shape, role: str = "shell"):
        self.parts[name] = check_solid(_as_shape(shape), f"{self.kind} {name}")
        self.roles[name] = role
        return self.parts[name]

    def part(self, name: str):
        return self.parts[name]

    def lift(self, dz: float):
        """Move everything (parts, hulls, envelopes, hardpoints, axes) up by `dz` mm."""
        loc = bd.Location((0, 0, dz))
        for d in (self.parts, self.hulls, self.envelopes):
            for n in d:
                d[n] = d[n].moved(loc)
        self.hardpoints = {n: (p[0], p[1], p[2] + dz) for n, p in self.hardpoints.items()}
        self.axes = {n: ((o[0], o[1], o[2] + dz), v) for n, (o, v) in self.axes.items()}
        return self

    def compound(self, roles=None):
        keep = [s for n, s in self.parts.items() if roles is None or self.roles[n] in roles]
        return bd.Compound(keep)


def _placed(shape, origin=(0, 0, 0), rot=(0.0, 0.0, 0.0)):
    """Rotate `shape` about X, then Y, then Z (degrees) around the origin, then move it."""
    s = _as_shape(shape)
    rx, ry, rz = rot
    for axis, ang in ((bd.Axis.X, rx), (bd.Axis.Y, ry), (bd.Axis.Z, rz)):
        if ang:
            s = s.rotate(axis, ang)
    return s.moved(bd.Location(tuple(float(v) for v in origin)))


def _limb(stations, origin, rot, *, shell: bool, wall: float, seam_deg: float = 100.0,
          open_ends=(True, True)):
    body = (skin_shell(stations, wall, open_ends, seam_deg) if shell
            else station_loft(stations, seam_deg=seam_deg))
    return _placed(body, origin, rot)


def _revolved(profile_pts, scale=(1.0, 1.0, 1.0)):
    """A smooth body of revolution about Z through (r, z) profile points (r=0 at both ends),
    optionally scaled per axis: helmets, heads, pods."""
    pts = [(float(r), 0.0, float(z)) for r, z in profile_pts]
    sp = bd.Spline(*[bd.Vector(*p) for p in pts])
    w = bd.Wire([sp, bd.Line(pts[-1], pts[0])])
    body = _as_shape(bd.revolve(bd.Face(w), axis=bd.Axis.Z))
    if scale != (1.0, 1.0, 1.0):
        body = _as_shape(bd.scale(body, by=scale))
    return body


def _joint(me: Archetype, name: str, at, r: float, w: float, rom: float, rx: float, *,
           shell: bool, wall: float, role: str = "joint"):
    """An actuator envelope on an X axis at `at`, and (shell) its cowl turned `rx` deg about X
    so the open sector faces the way the link swings."""
    me.axes[name] = (tuple(at), (1.0, 0.0, 0.0))
    me.hardpoints[name] = tuple(at)
    me.envelopes[name] = _placed(bd.Solid.make_cylinder(r, w, bd.Plane.YZ.offset(-w / 2)), at)
    if shell:
        cowl = joint_cowl(r, w, 0.04 * r, wall, rom, bulge=0.08 * r, bore=0)
        me.add(f"{name}_cowl", _placed(cowl, at, (rx, 0, 0)), role)


def _girth(a: float, b: float) -> float:
    """Ramanujan's ellipse perimeter: the girth of a section with half-axes a, b."""
    h = ((a - b) / (a + b)) ** 2
    return math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))


def _yoke_section(x: float, zc: float, a: float, b: float, n: float, dy: float, inset: float):
    """A section of a loft along X (the plane x = const): a squircle in Y-Z, half-height `a`
    (Z) and half-depth `b` (Y), centred on (dy, zc)."""
    return squircle(b - inset, a - inset, n, bd.Plane(origin=(x, dy, zc), x_dir=(0, 1, 0),
                                                      z_dir=(1, 0, 0)), seam_deg=200.0)


def humanoid(height: float = 1700.0, heads: float = 8.0, shoulder_heads: float = 1.95,
             hip_heads: float = 0.87, shell: bool = True, wall: float = 3.0):
    """Covered humanoid starter: sculpted shells, an integrated shoulder yoke, 8 head heights.

    Stands on z=0 facing -Y (Z up, +X = its left side). `height` and `heads` (head heights per
    body; 7.5-8 reads heroic) set the unit; `shoulder_heads` / `hip_heads` are the shoulder and
    hip joint spans in heads. The language is the covered service-humanoid one (light shells,
    graphite joints, a glossy face): a helmet split from its face by a panel gap; a neck; a
    chest-and-shoulder yoke lofted shoulder to shoulder so the shoulder cowls grow out of the
    torso, split front / back by a real seam; banded abdomen plates; pelvis; thighs with a
    quadriceps mass and a sloped hip line, split front / back; shins flattened at the sides
    with a posterior calf mass; biceps-heavy upper arms over slimmer forearms; recessed
    graphite cowls at elbows, knees and ankles nested in socketed shell ends; hands with a palm,
    four fingers and a thumb; feet with heel, arch and toe, a toe-cap gap and a dark sole. No
    bars and no exposed actuators: the actuators are `.envelopes` on the joint axes in `.axes`
    (shoulders inside the yoke, hips inside the pelvis). `.units` carries the head unit and the
    limb girths; `.hulls` the solid outer lofts. `shell=False` returns solid massing for
    concept sketches. Returns an Archetype."""
    u = height / heads
    k = height / 1700.0                        # stations are drawn for a 1.7 m, 8-head body
    q = 8.0 / heads
    sw = shoulder_heads * u / 2
    hw = hip_heads * u / 2
    z_sh = height - 1.5 * u
    z_waist = height - 3.0 * u
    z_hip = height - 3.95 * u
    z_knee = 0.285 * height
    z_ank = 0.05 * height
    z_elb = z_sh - 1.42 * u
    z_wri = z_elb - 1.18 * u
    W = wall
    me = Archetype("humanoid", {"height": height, "head_heights": heads,
                                "head_unit": round(u, 1),
                                "shoulder_to_hip_span": round(shoulder_heads / hip_heads, 2)})

    def S(z, a, b, n=2.4, dx=0.0, dy=0.0):
        return (z * k, a * k, b * k, n, dx * k, dy * k)

    def body(stations, open_ends=(True, True)):
        return (skin_shell(stations, W, open_ends, 100.0) if shell
                else station_loft(stations, seam_deg=100.0))

    def hull(stations):
        return station_loft(stations, seam_deg=100.0)

    # ---- head: a revolved helmet split by a panel gap into a glossy face and a light shell
    hh = 212.0 * k * q
    hz = height - hh
    head = _revolved([(0, 0), (40, 4), (62, 30), (72, 85), (70, 150), (56, 195), (30, 214),
                      (0, 218)], scale=(k * q, 1.2 * k * q, hh / 218.0))
    head = _placed(head, (0, 8 * k, hz))
    face, helmet = panel_split(head, bd.Plane(origin=(0, -30 * k * q, hz + 0.5 * hh),
                                              z_dir=(0, -1, 0.25)), gap=1.5)
    me.add("head_face", face, "accent")
    me.add("head_shell", helmet, "shell")
    # ---- neck: a defined column (narrowest at its waist) from the yoke into the head
    neck_st = [S(0, 50, 46, 2.3, 0, 4), S(45, 40, 38, 2.2, 0, 6), S(90, 44, 42, 2.2, 0, 8)]
    me.add("neck", _placed(station_loft(neck_st, seam_deg=100.0),
                           (0, 0, z_sh + 40 * k)), "joint")
    me.hardpoints["neck"] = (0, 6 * k, z_sh + 85 * k)

    # ---- the yoke: chest, upper back and shoulders in ONE loft along X, so the shoulders
    # grow out of the torso. It ends in an open ring at each shoulder, closed by a graphite
    # domed cowl over the shoulder actuator: the joint is celebrated, not half-exposed.
    zs = z_sh / k
    # (x, centre z, half-height, half-depth, n, forward bulge). Few stations and a near-
    # constant exponent: every change between stations is a ripple in the reflections, so the
    # V taper is monotone and the pec bulge eases out toward the shoulders
    yk = [(0, zs - 92, 166, 110, 2.5, -12), (110, zs - 80, 150, 104, 2.5, -9),
          (170, zs - 40, 108, 88, 2.5, -3), (204, zs - 10, 76, 72, 2.4, 0),
          (224, zs - 4, 64, 64, 2.3, 0)]
    rows = [(-x, zc, a, b, n, dy) for x, zc, a, b, n, dy in reversed(yk[1:])] + yk
    xs = [x * k for x, *_r in rows]

    def yoke_loft(inset):
        """Outer loft, or (inset > 0) the cavity through the SAME stations (the same
        interpolation, so the wall stays even), run out through both ends: open rings."""
        loft = _as_shape(bd.Solid.make_loft([
            _yoke_section(xs[i], rows[i][1] * k, rows[i][2] * k, rows[i][3] * k, rows[i][4],
                          rows[i][5] * k, inset) for i in range(len(rows))]))
        if inset > 0:
            for i, sgn in ((0, -1.0), (len(rows) - 1, 1.0)):
                face = bd.Face(_yoke_section(xs[i], rows[i][1] * k, rows[i][2] * k,
                                             rows[i][3] * k, rows[i][4], rows[i][5] * k, inset))
                loft = _as_shape(loft + bd.extrude(face, amount=10 * k, dir=(sgn, 0, 0)))
        return loft
    yoke_hull = check_solid(yoke_loft(0.0), "humanoid yoke", hint=_LOFT_HINT)
    me.hulls["yoke"] = yoke_hull
    if shell:
        yoke = trust(_as_shape(yoke_hull - yoke_loft(W)).solids()[0], "humanoid yoke shell")
        neck_hole = _placed(bd.Solid.make_cylinder(46 * k, 200 * k), (0, 4 * k, z_sh + 10 * k))
        yoke = _as_shape(yoke - neck_hole)
        chest, back = panel_split(yoke, bd.Plane(origin=(0, 18 * k, 0), z_dir=(0, -1, 0)),
                                  gap=1.2)
        # a sternum gap: the chest reads as two pec plates
        pec_l, pec_r = panel_split(chest, bd.Plane.YZ, gap=1.2)
        me.add("chest_left", pec_l, "shell")
        me.add("chest_right", pec_r, "shell")
        me.add("back_plate", back, "shell")
    else:
        me.add("yoke", yoke_hull, "shell")
    # ---- a graphite segmented abdomen (three bands, real gaps) tucked under the chest: the
    # light chest over a dark core over a light pelvis is the torso's V read; waist; pelvis
    zw = z_waist / k
    abd_st = [S(zw - 60, 100, 76, 2.4, 0, -2), S(zw, 108, 80, 2.5, 0, -6),
              S(zw + 95, 120, 90, 2.6, 0, -8)]
    abdomen = body(abd_st)
    if shell:
        upper, rest = panel_split(abdomen, bd.Plane.XY.offset(z_waist + 50 * k), gap=1.5)
        middle, lower = panel_split(rest, bd.Plane.XY.offset(z_waist - 5 * k), gap=1.5)
        for name, band in (("abdomen_upper", upper), ("abdomen_middle", middle),
                           ("abdomen_lower", lower)):
            me.add(name, band, "joint")
    else:
        me.add("abdomen", abdomen, "joint")
    me.add("waist", station_loft([S(zw - 132, 92, 70, 2.3), S(zw - 90, 88, 68, 2.3),
                                  S(zw - 40, 96, 74, 2.3)], seam_deg=100.0), "joint")
    zh = z_hip / k
    pel_st = [S(zh - 72, 60, 66, 2.2, 0, 4), S(zh - 30, 112, 82, 2.5), S(zh + 20, 132, 88, 2.7),
              S(zh + 70, 122, 84, 2.6), S(zh + 112, 100, 76, 2.4)]
    me.hulls["pelvis"] = hull(pel_st)
    me.add("pelvis", body(pel_st, (False, True)), "shell")

    J = {"elbow": (34, 60, 120, 15), "knee": (42, 70, 130, 45), "ankle": (26, 50, 120, 0)}
    ro = {n: (1.12 * r * k + W) / k for n, (r, *_rest) in J.items()}

    def socket(shape, at, name):
        rad = (ro[name] + 2.0) * k
        cyl = _placed(bd.Solid.make_cylinder(rad, 600 * k, bd.Plane.YZ.offset(-300 * k)), at)
        return max(_as_shape(_as_shape(shape) - cyl).solids(), key=lambda s: s.volume)

    def clam(name, shape, y0):
        """Front / back halves with a 0.8 mm seam on the side silhouette."""
        if not shell:
            me.add(name, shape, "shell")
            return
        front, back = panel_split(shape, bd.Plane(origin=(0, y0, 0), z_dir=(0, -1, 0)), gap=0.8)
        me.add(f"{name}_front", front, "shell")
        me.add(f"{name}_back", back, "shell")

    girths = {}
    for side, sx in (("left", 1.0), ("right", -1.0)):
        # ---- legs: hip (inside the pelvis) -> knee -> ankle
        hip = (sx * hw, 0.0, z_hip)
        knee = (sx * hw * 0.92, -4 * k, z_knee)
        ankle = (sx * hw * 0.92, 2 * k, z_ank)
        Lt, Ls = (z_hip - z_knee) / k, (z_knee - z_ank) / k
        lean = -sx * (hw - hw * 0.92) / k
        thigh_st = [S(40, 64, 72, 2.3, 0, -2), S(-0.16 * Lt, 82, 86, 2.4, 0.16 * lean, -10),
                    S(-0.42 * Lt, 76, 80, 2.5, 0.42 * lean, -9),
                    S(-0.72 * Lt, 60, 62, 2.4, 0.72 * lean, -4),
                    S(-Lt + 0.5 * ro["knee"], 50, 54, 2.3, lean, -3)]
        shin_st = [S(0.45 * ro["knee"], 50, 54, 2.3), S(-0.18 * Ls, 50, 62, 2.4, 0, 10),
                   S(-0.34 * Ls, 48, 64, 2.4, 0, 14), S(-0.62 * Ls, 38, 46, 2.3, 0, 6),
                   S(-Ls + 0.55 * ro["ankle"], 31, 35, 2.2)]
        if side == "left":
            girths["thigh"] = round(max(_girth(a, b) for _z, a, b, *_r in thigh_st), 1)
            girths["calf"] = round(max(_girth(a, b) for _z, a, b, *_r in shin_st), 1)
        thigh = _placed(body(thigh_st), hip)
        me.hulls[f"thigh_{side}"] = _placed(hull(thigh_st), hip)
        cut = bd.Plane(origin=(hip[0], 0, hip[2] + 10 * k), z_dir=(-sx * 0.55, 0, 1))
        thigh = _as_shape(bd.split(thigh, bisect_by=cut, keep=bd.Keep.BOTTOM)).solids()[0]
        clam(f"thigh_{side}", socket(thigh, knee, "knee") if shell else thigh, -6 * k)
        shin = _placed(body(shin_st), knee)
        me.hulls[f"shin_{side}"] = _placed(hull(shin_st), knee)
        if shell:
            shin = socket(socket(shin, knee, "knee"), ankle, "ankle")
        me.add(f"shin_{side}", shin, "shell")
        # ---- foot: heel, arch, ball and toe; a toe cap behind a real gap and a dark sole
        foot_st = [S(-80, 34, 26, 2.4, 0, 26), S(-56, 42, 40, 2.8, 0, 40), S(0, 46, 46, 2.9, 0, 46),
                   S(90, 50, 32, 3.0, 0, 32), S(150, 48, 22, 2.8, 0, 22), S(186, 40, 15, 2.5, 0, 15),
                   S(200, 26, 9, 2.2, 0, 9)]
        foot = _placed(body(foot_st, (False, False)), (ankle[0], ankle[1], 0), (90, 0, 0))
        if shell:
            foot = socket(foot, ankle, "ankle")
            upper, sole = panel_split(foot, bd.Plane.XY.offset(12 * k), gap=1.0)
            toe, upper = panel_split(upper, bd.Plane(origin=(0, ankle[1] - 132 * k, 0),
                                                     z_dir=(0, -1, 0)), gap=1.0)
            me.add(f"foot_{side}", upper, "shell")
            me.add(f"toe_{side}", toe, "shell")
            me.add(f"sole_{side}", sole, "joint")
        else:
            me.add(f"foot_{side}", foot, "shell")
        # ---- arms: a slight A-pose; shoulder (inside the yoke) -> elbow -> wrist
        tilt = sx * 6.0
        s6 = math.sin(math.radians(6.0)) * sx
        shoulder = (sx * sw, 0.0, z_sh)
        La, Lf = (z_sh - z_elb) / k, (z_elb - z_wri) / k
        elbow = (shoulder[0] + s6 * La * k, 0.0, z_elb)
        wrist = (elbow[0] + s6 * Lf * k, 0.0, z_wri)
        up_st = [S(12, 52, 54, 2.3), S(-0.28 * La, 56, 60, 2.4, 0, -5),
                 S(-0.6 * La, 50, 52, 2.4, 0, -2), S(-La + 0.5 * ro["elbow"], 42, 44, 2.3)]
        fo_st = [S(0.45 * ro["elbow"], 44, 46, 2.3), S(-0.2 * Lf, 47, 49, 2.4, 0, -2),
                 S(-0.6 * Lf, 38, 40, 2.3), S(-Lf + 14, 30, 32, 2.2)]
        if side == "left":
            girths["upper_arm"] = round(max(_girth(a, b) for _z, a, b, *_r in up_st), 1)
            girths["forearm"] = round(max(_girth(a, b) for _z, a, b, *_r in fo_st), 1)
        upper = _placed(body(up_st), shoulder, (0, tilt, 0))
        me.hulls[f"upper_arm_{side}"] = _placed(hull(up_st), shoulder, (0, tilt, 0))
        me.add(f"upper_arm_{side}", socket(upper, elbow, "elbow") if shell else upper, "shell")
        fore = _placed(body(fo_st), elbow, (0, tilt, 0))
        me.hulls[f"forearm_{side}"] = _placed(hull(fo_st), elbow, (0, tilt, 0))
        me.add(f"forearm_{side}", socket(fore, elbow, "elbow") if shell else fore, "shell")
        # ---- hand: palm, four slightly curled fingers and a thumb (palm faces the body)
        palm = station_loft([S(30, 27, 34, 2.3), S(-20, 22, 44, 2.6), S(-82, 18, 45, 2.8),
                             S(-92, 16, 42, 2.6)], seam_deg=100.0)
        bits = [palm]
        for y, ln in ((-33, 74), (-11, 82), (11, 78), (33, 64)):
            bits.append(tapered_capsule(9.5 * k, 7.5 * k, start=(0, y * k, -84 * k),
                                        end=(-sx * 14 * k, y * 0.9 * k, (-84 - ln) * k)))
        bits.append(tapered_capsule(11 * k, 9 * k, start=(-sx * 8 * k, -36 * k, -18 * k),
                                    end=(-sx * 20 * k, -56 * k, -76 * k)))
        hand = _as_shape(bits[0].fuse(*bits[1:]))
        me.add(f"hand_{side}", _placed(hand, wrist, (0, tilt, 0)), "joint")
        me.add(f"wrist_{side}", _placed(station_loft([S(30, 30, 33, 2.3), S(-6, 27, 31, 2.3)],
                                                     seam_deg=100.0), wrist, (0, tilt, 0)),
               "joint")
        me.hardpoints[f"wrist_{side}"] = wrist
        # ---- joints: shoulders inside the yoke, hips inside the pelvis (envelopes only);
        # elbow, knee and ankle cowls nested in the socketed shell ends
        _joint(me, f"shoulder_{side}", shoulder, 50 * k, 64 * k, 100, 0, shell=shell, wall=W)
        _joint(me, f"hip_{side}", hip, 48 * k, 70 * k, 0, 0, shell=False, wall=W)
        for jn, at in (("elbow", elbow), ("knee", knee), ("ankle", ankle)):
            r, w, rom, rx = J[jn]
            _joint(me, f"{jn}_{side}", at, r * k, w * k, rom, rx, shell=shell, wall=W)
    me.units.update({f"{n}_girth_mm": round(g * k, 1) for n, g in girths.items()})
    me.hardpoints.update({"head": (0, 0, hz + 0.5 * hh), "waist": (0, 0, z_waist),
                          "eye_line": (0, -80 * k, hz + 0.56 * hh)})
    return me


def _along_x(stations, *, shell: bool, wall: float, open_ends=(False, False),
             seam_deg: float = 100.0):
    """A body lofted along +X: stations (x, half_height, half_width, n, z_centre). Built as a
    station loft along Z, turned so local Z -> +X (local X -> -Z, hence dx = -z_centre)."""
    rows = [(x, a, b, n, -zc, 0.0) for x, a, b, n, zc in stations]
    body = (skin_shell(rows, wall, open_ends, seam_deg) if shell
            else station_loft(rows, seam_deg=seam_deg))
    return _placed(body, (0, 0, 0), (0, 90, 0))


def quadruped(body_length: float = 700.0, leg_ratio: float = 0.6, shell: bool = True,
              wall: float = 2.5):
    """Covered legged-robot starter: a massed body, sensor head, thigh shells over actuators.

    Body along X (head at +X), Z up, feet on z=0. `body_length` sets the scale; each leg's
    thigh and calf are `leg_ratio` x body length together (about 2:1 body:leg reads planted
    and low). The body is a soft-box loft with haunch and shoulder massing where the legs
    mount, split by a real panel gap into a light top shell and a dark belly pan; a separate
    sensor head carries a glossy face behind a panel gap. Each thigh is a shell whose domed top
    houses BOTH the hip and the knee actuator (coaxial on the hip axis, as in compact robot
    dogs; `.envelopes`), tapering to a recessed dark knee cowl; the calf tapers to a rubber
    boot. Knees point back. `.hulls` holds each thigh's solid outer loft. `shell=False` returns
    solid massing. Returns an Archetype."""
    L = float(body_length)
    k = L / 700.0
    leg = leg_ratio * L / 2                     # thigh, and calf to the ground contact
    th = math.radians(35.0)
    zc = 2 * leg * math.cos(th)                 # hip axis height (the foot contact on z=0)
    hx = L / 2 - 95 * k
    me = Archetype("quadruped", {"body_length": L, "leg_length": round(2 * leg, 1),
                                 "body_to_leg": round(L / (2 * leg), 2),
                                 "stance_height": round(zc, 1)})

    def S(x, a, b, n, dz=0.0):
        return (x * k - L / 2, a * k, b * k, n, zc + 30 * k + dz * k)

    body_st = [S(0, 62, 90, 2.6, 6), S(28, 88, 116, 2.9, 4), S(95, 98, 126, 3.1, 2),
               S(230, 86, 112, 3.0), S(470, 88, 114, 3.0), S(605, 100, 128, 3.1, 2),
               S(672, 90, 118, 2.9, 4), S(700, 64, 92, 2.6, 6)]
    body = _along_x(body_st, shell=shell, wall=wall)
    me.hulls["body"] = _along_x(body_st, shell=False, wall=wall)
    hy = 128 * k + 40 * k                       # hip plane, outboard of the body side
    # a flat, wide sensor head that drops slightly forward of the body
    head_st = [(L / 2 - 30 * k, 64 * k, 100 * k, 3.0, -(zc + 34 * k), 0.0),
               (L / 2 + 45 * k, 58 * k, 94 * k, 3.0, -(zc + 26 * k), 0.0),
               (L / 2 + 88 * k, 44 * k, 82 * k, 2.8, -(zc + 16 * k), 0.0),
               (L / 2 + 104 * k, 22 * k, 52 * k, 2.4, -(zc + 12 * k), 0.0)]
    head = _placed(station_loft(head_st, seam_deg=100.0), (0, 0, 0), (0, 90, 0))
    if shell:
        top, belly = panel_split(body, bd.Plane.XY.offset(zc - 12 * k), gap=1.0)
        me.add("body_top", top, "shell")
        me.add("belly_pan", belly, "joint")
        face, cap = panel_split(head, bd.Plane(origin=(L / 2 + 80 * k, 0, zc + 26 * k),
                                               z_dir=(1, 0, -0.75)), gap=1.0)
        me.add("sensor_face", face, "accent")
        me.add("head", cap, "shell")
    else:
        me.add("body", body, "shell")
        me.add("head", head, "shell")
    r_hip, w_hip, r_kn, w_kn = 38 * k, 40 * k, 34 * k, 34 * k
    r_cw = 22 * k                               # knee joint cowl (the linkage pivot)
    ro_k = 1.12 * r_cw + wall

    def socket(shape, at):
        cyl = _placed(bd.Solid.make_cylinder(ro_k + 2 * k, 400 * k,
                                             bd.Plane.XZ.offset(-200 * k)), at)
        return max(_as_shape(_as_shape(shape) - cyl).solids(), key=lambda s: s.volume)

    deg = math.degrees(th)
    for name, sx, sy in (("front_left", 1, 1), ("front_right", 1, -1), ("rear_left", -1, 1),
                         ("rear_right", -1, -1)):
        hip = (sx * hx, sy * hy, zc)
        knee = (hip[0] - leg * math.sin(th), hip[1], zc - leg * math.cos(th))
        foot = (knee[0] + leg * math.sin(th), hip[1], 0.0)
        # thigh: a dome over the coaxial hip and knee actuators, tapering to the knee
        thigh_st = [(58 * k, 18 * k, 34 * k, 2.2, 0, 0), (44 * k, 44 * k, 50 * k, 2.6, 0, 0),
                    (0, 52 * k, 52 * k, 2.8, 0, 0), (-0.45 * leg, 36 * k, 38 * k, 2.6, 4 * k, 0),
                    (-leg + 0.5 * ro_k, 25 * k, 30 * k, 2.4, 0, 0)]
        hull = _placed(station_loft(thigh_st, seam_deg=100.0), hip, (0, deg, 0))
        me.hulls[f"thigh_{name}"] = hull
        thigh = (_placed(skin_shell(thigh_st, wall, (False, True), 100.0), hip, (0, deg, 0))
                 if shell else hull)
        calf_st = [(0.45 * ro_k, 25 * k, 27 * k, 2.4, 0, 0), (-0.3 * leg, 22 * k, 22 * k, 2.4, 3 * k, 0),
                   (-leg + 52 * k, 15 * k, 15 * k, 2.2, 0, 0)]
        me.hulls[f"calf_{name}"] = _placed(station_loft(calf_st, seam_deg=100.0), knee,
                                           (0, -deg, 0))
        calf = _placed(skin_shell(calf_st, wall, (True, True), 100.0) if shell
                       else station_loft(calf_st, seam_deg=100.0), knee, (0, -deg, 0))
        if shell:
            thigh, calf = socket(thigh, knee), socket(calf, knee)
        me.add(f"thigh_{name}", thigh, "shell")
        me.add(f"calf_{name}", calf, "shell")
        # rubber boot: a short egg on the calf axis whose lowest point is the ground contact
        boot = station_loft([(-leg + 60 * k, 16 * k, 16 * k, 2.2, 0, 0),
                             (-leg + 26 * k, 25 * k, 23 * k, 2.2, 0, 0),
                             (-leg + 8 * k, 20 * k, 19 * k, 2.2, 0, 0),
                             (-leg + 1 * k, 9 * k, 9 * k, 2.0, 0, 0)], seam_deg=100.0)
        me.add(f"foot_{name}", _placed(boot, knee, (0, -deg, 0)), "joint")
        hp, kp = f"hip_{name}", f"knee_{name}"
        me.hardpoints.update({hp: hip, kp: knee, f"foot_{name}": foot})
        me.axes[hp] = (hip, (0.0, 1.0, 0.0))
        me.axes[kp] = (knee, (0.0, 1.0, 0.0))
        # the hip actuator inboard, the knee actuator outboard, both on the hip axis inside the
        # thigh dome (the knee is driven by a linkage down the thigh)
        for jn, r, w, off in (("hip", r_hip, w_hip, -22 * k), ("knee_drive", r_kn, w_kn, 20 * k)):
            me.envelopes[f"{jn}_{name}"] = _placed(bd.Solid.make_cylinder(
                r, w, bd.Plane.XZ.offset(-w / 2)), (hip[0], hip[1] + sy * off, hip[2]))
        if shell:
            cowl = joint_cowl(r_cw, 40 * k, 0.04 * r_cw, wall, 110, bulge=0.08 * r_cw, bore=0)
            me.add(f"knee_{name}_cowl", _placed(cowl, knee, (-20, 0, 90)), "joint")
    # stand on z = 0: the boots' rims, tilted with the calf, reach a little below their axis
    dz = -min(me.parts[f"foot_{n}"].bounding_box().min.Z
              for n in ("front_left", "front_right", "rear_left", "rear_right"))
    me.lift(dz)
    me.units["stance_height"] = round(zc + dz, 1)
    for n in ("front_left", "front_right", "rear_left", "rear_right"):
        me.hardpoints[f"foot_{n}"] = (*me.hardpoints[f"foot_{n}"][:2], 0.0)
    return me


# ---- car body: one loft whose sections carry the cabin (glass is cut from it)

def _ray_exit(o, d, c, a, b_up, b_dn, n_up, n_dn) -> float:
    """Distance from `o` along unit `d` (2D, y-z) to the boundary of an up/down-asymmetric
    superellipse centred at `c` (half-width a; half-heights b_up above, b_dn below)."""
    def inside(s):
        y, z = o[0] + s * d[0] - c[0], o[1] + s * d[1] - c[1]
        b, n = (b_up, n_up) if z >= 0 else (b_dn, n_dn)
        return abs(y / a) ** n + abs(z / b) ** n < 1.0
    if not inside(0.0):
        return 0.0
    lo, hi = 0.0, 1.0
    while inside(hi):
        hi *= 2.0
    for _ in range(48):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if inside(mid) else (lo, mid)
    return lo


def _car_section(x: float, st: dict, inset: float = 0.0, k: int = 96):
    """A closed spline in the plane x = const: the lower body (an asymmetric superellipse
    with a shoulder at `zc`) united with the cabin (a superellipse sitting on the beltline);
    the beltline is the crease where they meet. The outline is traced densely by ray casting,
    then resampled at equal arc-length fractions from the bottom centre, so point i of every
    section sits at the same fraction of its outline and the loft does not jump between body
    and cabin from one station to the next. `inset` > 0 offsets those same points inward along
    their normals (the cavity's section corresponds point for point: an even wall)."""
    w, bot, top, zc = st["w"], st["bot"], st["top"], st["zc"]
    shapes = [((0.0, zc), w, top - zc, zc - bot, 2.6, 3.4)]
    if st.get("roof", 0) > st["belt"] + 1:
        cz = st["belt"] - 40
        shapes.append(((0.0, cz), st["wc"], st["roof"] - cz, 40.0, 2.8, 2.0))
    o = (0.0, min(top, st["belt"]) - 60)
    m = 6 * k
    dense = []
    for i in range(m + 1):
        t = -math.pi / 2 + 2 * math.pi * i / m
        d = (math.cos(t), math.sin(t))
        # a soft maximum (p-norm) of the two outlines: the beltline crease gets a small
        # radius instead of a corner, which a fitted spline would overshoot
        r = sum(_ray_exit(o, d, *s) ** 12 for s in shapes) ** (1 / 12)
        dense.append((o[0] + r * d[0], o[1] + r * d[1]))
    acc = [0.0]
    for (ya, za), (yb, zb) in zip(dense, dense[1:]):
        acc.append(acc[-1] + math.hypot(yb - ya, zb - za))
    total, pts, j = acc[-1], [], 0
    for i in range(k):
        s_ = total * i / k
        while acc[j + 1] < s_:
            j += 1
        f = (s_ - acc[j]) / ((acc[j + 1] - acc[j]) or 1.0)
        (ya, za), (yb, zb) = dense[j], dense[j + 1]
        pts.append((ya + f * (yb - ya), za + f * (zb - za)))
    if inset > 0:
        # counter-clockwise outline: the outward normal is the tangent turned clockwise
        off = []
        for i, (y, z) in enumerate(pts):
            (ya, za), (yb, zb) = pts[i - 1], pts[(i + 1) % k]
            ty, tz = yb - ya, zb - za
            ln = math.hypot(ty, tz) or 1.0
            off.append((y - inset * tz / ln, z + inset * ty / ln))
        pts = off
    # a periodic interpolating spline with the SAME uniform parameters on every section: point
    # i is at parameter i/k everywhere, so the loft pairs corresponding points (a fitted
    # spline gets its own knots per section and the loft twists and pinches the cabin)
    vs = [bd.Vector(x, y, z) for y, z in pts]
    return bd.Wire([bd.Edge.make_spline(vs, periodic=True, parameters=_periodic_params(k))])


def car_body(wheelbase: float = 2700.0, wheel_d: float = 680.0, length: float | None = None,
             width: float = 1850.0, height: float = 1420.0, greenhouse: float = 0.36,
             shell: bool = True, wall: float = 3.0):
    """Car body starter: one shelled body loft carrying the cabin, glass cut from it, arches.

    Faces +X, wheels on z=0, centred on the wheelbase. Proportions in wheel units: `wheelbase`
    about 3.5-4 `wheel_d` (longer reads premium); `length` defaults to the wheelbase plus 2
    wheel diameters of overhang (rear longer); `greenhouse` is the glass share of the height
    above the beltline (about 1/3 reads sporty). Every section of the body loft is the lower
    body (a shoulder line where it is widest, tumbling in to the beltline) united with the
    cabin, so hood, greenhouse and tail are one surface with a beltline crease. The shell is
    the loft minus its inset loft (closed). The glass band (windshield, side and rear windows)
    is cut from the same shell with a real gap, leaving a B pillar and a body-colour roof; the
    light bands are cut from nose and tail the same way. Wheel wells are per side, about 1.2x
    the tyre radius, with each tyre inboard of the body side. Door, hood and trunk shut lines
    are left to the build (cut the body into panels with ms.panel_split; a narrow slot through
    the one-face body skin does not mesh). Returns an Archetype (`.hulls["body"]` is the solid
    outer loft)."""
    WB, R = float(wheelbase), wheel_d / 2
    Lc = length if length is not None else WB + 4.0 * R
    ov_r = 0.55 * (Lc - WB)
    x0, x1 = -WB / 2 - ov_r, -WB / 2 - ov_r + Lc
    H, hw = float(height), width / 2
    belt = H * (1 - greenhouse)
    gt = H - belt
    gc = 0.4 * R                                          # ground clearance
    tw = 0.34 * R                                         # tyre half-width
    arch_r = 1.2 * R
    me = Archetype("car", {"wheelbase": WB, "wheel_d": wheel_d,
                           "wheelbase_in_wheels": round(WB / wheel_d, 2), "length": round(Lc),
                           "greenhouse_share": greenhouse, "beltline_z": round(belt),
                           "arch_to_tyre": round(arch_r / R, 2)})

    def X(f):
        return x0 + f * (x1 - x0)

    # (f, top, bot, half width, roof, cabin half width); the shoulder sits at 72% of the belt
    rows = [(0.000, 0.82 * belt, gc + 170, 0.80 * hw, 0, 0),
            (0.025, 0.96 * belt, gc + 70, 0.95 * hw, 0, 0),
            (0.10, belt, gc + 10, hw, 0, 0),
            (0.17, belt, gc, hw, belt + 4, 0.68 * hw),
            (0.25, belt, gc, hw, belt + 0.62 * gt, 0.75 * hw),
            (0.34, belt, gc, hw, 0.995 * H, 0.80 * hw),
            (0.40, belt, gc, hw, H, 0.805 * hw),     # the roof stations keep the crown flat:
            (0.46, belt, gc, hw, H, 0.81 * hw),      # a cubic through two equal peaks between
            (0.53, belt, gc, hw, 0.995 * H, 0.81 * hw),  # steep neighbours sags in the middle
            (0.62, 0.99 * belt, gc, hw, belt + 0.80 * gt, 0.80 * hw),
            (0.72, 0.95 * belt, gc, hw, belt + 0.25 * gt, 0.76 * hw),
            (0.76, 0.95 * belt, gc, 0.995 * hw, belt + 2, 0.70 * hw),
            (0.88, 0.90 * belt, gc + 10, 0.98 * hw, 0, 0),   # the hood stays well above
            (0.97, 0.76 * belt, gc + 50, 0.92 * hw, 0, 0),   # the front arch
            (1.000, 0.60 * belt, gc + 150, 0.78 * hw, 0, 0)]
    sts = [dict(x=X(f), top=top, bot=bot, w=w, zc=bot + 0.72 * (min(top, belt) - bot),
                belt=min(belt, top), roof=roof, wc=wc) for f, top, bot, w, roof, wc in rows]
    outer = check_solid(bd.Solid.make_loft([_car_section(s["x"], s) for s in sts]), "car body",
                        hint=_LOFT_HINT)
    me.hulls["body"] = outer
    if not shell:                                         # massing: body and tyres only
        me.add("body", outer, "shell")
        for name, ax, sy in (("front_left", WB / 2, 1), ("front_right", WB / 2, -1),
                             ("rear_left", -WB / 2, 1), ("rear_right", -WB / 2, -1)):
            me.add(f"wheel_{name}", _placed(bd.Solid.make_cylinder(
                R, 2 * tw, bd.Plane.XZ.offset(-tw)), (ax, sy * (hw - 30 - tw), R)), "joint")
        return me
    inner_sts = [dict(s) for s in sts]
    inner_sts[0]["x"] += wall
    inner_sts[-1]["x"] -= wall
    inner = bd.Solid.make_loft([_car_section(s["x"], s, inset=wall) for s in inner_sts])
    body = _as_shape(outer - inner)

    # wheel wells: a cylinder per side cut into the side, open to the ground
    # ... with a dark liner (the well's tube, wall thick, inside the body) so the arch shows a
    # wheel house, not the inside of the skin
    depth = 2 * tw + 140
    for ax in (-WB / 2, WB / 2):
        for sy in (1, -1):
            at = (ax, sy * (hw + 60) - sy * depth / 2, R)
            well = _placed(bd.Solid.make_cylinder(arch_r, depth, bd.Plane.XZ.offset(-depth / 2)),
                           at)
            body = _as_shape(body - well)
            tube = _placed(bd.Solid.make_cylinder(arch_r + wall, depth - 40,
                                                  bd.Plane.XZ.offset(-(depth - 40) / 2))
                           - bd.Solid.make_cylinder(arch_r, depth, bd.Plane.XZ.offset(-depth / 2)),
                           (ax, sy * (hw + 40) - sy * depth / 2, R))
            liner = _as_shape(tube & outer)
            if liner.solids():
                me.add(f"wheel_house_{'front' if ax > 0 else 'rear'}_"
                       f"{'left' if sy > 0 else 'right'}",
                       max(liner.solids(), key=lambda s_: s_.volume), "joint")
    # Panels by plane splits (robust half-space cuts, each a real gap): the lower body up to
    # the beltline; above it the greenhouse band (windshield, side and rear glass) between the
    # beltline and the roof line, with a B pillar; the roof above; light bands sliced off the
    # nose and tail caps.
    g = 3.0

    def cut(shape, plane):
        """(+normal side, -normal side) with a g-wide gap, or (None, shape) when nothing is
        on the + side."""
        try:
            return panel_split(shape, plane, gap=g)
        except ValueError:
            return None, shape

    zplane = bd.Plane.XY
    upper, lower = cut(body, zplane.offset(belt + 18))
    roof, band = cut(upper, zplane.offset(H - 0.18 * gt)) if upper is not None else (None, None)
    xbp = X(0.47)
    pillars, glass = [], []
    if band is not None:
        # a deck or hood crown that reaches above the glass line is body, not glass: the glass
        # runs only between the rear window base and the windshield base
        hood_crown, band = cut(band, bd.Plane.YZ.offset(X(0.775)))
        band, deck_crown = cut(band, bd.Plane.YZ.offset(X(0.165)))
        for name, crown in (("hood_crown", hood_crown), ("deck_crown", deck_crown)):
            if crown is not None and crown.solids():
                me.add(name, crown, "shell")
    if band is not None:
        front, rest = cut(band, bd.Plane.YZ.offset(xbp + 45))
        pillar, rear = cut(rest, bd.Plane.YZ.offset(xbp - 45))
        glass = [p for p in (front, rear) if p is not None]
        pillars = [pillar] if pillar is not None else []
    def end_cap(shape, x, front: bool):
        """(cap, rest) cut at x, or (None, shape) when either piece would not be valid."""
        try:
            a_, b_ = cut(shape, bd.Plane.YZ.offset(x))
            cap_, rest_ = (a_, b_) if front else (b_, a_)
            if cap_ is None or rest_ is None:
                return None, shape
            check_solid(cap_, "end cap")
            check_solid(rest_, "body")
            return cap_, rest_
        except ValueError:
            return None, shape
    nose, lower = end_cap(lower, X(0.975), True)
    tail, lower = end_cap(lower, X(0.03), False)
    for name, cap, za, zb in (("headlights", nose, 0.47 * belt, 0.54 * belt),
                              ("taillights", tail, 0.62 * belt, 0.70 * belt)):
        if cap is None:
            continue
        stem = name.replace("lights", "")
        try:
            top_, rest_ = cut(cap, zplane.offset(zb))
            light, bot_ = cut(rest_, zplane.offset(za))
            pieces = [(f"{stem}_cap_upper", top_, "shell"), (f"{stem}_cap_lower", bot_, "shell"),
                      (name, light, "accent")]
            for _n, piece, _r in pieces:
                if piece is not None:
                    check_solid(piece, _n)
        except ValueError:              # a slice too thin to be valid: keep the cap whole
            pieces = [(f"{stem}_cap", cap, "shell")]
        for n_, piece, role in pieces:
            if piece is not None:
                me.add(n_, piece, role)
    me.add("body", trust(lower if not lower.solids() or len(lower.solids()) > 1
                         else lower.solids()[0], "car body shell"), "shell")
    if roof is not None:
        me.add("roof", roof, "shell")
    for i, pl in enumerate(pillars):
        me.add("b_pillars" if i == 0 else f"b_pillars_{i}", pl, "shell")
    for name, gl in zip(("glass_front", "glass_rear"), glass):
        me.add(name, gl, "accent")
    for name, ax, sy in (("front_left", WB / 2, 1), ("front_right", WB / 2, -1),
                         ("rear_left", -WB / 2, 1), ("rear_right", -WB / 2, -1)):
        at = (ax, sy * (hw - 30 - tw), R)                 # the outer face 30 mm inboard
        tyre = blend_edges(bd.Solid.make_cylinder(R, 2 * tw, bd.Plane.XZ.offset(-tw)), 0.3 * tw)
        rim = bd.Solid.make_cylinder(0.66 * R, 2 * tw + 4, bd.Plane.XZ.offset(-tw - 2))
        me.add(f"wheel_{name}", _placed(tyre - rim, at), "joint")
        me.add(f"rim_{name}", _placed(rim - bd.Solid.make_cylinder(
            0.66 * R, 2 * tw - 30, bd.Plane.XZ.offset(-tw + 15)), at), "accent")
        me.axes[f"wheel_{name}"] = (at, (0.0, 1.0, 0.0))
        me.hardpoints[f"wheel_{name}"] = at
        me.envelopes[f"wheel_{name}"] = _placed(bd.Solid.make_cylinder(
            R, 2 * tw, bd.Plane.XZ.offset(-tw)), at)
    me.hardpoints["beltline"] = (0, 0, belt)
    return me


