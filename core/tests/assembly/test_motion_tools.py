"""Plan 7 R11 / R19: assembly_add_part, assembly_move, assembly_animate and the interference
sweep of assembly_check."""
import json
from types import SimpleNamespace

import pytest

from mises.assembly import tools as asm_tools
from mises.assembly.geometry import GeometryResult
from mises.assembly.model import AssemblyStore
from mises.assembly.tools import (AddJointArgs, AddPartArgs, AnimateArgs, CheckArgs, MoveArgs,
                                  assembly_add_joint, assembly_add_part, assembly_animate,
                                  assembly_check, assembly_move, register_assembly_tools)
from mises.events import EventBus
from mises.tools.registry import ToolRegistry
from mises.types import MisesError, SafetyClass


@pytest.fixture
def ctx(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    return SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                           run_id=None)


def _pt(t, p):
    return [sum(t[i][k] * (list(p) + [1])[k] for k in range(4)) for i in range(3)]


def _metrics(tmp_path, part_id, faces):
    d = tmp_path / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metrics.json").write_text(json.dumps({"face_list": faces}))


async def _add(ctx, part_id, **place):
    r = await assembly_add_part.handler(ctx, AddPartArgs(project_id="p", part_id=part_id,
                                                         place=place))
    assert r.ok, r.summary
    return r


def _frame(iid, axis=(0, 0, 1), origin=(0, 0, 0)):
    return {"instance_id": iid, "frame": {"origin": list(origin), "axis": list(axis)}}


# ---- assembly_add_part
async def test_first_part_is_grounded_without_a_joint(ctx):
    r = await _add(ctx, "base", offset=[0, 0, 5])
    inst = r.data["instance"]
    assert inst["fixed"] is True and r.data["joint"] is None
    assert inst["transform"][2][3] == 5
    assert "grounded" in r.summary


async def test_next_part_gets_a_default_fixed_joint_to_the_grounded_one(ctx):
    base = (await _add(ctx, "base")).data["instance"]["id"]
    r = await _add(ctx, "arm", offset=[10, 0, 0])
    j = r.data["joint"]
    assert j["kind"] == "fixed" and j["a"]["instance_id"] == base
    assert j["target"] == base
    assert _pt(r.data["world_transform"], [0, 0, 0]) == pytest.approx([10, 0, 0])
    asm = ctx.services["assembly"].get("p")
    assert len(asm.instances) == 2 and len(asm.joints) == 1


async def test_on_face_sits_one_face_on_another(ctx, tmp_path):
    # base: top face at z=5 facing +z; lid: bottom face at z=-2 facing -z
    _metrics(tmp_path, "base", [{"id": "f1", "center": [0, 0, 5], "normal": [0, 0, 1]}])
    _metrics(tmp_path, "lid", [{"id": "f4", "center": [0, 0, -2], "normal": [0, 0, -1]}])
    await _add(ctx, "base")
    r = await _add(ctx, "lid", on_face="f4", mate={"face_id": "f1"})
    t = r.data["world_transform"]
    # the lid's bottom face centre lands on the base's top face centre, lid above it
    assert _pt(t, [0, 0, -2]) == pytest.approx([0, 0, 5], abs=1e-9)
    assert _pt(t, [0, 0, 0])[2] == pytest.approx(7)


async def test_add_part_with_a_revolute_mate(ctx):
    await _add(ctx, "base")
    r = await _add(ctx, "arm", mate={"kind": "revolute", "limits": [-90, 90]})
    assert r.data["joint"]["kind"] == "revolute" and r.data["joint"]["limits"] == [-90, 90]


async def test_add_part_unknown_mate_instance(ctx):
    await _add(ctx, "base")
    with pytest.raises(MisesError, match="no instance"):
        await _add(ctx, "arm", mate={"instance_id": "nope"})


def test_new_tools_registered_with_classes():
    reg = ToolRegistry()
    register_assembly_tools(reg)
    names = {t.name: t.safety for t in reg.list()}
    assert names["assembly_add_part"] == SafetyClass.LOCAL_MUTATE
    assert names["assembly_move"] == SafetyClass.LOCAL_MUTATE
    assert names["assembly_animate"] == SafetyClass.READ


# ---- assembly_move
async def _two_with(ctx, kind, axis=(0, 0, 1), limits=None):
    a = (await _add(ctx, "base")).data["instance"]["id"]
    r = await _add(ctx, "arm")
    b = r.data["instance"]["id"]
    # replace the default fixed joint with the joint under test
    ctx.services["assembly"].delete_joint("p", r.data["joint"]["id"])
    j = await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind=kind, a=_frame(a, axis), b=_frame(b, axis), limits=limits))
    return a, b, j.data["joint"]["id"]


