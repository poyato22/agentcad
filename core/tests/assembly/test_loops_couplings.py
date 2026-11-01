"""Plan 10 shared contract: coupled joints (gear / belt / rack / screw) and loop-closure
constraints (closed chains), solved by assembly/solve.py and stored by the AssemblyStore."""
import math

import pytest

from mises.assembly.model import (Assembly, AssemblyStore, Coupling, Frame, Instance, Joint,
                                  JointRef, Loop)
from mises.assembly.solve import solve, transform_point
from mises.types import MisesError

A, B, C, D = 40.0, 120.0, 80.0, 100.0      # crank, coupler, rocker, ground (a Grashof crank-rocker)


def _ref(iid, origin, axis=(0, 0, 1)):
    return JointRef(instance_id=iid, frame=Frame(origin=list(origin), axis=list(axis)))


def rocker_analytic(t2_deg: float, branch: int = 1) -> float:
    """Freudenstein closure of the four-bar: the rocker angle (deg) for a crank angle, on one of
    its two assembly branches."""
    t2 = math.radians(t2_deg)
    px, py = A * math.cos(t2), A * math.sin(t2)
    dx, dy = px - D, py
    r = math.hypot(dx, dy)
    gam = math.acos((C * C + r * r - B * B) / (2 * C * r))
    return math.degrees(math.atan2(dy, dx) + branch * gam)


def coupler_analytic(t2_deg: float, branch: int = 1) -> float:
    """The crank-to-coupler joint value that closes the loop on that branch."""
    t2, t4 = math.radians(t2_deg), math.radians(rocker_analytic(t2_deg, branch))
    px, py = A * math.cos(t2), A * math.sin(t2)
    rx, ry = D + C * math.cos(t4), C * math.sin(t4)
    return math.degrees(math.atan2(ry - py, rx - px)) - t2_deg


def _same_angle(a, b, tol):
    return abs(((a - b + 180) % 360) - 180) < tol


def four_bar(crank_deg: float, guess_coupler=10.0, guess_rocker=90.0) -> Assembly:
    inst = [Instance(id="g", part_id="ground", name="g", fixed=True),
            Instance(id="crank", part_id="crank", name="crank"),
            Instance(id="coupler", part_id="coupler", name="coupler"),
            Instance(id="rocker", part_id="rocker", name="rocker")]
    joints = [Joint(id="j_in", kind="revolute", a=_ref("g", (0, 0, 0)), b=_ref("crank", (0, 0, 0)),
                    value=crank_deg),
              Joint(id="j_cc", kind="revolute", a=_ref("crank", (A, 0, 0)),
                    b=_ref("coupler", (0, 0, 0)), value=guess_coupler),
              Joint(id="j_r", kind="revolute", a=_ref("g", (D, 0, 0)), b=_ref("rocker", (0, 0, 0)),
                    value=guess_rocker)]
    loop = Loop(id="L", joint_a="coupler", joint_b="rocker", frame_a=Frame(origin=[B, 0, 0],
                axis=[0, 0, 1]), frame_b=Frame(origin=[C, 0, 0], axis=[0, 0, 1]),
                drivers=["j_in"])
    return Assembly(instances=inst, joints=joints, loops=[loop])


@pytest.mark.parametrize("crank", [0.0, 37.0, 90.0, 180.0, 275.0])
def test_four_bar_loop_matches_freudenstein(crank):
    # a pose a few degrees off the closed one (as after the driver moved) closes on its branch
    res = solve(four_bar(crank, guess_coupler=coupler_analytic(crank) + 6.0,
                         guess_rocker=rocker_analytic(crank) + 6.0))
    assert not res.loop_errors
    assert _same_angle(res.applied["j_r"], rocker_analytic(crank), 0.01)
    # the coupler's far end really sits on the rocker's far end
    pa = transform_point(res.transforms["coupler"], [B, 0, 0])
    pb = transform_point(res.transforms["rocker"], [C, 0, 0])
    assert math.dist(pa, pb) < 1e-4
    assert res.applied["j_in"] == crank          # the driver is held
    assert "j_r" in res.solved and "j_in" not in res.solved


def test_loop_that_cannot_close_is_reported():
    asm = four_bar(0.0)
    # a coupler too short to ever reach the rocker
    asm.loops[0] = asm.loops[0].model_copy(update={"frame_a": Frame(origin=[5, 0, 0],
                                                                     axis=[0, 0, 1])})
    res = solve(asm)
    assert res.loop_errors and res.loop_errors[0]["loop_id"] == "L"
    assert "cannot close" in res.loop_errors[0]["reason"]


def test_loops_count_in_the_dof():
    res = solve(four_bar(10.0))
    # spatial count: 3 bodies x 6 - 3 revolute x 5 - 1 revolute loop x 5 (planar overconstraint)
    assert res.dof_remaining == 0


