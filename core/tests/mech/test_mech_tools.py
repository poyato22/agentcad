"""Plan 10 C2 tools: mech_list, mech_insert (rows, couplings, loops, the group record),
mech_check (E27 design rules, mobility, loop closure, the ROM sweep), drivetrain_plan and
material_pick. Part building is replaced by a fake here; the slow test builds for real."""
import json
from types import SimpleNamespace

import pytest

from mises.assembly.model import AssemblyStore, Frame, Instance, Joint, JointRef
from mises.assembly.solve import solve
from mises.events import EventBus
from mises.mech import tools as mt
from mises.types import MisesError, SafetyClass


@pytest.fixture
def ctx(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    return SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                           run_id=None, store=None)


@pytest.fixture
def fake_build(monkeypatch):
    calls = []

    async def build(ctx, pid, mech_id, gid, plan, parts_root):
        calls.append((mech_id, [p.key for p in plan.parts]))
        return {p.key: {"part_id": mt._pid_base(mech_id, p.key) + "_" + gid[:4].replace("0", "o")
                        .translate(str.maketrans("123456789", "abcdefghi")),
                        "existing": False, "meta": {"label": p.label}} for p in plan.parts}
    monkeypatch.setattr(mt, "_build_parts", build)
    return calls


async def test_mech_list_ranks_by_function_and_shows_params(ctx):
    out = await mt.mech_list.handler(ctx, mt.ListArgs(function="convert rotation to linear"))
    ids = [r["id"] for r in out.data["mechanisms"]]
    assert {"rack_pinion", "lead_screw_axis", "slider_crank", "belt_linear_axis"} & set(ids[:6])
    assert "params" in out.data["mechanisms"][0] and out.data["total"] >= 40


async def test_mech_insert_writes_instances_joints_couplings_and_the_record(ctx, fake_build):
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="spur_pair",
                                                          params={"z1": 18, "z2": 54}))
    st = ctx.services["assembly"]
    asm = st.get("p")
    gid = out.data["group"]
    card = out.data["card"]
    assert len(asm.instances) == 7 and len(asm.joints) == 6
    root = next(i for i in asm.instances if i.fixed)
    assert root.id == card["instance_ids"]["frame"]
    follower = next(j for j in asm.joints if j.coupling)
    assert follower.coupling.ratio == pytest.approx(-18 / 54)
    assert follower.coupling.partner_joint == card["joint_ids"]["in_rev"]
    rec = st.get_mech("p", gid)
    assert rec["mech_id"] == "spur_pair" and rec["params"]["z1"] == 18
    assert rec["inputs"] == [card["joint_ids"]["in_rev"]]
    assert card["sizing"]["ratio"] == 3.0 and card["ports"][0]["instance_id"]
    # driving the input turns the output by the ratio
    from mises.assembly.tools import DriveArgs, assembly_drive
    r = await assembly_drive.handler(ctx, DriveArgs(project_id="p",
                                                    joint_id=card["joint_ids"]["in_rev"],
                                                    value=30.0))
    assert r.data["followers"][follower.id] == pytest.approx(-10.0)


async def test_mech_insert_closed_chain_and_attach_to(ctx, fake_build):
    st = ctx.services["assembly"]
    st.add_instance("p", Instance(id="chassis", part_id="chassis_plate", name="chassis",
                                  fixed=True))
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(
        project_id="p", mech_id="four_bar", attach_to="chassis",
        at={"origin": [50, 0, 10], "axis": [0, 0, 1]}))
    asm = st.get("p")
    assert len(asm.loops) == 1
    ground = out.data["card"]["instance_ids"]["ground"]
    attach = next(j for j in asm.joints if j.b.instance_id == ground and j.kind == "fixed")
    assert attach.a.instance_id == "chassis"
    assert not next(i for i in asm.instances if i.id == ground).fixed
    res = solve(asm)
    assert not res.loop_errors
    assert res.transforms[ground][0][3] == pytest.approx(50.0)


async def test_mech_insert_refuses_bad_params(ctx, fake_build):
    with pytest.raises(MisesError, match="outside"):
        await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="spur_pair",
                                                        params={"module": 99}))
    assert fake_build == []


def _hinge_asm(design):
    return [Instance(id="a", part_id="plate_a", name="a", fixed=True),
            Instance(id="b", part_id="plate_b", name="b")], \
        Joint(id="h", kind="revolute",
              a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[1, 0, 0])),
              b=JointRef(instance_id="b", frame=Frame(origin=[0, 0, 0], axis=[1, 0, 0])),
              limits=[0, 90], design=design)


async def test_design_rules_fail_a_revolute_without_bearing_or_retention(ctx):
    st = ctx.services["assembly"]
    inst, j = _hinge_asm(None)
    for i in inst:
        st.add_instance("p", i)
    st.add_joint("p", j)
    out = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", sweep=False))
    fails = [r for r in out.data["results"] if r["status"] == "fail"]
    assert any(r["check"] == "bearing" for r in fails)
    st.update_joint("p", "h", design={"bearing": "bushing", "clearance_mm": 0.8})
    out = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", sweep=False))
    checks = {(r["check"], r["status"]) for r in out.data["results"]}
    assert ("retention", "fail") in checks and ("clearance", "warn") in checks
    st.update_joint("p", "h", design={"bearing": "bushing", "clearance_mm": 0.3,
                                      "retention": "e-clip", "fastener": "M3",
                                      "shaft": {"d_mm": 3}})
    out = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", sweep=False))
    assert out.data["counts"]["fail"] == 0


