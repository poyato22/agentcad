"""The thrust-vector gimbal mechanism (demo build 2): a 2-axis servo-driven motor mount whose
servo travel reaches exactly +-max deflection on both axes, with no interference anywhere in
the +-5 deg box, and the library finding it for a TVC request."""
import itertools

import pytest

from mises.assembly.solve import solve
from mises.mech import library
from mises.mech.assemble import joint_ids, plan_assembly, preview_shapes
from mises.types import MisesError


def _gimbal(plan, values):
    asm = plan_assembly(plan)
    jid = joint_ids(plan, {i.key: i.key for i in plan.instances})
    asm = asm.model_copy(update={"joints": [
        j.model_copy(update={"value": values[k]}) if (k := next(
            (kk for kk, vv in jid.items() if vv == j.id), None)) in values else j
        for j in asm.joints]})
    res = solve(asm)
    assert not res.loop_errors, res.loop_errors
    vals = {j.id: j.value for j in asm.joints} | res.solved
    return vals[jid["pitch"]], vals[jid["yaw"]]


def test_card_is_two_dof_with_servo_sizing():
    m, p, plan = library.plan_for("tvc_gimbal", {})
    card = library.card_for(m, p, plan)
    assert card.dof == card.dof_intended == 2
    assert set(plan.inputs) == {"pitch_servo", "yaw_servo"}
    s = card.sizing
    assert 0.3 < s["pitch_deg_per_servo_deg"] < 0.7 and 0.3 < s["yaw_deg_per_servo_deg"] < 0.7
    assert s["servo_stall_nm"] == pytest.approx(0.177)
    assert s["servo_torque_margin"] > 2
    assert s["lug_bearing_sf"] > 2
    assert not solve(plan_assembly(plan)).loop_errors      # exact at the inserted pose


def test_servo_travel_reaches_exactly_the_max_deflection():
    m, p, plan = library.plan_for("tvc_gimbal", {"max_deflection_deg": 5.0})
    lo, hi = plan.rom["pitch_servo"]
    assert _gimbal(plan, {"pitch_servo": lo})[0] == pytest.approx(-5.0, abs=0.05)
    assert _gimbal(plan, {"pitch_servo": hi})[0] == pytest.approx(5.0, abs=0.05)
    lo, hi = plan.rom["yaw_servo"]
    assert _gimbal(plan, {"yaw_servo": lo})[1] == pytest.approx(-5.0, abs=0.05)
    assert _gimbal(plan, {"yaw_servo": hi})[1] == pytest.approx(5.0, abs=0.05)
    # the yaw rod runs on the X axis, so a full pitch swing moves yaw by about a degree only
    assert abs(_gimbal(plan, {"pitch_servo": hi})[1]) < 1.5


def _overlap(a, b) -> float:
    c = a.intersect(b)
    if c is None:
        return 0.0
    if type(c).__name__ == "ShapeList":
        return sum(getattr(x, "volume", 0.0) for x in c)
    return c.volume


@pytest.mark.parametrize("airframe_id,layout", [(76.2, "outboard"), (66.0, "inboard")])
def test_no_interference_through_the_full_two_axis_range(airframe_id, layout):
    # 76.2 mm (3 in) fits the pitch servo beside the gimbal; a 66 mm BT-80 puts it over the motor
    m, p, plan = library.plan_for("tvc_gimbal", {"airframe_id": airframe_id})
    assert plan.sizing["servo_layout"] == layout
    contacts = {frozenset(c) for c in plan.contacts}
    pr, yr = plan.rom["pitch_servo"], plan.rom["yaw_servo"]
    poses = [{}] + [{"pitch_servo": a, "yaw_servo": b} for a in (*pr, 0.0) for b in (*yr, 0.0)]
    for vals in poses:
        shapes, res = preview_shapes(plan, values=vals)
        assert not res.loop_errors, (vals, res.loop_errors)
        for a, b in itertools.combinations(shapes, 2):
            v = _overlap(shapes[a], shapes[b])
            limit = 2.0 if frozenset((a, b)) in contacts else 0.01
            assert v < limit, (vals, a, b, v)


def test_library_finds_it_for_a_tvc_request():
    top = library.rank("thrust vector control gimbal for a rocket motor", limit=3)
    assert top[0]["id"] == "tvc_gimbal"


def test_too_small_an_airframe_is_refused_with_the_size_needed():
    with pytest.raises(MisesError, match="airframe ID"):
        library.plan_for("tvc_gimbal", {"airframe_id": 60.0})