def _pair(ratio=-0.5, offset=0.0, partner="j1"):
    inst = [Instance(id="g", part_id="frame", name="g", fixed=True),
            Instance(id="p", part_id="pinion", name="p"),
            Instance(id="w", part_id="wheel", name="w")]
    j1 = Joint(id="j1", kind="revolute", a=_ref("g", (0, 0, 0)), b=_ref("p", (0, 0, 0)),
               value=30.0)
    j2 = Joint(id="j2", kind="revolute", a=_ref("g", (30, 0, 0)), b=_ref("w", (0, 0, 0)),
               value=0.0, coupling=Coupling(kind="gear", ratio=ratio, partner_joint=partner,
                                            offset=offset))
    return Assembly(instances=inst, joints=[j1, j2])


def test_gear_coupling_drives_the_follower():
    res = solve(_pair())
    assert res.applied["j2"] == pytest.approx(-15.0)
    assert res.solved == {"j2": pytest.approx(-15.0)}
    assert res.dof_remaining == 6 * 2 - 10 - 1


def test_coupling_offset_and_chain():
    asm = _pair(offset=5.0)
    inst = [*asm.instances, Instance(id="o", part_id="out", name="o")]
    j3 = Joint(id="j3", kind="revolute", a=_ref("g", (60, 0, 0)), b=_ref("o", (0, 0, 0)),
               coupling=Coupling(kind="belt", ratio=2.0, partner_joint="j2"))
    res = solve(Assembly(instances=inst, joints=[j3, *asm.joints]))   # follower listed first
    assert res.applied["j2"] == pytest.approx(5.0 - 15.0)
    assert res.applied["j3"] == pytest.approx(-20.0)


def test_coupling_cycle_is_refused():
    asm = _pair()
    j1 = asm.joints[0].model_copy(update={"coupling": Coupling(kind="gear", ratio=-2,
                                                               partner_joint="j2")})
    with pytest.raises(MisesError, match="cycle"):
        solve(Assembly(instances=asm.instances, joints=[j1, asm.joints[1]]))


def test_rack_coupling_moves_a_slider():
    inst = [Instance(id="g", part_id="frame", name="g", fixed=True),
            Instance(id="p", part_id="pinion", name="p"),
            Instance(id="r", part_id="rack", name="r")]
    r_mm_per_deg = 10.0 * math.pi / 180      # pitch radius 10 mm
    joints = [Joint(id="jp", kind="revolute", a=_ref("g", (0, 0, 0)), b=_ref("p", (0, 0, 0)),
                    value=90.0),
              Joint(id="jr", kind="slider", a=_ref("g", (0, -10, 0), (1, 0, 0)),
                    b=_ref("r", (0, 0, 0), (1, 0, 0)),
                    coupling=Coupling(kind="rack", ratio=-r_mm_per_deg, partner_joint="jp"))]
    res = solve(Assembly(instances=inst, joints=joints))
    assert transform_point(res.transforms["r"], [0, 0, 0])[0] == pytest.approx(-15.708, abs=1e-3)


def test_only_revolute_or_slider_joints_couple():
    with pytest.raises(ValueError):
        Joint(kind="ball", a=_ref("a", (0, 0, 0)), b=_ref("b", (0, 0, 0)),
              coupling=Coupling(kind="gear", ratio=1, partner_joint="x"))


# ---- storage
def test_store_round_trips_couplings_design_and_loops(tmp_path):
    st = AssemblyStore(tmp_path / "a.db")
    asm = four_bar(20.0)
    for i in asm.instances:
        st.add_instance("p", i)
    for j in asm.joints:
        st.add_joint("p", Joint.model_validate({**j.model_dump(), "design": {
            "bearing": "bushing", "clearance_mm": 0.3}}))
    st.add_loop("p", asm.loops[0])
    pair = _pair()
    st.add_instance("q", pair.instances[0])
    st.add_instance("q", pair.instances[1])
    st.add_instance("q", pair.instances[2])
    for j in pair.joints:
        st.add_joint("q", j)
    got = st.get("p")
    assert got.loops[0].drivers == ["j_in"] and got.loops[0].frame_a.origin == [B, 0, 0]
    assert got.joints[0].design.bearing == "bushing"
    q = st.get("q")
    assert q.joints[1].coupling.ratio == -0.5 and q.joints[0].coupling is None
    # payloads leave the new fields out when unset
    assert "coupling" not in q.joints[0].model_dump() and "design" not in q.joints[0].model_dump()
    assert q.joints[1].model_dump()["coupling"]["partner_joint"] == "j1"
    # deleting the partner un-couples the follower; deleting a body drops its loop
    st.delete_joint("q", "j1")
    assert st.get("q").joints[0].coupling is None
    st.delete_instance("p", "rocker")
    assert st.get("p").loops == []


def test_take_out_and_put_back_keep_loops(tmp_path):
    st = AssemblyStore(tmp_path / "a.db")
    asm = four_bar(20.0)
    for i in asm.instances:
        st.add_instance("p", i)
    for j in asm.joints:
        st.add_joint("p", j)
    st.add_loop("p", asm.loops[0])
    undo = st.take_out("p", {"rocker"})
    assert st.get("p").loops == [] and len(undo["loops"]) == 1
    st.put_back("p", undo)
    assert [lp.id for lp in st.get("p").loops] == ["L"]


