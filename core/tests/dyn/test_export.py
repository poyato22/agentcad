"""Assembly -> dynamics model (Plan 10 C3): tree, clusters, frames, inertia, feet, actuators,
couplings and loops, on the quadruped fixture and a small arm."""
import math

import numpy as np
import pytest

from mises.assembly.model import Assembly, Frame, Instance, Joint, JointRef
from mises.dyn import engine, export

from . import quad


@pytest.fixture
def home(tmp_path):
    quad.write_parts(tmp_path)
    return tmp_path


def test_quadruped_exports_a_free_floating_tree_with_four_feet(home):
    ex = export.export(home, None, quad.assembly())
    m = ex.model
    assert ex.base == "free" and m["bodies"][0]["free"] is True
    assert len(m["bodies"]) == 9 and len(m["actuators"]) == 8
    feet = [g for b in m["bodies"] for g in b["geoms"] if g.get("foot")]
    assert len(feet) == 4
    # mass: 1.6e6 mm3 torso + 4 x (24x24x120 + 20x20x130) mm3 of PLA
    want = (1.6e6 + 4 * (24 * 24 * 120 + 20 * 20 * 130)) * 1.24e-6
    assert ex.total_mass_kg == pytest.approx(want, rel=1e-6)
    # qpos == the Mises value: ref is the joint's value in rad, limits in rad
    hip = next(j for b in m["bodies"] for j in b["joints"] if j["name"] == "j_fl_hip")
    assert hip["ref"] == pytest.approx(math.radians(30))
    assert hip["range"] == pytest.approx([math.radians(-90), math.radians(90)])
    assert hip["axis"] == pytest.approx([0, 1, 0])
    # the floor sits under the feet: hip at z=0, 2 x 120 cos 30 = 208 mm below + foot box
    assert m["floor"]["z"] < -0.2
    assert ex.map["joints"]["j_fl_knee"]["joint_id"] == "fl_knee"


def test_quadruped_holds_its_pose_and_carries_its_weight(home):
    ex = export.export(home, None, quad.assembly())
    out = engine.run({"task": "stability", "model": ex.model, "tilt": False})
    assert out["standing"] and out["contacts"] >= 4
    assert out["floor_force_n"] == pytest.approx(ex.total_mass_kg * 9.81, rel=0.03)
    # CoM over the middle of the 260 x 184 mm foot rectangle: ~92 mm to the long side
    assert 0.07 < out["support_margin_m"] < 0.1


def test_squat_needs_more_knee_than_hip_torque(home):
    ex = export.export(home, None, quad.assembly())
    out = engine.run({"task": "torque", "model": ex.model,
                      "trajectory": {"kind": "squat", "depth_m": 0.05, "period_s": 1.5}})
    j = {x["joint"]: x for x in out["joints"]}
    knee, hip = j["j_fl_knee"], j["j_fl_hip"]
    assert knee["peak_inverse"] > hip["peak_inverse"]
    # quasi-static: W/4 on each foot at the knee's horizontal lever (120 sin(30 + squat))
    w4 = ex.total_mass_kg * 9.81 / 4
    assert 0.5 * w4 * 0.06 < knee["peak_inverse"] < 2.5 * w4 * 0.12
    assert out["ik_error_m"] < 0.002
    assert out["stability"]["min_cop_margin_m"] > 0.03


def test_reversed_joint_negates_the_axis(home):
    asm = quad.assembly()
    j = next(x for x in asm.joints if x.id == "fl_hip")
    j.a, j.b = j.b, j.a                  # thigh is `a` now: the tree runs b -> a
    ex = export.export(home, None, asm)
    hip = next(x for b in ex.model["bodies"] for x in b["joints"] if x["name"] == "j_fl_hip")
    assert hip["axis"] == pytest.approx([0, -1, 0])


