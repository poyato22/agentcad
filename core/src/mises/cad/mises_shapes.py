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
    """Volume of a tessellation (0.05 mm, coarser for big parts: 1e-4 of the diagonal)."""
    import numpy as np   # a build123d dependency, so always present in the worker

    shape = _as_shape(shape)
    if tol is None:
        tol = max(0.05, 1e-4 * shape.bounding_box().diagonal)
    verts, tris = shape.tessellate(tol, 0.1)
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
        part = _as_shape(bd.split(shape, bisect_by=plane.offset(off), keep=keep))
        solids = part.solids() if part is not None else []
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

    def add(self, name: str, shape, role: str = "shell"):
        self.parts[name] = check_solid(_as_shape(shape), f"{self.kind} {name}")
        self.roles[name] = role
        return self.parts[name]

    def part(self, name: str):
        return self.parts[name]

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


def humanoid(height: float = 1700.0, heads: float = 8.0, shoulder_heads: float = 1.95,
             hip_heads: float = 0.87, shell: bool = True, wall: float = 3.0):
    """Sleek covered humanoid starter (Optimus / NEO class): shells, cowls, skeleton, 8 heads.

    Stands on z=0 facing -Y (Z up, +X = its left side). `height` and `heads` (head heights per
    body; 7.5-8 reads heroic) set the unit; `shoulder_heads` / `hip_heads` are the shoulder and
    hip joint spans in heads. Every visible part is a cover: a helmet with a glossy face (a panel
    gap between them), a V-tapered lofted chest with a panel gap, pelvis shell, tapered thigh /
    shin / arm shells (skin_shell, open ends tucked into the cowls), closed revolved cowls at
    shoulders, elbows, knees and ankles, a dark waist and neck, mitten hands and shelled feet.
    No bars and no exposed actuators: the actuators are `.envelopes` on the joint axes in
    `.axes` (the hips sit inside the pelvis cover). `shell=False` returns solid massing for
    concept sketches. Returns an Archetype."""
    u = height / heads
    k = height / 1700.0                        # stations are drawn for a 1.7 m, 8-head body
    q = 8.0 / heads                            # head size relative to the 8-head canon
    sw = shoulder_heads * u / 2
    hw = hip_heads * u / 2
    z_sh = height - 1.5 * u                    # shoulder joint
    z_waist = height - 3.0 * u                 # navel
    z_hip = height - 3.95 * u
    z_knee = 0.285 * height
    z_ank = 0.05 * height
    z_elb = z_sh - 1.42 * u
    z_wri = z_elb - 1.18 * u
    me = Archetype("humanoid", {"height": height, "head_heights": heads,
                                "head_unit": round(u, 1),
                                "shoulder_to_hip_span": round(shoulder_heads / hip_heads, 2)})
    W = wall

    def S(z, a, b, n=2.4, dx=0.0, dy=0.0):
        return (z * k, a * k, b * k, n, dx * k, dy * k)

    def body(stations, open_ends=(True, True)):
        return (skin_shell(stations, W, open_ends, 100.0) if shell
                else station_loft(stations, seam_deg=100.0))

    # ---- head: a revolved helmet, split by a panel gap into a glossy face and a light shell
    hh = 212.0 * k * q                         # one head unit, crown to jaw
    hz = height - hh
    head = _revolved([(0, 0), (40, 4), (62, 30), (72, 85), (70, 150), (56, 195), (30, 214),
                      (0, 218)], scale=(k * q, 1.2 * k * q, hh / 218.0))
    head = _placed(head, (0, 8 * k, hz))
    face, helmet = panel_split(head, bd.Plane(origin=(0, -30 * k * q, hz + 0.5 * hh),
                                              z_dir=(0, -1, 0.25)), gap=1.5)
    me.add("head_face", face, "accent")
    me.add("head_shell", helmet, "shell")
    me.add("neck", tapered_capsule(36 * k, 30 * k, start=(0, 6 * k, z_sh + 30 * k),
                                   end=(0, 8 * k, hz + 0.2 * hh)), "joint")

    # ---- torso: a V-tapered chest shell (open at waist and neck) with a panel gap, broad
    # shoulders sloping into the neck (trapezius), and a short dark waist into the pelvis
    zw = z_waist
    chest = body([S(-30, 100, 78, 2.3), S(0, 104, 80, 2.3), S(80, 124, 88, 2.4, 0, -4),
                  S(170, 148, 100, 2.6, 0, -10), S(250, 160, 102, 2.8, 0, -8),
                  S(310, 160, 94, 3.0, 0, -2), S(350, 140, 80, 2.8, 0, 2),
                  S(378, 96, 62, 2.4, 0, 4), S(392, 66, 52, 2.2, 0, 5)])
    chest = _placed(chest, (0, 0, zw + 18 * k))
    if shell:
        upper, lower = panel_split(chest, bd.Plane.XY.offset(zw + 140 * k), gap=1.2)
        me.add("chest", upper, "shell")
        me.add("ribcage", lower, "shell")
    else:
        me.add("chest", chest, "shell")
    me.add("waist", _placed(station_loft([S(0, 94, 72, 2.3), S(80, 88, 68, 2.3),
                                          S(170, 98, 76, 2.3)], seam_deg=100.0),
                            (0, 0, zw - 120 * k)), "joint")
    me.add("pelvis", _placed(body([S(0, 62, 70, 2.2, 0, 4), S(40, 112, 84, 2.5),
                                   S(95, 128, 88, 2.7), S(150, 118, 84, 2.6),
                                   S(200, 104, 78, 2.4)], (False, True)),
                             (0, 0, z_hip - 75 * k)), "shell")

    # elbow, knee, ankle: actuator radius and width, link swing (deg) and the cowl's turn so
    # its open sector faces the lower link. ro = the cowl's outer radius (joint_cowl crown)
    J = {"elbow": (34, 60, 120, 15), "knee": (42, 70, 130, 45), "ankle": (26, 50, 120, 0)}
    ro = {n: (1.12 * r * k + W) / k for n, (r, *_rest) in J.items()}

    def socket(shape, at, name):
        """Shell ends are cut concave around the joint's cowl (2 mm running clearance), so the
        cowl nests in them through the swing instead of clashing: the honest joint read."""
        rad = (ro[name] + 2.0) * k
        cyl = _placed(bd.Solid.make_cylinder(rad, 600 * k, bd.Plane.YZ.offset(-300 * k)), at)
        return max(_as_shape(_as_shape(shape) - cyl).solids(), key=lambda s: s.volume)

    for side, sx in (("left", 1.0), ("right", -1.0)):
        # ---- legs: hip (inside the pelvis) -> knee -> ankle; a forward thigh belly, a calf
        hip = (sx * hw, 0.0, z_hip)
        knee = (sx * hw * 0.92, -4 * k, z_knee)
        ankle = (sx * hw * 0.92, 2 * k, z_ank)
        Lt, Ls = (z_hip - z_knee) / k, (z_knee - z_ank) / k
        lean = -sx * (hw - hw * 0.92) / k
        thigh = _placed(body([
            S(40, 66, 72, 2.3, 0, -2), S(-0.22 * Lt, 74, 78, 2.4, 0.22 * lean, -6),
            S(-0.6 * Lt, 62, 64, 2.4, 0.6 * lean, -4),
            S(-Lt + 0.5 * ro["knee"], 52, 55, 2.3, lean, -3)]), hip)
        # the hip line: the thigh shell's top is cut on a slope, high outside, low inside
        cut = bd.Plane(origin=(hip[0], 0, hip[2] + 10 * k), z_dir=(-sx * 0.55, 0, 1))
        thigh = _as_shape(bd.split(thigh, bisect_by=cut, keep=bd.Keep.BOTTOM)).solids()[0]
        me.add(f"thigh_{side}", socket(thigh, knee, "knee") if shell else thigh, "shell")
        shin = _placed(body([
            S(0.45 * ro["knee"], 52, 55, 2.3), S(-0.24 * Ls, 54, 62, 2.4, 0, 8),
            S(-0.63 * Ls, 42, 46, 2.3, 0, 3), S(-Ls + 0.55 * ro["ankle"], 33, 36, 2.2)]), knee)
        if shell:
            shin = socket(socket(shin, knee, "knee"), ankle, "ankle")
        me.add(f"shin_{side}", shin, "shell")
        foot = _placed(body([S(-72, 40, 32, 2.8, 0, 32), S(0, 46, 44, 2.8, 0, 44),
                             S(110, 50, 30, 3.0, 0, 30), S(186, 42, 15, 2.6, 0, 15)],
                            (False, False)), (ankle[0], ankle[1], 0), (90, 0, 0))
        me.add(f"foot_{side}", socket(foot, ankle, "ankle") if shell else foot, "shell")
        # ---- arms: a slight A-pose; shoulder -> elbow -> wrist
        tilt = sx * 6.0
        s6 = math.sin(math.radians(6.0)) * sx
        shoulder = (sx * sw, 0.0, z_sh)
        La, Lf = (z_sh - z_elb) / k, (z_elb - z_wri) / k
        elbow = (shoulder[0] + s6 * La * k, 0.0, z_elb)
        wrist = (elbow[0] + s6 * Lf * k, 0.0, z_wri)
        upper = _placed(body([S(12, 48, 50, 2.3), S(-0.3 * La, 50, 54, 2.4, 0, -3),
                              S(-La + 0.5 * ro["elbow"], 42, 44, 2.3)]), shoulder, (0, tilt, 0))
        me.add(f"upper_arm_{side}", socket(upper, elbow, "elbow") if shell else upper, "shell")
        fore = _placed(body([S(0.45 * ro["elbow"], 43, 45, 2.3), S(-0.25 * Lf, 45, 47, 2.4),
                             S(-Lf + 14, 30, 32, 2.2)]), elbow, (0, tilt, 0))
        me.add(f"forearm_{side}", socket(fore, elbow, "elbow") if shell else fore, "shell")
        hand = station_loft([S(34, 28, 34, 2.3), S(-20, 25, 42, 2.6), S(-100, 20, 44, 2.9),
                             S(-150, 14, 36, 2.6), S(-172, 8, 22, 2.2)], seam_deg=100.0)
        thumb = tapered_capsule(14 * k, 11 * k, start=(-sx * 6 * k, -30 * k, -24 * k),
                                end=(-sx * 12 * k, -44 * k, -78 * k))
        me.add(f"hand_{side}", _placed(_as_shape(hand + thumb), wrist, (0, tilt, 0)), "joint")
        # ---- shoulder: a light deltoid cap lofted outward from the chest over the actuator
        cap = station_loft([S(0, 74, 86, 2.4), S(45, 72, 72, 2.5), S(95, 62, 64, 2.4),
                            S(122, 44, 46, 2.2), S(136, 24, 26, 2.0), S(141, 8, 9, 2.0)],
                           seam_deg=100.0)
        x_in = shoulder[0] - sx * 62 * k
        me.add(f"shoulder_cap_{side}", _placed(cap, (x_in, 0, z_sh + 8 * k), (0, 90 * sx, 0)),
               "shell")
        _joint(me, f"shoulder_{side}", shoulder, 52 * k, 84 * k, 0, 0, shell=False, wall=W)
        # ---- elbow, knee and ankle: cowls the width of the limb, nested in the socketed
        # shell ends, open toward the lower link
        for jn, at in (("elbow", elbow), ("knee", knee), ("ankle", ankle)):
            r, w, rom, rx = J[jn]
            _joint(me, f"{jn}_{side}", at, r * k, w * k, rom, rx, shell=shell, wall=W)
        # wrist cuff: a short dark band between the forearm shell and the hand
        me.add(f"wrist_{side}", _placed(station_loft([S(30, 30, 33, 2.3), S(-6, 27, 31, 2.3)],
                                                     seam_deg=100.0), wrist, (0, tilt, 0)),
               "joint")
        _joint(me, f"hip_{side}", hip, 48 * k, 70 * k, 0, 0, shell=False, wall=W)
        me.hardpoints[f"wrist_{side}"] = wrist
    me.hardpoints.update({"head": (0, 0, hz + 110 * k), "neck": (0, 6 * k, z_sh + 40 * k),
                          "waist": (0, 0, z_waist), "eye_line": (0, -80 * k, hz + 120 * k)})
    return me