async def test_move_free_instance_translates_and_rotates(ctx):
    a = (await _add(ctx, "base")).data["instance"]["id"]
    r = await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=a,
                                                  translate=[5, 0, 0]))
    assert r.data["joint_id"] is None
    assert _pt(r.data["world_transform"], [0, 0, 0]) == pytest.approx([5, 0, 0])
    r = await assembly_move.handler(ctx, MoveArgs(
        project_id="p", instance_id=a, rotate={"axis": [0, 0, 1], "angle_deg": 90}))
    # rotated about its own origin (5,0,0): its +x point goes to +y
    assert _pt(r.data["world_transform"], [1, 0, 0]) == pytest.approx([5, 1, 0], abs=1e-9)
    r = await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=a,
                                                  translate=[1, 2, 3], relative=False))
    assert _pt(r.data["world_transform"], [0, 0, 0]) == pytest.approx([1, 2, 3])


async def test_attached_parts_follow_a_moved_anchor(ctx):
    a = (await _add(ctx, "base")).data["instance"]["id"]
    b = (await _add(ctx, "arm", offset=[10, 0, 0])).data["instance"]["id"]
    await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=a, translate=[0, 0, 7]))
    asm = ctx.services["assembly"].get("p")
    res = asm_tools.solve(asm)
    assert _pt(res.transforms[b], [0, 0, 0]) == pytest.approx([10, 0, 7])


async def test_slider_moves_only_along_its_axis(ctx):
    _, b, jid = await _two_with(ctx, "slider", axis=(1, 0, 0))
    r = await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=b,
                                                  translate=[4, 3, 0]))
    assert r.data["joint_id"] == jid and r.data["value"] == pytest.approx(4)
    assert _pt(r.data["world_transform"], [0, 0, 0]) == pytest.approx([4, 0, 0], abs=1e-9)
    assert any("off the slider axis" in w for w in r.warnings)
    with pytest.raises(MisesError, match="does not rotate"):
        await assembly_move.handler(ctx, MoveArgs(
            project_id="p", instance_id=b, rotate={"axis": [0, 0, 1], "angle_deg": 5}))


async def test_slider_move_clamped_to_limits(ctx):
    _, b, jid = await _two_with(ctx, "slider", limits=[0, 10])
    r = await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=b,
                                                  translate=[0, 0, 25]))
    assert r.data["value"] == 10 and any("clamped" in w for w in r.warnings)
    assert ctx.services["assembly"].get_joint("p", jid).value == 10


async def test_revolute_turns_only_about_its_axis(ctx):
    _, b, jid = await _two_with(ctx, "revolute")
    r = await assembly_move.handler(ctx, MoveArgs(
        project_id="p", instance_id=b, rotate={"axis": [0, 0, 1], "angle_deg": 30}))
    assert r.data["value"] == pytest.approx(30)
    r = await assembly_move.handler(ctx, MoveArgs(
        project_id="p", instance_id=b, rotate={"axis": [0, 0, -1], "angle_deg": 10}))
    assert r.data["value"] == pytest.approx(20)        # -z axis: the other way
    with pytest.raises(MisesError, match="turns only about the joint axis"):
        await assembly_move.handler(ctx, MoveArgs(
            project_id="p", instance_id=b, rotate={"axis": [1, 0, 0], "angle_deg": 10}))
    with pytest.raises(MisesError, match="does not slide"):
        await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=b,
                                                  translate=[1, 0, 0]))


async def test_fixed_joint_instance_cannot_move(ctx):
    await _add(ctx, "base")
    b = (await _add(ctx, "arm")).data["instance"]["id"]
    with pytest.raises(MisesError, match="fixed joint"):
        await assembly_move.handler(ctx, MoveArgs(project_id="p", instance_id=b,
                                                  translate=[1, 0, 0]))


def test_move_needs_something_to_do():
    with pytest.raises(ValueError):
        MoveArgs(project_id="p", instance_id="x")


# ---- assembly_animate
async def test_animate_emits_event_and_returns_key_poses(ctx):
    _, b, jid = await _two_with(ctx, "revolute")
    sub = ctx.events.subscribe()
    r = await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "joint_id": jid, "from": 0, "to": 90, "duration_s": 3,
         "loop": True, "keyframes": 3}))
    ev = sub.queue.get_nowait()
    assert ev.type == "assembly_animate"
    assert ev.data == {"project_id": "p", "joint_id": jid, "from": 0, "to": 90,
                       "duration_s": 3, "loop": True}
    poses = r.data["poses"]
    assert [p["t"] for p in poses] == [0, 1.5, 3]
    assert [p["value"] for p in poses] == [0, 45, 90]
    assert _pt(poses[-1]["transforms"][b], [1, 0, 0]) == pytest.approx([0, 1, 0], abs=1e-4)
    # nothing stored: the joint keeps its value
    assert ctx.services["assembly"].get_joint("p", jid).value == 0