def test_fixed_joints_merge_into_one_body_and_loops_become_connects(home):
    asm = quad.assembly()
    # bolt a (thigh) bracket rigidly to the torso: one more instance, same body
    asm.instances.append(Instance(id="bracket", part_id="quad_fl_thigh", name="bracket"))
    f = Frame(origin=[0, 0, 0], axis=[0, 0, 1])
    asm.joints.append(Joint(id="bolt", kind="fixed", a=JointRef(instance_id="torso", frame=f),
                            b=JointRef(instance_id="bracket", frame=f)))
    # a pushrod-like closure: the fl shank pinned to the fr shank (a loop)
    kf = Frame(origin=[130, 0, -200], axis=[0, 1, 0])
    asm.joints.append(Joint(id="rod", kind="revolute",
                            a=JointRef(instance_id="fl_shank", frame=kf),
                            b=JointRef(instance_id="fr_shank", frame=kf)))
    ex = export.export(home, None, asm)
    assert len(ex.model["bodies"]) == 9
    root = ex.model["bodies"][0]
    assert sorted(root["instances"]) == ["bracket", "torso"]
    eq = [e for e in ex.model["equalities"] if e["type"] == "connect"]
    assert len(eq) == 1


def test_coupling_contract_becomes_an_equality_joint(home):
    asm = quad.assembly()
    j = next(x for x in asm.joints if x.id == "fl_knee")
    # p10-c2's Joint carries the coupling field itself (Plan 10 shared contract)
    from mises.assembly.model import Coupling
    j.coupling = Coupling(kind="belt", ratio=-2.0, partner_joint="fl_hip")
    ex = export.export(home, None, asm)
    eq = [e for e in ex.model["equalities"] if e["type"] == "joint"]
    assert eq == [{"type": "joint", "joint1": "j_fl_knee", "joint2": "j_fl_hip",
                   "polycoef": [0.0, -2.0, 0.0, 0.0, 0.0]}]
    assert "a_fl_knee" not in ex.map["actuators"]      # driven through its partner


def test_catalog_actuators_set_the_servo_limits(home):
    ex = export.export(home, None, quad.assembly(), actuators={"fl_knee": "mg996r"})
    a = next(x for x in ex.model["actuators"] if x["name"] == "a_fl_knee")
    assert a["forcerange"] == pytest.approx([-1.08, 1.08])
    assert ex.map["actuators"]["a_fl_knee"]["actuator"] == "servo_mg996r_standard"


def test_an_arm_on_its_base_is_fixed(home):
    asm = Assembly(instances=[Instance(id="base", part_id="quad_torso", name="base", fixed=True),
                              Instance(id="l1", part_id="quad_fl_thigh", name="l1")],
                   joints=[Joint(id="sh", kind="revolute",
                                 a=JointRef(instance_id="base",
                                            frame=Frame(origin=[130, 92, 0], axis=[0, 1, 0])),
                                 b=JointRef(instance_id="l1",
                                            frame=Frame(origin=[130, 92, 0], axis=[0, 1, 0])),
                                 value=-90.0)])
    ex = export.export(home, None, asm)
    assert ex.base == "fixed" and ex.model["floor"] is None
    # link along +x after -90 deg about y; static torque = m g r
    out = engine.run({"task": "torque", "model": ex.model,
                      "trajectory": {"kind": "hold", "duration_s": 0.05}})
    m1 = 24 * 24 * 120 * 1.24e-6
    assert out["joints"][0]["peak_inverse"] == pytest.approx(m1 * 9.81 * 0.06, rel=1e-3)
    assert np.isfinite(out["joints"][0]["peak_servo"])


def test_rotary_actuator_on_a_slider_is_not_a_force(home):
    fx = Frame(origin=[0, 0, 0], axis=[1, 0, 0])
    asm = Assembly(instances=[Instance(id="base", part_id="quad_torso", name="base",
                                       fixed=True),
                              Instance(id="rack", part_id="quad_fl_shank", name="rack")],
                   joints=[Joint(id="slide", kind="slider",
                                 a=JointRef(instance_id="base", frame=fx),
                                 b=JointRef(instance_id="rack", frame=fx))])
    ex = export.export(home, None, asm, actuators={"slide": "mg996r"})
    assert ex.model["actuators"][0]["forcerange"] is None
    assert any("rotary" in n for n in ex.notes)
    ex = export.export(home, None, asm, slider_force_n={"slide": 40.0})
    assert ex.model["actuators"][0]["forcerange"] == [-40.0, 40.0]
