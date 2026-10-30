"""mises_shapes recipe library (Plan 8 G2): every recipe builds valid geometry, the airfoils
follow the published NACA formulas, the B-2 body is smooth and the enclosure fits its internals."""
import importlib.util
import math
from pathlib import Path

import build123d as bd
import pytest

from mises.cad import mises_shapes as ms

pytestmark = pytest.mark.timeout(120)

GALLERY = Path(__file__).resolve().parents[2] / "scripts" / "recipe_gallery.py"


def _gallery():
    spec = importlib.util.spec_from_file_location("recipe_gallery", GALLERY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXAMPLES = _gallery().examples()


def _closed_valid(shape):
    shape = shape.part if hasattr(shape, "part") else shape
    assert shape.is_valid
    assert shape.volume > 0
    for s in shape.solids():
        ms.check_solid(s)          # closed: no free edges
    return shape


def test_gallery_covers_every_recipe():
    assert set(EXAMPLES) == set(ms.__all__)


@pytest.mark.parametrize("name", sorted(ms.__all__))
def test_every_recipe_builds_a_valid_closed_solid(name):
    _closed_valid(EXAMPLES[name]())


# ---------------------------------------------------------------- airfoils

def _published_naca4(x, m, p, t):
    """NACA Report 460 / Abbott & von Doenhoff, open trailing edge (a4 = -0.1015)."""
    yt = 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3
                  - 0.1015 * x**4)
    if x < p:
        yc, dy = m / p**2 * (2 * p * x - x**2), 2 * m / p**2 * (p - x)
    else:
        yc, dy = (m / (1 - p)**2 * ((1 - 2 * p) + 2 * p * x - x**2), 2 * m / (1 - p)**2 * (p - x))
    th = math.atan(dy)
    return (x - yt * math.sin(th), yc + yt * math.cos(th)), (x + yt * math.sin(th),
                                                             yc - yt * math.cos(th))


def test_naca2412_matches_the_published_formula():
    n = 40
    pts = ms.naca4("2412", n=n)
    assert len(pts) == 2 * n + 1
    upper, lower = list(reversed(pts[:n + 1])), pts[n:]
    for i in range(n + 1):
        x = 0.5 * (1 - math.cos(math.pi * i / n))
        u, lo = _published_naca4(x, 0.02, 0.4, 0.12)
        assert upper[i] == pytest.approx(u, abs=1e-12)
        assert lower[i] == pytest.approx(lo, abs=1e-12)
    # Selig order: TE -> upper -> LE -> lower -> TE
    assert pts[0][0] == pytest.approx(1.0, abs=1e-3) and pts[n] == pytest.approx((0, 0))
    assert pts[1][1] > 0 and pts[-2][1] < 0


def test_naca0012_tabulated_thickness():
    # Abbott & von Doenhoff tabulate y = 6.002 % chord at x = 30 % for the 0012
    pts = ms.naca4("0012", n=200)
    best = min(pts, key=lambda p: abs(p[0] - 0.3) + (0 if p[1] > 0 else 9))
    assert best[1] == pytest.approx(0.06002, abs=2e-4)
    assert max(p[1] for p in pts) == pytest.approx(0.06, abs=2e-4)


def test_naca4_closed_te_and_bad_codes():
    pts = ms.naca4("0012", closed_te=True)
    assert math.dist(pts[0], pts[-1]) < 1e-9
    with pytest.raises(ValueError, match="4 digits"):
        ms.naca4("24x2")
    with pytest.raises(ValueError):
        ms.naca5("23112")          # reflexed


def test_naca23012_camber_peak():
    # 23012: max camber ~1.8 % at 15 % chord (design cl 0.3)
    pts = ms.naca5("23012", n=200)
    n = 200
    upper, lower = list(reversed(pts[:n + 1])), pts[n:]
    mids = [((u[0] + lo[0]) / 2, (u[1] + lo[1]) / 2) for u, lo in zip(upper, lower)]
    xm, ym = max(mids, key=lambda p: p[1])
    assert xm == pytest.approx(0.15, abs=0.02)
    assert ym == pytest.approx(0.0183, abs=0.0008)