def test_old_database_gets_the_new_columns(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("CREATE TABLE assembly_joints (project_id TEXT, id TEXT, kind TEXT, a TEXT,"
                     " b TEXT, value TEXT, limits TEXT, ord INTEGER, PRIMARY KEY (project_id, id));")
    db.close()
    st = AssemblyStore(tmp_path / "old.db")
    pair = _pair()
    for i in pair.instances:
        st.add_instance("q", i)
    for j in pair.joints:
        st.add_joint("q", j)
    assert st.get("q").joints[1].coupling.kind == "gear"


def test_set_values_stores_solved_values(tmp_path):
    st = AssemblyStore(tmp_path / "a.db")
    asm = four_bar(20.0)
    for i in asm.instances:
        st.add_instance("p", i)
    for j in asm.joints:
        st.add_joint("p", j)
    st.add_loop("p", asm.loops[0])
    res = solve(st.get("p"))
    st.set_values("p", res.solved)
    again = solve(st.get("p"))
    # stored: the next solve starts closed and stays put
    assert again.applied["j_r"] == pytest.approx(res.applied["j_r"], abs=1e-6)
    assert any(_same_angle(res.applied["j_r"], rocker_analytic(20.0, b), 0.01) for b in (1, -1))


# ---- tools
@pytest.fixture
def ctx(tmp_path):
    from types import SimpleNamespace

    from mises.events import EventBus
    store = AssemblyStore(tmp_path / "assembly.db")
    return SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                           run_id=None)


def _load(ctx, asm, loops=True):
    st = ctx.services["assembly"]
    for i in asm.instances:
        st.add_instance("p", i)
    for j in asm.joints:
        st.add_joint("p", j)
    if loops:
        for lp in asm.loops:
            st.add_loop("p", lp)
    return st


async def test_add_loop_tool_closes_and_refuses(ctx):
    from mises.assembly.tools import AddLoopArgs, assembly_add_loop
    st = _load(ctx, four_bar(30.0, guess_coupler=coupler_analytic(30.0) + 3,
                             guess_rocker=rocker_analytic(30.0) + 3), loops=False)
    bad = AddLoopArgs(project_id="p", a=_ref("coupler", (5, 0, 0)), b=_ref("rocker", (C, 0, 0)))
    with pytest.raises(MisesError, match="cannot close"):
        await assembly_add_loop.handler(ctx, bad)
    ok = AddLoopArgs(project_id="p", a=_ref("coupler", (B, 0, 0)), b=_ref("rocker", (C, 0, 0)),
                     drivers=["j_in"])
    r = await assembly_add_loop.handler(ctx, ok)
    assert r.ok and set(r.data["solved"]) == {"j_cc", "j_r"}
    # the stored values are now the closed pose
    assert _same_angle(st.get_joint("p", "j_r").value, rocker_analytic(30.0), 0.01)


async def test_drive_moves_followers_and_animate_reports_them(ctx):
    from mises.assembly.tools import AnimateArgs, DriveArgs, assembly_animate, assembly_drive
    st = _load(ctx, four_bar(30.0, guess_coupler=coupler_analytic(30.0),
                             guess_rocker=rocker_analytic(30.0)))
    r = await assembly_drive.handler(ctx, DriveArgs(project_id="p", joint_id="j_in", value=40.0))
    assert set(r.data["followers"]) == {"j_cc", "j_r"}
    assert _same_angle(st.get_joint("p", "j_r").value, rocker_analytic(40.0), 0.01)
    a = await assembly_animate.handler(ctx, AnimateArgs(project_id="p", joint_id="j_in",
                                                        to=400.0, keyframes=9))
    step = a.data["animation"]
    follow = {f["joint_id"]: f["values"] for f in step["follow"]}
    assert set(follow) == {"j_cc", "j_r"} and len(follow["j_r"]) == 9
    for k, v in enumerate(follow["j_r"]):           # one full crank turn, on one branch
        assert _same_angle(v, rocker_analytic(40.0 + 45.0 * k), 0.05)


async def test_add_joint_tool_takes_a_coupling(ctx):
    from mises.assembly.tools import AddJointArgs, assembly_add_joint
    asm = _pair()
    st = _load(ctx, Assembly(instances=asm.instances, joints=[asm.joints[0]]))
    with pytest.raises(MisesError, match="no joint"):
        await assembly_add_joint.handler(ctx, AddJointArgs(
            project_id="p", kind="revolute", a=_ref("g", (30, 0, 0)), b=_ref("w", (0, 0, 0)),
            coupling=Coupling(kind="gear", ratio=-0.5, partner_joint="nope")))
    r = await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_ref("g", (30, 0, 0)), b=_ref("w", (0, 0, 0)),
        coupling=Coupling(kind="gear", ratio=-0.5, partner_joint="j1"),
        design={"bearing": "bearing_608_deep_groove", "retention": "snap ring"}))
    jid = r.data["joint"]["id"]
    assert st.get_joint("p", jid).value == pytest.approx(-15.0)
    assert st.get_joint("p", jid).design.retention == "snap ring"
