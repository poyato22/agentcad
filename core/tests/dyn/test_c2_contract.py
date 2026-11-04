"""The exporter against C2's assembly contract (Plan 10 shared contract, shapes copied from
p10-c2's mises/assembly/model.py): couplings in degrees and millimetres with an `offset`
(value = offset + ratio x partner), loops with `type` (revolute / ball / fixed) and `drivers`,
`joint_a` / `joint_b` instance ids, and `design.actuator` {item_id, ratio}."""
import math
from typing import Literal

import mujoco
import pytest
from pydantic import BaseModel, Field

from mises.assembly.model import Assembly, Frame, Instance, Joint, JointRef
from mises.dyn import export, mjmodel

from . import quad


# ---- C2-shaped models (p10-c2 core/src/mises/assembly/model.py)

class Coupling(BaseModel):
    kind: Literal["gear", "belt", "rack", "screw"]
    ratio: float
    partner_joint: str
    offset: float = 0.0


class JointDesign(BaseModel):
    actuator: dict | None = None


class C2Joint(Joint):
    coupling: Coupling | None = None
    design: JointDesign | None = None


class Loop(BaseModel):
    id: str = "loop1"
    kind: Literal["loop"] = "loop"
    joint_a: str
    joint_b: str
    frame_a: Frame
    frame_b: Frame
    type: Literal["revolute", "ball", "fixed"] = "revolute"
    drivers: list[str] | None = None


class C2Assembly(Assembly):
    joints: list[C2Joint] = Field(default_factory=list)
    loops: list[Loop] = Field(default_factory=list)


@pytest.fixture
def home(tmp_path):
    quad.write_parts(tmp_path)
    return tmp_path


Z = Frame(origin=[0, 0, 0], axis=[0, 0, 1])
X = Frame(origin=[0, 0, 0], axis=[1, 0, 0])


def _two(kind_a, kind_b, coupling, value_a=0.0, value_b=0.0, coupled="b"):
    """Joint `ja` (base -> p1) and `jb` (base -> p2) on a fixed base; one is coupled."""
    ja = C2Joint(id="ja", kind=kind_a, value=value_a, a=JointRef(instance_id="base",
                 frame=Z if kind_a == "revolute" else X),
                 b=JointRef(instance_id="p1", frame=Z if kind_a == "revolute" else X))
    jb = C2Joint(id="jb", kind=kind_b, value=value_b, a=JointRef(instance_id="base",
                 frame=Z if kind_b == "revolute" else X),
                 b=JointRef(instance_id="p2", frame=Z if kind_b == "revolute" else X))
    if coupled == "b":
        jb.coupling = coupling
    else:
        ja.coupling = coupling
    return C2Assembly(instances=[Instance(id="base", part_id="quad_torso", name="base", fixed=True),
                                 Instance(id="p1", part_id="quad_fl_thigh", name="p1"),
                                 Instance(id="p2", part_id="quad_fl_shank", name="p2")],
                      joints=[ja, jb])


def _drive(ex, actuator: str, target: float):
    ex.model["gravity"] = [0, 0, 0]
    m, d = mjmodel.compile_model(ex.model)
    a = m.actuator(actuator).id
    for _ in range(4000):
        d.ctrl[a] = target
        mujoco.mj_step(m, d)
    return {m.joint(i).name: float(d.qpos[m.jnt_qposadr[i]]) for i in range(m.njnt)}


@pytest.mark.parametrize("stored_b", [25.0, 0.0])
def test_gear_coupling_with_offset_in_degrees(home, stored_b):
    """value_b = 30 - 0.5 x value_a (deg); the follower's stored value may be off it."""
    asm = _two("revolute", "revolute", Coupling(kind="gear", ratio=-0.5, partner_joint="ja",
                                                offset=30.0), value_a=10.0, value_b=stored_b)
    ex = export.export(home, None, asm)
    eq = next(e for e in ex.model["equalities"] if e["type"] == "joint")
    assert eq["polycoef"][1] == pytest.approx(-0.5)
    assert eq["polycoef"][0] == pytest.approx(math.radians(25.0 - stored_b))
    q = _drive(ex, "a_ja", math.radians(40.0))
    assert math.degrees(q["j_ja"]) == pytest.approx(40.0, abs=0.5)
    assert math.degrees(q["j_jb"]) == pytest.approx(30.0 - 0.5 * math.degrees(q["j_ja"]),
                                                    abs=0.3)
    assert (abs(25.0 - stored_b) > 1e-9) == any("off its coupling" in n for n in ex.notes)


def test_rack_follows_pinion_in_mm_per_degree(home):
    asm = _two("revolute", "slider", Coupling(kind="rack", ratio=0.2, partner_joint="ja"))
    ex = export.export(home, None, asm)
    eq = next(e for e in ex.model["equalities"] if e["type"] == "joint")
    assert eq["polycoef"][1] == pytest.approx(0.2 * 1e-3 / (math.pi / 180))   # m per rad
    q = _drive(ex, "a_ja", 0.5)
    assert q["j_jb"] * 1000 == pytest.approx(0.2 * math.degrees(q["j_ja"]), abs=0.05)