async def test_animate_sequence(ctx):
    _, _, jid = await _two_with(ctx, "slider", limits=[0, 5])
    sub = ctx.events.subscribe()
    r = await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "sequence": [{"joint_id": jid, "to": 20, "duration_s": 1},
                                         {"joint_id": jid, "to": 0, "duration_s": 1}]}))
    ev = sub.queue.get_nowait()
    assert ev.data["sequence"] == [
        {"joint_id": jid, "from": 0, "to": 5, "duration_s": 1},   # clamped to the limits
        {"joint_id": jid, "from": 5, "to": 0, "duration_s": 1}]
    assert ev.data["loop"] is False
    assert r.data["poses"][-1]["t"] == 2 and len(r.data["poses"]) == 10


def test_animate_args_need_one_form():
    with pytest.raises(ValueError):
        AnimateArgs(project_id="p")
    with pytest.raises(ValueError):
        AnimateArgs(project_id="p", joint_id="j")       # no `to`


async def test_animate_unknown_joint(ctx):
    with pytest.raises(MisesError, match="no joint"):
        await assembly_animate.handler(ctx, AnimateArgs(project_id="p", joint_id="j", to=1))


# ---- assembly_check sweep
async def test_sweep_reports_interference_per_position(ctx, monkeypatch):
    _, b, jid = await _two_with(ctx, "slider", axis=(1, 0, 0))
    seen = []

    async def fake_geometry(root, asm, res, want="both", **kw):
        x = res.transforms[b][0][3]
        seen.append(x)
        # the arm hits the base once it slides within 10 mm of it
        return GeometryResult(interferences=[{"a": "base", "b": "arm", "volume_mm3": 1.0}]
                              if x < 10 else [])

    monkeypatch.setattr(asm_tools, "run_geometry", fake_geometry)
    r = await assembly_check.handler(ctx, CheckArgs.model_validate(
        {"project_id": "p", "sweep": {"joint_id": jid, "from": 40, "to": 0, "steps": 5}}))
    assert seen == pytest.approx([40, 30, 20, 10, 0])
    assert r.data["interfering_positions"] == 1
    assert [p["value"] for p in r.data["positions"] if p["interferences"]] == [0]
    assert "interference at 1 of 5" in r.summary


async def test_sweep_needs_built_parts(ctx, monkeypatch):
    _, _, jid = await _two_with(ctx, "slider")

    async def none_built(*a, **k):
        raise MisesError(asm_tools.ErrorCode.NOT_FOUND, "no built parts in this assembly")

    monkeypatch.setattr(asm_tools, "run_geometry", none_built)
    with pytest.raises(MisesError, match="no built parts"):
        await assembly_check.handler(ctx, CheckArgs.model_validate(
            {"project_id": "p", "sweep": {"joint_id": jid, "to": 5}}))


BOX = ('from build123d import *\nVARS={"s":10.0}\n'
       'def build(v):\n    return Box(v["s"], v["s"], v["s"])\n')


@pytest.mark.slow
@pytest.mark.timeout(120)
async def test_sweep_detects_real_interference(ctx, tmp_path):
    """Two real 10 mm cubes on a slider: apart at 40 mm, overlapping at 5 mm."""
    from mises.cad.tools import CreateArgs, mirror_part_create
    r = await mirror_part_create.handler(SimpleNamespace(workdir=tmp_path),
                                         CreateArgs(part_id="cube", script=BOX))
    assert r.ok, r.summary
    a = (await _add(ctx, "cube")).data["instance"]["id"]
    r = await _add(ctx, "cube", mate={"kind": "slider"})
    ctx.services["assembly"].delete_joint("p", r.data["joint"]["id"])
    j = await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="slider", a=_frame(a, (1, 0, 0)),
        b=_frame(r.data["instance"]["id"], (1, 0, 0))))
    out = await assembly_check.handler(ctx, CheckArgs.model_validate(
        {"project_id": "p", "sweep": {"joint_id": j.data["joint"]["id"], "from": 40, "to": 5,
                                      "steps": 2}}))
    first, last = out.data["positions"]
    assert first["interferences"] == []
    assert len(last["interferences"]) == 1
    assert last["interferences"][0]["volume_mm3"] == pytest.approx(500, rel=0.02)
