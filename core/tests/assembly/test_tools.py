from types import SimpleNamespace

import pytest

from mises.assembly.model import AssemblyStore
from mises.assembly.tools import (AddInstanceArgs, AddJointArgs, DriveArgs, ProjectArgs,
                                   RemoveArgs, assembly_add_instance, assembly_add_joint,
                                   assembly_drive, assembly_get, assembly_remove,
                                   register_assembly_tools)
from mises.events import EventBus
from mises.tools.registry import ToolRegistry
from mises.types import MisesError, SafetyClass


@pytest.fixture
def ctx(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    return SimpleNamespace(workdir=tmp_path, events=EventBus(),
                           services={"assembly": store})


def _frame(iid):
    return {"instance_id": iid, "frame": {"origin": [0, 0, 0], "axis": [0, 0, 1]}}


async def test_add_instance_emits_changed(ctx):
    sub = ctx.events.subscribe()
    r = await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="base", fixed=True))
    assert r.ok and r.data["instance"]["fixed"] is True
    ev = await sub.queue.get()
    assert ev.type == "assembly_changed" and ev.data["project_id"] == "p"


async def test_add_joint_and_drive(ctx):
    a = await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="base", fixed=True))
    b = await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="flap"))
    aid, bid = a.data["instance"]["id"], b.data["instance"]["id"]
    j = await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(aid), b=_frame(bid)))
    jid = j.data["joint"]["id"]
    r = await assembly_drive.handler(ctx, DriveArgs(project_id="p", joint_id=jid, value=90.0))
    assert r.ok
    # flap pivots about +Z: a +x offset point goes to +y
    t = r.data["transforms"][bid]
    pt = [sum(t[i][k] * ([1, 0, 0, 1][k]) for k in range(4)) for i in range(3)]
    assert pt == pytest.approx([0, 1, 0], abs=1e-9)


async def test_add_joint_rejects_loop(ctx):
    ids = []
    for name in ("a", "b", "c"):
        r = await assembly_add_instance.handler(
            ctx, AddInstanceArgs(project_id="p", part_id="plate", name=name,
                                 fixed=(name == "a")))
        ids.append(r.data["instance"]["id"])
    await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(ids[0]), b=_frame(ids[1])))
    await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(ids[1]), b=_frame(ids[2])))
    with pytest.raises(MisesError, match="loop"):
        await assembly_add_joint.handler(ctx, AddJointArgs(
            project_id="p", kind="revolute", a=_frame(ids[2]), b=_frame(ids[0])))
    # the rejected joint was not written
    assert len(ctx.services["assembly"].get("p").joints) == 2


async def test_remove_requires_exactly_one_target(ctx):
    with pytest.raises(MisesError, match="exactly one"):
        await assembly_remove.handler(ctx, RemoveArgs(project_id="p"))


async def test_get_reports_dof(ctx):
    await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="base", fixed=True))
    r = await assembly_get.handler(ctx, ProjectArgs(project_id="p"))
    assert r.data["dof_remaining"] == 0  # the only instance is fixed


def test_tool_safety_classes():
    reg = ToolRegistry()
    register_assembly_tools(reg)
    names = {t.name: t.safety for t in reg.list()}
    assert names["assembly_get"] == SafetyClass.READ
    assert names["assembly_check"] == SafetyClass.READ
    assert names["assembly_add_instance"] == SafetyClass.LOCAL_MUTATE
    assert names["assembly_drive"] == SafetyClass.LOCAL_MUTATE


async def test_traversing_part_id_rejected_by_tool(ctx):
    from pydantic import ValidationError

    from mises.assembly.model import Instance
    with pytest.raises(ValidationError, match="part_id"):
        AddInstanceArgs(project_id="p", part_id="../x", name="evil")
    # the stored model refuses it too, whatever the entry point
    with pytest.raises(ValidationError):
        Instance(part_id="../x", name="evil")
    assert ctx.services["assembly"].get("p").instances == []


async def test_remove_missing_instance_is_not_found(ctx):
    from mises.types import ErrorCode
    with pytest.raises(MisesError) as e:
        await assembly_remove.handler(ctx, RemoveArgs(project_id="p", instance_id="ghost"))
    assert e.value.code == ErrorCode.NOT_FOUND


async def test_agent_run_cannot_touch_another_builds_assembly(tmp_path):
    """I8: an agent run may only change its own build's assembly, and the build must exist."""
    from mises.store import Store
    from mises.types import ErrorCode

    store = Store(":memory:")
    a = store.create_project("A")
    b = store.create_project("B")
    store.register_part("plate", a.id)
    run = store.create_run("designer", "fake", "m", "t", project_id=a.id)
    asm = AssemblyStore(tmp_path / "assembly.db")
    rctx = SimpleNamespace(workdir=tmp_path, events=EventBus(), store=store, run_id=run.id,
                           call_id="c", policy=None, services={"assembly": asm})
    # its own build is fine
    r = await assembly_add_instance.handler(
        rctx, AddInstanceArgs(project_id=a.id, part_id="plate", name="base", fixed=True))
    assert r.ok
    # its own build still reads fine
    assert (await assembly_get.handler(rctx, ProjectArgs(project_id=a.id))).ok
    # P11 F5: leaving project_id out (as the out-of-scope hint says) is its own build, for
    # every assembly tool (the schema no longer requires it)
    got = await assembly_get.handler(rctx, ProjectArgs())
    assert got.ok and [i["name"] for i in got.data["instances"]] == ["base"]
    assert (await assembly_add_instance.handler(
        rctx, AddInstanceArgs(part_id="plate", name="second"))).ok
    assert "project_id" not in ProjectArgs.model_json_schema().get("required", [])
    # another build is refused
    with pytest.raises(MisesError) as e:
        await assembly_add_instance.handler(
            rctx, AddInstanceArgs(project_id=b.id, part_id="plate", name="x"))
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_check_reports_overconstraint(ctx):
    from mises.assembly.tools import assembly_check
    a = await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="a", fixed=True))
    b = await assembly_add_instance.handler(
        ctx, AddInstanceArgs(project_id="p", part_id="plate", name="b", fixed=True))
    r = await assembly_check.handler(ctx, ProjectArgs(project_id="p"))
    assert r.data["overconstrained"] == []  # two fixed, no joint path: independent
    aid, bid = a.data["instance"]["id"], b.data["instance"]["id"]
    await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(aid), b=_frame(bid)))
    r = await assembly_check.handler(ctx, ProjectArgs(project_id="p"))
    assert r.data["overconstrained"] == [sorted([aid, bid])]
    assert "over-constrained" in r.summary


async def test_remove_a_whole_mech_group_at_once(ctx):
    store = ctx.services["assembly"]
    ids = []
    for name in ("base", "arm", "other"):
        r = await assembly_add_instance.handler(
            ctx, AddInstanceArgs(project_id="p", part_id="plate", name=name,
                                 fixed=(name == "base")))
        ids.append(r.data["instance"]["id"])
    await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(ids[0]), b=_frame(ids[1])))
    store.save_mech("p", "g1", {"instances": {"base": ids[0], "arm": ids[1]}})
    r = await assembly_remove.handler(ctx, RemoveArgs(project_id="p", mech_group="g1"))
    assert "2 instance(s)" in r.summary
    asm = store.get("p")
    assert [i.id for i in asm.instances] == [ids[2]] and not asm.joints
    assert store.list_mechs("p") == []
    with pytest.raises(MisesError, match="nothing to remove"):
        await assembly_remove.handler(ctx, RemoveArgs(project_id="p", mech_group="g1"))
