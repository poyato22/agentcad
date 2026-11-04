"""The dynamics engine on hand-checkable models (Plan 10 C3, research §6.4): a static 2-link
arm (inverse dynamics and joint reactions exact), a rectangle-foot box (support margin and
tip angle vs geometry), a four-bar loop (equality connect) and a coupled gear pair."""
import math

import numpy as np
import pytest

from mises.dyn import engine

G = 9.81


def _box_inertia(m, x, y, z):
    return [[m * (y * y + z * z) / 12, 0, 0], [0, m * (x * x + z * z) / 12, 0],
            [0, 0, m * (x * x + y * y) / 12]]


def arm_model():
    """Spike A: l1 (0.5 kg, com 0.1 m) on a hinge about y at the world point (0, 0, 1); l2 at
    x = 0.2 (0.3 kg box at 0.075 + 0.2 kg sphere at 0.15: 0.5 kg, com 0.105 m)."""
    return {"timestep": 0.001, "floor": None, "bodies": [
        {"name": "l1", "parent": None, "pos": [0, 0, 1], "mass": 0.5, "com": [0.1, 0, 0],
         "inertia": _box_inertia(0.5, 0.2, 0.04, 0.04),
         "joints": [{"name": "j1", "type": "hinge", "axis": [0, 1, 0], "pos": [0, 0, 0]}],
         "geoms": [{"name": "g1", "size": [0.1, 0.02, 0.02], "pos": [0.1, 0, 0]}]},
        {"name": "l2", "parent": "l1", "pos": [0.2, 0, 0], "mass": 0.5, "com": [0.105, 0, 0],
         "inertia": _box_inertia(0.5, 0.15, 0.04, 0.04),
         "joints": [{"name": "j2", "type": "hinge", "axis": [0, 1, 0], "pos": [0, 0, 0]}],
         "geoms": [{"name": "g2", "size": [0.075, 0.02, 0.02], "pos": [0.075, 0, 0]}]}],
        "actuators": [{"name": "a1", "joint": "j1", "kp": 400, "kv": 5},
                      {"name": "a2", "joint": "j2", "kp": 400, "kv": 5}]}


def test_two_link_arm_inverse_dynamics_matches_hand_formula():
    out = engine.run({"task": "torque", "model": arm_model(),
                      "trajectory": {"kind": "hold", "duration_s": 0.2}})
    by = {j["joint"]: j for j in out["joints"]}
    assert by["j1"]["peak_inverse"] == pytest.approx(G * (0.5 * 0.1 + 0.5 * 0.305), rel=1e-6)
    assert by["j2"]["peak_inverse"] == pytest.approx(G * 0.5 * 0.105, rel=1e-6)
    assert by["j1"]["peak_inverse"] == pytest.approx(1.9865, abs=1e-4)
    assert by["j2"]["peak_inverse"] == pytest.approx(0.515, abs=1e-3)
    assert out["free_base"] is False


def test_two_link_arm_reactions_exact_at_the_anchors():
    out = engine.run({"task": "torque", "model": arm_model(), "reactions": True,
                      "trajectory": {"kind": "hold", "duration_s": 0.1, "settle_s": 0.5}})
    # a stiff servo holds the pose with tiny sag; reactions are the static ones to <0.5%
    r = {x["body"]: x for x in out["reactions"]}
    assert r["l1"]["peak_force_n"] == pytest.approx(9.81, rel=5e-3)
    assert r["l2"]["peak_force_n"] == pytest.approx(4.905, rel=5e-3)
    # the moment at a hinge about y is the torque the actuator holds (about the axis)
    assert abs(r["l1"]["moment_nm"][1]) == pytest.approx(1.9865, rel=5e-3)
    assert abs(r["l2"]["moment_nm"][1]) == pytest.approx(0.515, rel=5e-3)
    # the parent's push on l1 is up (+z), and l2 pushes l1 down at its anchor
    assert r["l1"]["force_n"][2] == pytest.approx(9.81, rel=5e-3)
    child = r["l1"]["children"][0]
    assert child["body"] == "l2" and child["force_n"][2] == pytest.approx(-4.905, rel=5e-3)


def box_model(x=0.2, y=0.1, z=0.05, mass=1.0):
    return {"timestep": 0.001, "floor": {"z": 0.0, "friction": 1.0}, "bodies": [
        {"name": "box", "parent": None, "pos": [0, 0, z / 2], "free": True, "mass": mass,
         "com": [0, 0, 0], "inertia": _box_inertia(mass, x, y, z), "joints": [],
         "geoms": [{"name": "b", "size": [x / 2, y / 2, z / 2], "foot": True}]}],
        "actuators": []}


def test_stability_margin_of_a_rectangle_foot_box_matches_geometry():
    out = engine.run({"task": "stability", "model": box_model(), "tilt": False})
    assert out["standing"] is True
    # CoM over the centre of a 200 x 100 mm footprint: 50 mm to the nearest edge
    assert out["support_margin_m"] == pytest.approx(0.05, abs=1e-3)
    assert out["com_height_m"] == pytest.approx(0.025, abs=1e-3)
    assert out["static_tip_angle_deg"] == pytest.approx(math.degrees(math.atan2(0.05, 0.025)),
                                                        abs=1.0)
    assert out["floor_force_n"] == pytest.approx(9.81, rel=0.02)


