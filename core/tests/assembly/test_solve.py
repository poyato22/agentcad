import math

import pytest

from mises.assembly.model import Assembly, Frame, Instance, Joint, JointRef
from mises.assembly.solve import (clamp_value, frame_matrix, inverse_rigid, joint_motion, matmul,
                                   solve, transform_point)
from mises.types import MisesError


def _plate(iid, part, fixed=False):
    return Instance(id=iid, part_id=part, name=iid, fixed=fixed)


def _hinge(value, limits=None):
    """Two plates joined by a revolute joint about +Z through the point (10, 0, 0)."""
    inst = [_plate("base", "plate_a", fixed=True), _plate("flap", "plate_b")]
    j = Joint(id="h", kind="revolute",
              a=JointRef(instance_id="base", frame=Frame(origin=[10, 0, 0], axis=[0, 0, 1])),
              b=JointRef(instance_id="flap", frame=Frame(origin=[10, 0, 0], axis=[0, 0, 1])),
              value=value, limits=limits)
    return Assembly(instances=inst, joints=[j])


def test_matrix_inverse_roundtrips():
    m = matmul(frame_matrix([3, 4, 5], [0, 1, 0]), joint_motion("revolute", 37))
    back = matmul(inverse_rigid(m), m)
    for i in range(4):
        for j in range(4):
            assert back[i][j] == pytest.approx(1.0 if i == j else 0.0, abs=1e-9)


def test_hinge_at_zero_is_aligned():
    res = solve(_hinge(0.0))
    tq = res.transforms["flap"]
    # the frame origin (10,0,0) maps onto the base frame origin (10,0,0)
    assert transform_point(tq, [10, 0, 0]) == pytest.approx([10, 0, 0], abs=1e-9)
    # a point offset +x from the pivot stays +x
    assert transform_point(tq, [11, 0, 0]) == pytest.approx([11, 0, 0], abs=1e-9)


def test_hinge_at_ninety_rotates_about_axis():
    res = solve(_hinge(90.0))
    tq = res.transforms["flap"]
    # pivot is fixed
    assert transform_point(tq, [10, 0, 0]) == pytest.approx([10, 0, 0], abs=1e-9)
    # +x offset from the pivot rotates to +y (90 deg about +Z)
    assert transform_point(tq, [11, 0, 0]) == pytest.approx([10, 1, 0], abs=1e-9)


def test_slider_translates_along_axis_and_clamps():
    inst = [_plate("g", "rail", fixed=True), _plate("c", "car")]
    j = Joint(id="s", kind="slider",
              a=JointRef(instance_id="g", frame=Frame(origin=[0, 0, 0], axis=[1, 0, 0])),
              b=JointRef(instance_id="c", frame=Frame(origin=[0, 0, 0], axis=[1, 0, 0])),
              value=50.0, limits=[0.0, 20.0])
    res = solve(Assembly(instances=inst, joints=[j]))
    # value 50 clamped to 20, slide along the frame axis (+x)
    assert res.applied["s"] == pytest.approx(20.0)
    assert transform_point(res.transforms["c"], [0, 0, 0]) == pytest.approx([20, 0, 0], abs=1e-9)


def test_clamp_value_scalar_and_vector():
    assert clamp_value(5.0, [0.0, 3.0]) == pytest.approx(3.0)
    assert clamp_value([-1.0, 9.0], [0.0, 4.0]) == pytest.approx([0.0, 4.0])
    assert clamp_value(5.0, None) == 5.0


def test_loop_is_rejected():
    inst = [_plate("a", "plate", fixed=True), _plate("b", "plate"), _plate("c", "plate")]

    def j(jid, x, y):
        return Joint(id=jid, kind="revolute",
                     a=JointRef(instance_id=x, frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
                     b=JointRef(instance_id=y, frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))
    asm = Assembly(instances=inst, joints=[j("j1", "a", "b"), j("j2", "b", "c"), j("j3", "c", "a")])
    with pytest.raises(MisesError, match="loop"):
        solve(asm)


def test_unknown_instance_rejected():
    inst = [_plate("a", "plate", fixed=True)]
    j = Joint(id="j", kind="fixed",
              a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
              b=JointRef(instance_id="ghost", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))
    with pytest.raises(MisesError, match="unknown instance"):
        solve(Assembly(instances=inst, joints=[j]))


def test_dof_remaining_counts_constraints():
    res = solve(_hinge(0.0))
    # one movable body (6 dof) minus a revolute (5) = 1
    assert res.dof_remaining == 1


def test_face_id_frame_uses_resolver():
    inst = [_plate("g", "rail", fixed=True), _plate("c", "car")]
    j = Joint(id="s", kind="slider",
              a=JointRef(instance_id="g", frame=Frame(face_id="f0")),
              b=JointRef(instance_id="c", frame=Frame(origin=[0, 0, 0], axis=[1, 0, 0])),
              value=5.0)
    calls = []

    def resolve(part_id, face_id):
        calls.append((part_id, face_id))
        return ([0, 0, 0], [1, 0, 0])
    res = solve(Assembly(instances=inst, joints=[j]), resolve_face=resolve)
    assert ("rail", "f0") in calls
    assert transform_point(res.transforms["c"], [0, 0, 0]) == pytest.approx([5, 0, 0], abs=1e-9)


def test_free_body_keeps_its_transform():
    inst = [Instance(id="loose", part_id="plate", name="loose",
                     transform=[[1, 0, 0, 7], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])]
    res = solve(Assembly(instances=inst, joints=[]))
    assert transform_point(res.transforms["loose"], [0, 0, 0]) == pytest.approx([7, 0, 0])


def test_fixed_instance_is_never_moved_when_overconstrained():
    shifted = [[1, 0, 0, 50], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    inst = [_plate("a", "plate", fixed=True),
            Instance(id="b", part_id="plate2", name="b", fixed=True, transform=shifted)]
    j = Joint(id="j", kind="fixed",
              a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
              b=JointRef(instance_id="b", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))
    res = solve(Assembly(instances=inst, joints=[j]))
    assert res.overconstrained == [["a", "b"]]
    assert transform_point(res.transforms["b"], [0, 0, 0]) == pytest.approx([50, 0, 0])