def test_airfoil_from_points_normalizes_and_rejoins_lednicer():
    sel = ms.naca4("2412", n=30)
    scaled = [(10 + 3 * x, 5 + 3 * y) for x, y in sel]
    out = ms.airfoil_from_points(scaled)
    assert min(x for x, _ in out) == 0 and max(x for x, _ in out) == pytest.approx(1)
    assert out[0] == pytest.approx(sel[0], abs=2e-4)      # cambered TE pokes past x = 1
    n = 30
    led = list(reversed(sel[:n + 1])) + sel[n + 1:]     # upper LE->TE, then lower LE->TE
    assert ms.airfoil_from_points(led, normalize=False) == pytest.approx(sel)
    with pytest.raises(ValueError):
        ms.airfoil_from_points([(0, 0), (1, 0)])


# ---------------------------------------------------------------- wing

def test_wing_volume_matches_the_section_integral():
    w = ms.wing(span=1000, root_chord=250, tip_chord=100, sweep_deg=0, dihedral_deg=0,
                twist_deg=0, airfoil="0012")
    bb = w.bounding_box()
    assert bb.size.Y == pytest.approx(1000, rel=1e-3)
    assert bb.size.X == pytest.approx(250, rel=1e-2)
    # NACA 00xx area = 0.6851 t c^2; mean c^2 over a linear taper = (r^2 + r t + t^2) / 3
    expected = 0.6851 * 0.12 * (250**2 + 250 * 100 + 100**2) / 3 * 1000
    assert w.volume == pytest.approx(expected, rel=0.02)
    assert len(w.solids()) == 1


def test_wing_sections_override_and_twist():
    w = ms.wing(span=600, sections=[{"y": 0, "chord": 200}, {"y": 150, "chord": 180},
                                    {"y": 300, "chord": 60, "twist": -4}], symmetric=False)
    assert w.bounding_box().size.Y == pytest.approx(300, rel=1e-3)
    with pytest.raises(ValueError):
        ms.wing(tip_chord=0)


# ---------------------------------------------------------------- blended wing body

@pytest.fixture(scope="module")
def bwb():
    return ms.blended_wing_body()


def test_bwb_is_one_valid_solid_without_self_intersection(bwb):
    assert len(bwb.solids()) == 1
    ms.check_solid(bwb, self_intersection=True)
    bb = bwb.bounding_box()
    assert bb.size.Y == pytest.approx(524, rel=1e-3)              # 1:100 B-2 span
    assert 200 < bb.size.X < 260 and 20 < bb.size.Z < 50


def test_bwb_skin_is_one_smooth_surface(bwb):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_C1

    skin = max(bwb.faces(), key=lambda f: f.area)
    assert skin.geom_type == bd.GeomType.BSPLINE
    assert skin.area > 0.9 * sum(f.area for f in bwb.faces()
                                 if f.geom_type == bd.GeomType.BSPLINE)
    surf = BRepAdaptor_Surface(skin.wrapped)
    assert surf.UContinuity().value >= GeomAbs_C1.value
    assert surf.VContinuity().value >= GeomAbs_C1.value


def test_bwb_center_body_is_thicker_and_blends(bwb):
    def thickness_at(y):
        slab = bd.Solid.make_box(400, 2, 200, bd.Plane.XY.move(bd.Location((-50, y - 1, -100))))
        return (bwb & slab).bounding_box().size.Z

    t = [thickness_at(y) for y in (0, 30, 60, 90, 150, 220)]
    assert t[0] > 2 * t[-2]                         # thick center body, thin outer wing
    assert all(a >= b - 0.3 for a, b in zip(t, t[1:]))  # monotone blend: no ripples