def test_revolute_follows_slider_in_degrees_per_mm(home):
    asm = _two("slider", "revolute", Coupling(kind="screw", ratio=5.0, partner_joint="ja"))
    ex = export.export(home, None, asm)
    eq = next(e for e in ex.model["equalities"] if e["type"] == "joint")
    assert eq["polycoef"][1] == pytest.approx(5.0 * (math.pi / 180) / 1e-3)    # rad per m
    q = _drive(ex, "a_ja", 0.005)
    assert math.degrees(q["j_jb"]) == pytest.approx(5.0 * q["j_ja"] * 1000, abs=0.3)
    assert math.degrees(q["j_jb"]) == pytest.approx(25.0, abs=0.6)


def _quad_c2(loops):
    base = quad.assembly()
    js = [C2Joint(**j.model_dump()) for j in base.joints]
    return C2Assembly(instances=base.instances, joints=js, loops=loops)


def test_fixed_loop_is_a_weld_and_drivers_leave_the_loop_path_passive(home):
    kf = Frame(origin=[130, 0, -200], axis=[0, 1, 0])
    pin = Loop(joint_a="fl_shank", joint_b="fr_shank", frame_a=kf, frame_b=kf,
               drivers=["fl_hip"])
    weld = Loop(id="l2", joint_a="hl_shank", joint_b="hr_shank", frame_a=kf, frame_b=kf,
                type="fixed")
    ex = export.export(home, None, _quad_c2([pin, weld]))
    types = sorted(e["type"] for e in ex.model["equalities"])
    assert types == ["connect", "weld"]
    acts = set(ex.map["actuators"])
    # loop fl_shank..fr_shank runs fl_knee, fl_hip, fr_hip, fr_knee: only fl_hip drives it
    assert "a_fl_hip" in acts
    assert not {"a_fl_knee", "a_fr_hip", "a_fr_knee"} & acts
    # the welded hind pair: its default driver is the path joint nearest the root
    hind = {"a_hl_hip", "a_hl_knee", "a_hr_hip", "a_hr_knee"} & acts
    assert len(hind) == 1 and hind <= {"a_hl_hip", "a_hr_hip"}
    m, d = mjmodel.compile_model(ex.model)          # it compiles and holds together
    for _ in range(200):
        mujoco.mj_step(m, d)


def test_design_actuator_item_id_is_read(home):
    asm = _quad_c2([])
    asm.joints[1].design = JointDesign(actuator={"item_id": "dynamixel_xm430_w350", "ratio": 1})
    ex = export.export(home, None, asm)
    a = ex.map["actuators"][f"a_{asm.joints[1].id}"]
    assert a["actuator"] == "dynamixel_xm430_w350"


def test_kinematics_pose_carries_the_loops(home):
    kf = Frame(origin=[130, 0, -200], axis=[0, 1, 0])
    asm = _quad_c2([Loop(joint_a="fl_shank", joint_b="fr_shank", frame_a=kf, frame_b=kf)])
    seen = []
    import mises.dyn.export as ex_mod
    real = ex_mod.solve

    def spy(a, **kw):
        seen.append(len(getattr(a, "loops", []) or []))
        return real(a, **kw)
    ex_mod.solve = spy
    try:
        kin = export.kinematics(home, None, asm)
        kin["pose"]({"fl_hip": 10.0})
    finally:
        ex_mod.solve = real
    assert seen and all(n == 1 for n in seen)


def test_kinematics_marks_a_loops_passive_joints_and_the_power_tools_skip_them(home):
    # the TVC build: electronics_estimate counted the gimbal rings and pushrod pins (loop
    # followers) as seven MG996R servos; only the loop's driver is actuated
    from mises.engineering.eng_tools import _perfs
    kf = Frame(origin=[130, 0, -200], axis=[0, 1, 0])
    asm = _quad_c2([Loop(joint_a="fl_shank", joint_b="fr_shank", frame_a=kf, frame_b=kf,
                         drivers=["fl_hip"])])
    kin = export.kinematics(home, None, asm)
    passive = {j["id"] for j in kin["tree"] if j["passive"]}
    assert passive == {"fl_knee", "fr_hip", "fr_knee"}
    perfs = _perfs(kin, {})
    assert "fl_hip" in perfs and not passive & set(perfs)
    assert "fr_hip" in _perfs(kin, {"fr_hip": "servo_sg90_micro"})   # named: counted


def test_a_loops_passive_joints_are_bare_pins_in_the_model(home):
    # a TVC gimbal's rings got the ideal-servo armature (0.005 kg m2, 12x the motor) and a
    # 0.02 N m s damper, which alone stalled its micro servos
    kf = Frame(origin=[130, 0, -200], axis=[0, 1, 0])
    ex = export.export(home, None, _quad_c2([Loop(joint_a="fl_shank", joint_b="fr_shank",
                                                  frame_a=kf, frame_b=kf,
                                                  drivers=["fl_hip"])]))
    js = {j["name"]: j for b in ex.model["bodies"] for j in b["joints"]}
    for name in ("j_fl_knee", "j_fr_hip", "j_fr_knee"):
        assert js[name]["armature"] == export.PASSIVE_ARMATURE
        assert js[name]["damping"] == export.PASSIVE_DAMPING
    assert js["j_fl_hip"]["armature"] > export.PASSIVE_ARMATURE