def test_tilt_test_of_a_tall_box_tips_near_its_static_angle():
    # 60 x 60 mm footprint, 240 mm tall: static tip angle atan(30/120) = 14 deg; friction 1.0
    out = engine.run({"task": "stability", "model": box_model(0.06, 0.06, 0.24),
                      "tilt": True, "tilt_max_deg": 25})
    tip = out["tilt_test"]["min_tip_deg"]
    assert tip is not None and abs(tip - out["static_tip_angle_deg"]) <= 5.0


def fourbar_model(a=0.04, b=0.12, c=0.08, dd=0.10):
    """Spike fourbar: crank a, coupler b, rocker c, ground d in the XZ plane, closed by an
    equality connect at the coupler tip."""
    t1 = math.radians(60)

    def rocker(t):
        P = np.array([a * math.cos(t), a * math.sin(t)])
        O4 = np.array([dd, 0.0])
        r = np.linalg.norm(P - O4)
        base = math.atan2((P - O4)[1], (P - O4)[0])
        return base + math.acos((c * c + r * r - b * b) / (2 * c * r))
    t4 = rocker(t1)
    P = np.array([a * math.cos(t1), a * math.sin(t1)])
    R = np.array([dd + c * math.cos(t4), c * math.sin(t4)])
    t2 = math.atan2((R - P)[1], (R - P)[0])
    rot = lambda v, t: [v * math.cos(t), 0.0, v * math.sin(t)]  # noqa: E731
    small = [[1e-6, 0, 0], [0, 1e-6, 0], [0, 0, 1e-6]]
    model = {"timestep": 0.0005, "gravity": [0, 0, 0], "floor": None, "bodies": [
        {"name": "crank", "parent": None, "pos": [0, 0, 0], "mass": 0.02,
         "com": rot(a / 2, t1), "inertia": small,
         "joints": [{"name": "q1", "type": "hinge", "axis": [0, -1, 0], "ref": t1,
                     "armature": 5e-4, "damping": 1e-3}], "geoms": []},
        {"name": "coupler", "parent": "crank", "pos": rot(a, t1), "mass": 0.05,
         "com": rot(b / 2, t2), "inertia": small,
         "joints": [{"name": "q2", "type": "hinge", "axis": [0, -1, 0], "ref": 0.0,
                     "armature": 5e-4, "damping": 1e-3}], "geoms": []},
        {"name": "rocker", "parent": None, "pos": [dd, 0, 0], "mass": 0.03,
         "com": rot(c / 2, t4), "inertia": small,
         "joints": [{"name": "q4", "type": "hinge", "axis": [0, -1, 0], "ref": t4,
                     "armature": 5e-4, "damping": 1e-3}], "geoms": []}],
        "actuators": [{"name": "drive", "joint": "q1", "kp": 50, "kv": 1}],
        "equalities": [{"type": "connect", "body1": "coupler", "body2": "rocker",
                        "anchor": rot(b, t2)}]}
    return model, rocker, t1


def test_fourbar_loop_closure_follows_freudenstein():
    model, rocker, t1 = fourbar_model()
    out = engine.run({"task": "build", "model": model})
    assert out["loop_residual_m"] < 1e-6
    out = engine.run({"task": "torque", "model": model, "trajectory": {
        "kind": "keyframes", "keys": [{"t": 2.0, "q": {"q1": t1 + math.pi / 2}}]}})
    # the drive reached its target; the rocker followed the closed loop
    assert out["joints"][0]["max_tracking_error"] < math.radians(5)


def test_fourbar_ideal_torque_follows_the_closed_loop_not_a_torn_one():
    # the inverse dynamics of a loop mechanism must use poses where the loop is closed: with
    # only the crank moved the passive joints stayed put and the 'ideal' torque read 2262 N m
    # against a 0.0045 N m servo (the TVC gimbal demo read 3077 N m on a micro servo)
    model, rocker, t1 = fourbar_model()
    out = engine.run({"task": "torque", "model": model, "trajectory": {
        "kind": "keyframes", "keys": [{"t": 2.0, "q": {"q1": t1 + math.pi / 2}}]}})
    j = out["joints"][0]
    assert j["peak_inverse"] < 5 * j["peak_servo"] + 1e-3, (j["peak_inverse"], j["peak_servo"])


