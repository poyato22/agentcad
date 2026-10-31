"""Plan 9 G-B: skin-over-skeleton recipes and the archetype starters (humanoid, quadruped, car).

Every recipe builds a valid solid whose precise volume is within 1% of a reference: analytic
where one exists (ellipse lofts, shells, capsules), else the value measured when the recipe was
written (a regression pin). The archetypes are checked for what makes them design-grade: every
visible part is a cover (no straight bars), the actuators sit inside their cowls, at most three
CMF roles, and the proportions the docstrings promise."""
import importlib.util
import math
from pathlib import Path

import build123d as bd
import pytest

from mises.cad import mises_shapes as ms
from mises.design_intensity import bar_like_solid

pytestmark = pytest.mark.timeout(600)

GALLERY = Path(__file__).resolve().parents[2] / "scripts" / "recipe_gallery.py"


def _gallery():
    spec = importlib.util.spec_from_file_location("recipe_gallery", GALLERY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _gallery()
EX = G.examples()


def vol(s) -> float:
    return ms._occ_volume(s)


def _overlap(a, b) -> float:
    """Volume of a & b (an empty intersection is None or has no solids)."""
    both = a & b
    return 0.0 if both is None or not both.solids() else both.volume


def near(a, b, rel=0.01):
    assert abs(a - b) <= rel * abs(b), f"{a:.1f} vs reference {b:.1f}"


# ---------------------------------------------------------------- primitives (analytic)

def test_squircle_n2_is_an_ellipse_loft():
    s = ms.station_loft([(0, 30, 20, 2.0), (100, 30, 20, 2.0)])
    near(vol(s), math.pi * 30 * 20 * 100)


def test_squircle_returns_one_closed_edge_with_the_seam_where_asked():
    w = ms.squircle(40, 20, 3.0, seam_deg=90)
    assert len(w.edges()) == 1 and w.is_closed
    start = w.edges()[0] @ 0
    assert start.Y == pytest.approx(20, abs=0.1) and abs(start.X) < 0.1


def test_station_loft_rejects_non_monotonic_or_too_thin_stations():
    with pytest.raises(ValueError, match="monotonic"):
        ms.station_loft([(0, 10, 10), (50, 10, 10), (20, 10, 10)])
    with pytest.raises(ValueError, match="thinner than the wall"):
        ms.skin_shell([(0, 3, 10, 2.0), (50, 10, 10, 2.0)], wall=4)


def test_skin_shell_volume_is_the_inset_difference():
    a, b, w, h = 30, 20, 2.0, 100
    near(vol(ms.skin_shell([(0, a, b, 2.0), (h, a, b, 2.0)], wall=w)),
         math.pi * (a * b - (a - w) * (b - w)) * h)


def test_skin_shell_closed_end_keeps_a_cap():
    a, b, w, h = 30, 20, 2.0, 100
    s = ms.skin_shell([(0, a, b, 2.0), (h, a, b, 2.0)], wall=w, open_ends=(True, False))
    near(vol(s), math.pi * (a * b - (a - w) * (b - w)) * (h - w) + math.pi * a * b * w)


def test_tapered_capsule_is_the_hull_of_two_spheres():
    r1, r2, L = 20.0, 12.0, 100.0
    a = math.asin((r1 - r2) / L)
    h1, h2 = r1 * (1 + math.sin(a)), r2 * (1 - math.sin(a))
    R1, R2 = r1 * math.cos(a), r2 * math.cos(a)
    H = L + (r2 - r1) * math.sin(a)
    ref = (math.pi * h1 ** 2 * (3 * r1 - h1) / 3 + math.pi * h2 ** 2 * (3 * r2 - h2) / 3
           + math.pi * H * (R1 * R1 + R1 * R2 + R2 * R2) / 3)
    near(vol(ms.tapered_capsule(r1, r2, L)), ref)
    placed = ms.tapered_capsule(r1, r2, start=(0, 0, 0), end=(60, 0, 80))
    near(vol(placed), vol(ms.tapered_capsule(r1, r2, 100.0)))
    bb = placed.bounding_box()
    assert bb.max.X == pytest.approx(60 + r2, abs=0.5) and bb.min.Z == pytest.approx(-r1, abs=0.5)


def test_tapered_capsule_has_no_flat_faces():
    """A capsule is never a bar: no planar faces, and the bar heuristic agrees."""
    c = ms.tapered_capsule(15, 10, 200)
    assert all(f.geom_type != bd.GeomType.PLANE for f in c.faces())
    assert not bar_like_solid(c)
    assert bar_like_solid(bd.Solid.make_box(20, 20, 200))


def test_joint_cowl_encloses_its_actuator_and_is_open_for_the_swing():
    r, w = 34.0, 50.0
    cowl = ms.joint_cowl(r, w, rom_deg=120)
    actuator = bd.Solid.make_cylinder(r, w, bd.Plane.YZ.offset(-w / 2))
    assert _overlap(actuator, cowl) < 1.0
    bb = cowl.bounding_box()
    assert bb.max.Z >= r + 2.0 + 2.5 - 1e-3                 # clearance + wall over the top
    closed = ms.joint_cowl(r, w, rom_deg=0)
    assert vol(closed) > vol(cowl)                           # the swing sector is removed
    near(vol(cowl), 113935.5)


def test_panel_split_leaves_a_real_gap():
    shell = ms.skin_shell(G.THIGH, wall=2.5)
    upper, lower = ms.panel_split(shell, bd.Plane.XY.offset(-110), gap=0.8)
    assert upper.bounding_box().min.Z == pytest.approx(-110 + 0.4, abs=1e-3)
    assert lower.bounding_box().max.Z == pytest.approx(-110 - 0.4, abs=1e-3)
    assert vol(upper) + vol(lower) < vol(shell)
    near(vol(upper) + vol(lower), 115210.8)
    with pytest.raises(ValueError, match="nothing on the"):
        ms.panel_split(shell, bd.Plane.XY.offset(500))


def test_clamshell_halves_split_on_the_side_silhouette():
    front, back = ms.clamshell(ms.skin_shell(G.THIGH, wall=2.5))
    assert front.bounding_box().max.Y < 0 and back.bounding_box().min.Y > 0


def test_cover_for_asserts_the_fit_and_adds_a_bulkhead():
    servo = ((0, 2, -60), (40, 20, 40))
    with_rib = ms.cover_for(G.THIGH, components=[servo], bulkheads=[-90])
    plain = ms.skin_shell(G.THIGH, wall=2.5)
    assert vol(with_rib) > vol(plain) + 1000                 # the bulkhead is there
    near(vol(with_rib), 126458.4)
    with pytest.raises(ValueError, match="component 0 does not fit"):
        ms.cover_for(G.THIGH, components=[((0, 2, -60), (90, 20, 40))])


def test_trust_accepts_a_good_shell_and_refuses_disagreeing_volumes(monkeypatch):
    good = ms.skin_shell(G.THIGH, wall=2.5)
    assert ms.trust(good, "thigh") is good
    monkeypatch.setattr(ms, "_mesh_volume", lambda s, tol=None: ms._occ_volume(s) * 1.2)
    with pytest.raises(ValueError, match="untrustworthy"):
        ms.trust(good, "thigh")


@pytest.mark.parametrize("name, ref", [
    ("squircle", 108908.7), ("station_loft", 858365.9), ("skin_shell", 115689.9),
    ("tapered_capsule", 105412.0), ("joint_cowl", 113935.5), ("panel_split", 115210.8),
    ("clamshell", 115386.3), ("cover_for", 126458.4), ("trust", 115689.9),
    ("humanoid", 64285498.9), ("quadruped", 35605121.9), ("car_body", 5918928067.0)])
def test_new_recipe_examples_match_their_reference_volume(name, ref):
    near(vol(EX[name]()), ref)


# ---------------------------------------------------------------- archetypes (shelled)

@pytest.fixture(scope="module")
def humanoid():
    return ms.humanoid()


@pytest.fixture(scope="module")
def quadruped():
    return ms.quadruped()


@pytest.fixture(scope="module")
def car():
    return ms.car_body()


def _covers_everything(arch):
    for name, part in arch.parts.items():
        ms.check_solid(part, name)
        for s in part.solids():
            assert not bar_like_solid(s), f"{name} reads as a straight bar"
    assert len(set(arch.roles.values())) <= 3, arch.roles


def _envelopes_inside_cowls(arch):
    for jn, env in arch.envelopes.items():
        cowl = arch.parts.get(f"{jn}_cowl")
        if cowl is None:
            continue
        assert _overlap(env, cowl) < 1.0, f"{jn} actuator clashes with its cowl"
        bb, cb = env.bounding_box(), cowl.bounding_box()
        for lo, hi, clo, chi in ((bb.min.X, bb.max.X, cb.min.X, cb.max.X),
                                 (bb.min.Y, bb.max.Y, cb.min.Y, cb.max.Y),
                                 (bb.min.Z, bb.max.Z, cb.min.Z, cb.max.Z)):
            assert clo <= lo + 1e-6 and chi >= hi - 1e-6, f"{jn} actuator pokes out of its cowl"


def test_humanoid_is_eight_heads_and_fully_covered(humanoid):
    h = humanoid
    _covers_everything(h)
    _envelopes_inside_cowls(h)
    bb = h.compound().bounding_box()
    assert bb.min.Z == pytest.approx(0, abs=1.0)
    near(bb.max.Z, 1700, rel=0.01)
    head = h.parts["head_shell"].bounding_box().max.Z - h.parts["head_face"].bounding_box().min.Z
    assert head == pytest.approx(1700 / 8, rel=0.08)          # one head unit
    assert {"head_face", "head_shell", "chest", "ribcage", "pelvis", "waist",
            "thigh_left", "shin_right", "knee_left_cowl", "elbow_right_cowl",
            "shoulder_cap_left", "hand_right", "foot_left"} <= set(h.parts)
    assert {"shoulder_left", "elbow_left", "hip_left", "knee_left", "ankle_left"} <= set(h.axes)
    # the shells are trusted covers, and the chest carries a real panel gap
    for name in ("chest", "thigh_left", "shin_left", "upper_arm_left", "forearm_left"):
        ms.trust(h.parts[name], name)
    assert (h.parts["chest"].bounding_box().min.Z
            - h.parts["ribcage"].bounding_box().max.Z) == pytest.approx(1.2, abs=1e-3)
    assert set(h.roles.values()) == {"shell", "joint", "accent"}


def test_humanoid_hips_and_actuators_are_hidden_inside_covers(humanoid):
    """The hip actuators have no cowl: they sit inside the pelvis/thigh covers."""
    h = humanoid
    assert "hip_left_cowl" not in h.parts
    hull = h.parts["pelvis"].bounding_box()
    hip = h.envelopes["hip_left"].bounding_box()
    assert hull.min.X - 1 <= hip.min.X and hip.max.X <= hull.max.X + 1
    assert hull.min.Z - 1 <= hip.min.Z


def test_humanoid_massing_scales_with_height_and_heads():
    m = ms.humanoid(height=1800, heads=7.5, shell=False)
    assert m.units["head_unit"] == pytest.approx(240.0)
    near(m.compound().bounding_box().max.Z, 1800, rel=0.01)


def test_quadruped_is_covered_with_knees_back(quadruped):
    q = quadruped
    _covers_everything(q)
    _envelopes_inside_cowls(q)
    assert q.units["body_to_leg"] == pytest.approx(2.0)
    for leg in ("front_left", "rear_right"):
        hip, knee, foot = (q.hardpoints[f"{j}_{leg}"] for j in ("hip", "knee", "foot"))
        assert knee[0] < hip[0] and knee[0] < foot[0]       # the knee points back
    assert q.compound().bounding_box().min.Z == pytest.approx(0, abs=0.5)


def test_car_body_proportions_and_panel_gaps(car):
    c = car
    _covers_everything(c)
    assert c.units["wheelbase_in_wheels"] == pytest.approx(2700 / 680, rel=1e-3)
    wheels = [c.hardpoints[f"wheel_{w}"] for w in ("front_left", "rear_left")]
    assert wheels[0][0] - wheels[1][0] == pytest.approx(2700)
    assert {"roof", "glass", "front_body", "door_section", "rear_body"} <= set(c.parts)
    assert c.compound().bounding_box().min.Z == pytest.approx(0, abs=0.5)