def _along_x(stations, *, shell: bool, wall: float, open_ends=(False, False),
             seam_deg: float = 100.0):
    """A body lofted along +X: stations (x, half_height, half_width, n, z_centre). Built as a
    station loft along Z, turned so local Z -> +X (local X -> -Z, hence dx = -z_centre)."""
    rows = [(x, a, b, n, -zc, 0.0) for x, a, b, n, zc in stations]
    body = (skin_shell(rows, wall, open_ends, seam_deg) if shell
            else station_loft(rows, seam_deg=seam_deg))
    return _placed(body, (0, 0, 0), (0, 90, 0))


def quadruped(body_length: float = 700.0, leg_ratio: float = 0.5, shell: bool = True,
              wall: float = 2.5):
    """Covered robot-dog starter (Spot / Go2 class): a clamshell body, shelled legs, cowls.

    Body along X (head at +X), Z up, feet on z=0. `body_length` sets the scale; each leg's
    upper and lower links are `leg_ratio` x body length together (about 2:1 body:leg reads
    planted and low). The body is a soft-box loft split by a real panel gap into a light top
    shell and a dark belly pan, with a glossy sensor face; legs are tapered shells with knees
    pointing back, dark revolved cowls at hips and knees nested in socketed shell ends, and
    rubber feet. Actuators are `.envelopes` on the joint axes (Y). `shell=False` returns solid
    massing. Returns an Archetype."""
    L = float(body_length)
    k = L / 700.0
    leg = leg_ratio * L / 2                     # each link
    th = math.radians(32.0)
    drop = 2 * leg * math.cos(th)
    foot_r = 26 * k
    zc = drop + foot_r                          # hip axis height
    hx, hy = L / 2 - 95 * k, 150 * k
    me = Archetype("quadruped", {"body_length": L, "leg_length": round(2 * leg, 1),
                                 "body_to_leg": round(L / (2 * leg), 2),
                                 "stance_height": round(zc, 1)})

    def S(x, a, b, n, z):
        return (x * k - L / 2, a * k, b * k, n, z)

    body = _along_x([S(0, 66, 96, 2.6, zc + 10 * k), S(60, 90, 124, 2.9, zc + 14 * k),
                     S(350, 100, 134, 3.0, zc + 18 * k), S(610, 94, 128, 2.9, zc + 18 * k),
                     S(700, 66, 100, 2.6, zc + 14 * k)], shell=shell, wall=wall)
    if shell:
        top, belly = panel_split(body, bd.Plane.XY.offset(zc - 10 * k), gap=1.0)
        nose, top = panel_split(top, bd.Plane(origin=(L / 2 - 60 * k, 0, zc),
                                              z_dir=(1, 0, 0.35)), gap=1.0)
        me.add("body_top", top, "shell")
        me.add("belly_pan", belly, "joint")
        me.add("sensor_face", nose, "accent")
    else:
        me.add("body", body, "shell")
    ro_k = (1.12 * 34 * k + wall)

    def socket(shape, at):
        cyl = _placed(bd.Solid.make_cylinder(ro_k + 2 * k, 400 * k,
                                             bd.Plane.XZ.offset(-200 * k)), at)
        return max(_as_shape(_as_shape(shape) - cyl).solids(), key=lambda s: s.volume)

    for name, sx, sy in (("front_left", 1, 1), ("front_right", 1, -1), ("rear_left", -1, 1),
                         ("rear_right", -1, -1)):
        hip = (sx * hx, sy * hy, zc)
        knee = (hip[0] - leg * math.sin(th), hip[1], zc - leg * math.cos(th))
        foot = (knee[0] + leg * math.sin(th), hip[1], foot_r)
        deg = math.degrees(th)
        upper = station_loft([(30 * k, 40 * k, 30 * k, 2.6, 0, 0),
                              (-0.4 * leg, 46 * k, 30 * k, 2.6, -6 * k, 0),
                              (-leg + 0.5 * ro_k, 32 * k, 26 * k, 2.4, 0, 0)], seam_deg=100.0)
        if shell:
            upper = skin_shell([(30 * k, 40 * k, 30 * k, 2.6, 0, 0),
                                (-0.4 * leg, 46 * k, 30 * k, 2.6, -6 * k, 0),
                                (-leg + 0.5 * ro_k, 32 * k, 26 * k, 2.4, 0, 0)], wall,
                               (True, True), 100.0)
        upper = _placed(upper, hip, (0, deg, 0))       # +deg about Y: the knee goes back
        lower_st = [(0.45 * ro_k, 30 * k, 25 * k, 2.4, 0, 0), (-0.45 * leg, 24 * k, 20 * k, 2.3, 0, 0),
                    (-leg + 1.2 * foot_r, 15 * k, 14 * k, 2.2, 0, 0)]
        lower = (skin_shell(lower_st, wall, (True, True), 100.0) if shell
                 else station_loft(lower_st, seam_deg=100.0))
        lower = _placed(lower, knee, (0, -deg, 0))     # and the foot comes forward under it
        if shell:
            upper, lower = socket(socket(upper, knee), hip), socket(lower, knee)
        me.add(f"upper_leg_{name}", upper, "shell")
        me.add(f"lower_leg_{name}", lower, "shell")
        me.add(f"foot_{name}", _placed(bd.Solid.make_sphere(foot_r), foot), "joint")
        for jn, at, r, w in (("hip", hip, 38 * k, 56 * k), ("knee", knee, 34 * k, 50 * k)):
            jname = f"{jn}_{name}"
            me.axes[jname] = (at, (0.0, 1.0, 0.0))
            me.hardpoints[jname] = at
            me.envelopes[jname] = _placed(bd.Solid.make_cylinder(r, w, bd.Plane.XZ.offset(-w / 2)),
                                          at)
            if shell:
                cowl = joint_cowl(r, w, 0.04 * r, wall, 120, bulge=0.08 * r, bore=0)
                me.add(f"{jname}_cowl", _placed(cowl, at, (90 if jn == "knee" else 0, 0, 90)),
                       "joint")
        me.hardpoints[f"foot_{name}"] = foot
    return me


