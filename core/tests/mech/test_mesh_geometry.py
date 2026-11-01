"""Plan 10 C2 (research §6.4): every gear mesh is phased and coupled, so the meshing parts do not
interfere at the inserted pose or anywhere along the input's motion; the planetary needs the
ring phase (without it the research spike's 34 mm3 overlap comes back). In-process build123d
(the same recipes the sandboxed worker runs)."""
import pytest

from mises.mech import library
from mises.mech.assemble import preview_shapes

TOL = 0.05     # mm3: OCCT noise on curved contact


def overlap(a, b) -> float:
    c = a.intersect(b)
    if c is None:
        return 0.0
    if type(c).__name__ == "ShapeList":
        return sum(getattr(x, "volume", 0.0) for x in c)
    return c.volume


CASES = [
    ("spur_pair", "in_rev", [0.0, 6.0, 12.0], [("pinion", "gear")]),
    ("helical_pair", "in_rev", [0.0, 7.0], [("pinion", "gear")]),
    ("planetary", "sun_rev", [0.0, 10.0, 20.0],
     [("sun", "planet_0"), ("sun", "planet_2"), ("ring", "planet_0"), ("ring", "planet_1"),
      ("ring", "planet_2")]),
    ("planetary_compound", "sun_rev", [0.0, 25.0],
     [("sun", "planet_0"), ("ring1", "planet_1"), ("ring2", "planet_0_step2"),
      ("ring2", "planet_2_step2")]),
    ("bevel_pair", "in_rev", [0.0, 8.0, 16.0], [("pinion", "gear")]),
    ("worm_drive", "worm_rev", [0.0, 90.0, 200.0], [("worm", "wheel")]),
    ("rack_pinion", "in_rev", [0.0, 25.0], [("pinion", "rack")]),
    ("gear_train_compound", "in_rev", [0.0, 25.0], [("pinion1", "gear2"), ("pinion3", "gear4")]),
    ("cycloidal_reducer", "input_rev", [0.0, 75.0], [("housing", "disc"), ("output", "disc")]),
    ("bevel_differential", "left_rev", [0.0, 25.0],
     [("side_left", "spider_0"), ("side_right", "spider_0"), ("side_left", "spider_1")]),
    ("parallel_gripper", "pinion_rev", [0.0, -60.0], [("pinion_gear", "jaw_a"),
                                                       ("pinion_gear", "jaw_b")]),
    ("belt_drive_gt2", "drive_rev", [0.0, 40.0], [("belt", "pulley1"), ("belt", "pulley2")]),
]


@pytest.mark.parametrize("mech_id,joint,values,pairs", CASES, ids=[c[0] for c in CASES])
def test_meshes_never_interfere_along_the_motion(mech_id, joint, values, pairs):
    m, p, plan = library.plan_for(mech_id, {})
    for v in values:
        shapes, res = preview_shapes(plan, values={joint: v})
        for a, b in pairs:
            assert overlap(shapes[a], shapes[b]) < TOL, (mech_id, v, a, b)


def test_planetary_without_the_ring_phase_overlaps():
    m, p, plan = library.plan_for("planetary", {})
    assert plan.part("ring").kwargs["phase_deg"] != 0
    plan.part("ring").kwargs["phase_deg"] = 0.0
    shapes, _res = preview_shapes(plan)
    assert overlap(shapes["ring"], shapes["planet_0"]) > 1.0


def test_followers_turn_by_the_ratio():
    m, p, plan = library.plan_for("planetary", {})
    _s, res = preview_shapes(plan, values={"sun_rev": 40.0})
    jc = next(k for k in res.applied if k.endswith("carrier_rev"))
    assert res.applied[jc] == pytest.approx(10.0)           # 40 / (1 + 36/12)
    m, p, plan = library.plan_for("worm_drive", {})
    _s, res = preview_shapes(plan, values={"worm_rev": 360.0})
    jo = next(k for k in res.applied if k.endswith("out_rev"))
    assert abs(res.applied[jo]) == pytest.approx(12.0)      # one tooth of 30 per worm turn
