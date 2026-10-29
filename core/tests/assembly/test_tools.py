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