def car_body(wheelbase: float = 2700.0, wheel_d: float = 680.0, length: float | None = None,
             width: float = 1850.0, height: float = 1420.0, greenhouse: float = 0.36,
             shell: bool = True, wall: float = 3.0):
    """Car body starter: lower body, greenhouse with a roof panel, arches, wheels, beltline.

    Faces +X, wheels on z=0, centred on the wheelbase. Proportions in units: `wheelbase` about
    3.5-4 `wheel_d` (longer reads premium), overall `length` (default wheelbase + 1.65 wheel
    diameters of overhang, the rear a little longer), `width`, `height`, and `greenhouse` the
    glass-to-total height share above the beltline (about 1/3 reads sporty). The beltline is
    the panel gap between body and greenhouse; the roof is split from the glass by another; the
    doors are panel gaps. CMF: body shell, glass and tyres as dark/accent. `shell=False` returns
    solid massing. Returns an Archetype."""
    WB, R = float(wheelbase), wheel_d / 2
    Lc = length if length is not None else WB + 3.3 * R
    ov_f = 0.46 * (Lc - WB)
    x0, x1 = -WB / 2 - (Lc - WB - ov_f), WB / 2 + ov_f
    belt = height * (1 - greenhouse)
    hw = width / 2
    gc = 0.26 * R                                # ground clearance
    me = Archetype("car", {"wheelbase": WB, "wheel_d": wheel_d,
                           "wheelbase_in_wheels": round(WB / wheel_d, 2), "length": round(Lc),
                           "greenhouse_share": greenhouse, "beltline_z": round(belt)})

    def low(f, top, half_w, n=3.0, bot=gc):
        x = x0 + f * (x1 - x0)
        return (x, (top - bot) / 2, half_w, n, (top + bot) / 2)

    # a wedge: the tail is the beltline's high point, the nose drops low and wide
    body = _along_x([low(0.0, belt * 0.92, hw * 0.88, 2.8, gc * 1.6),
                     low(0.04, belt * 1.0, hw * 0.97), low(0.30, belt, hw),
                     low(0.62, belt * 0.97, hw), low(0.84, belt * 0.84, hw * 0.99),
                     low(0.96, belt * 0.66, hw * 0.94), low(1.0, belt * 0.52, hw * 0.88, 2.8,
                                                            gc * 1.3)],
                    shell=False, wall=wall)
    tw = 0.34 * R                                # tyre half-width
    depth = 2 * tw + 0.7 * tw + 60               # wheel well: the tyre plus steering room
    for ax in (-WB / 2, WB / 2):                 # a wheel well each side, not a trough across
        for sy in (1, -1):
            well = bd.Solid.make_cylinder(R * 1.1, depth, bd.Plane.XZ.offset(-depth / 2))
            body = _as_shape(body - _placed(well, (ax, sy * (hw + 50 - depth / 2), R)))
    body = max(body.solids(), key=lambda s: s.volume)

    def gh(f, top, half_w, n=3.0):
        x = x0 + f * (x1 - x0)
        bot = belt - 60
        return (x, (top - bot) / 2, half_w, n, (top + bot) / 2)

    gt = height - belt
    glass = _along_x([gh(0.18, belt + 15, hw * 0.70, 2.6),
                      gh(0.27, belt + 0.66 * gt, hw * 0.76, 2.8),
                      gh(0.40, height, hw * 0.78, 3.0), gh(0.56, height * 0.995, hw * 0.78, 3.0),
                      gh(0.68, belt + 0.55 * gt, hw * 0.76, 2.8),
                      gh(0.76, belt + 15, hw * 0.72, 2.6)], shell=False, wall=wall)
    if shell:
        roof, glass = panel_split(glass, bd.Plane.XY.offset(belt + 0.72 * gt), gap=2.0)
        me.add("roof", roof, "shell")
        me.add("glass", glass, "accent")
        # door gaps: real panel gaps as the feature lines of the side
        front, rest = panel_split(body, bd.Plane(origin=(x0 + 0.66 * (x1 - x0), 0, 0),
                                                 z_dir=(1, 0, 0)), gap=4.0)
        doors, rear = panel_split(rest, bd.Plane(origin=(x0 + 0.34 * (x1 - x0), 0, 0),
                                                 z_dir=(1, 0, 0)), gap=4.0)
        me.add("front_body", front, "shell")
        me.add("door_section", doors, "shell")
        me.add("rear_body", rear, "shell")
    else:
        me.add("body", body, "shell")
        me.add("greenhouse", glass, "accent")
    for name, ax, sy in (("front_left", WB / 2, 1), ("front_right", WB / 2, -1),
                         ("rear_left", -WB / 2, 1), ("rear_right", -WB / 2, -1)):
        at = (ax, sy * (hw - 1.25 * tw), R)          # tucked under the arch, flush-ish
        tyre = bd.Solid.make_cylinder(R, 2 * tw, bd.Plane.XZ.offset(-tw))
        tyre = blend_edges(tyre, 0.35 * tw)
        me.add(f"wheel_{name}", _placed(tyre, at), "joint")
        me.axes[f"wheel_{name}"] = (at, (0.0, 1.0, 0.0))
        me.hardpoints[f"wheel_{name}"] = at
    me.hardpoints["beltline"] = (0, 0, belt)
    return me