async def test_mech_check_on_a_group_reports_mobility_sizing_and_loops(ctx, fake_build,
                                                                      monkeypatch):
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="slider_crank"))
    gid = out.data["group"]
    seen = {}

    async def fake_sweep(ctx_, pid, asm, rec, steps):
        seen["inputs"] = rec["inputs"]
        return [mt._check("rom_sweep", "pass", "swept")]
    monkeypatch.setattr(mt, "_rom_sweep", fake_sweep)
    res = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", group=gid))
    by = {r["check"]: r for r in res.data["results"]}
    assert by["mobility"]["status"] == "pass"
    assert by["loop_closure"]["status"] == "pass"
    assert by["rom_sweep"]["status"] == "pass" and seen["inputs"]
    assert res.data["counts"]["fail"] == 0
    with pytest.raises(MisesError, match="no mechanism group"):
        await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", group="nope"))


async def test_drivetrain_plan_finds_options_with_margin(ctx):
    out = await mt.drivetrain_plan.handler(ctx, mt.PlanArgs(torque_nm=6.0, speed_rpm=20.0))
    rows = out.data["options"]
    assert rows and all(r["peak_output_nm"] >= 6.0 * 1.5 for r in rows)
    assert all(r["speed_at_load_rpm"] >= 20.0 for r in rows)
    big = await mt.drivetrain_plan.handler(ctx, mt.PlanArgs(torque_nm=5000.0))
    assert big.data["options"] == [] and big.warnings
    lock = await mt.drivetrain_plan.handler(ctx, mt.PlanArgs(torque_nm=1.0, speed_rpm=1.0,
                                                             self_locking=True))
    assert {r["mechanism"] for r in lock.data["options"]} <= {"worm_drive", None}


async def test_material_pick_ranks_and_refuses_apply_on_read_only(ctx):
    out = await mt.material_pick.handler(ctx, mt.MaterialArgs(
        requirements={"priority": "strength", "printable": False, "form": "shaft"}))
    assert out.data["ranked"][0]["name"] in ("Al 7075-T6", "Steel 4140 QT")
    ro = SimpleNamespace(**vars(ctx), policy=SimpleNamespace(allowed_classes={SafetyClass.READ}))
    with pytest.raises(MisesError, match="only read"):
        await mt.material_pick.handler(ro, mt.MaterialArgs(part_id="arm", apply=True))


def test_scripts_only_carry_literals():
    src = mt._script("spur_pair", "gear", "gear_spur", {"module": 1.0, "teeth": 20,
                                                         "bores": [[0, 0, 8.0]]})
    assert "ms.gear_spur(**ARGS)" in src and json.loads(src.split("json.loads(")[1].split(")")[0]
                                                        .strip("'"))["teeth"] == 20
    with pytest.raises(MisesError):
        mt._script("x", "y", "gear_spur", {"teeth": "20'); import os; os.system('x"})
    with pytest.raises(MisesError):
        mt._script("x", "y", "__import__", {})


def test_part_ids_are_valid_names():
    from mises.cad.tools import PART_ID_RE
    from mises.naming import check_name
    for key in ("planet2_0", "frame", "in_bearing", "jaw_a", "x9"):
        pid = mt._pid_base("planetary_compound", key)
        assert PART_ID_RE.fullmatch(pid)
        check_name(pid)


def test_tools_are_registered_with_their_safety(tmp_path):
    from mises.runtime import Runtime
    rt = Runtime(tmp_path / "home")
    reg = {t.name: t for t in rt.registry.list()}
    assert reg["mech_list"].safety == SafetyClass.READ
    assert reg["mech_check"].safety == SafetyClass.READ
    assert reg["drivetrain_plan"].safety == SafetyClass.READ
    assert reg["material_pick"].safety == SafetyClass.READ
    assert reg["mech_insert"].safety == SafetyClass.LOCAL_MUTATE
    assert reg["assembly_add_loop"].safety == SafetyClass.LOCAL_MUTATE


@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_gear_pair_builds_in_the_sandbox_and_sweeps_clean(ctx):
    # py_gearworks and bd_warehouse run inside the sandboxed worker (ms.gear_spur, catalog 608)
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="spur_pair"))
    parts = out.data["parts"]
    assert parts["bearing_608_deep_groove"] == "bearing_608_deep_groove"
    for pid in parts.values():
        assert (ctx.workdir / "parts" / pid / "model.step").exists(), pid
    res = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", group=out.data["group"],
                                                        steps=4))
    sweep = [r for r in res.data["results"] if r["check"] == "rom_sweep"]
    assert sweep and all(r["status"] == "pass" for r in sweep), sweep


@pytest.mark.slow
@pytest.mark.timeout(120)
async def test_mech_insert_builds_real_parts_in_the_sandbox(ctx):
    out = await mt.mech_insert.handler(ctx, mt.InsertArgs(project_id="p", mech_id="hinge"))
    parts = out.data["parts"]
    for pid in parts.values():
        assert (ctx.workdir / "parts" / pid / "model.step").exists(), pid
    gid = out.data["group"]
    res = await mt.mech_check.handler(ctx, mt.CheckArgs(project_id="p", group=gid, steps=5))
    sweep = [r for r in res.data["results"] if r["check"] == "rom_sweep"]
    assert sweep and all(r["status"] != "fail" for r in sweep), sweep