def test_bwb_sawtooth_trailing_edge(bwb):
    """The TE has the double-W: per side an inner notch, an aft point and an outer notch."""
    s, k, L = 262.0, math.tan(math.radians(33)), 0.4 * 524

    def te_x(y):
        slab = bd.Solid.make_box(400, 1, 200, bd.Plane.XY.move(bd.Location((-50, y - .5, -100))))
        return (bwb & slab).bounding_box().max.X

    notch, aft, outer = te_x(0.24 * s), te_x(0.43 * s), te_x(0.62 * s)
    assert te_x(0) == pytest.approx(L, abs=1.0)
    assert notch == pytest.approx(L - k * 0.24 * s, abs=1.0)
    assert aft > notch + 10 and aft > outer + 10
    assert te_x(-0.43 * s) == pytest.approx(aft, abs=0.5)        # symmetric


def test_bwb_smooth_te_option_and_bad_notches():
    ms.check_solid(ms.blended_wing_body(sawtooth=False))
    with pytest.raises(ValueError, match="notches"):
        ms.blended_wing_body(notches=(0.5, 0.4, 0.6))


# ---------------------------------------------------------------- enclosure with internals

def _overlap(a, b) -> float:
    r = a & b
    return 0.0 if r is None else sum(s.volume for s in r.solids())


def test_enclosure_with_internals_passes_interference():
    e = ms.enclosure((100, 70, 40), wall=2, ribs=0, snap_fits=2)
    floor = e.cavity.bounding_box().min.Z
    pcb_holes = [(-29, -24.5), (29, -24.5), (-29, 24.5), (29, 24.5)]
    posts = ms.standoffs(pcb_holes, 5.0, 5.0, 2.5, z=floor)
    pcb = bd.Solid.make_box(65, 56, 1.6, bd.Plane.XY.move(bd.Location((-32.5, -28, floor + 5))))
    battery = bd.Solid.make_box(50, 30, 8, bd.Plane.XY.move(bd.Location((-25, -15,
                                                                         floor + 8))))
    internals = {"pcb": pcb, "battery": battery}
    parts = {"base": e.base, "lid": e.lid, **internals}
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert _overlap(parts[a], parts[b]) < 1e-6, (a, b)
    # the standoffs touch the floor and carry the board, never poke through it
    assert _overlap(posts, pcb) < 1e-6
    for name, solid in internals.items():
        assert abs(_overlap(solid, e.cavity) - solid.volume) < 1e-6, name   # fully inside
    assert len(e.base.solids()) == 1 and len(e.lid.solids()) == 1


def test_enclosure_bosses_lip_and_organic_outer():
    e = ms.enclosure((80, 60, 30), wall=2)
    assert e.lid is not None and e.split_z == pytest.approx(22.5)
    # the lid's lip reaches below the parting line, inside the base wall
    assert e.lid.bounding_box().min.Z < e.split_z - 2
    holes = [f for f in e.base.faces() if f.geom_type == bd.GeomType.CYLINDER]
    assert len(holes) >= 8                       # 4 bosses: outside + pilot bore
    org = ms.enclosure(ms.fuselage([(0, 30, 30), (100, 40, 40), (160, 30, 30)]), bosses=[])
    assert _overlap(org.base, org.lid) < 1e-6
    with pytest.raises(ValueError):
        ms.enclosure((10, 10, 10), wall=3)


def test_check_solid_rejects_open_and_empty_shapes():
    face = bd.Face.make_rect(10, 10)
    with pytest.raises(ValueError, match="no solid"):
        ms.check_solid(face)
    with pytest.raises(ValueError):
        ms.thicken(face, 0)


def test_blend_edges_backs_off_and_reports(capsys):
    box = bd.Solid.make_box(10, 10, 10)
    out = ms.blend_edges(box, 8.0)                 # 8 mm cannot fit a 10 mm cube
    assert out.volume < box.volume
    assert "radius reduced" in capsys.readouterr().out
    with pytest.raises(ValueError):
        ms.blend_edges(box, 8.0, min_radius=7.0)


def test_gallery_renders_every_recipe_when_render_exists(tmp_path):
    pytest.importorskip("mises.cad.render", reason="G1 render_views not merged on this branch")
    assert _gallery().main(str(tmp_path)) == 0