def test_coupled_joints_follow_their_ratio():
    """A gear coupling (C2 contract) exported as an equality joint polycoef: q2 = -2 q1."""
    small = [[1e-5, 0, 0], [0, 1e-5, 0], [0, 0, 1e-5]]
    model = {"timestep": 0.001, "gravity": [0, 0, 0], "floor": None, "bodies": [
        {"name": "g1", "parent": None, "pos": [0, 0, 0], "mass": 0.05, "com": [0, 0, 0],
         "inertia": small, "joints": [{"name": "j1", "type": "hinge", "axis": [0, 0, 1]}],
         "geoms": []},
        {"name": "g2", "parent": None, "pos": [0.05, 0, 0], "mass": 0.05, "com": [0, 0, 0],
         "inertia": small, "joints": [{"name": "j2", "type": "hinge", "axis": [0, 0, 1]}],
         "geoms": []}],
        "actuators": [{"name": "a1", "joint": "j1", "kp": 5, "kv": 0.1}],
        "equalities": [{"type": "joint", "joint1": "j2", "joint2": "j1",
                        "polycoef": [0, -2.0, 0, 0, 0]}]}
    from mises.dyn import mjmodel
    import mujoco
    m, d = mjmodel.compile_model(model)
    for _ in range(2000):
        d.ctrl[0] = 0.5
        mujoco.mj_step(m, d)
    assert d.qpos[0] == pytest.approx(0.5, abs=0.02)
    assert d.qpos[1] == pytest.approx(-1.0, abs=0.03)


def test_rom_sweep_finds_a_self_collision():
    """A two-link arm whose distal link swings into a post on the base (MuJoCo skips a body
    and its parent, so the post is checked against the second link only)."""
    small = [[1e-4, 0, 0], [0, 1e-4, 0], [0, 0, 1e-4]]
    model = {"timestep": 0.001, "floor": None, "bodies": [
        {"name": "base", "parent": None, "pos": [0, 0, 0], "mass": 1.0, "com": [0, 0, 0],
         "inertia": small, "joints": [],
         "geoms": [{"name": "post", "size": [0.01, 0.01, 0.05], "pos": [0.1, 0.0, 0.05]}]},
        {"name": "hub", "parent": "base", "pos": [0, 0, 0.05], "mass": 0.05,
         "com": [0, 0, 0], "inertia": small,
         "joints": [{"name": "j", "type": "hinge", "axis": [0, 0, 1],
                     "range": [-math.pi / 2, math.pi / 2]}], "geoms": []},
        {"name": "link", "parent": "hub", "pos": [0, 0, 0], "mass": 0.1,
         "com": [0, 0.05, 0], "inertia": small,
         "joints": [{"name": "k", "type": "hinge", "axis": [1, 0, 0], "range": [0, 0.001]}],
         "geoms": [{"name": "bar", "size": [0.01, 0.06, 0.01], "pos": [0, 0.07, 0]}]}],
        "actuators": []}
    out = engine.run({"task": "rom", "model": model, "n": 200, "joints": ["j"]})
    assert out["colliding_pairs"] == 1 and out["rest_contacts"] == []
    hit = out["collisions"][0]
    assert sorted(hit["bodies"]) == ["base", "link"]
    assert hit["pose"]["j"] < -0.5        # the bar (+y) swings towards +x at a negative angle
    # counted in poses, never more than were tried (the TVC sweep read "6367 of 2000 poses":
    # every contact point counted, and the tally restarted at each deeper hit)
    assert 0 < hit["count"] <= 200
    out = engine.run({"task": "rom", "model": model, "n": 200, "joints": ["j"]})
    assert out["colliding_pairs"] == 1    # deterministic


def test_swinging_link_inverse_dynamics_matches_closed_form():
    """A dynamic known answer: one link (0.4 kg, com 0.1 m out, I_com 0.002 kg m2) lifted from
    horizontal by 1 rad over 1 s on a cosine profile. tau(t) = (I_com + m d^2) alpha +
    m g d cos(q), with q = 0.5 (1 - cos(pi t)), alpha = 0.5 pi^2 cos(pi t)."""
    m_, d, ic = 0.4, 0.1, 0.002
    model = {"timestep": 0.001, "floor": None, "bodies": [
        {"name": "link", "parent": None, "pos": [0, 0, 0], "mass": m_, "com": [d, 0, 0],
         "inertia": [[1e-4, 0, 0], [0, ic, 0], [0, 0, ic]],
         "joints": [{"name": "j", "type": "hinge", "axis": [0, -1, 0], "armature": 0.0,
                     "damping": 0.0}],
         "geoms": [{"name": "g", "size": [0.1, 0.01, 0.01], "pos": [0.1, 0, 0]}]}],
        "actuators": [{"name": "a", "joint": "j", "kp": 300, "kv": 5}]}
    out = engine.run({"task": "torque", "model": model, "trajectory": {
        "kind": "keyframes", "keys": [{"t": 1.0, "q": {"j": 1.0}}]}})
    s = out["joints"][0]["series"]
    checked = 0
    for t, tau in zip(s["t"], s["inverse"]):
        if not 0.1 < t < 0.9:
            continue
        q = 0.5 * (1 - math.cos(math.pi * t))
        alpha = 0.5 * math.pi ** 2 * math.cos(math.pi * t)
        want = (ic + m_ * d * d) * alpha + m_ * G * d * math.cos(q)
        assert tau == pytest.approx(want, rel=0.01, abs=2e-3), t
        checked += 1
    assert checked > 50
