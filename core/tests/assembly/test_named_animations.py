"""Plan 9 G-D (D11): assembly_animate can save a named animation (kept per build, so a view card
can replay it) and present it as a view card."""
from types import SimpleNamespace

import pytest

from mises.assembly.model import AssemblyStore
from mises.assembly.tools import (AddJointArgs, AddPartArgs, AnimateArgs, assembly_add_joint,
                                  assembly_add_part, assembly_animate)
from mises.events import EventBus
from mises.types import ToolOutput


@pytest.fixture
def ctx(tmp_path):
    store = AssemblyStore(tmp_path / "assembly.db")
    return SimpleNamespace(workdir=tmp_path, events=EventBus(), services={"assembly": store},
                           run_id=None)


def _frame(iid, axis=(0, 0, 1)):
    return {"instance_id": iid, "frame": {"origin": [0, 0, 0], "axis": list(axis)}}


async def _hinge(ctx):
    a = (await assembly_add_part.handler(ctx, AddPartArgs(project_id="p", part_id="base"))
         ).data["instance"]["id"]
    r = await assembly_add_part.handler(ctx, AddPartArgs(project_id="p", part_id="arm"))
    b = r.data["instance"]["id"]
    ctx.services["assembly"].delete_joint("p", r.data["joint"]["id"])
    j = await assembly_add_joint.handler(ctx, AddJointArgs(
        project_id="p", kind="revolute", a=_frame(a), b=_frame(b), limits=[0, 180]))
    return j.data["joint"]["id"]


def test_store_keeps_named_animations_per_build(tmp_path):
    store = AssemblyStore(tmp_path / "a.db")
    a = store.save_animation("p", "open lid", {"steps": [{"joint_id": "j", "from": 0, "to": 90,
                                                          "duration_s": 2}], "loops": 1})
    again = store.save_animation("p", "open lid", {"steps": [], "loops": 3})
    assert again["id"] == a["id"] and store.get_animation("p", a["id"])["loops"] == 3
    assert store.get_animation("p", "open lid")["id"] == a["id"]
    with pytest.raises(KeyError):
        store.get_animation("other", a["id"])           # another build's never leaks
    assert [x["name"] for x in store.list_animations("p")] == ["open lid"]
    store.purge("p")
    assert store.list_animations("p") == []


async def test_animate_with_a_name_saves_it_and_says_its_id(ctx):
    jid = await _hinge(ctx)
    r = await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "joint_id": jid, "from": 0, "to": 90, "duration_s": 3, "loops": 2,
         "name": "lid swing"}))
    aid = r.data["animation_id"]
    saved = ctx.services["assembly"].get_animation("p", aid)
    assert saved["name"] == "lid swing" and saved["loops"] == 2 and saved["duration_s"] == 3
    assert saved["steps"] == [{"joint_id": jid, "from": 0, "to": 90, "duration_s": 3}]
    assert r.data["animation"]["animation_id"] == aid and aid in r.summary
    # unnamed: nothing stored
    r2 = await assembly_animate.handler(ctx, AnimateArgs(project_id="p", joint_id=jid, to=10))
    assert "animation_id" not in r2.data
    assert len(ctx.services["assembly"].list_animations("p")) == 1


async def test_animate_present_returns_a_view_card(ctx, monkeypatch):
    jid = await _hinge(ctx)
    from mises.cad import views
    seen = []

    async def fake_present(c, args):
        seen.append(args)
        return ToolOutput(summary="presented", data={"view_spec": {"kind": args.kind}})
    monkeypatch.setattr(views, "present_view", SimpleNamespace(handler=fake_present))
    r = await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "joint_id": jid, "to": 90, "present": True, "caption": "the lid"}))
    assert len(seen) == 1 and seen[0].kind == "animation" and seen[0].caption == "the lid"
    # presenting saves it (under a default name): the card replays the stored motion
    assert seen[0].animation.animation_id == r.data["animation_id"]
    assert r.data["view_spec"] == {"kind": "animation"}


async def test_animate_present_without_built_parts_still_animates(ctx):
    jid = await _hinge(ctx)
    sub = ctx.events.subscribe()
    r = await assembly_animate.handler(ctx, AnimateArgs.model_validate(
        {"project_id": "p", "joint_id": jid, "to": 90, "present": True}))
    assert r.ok and any("card" in w for w in r.warnings)
    assert sub.queue.get_nowait().type == "assembly_animate"
