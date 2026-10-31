"""Plan 7 R16: announcements come from tool results in the core, whatever the model says."""
import pytest

import mises.runtime as runtime_mod
from mises import announce
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.types import StepKind, ToolOutput


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]] * 4)
    return Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "m"))


def ok(**data):
    return ToolOutput(summary="done", data=data)


def test_part_created_only_for_new_parts():
    a = announce.from_tool_result("mirror_part_create", {"part_id": "plate"},
                                  ok(part_id="plate", version=1))
    assert a == {"kind": "part_created", "title": "Created part plate",
                 "ref": {"part_id": "plate", "thumb_url": "/parts/plate/thumb.png"}}
    assert announce.from_tool_result("mirror_part_create", {}, ok(part_id="p", version=3)) is None
    assert announce.from_tool_result("catalog_insert", {"item_id": "m3"},
                                     ok(part_id="m3_screw", existing=True)) is None
    a = announce.from_tool_result("mcp__mises__catalog_insert", {"item_id": "m3"},
                                  ok(part_id="m3_screw", existing=False))
    assert a["kind"] == "part_created" and a["ref"]["catalog_id"] == "m3"


def test_topopt_accept_new_part_gets_a_card_and_is_auto_placed():
    # FW-core I-5: a generative-design variant accepted as a NEW part is a delivered part, so it
    # gets a part_created card AND an auto_placed follow-up, like mirror_part_create.
    out = ok(part_id="block_generative", version=1,
             placed={"project_id": "p1", "instance": {"id": "i1", "part_id": "block_generative",
                                                       "name": "Block Generative", "fixed": True}})
    anns = announce.all_from_tool_result("topopt_accept", {}, out)
    kinds = [a["kind"] for a in anns]
    assert kinds == ["part_created", "assembly_added"]
    assert anns[0]["ref"]["thumb_url"] == "/parts/block_generative/thumb.png"
    assert anns[1]["ref"]["instance_id"] == "i1"


def test_topopt_accept_replace_says_nothing():
    # mode=replace bumps the version and does not auto-place: no new-part card, no placement
    assert announce.all_from_tool_result("topopt_accept", {}, ok(part_id="block", version=3)) == []


def test_failed_or_unknown_tools_say_nothing():
    assert announce.from_tool_result("mirror_part_create", {},
                                     ToolOutput(ok=False, summary="x", data={})) is None
    assert announce.from_tool_result("mirror_measure", {}, ok(part_id="p")) is None


def test_assembly_added_mapping():
    a = announce.from_tool_result("assembly_add_part", {"part_id": "arm"}, ok(
        project_id="b1", instance={"id": "i2", "part_id": "arm", "fixed": False},
        joint={"id": "j1", "kind": "revolute", "target": "i1"}))
    assert a["kind"] == "assembly_added" and a["title"] == "Added arm to the assembly"
    assert a["detail"] == "revolute joint to i1"
    assert a["ref"] == {"project_id": "b1", "instance_id": "i2", "part_id": "arm",
                        "joint_id": "j1"}


def test_bom_and_part_shown_and_drawing_mappings():
    a = announce.from_tool_result("bom_add", {"name": "MG996R servo", "qty": 4},
                                  ok(item={"id": "bom1", "kind": "bought", "qty": 4}))
    assert a["kind"] == "bom_added" and a["title"].startswith("Added 4 × bought")
    assert a["detail"] == "MG996R servo" and a["ref"]["bom_id"] == "bom1"
    a = announce.from_tool_result("part_show", {}, ok(card={"name": "Servo", "card_id": "c1"}))
    assert a["kind"] == "part_shown" and a["ref"] == {"card_id": "c1"}
    assert a["detail"] == ""                    # page-derived names never reach the detail
    a = announce.from_tool_result("part_show", {"name": "MG996R"}, ok(card={"card_id": "c1"}))
    assert a["detail"] == "MG996R"
    # drawing_imported comes from the import itself (imports/drawing_import.py), not a mapper
    for name in ("drawing_import", "drawing_import_build"):
        assert announce.from_tool_result(name, {}, ok(import_id="imp1")) is None


def test_titles_are_clipped_and_kinds_checked():
    d = announce.make("r", "part_created", "x" * 500, "y\n" * 500)
    assert len(d["title"]) == announce.TITLE_CHARS and "\n" not in d["detail"]
    with pytest.raises(ValueError):
        announce.make("r", "nonsense", "t")


def test_plan8_view_and_critique_kinds_are_accepted_and_stored(rt):
    # Plan 8 G1 routes its cards through announce(): same signature, stored as steps
    from mises.events import EventBus
    rid = rt.store.create_run("designer", "fake", "m", "x").id
    bus = EventBus()
    sub = bus.subscribe()
    for kind, ref in (("view_presented", {"view_spec": {"preset": "iso"}}),
                      ("critique_round", {"round": 1, "max_rounds": 3, "score": 0.8})):
        announce.announce(bus, rt.store, rid, kind, "t", ref=ref)
        assert sub.queue.get_nowait().data["kind"] == kind
    stored = [s.payload["agent_announce"] for s in rt.store.steps(rid)
              if "agent_announce" in s.payload]
    assert [a["kind"] for a in stored] == ["view_presented", "critique_round"]
    assert stored[1]["ref"]["score"] == 0.8


async def _run(rt, **kw):
    rid = await rt.start_run("designer", "go", **kw)
    await rt.wait(rid)
    return rid


async def test_gate_announces_on_the_actor_and_its_lead(rt):
    lead = await _run(rt)
    sub = await _run(rt, parent_id=lead)
    project = rt.store.get_run(lead).project_id
    rt.store.register_part("arm", project)
    events = rt.events.subscribe()
    out = await rt.gate.call(sub, "assembly_add_part", {"part_id": "arm"}, call_id="c1",
                             workdir=rt.workdir(sub))
    assert out.ok
    got = []
    while not events.queue.empty():
        ev = events.queue.get_nowait()
        if ev.type == "agent_announce":
            got.append(ev)
    assert [e.run_id for e in got] == [sub, lead]
    assert all(e.data["run_id"] == sub and e.data["kind"] == "assembly_added" for e in got)
    for rid in (sub, lead):
        steps = [s.payload["agent_announce"] for s in rt.store.steps(rid)
                 if s.kind == StepKind.DECISION and "agent_announce" in s.payload]
        assert steps and steps[-1]["title"] == "Added arm to the assembly"


async def test_ui_calls_are_not_announced(rt):
    lead = await _run(rt)
    project = rt.store.get_run(lead).project_id
    rt.store.register_part("arm", project)
    events = rt.events.subscribe()
    call = rt.ui_tool_call("assembly_add_part", {"project_id": project, "part_id": "arm"},
                           project_id=project)
    await call.task
    assert call.status == "done", call.error
    types = []
    while not events.queue.empty():
        types.append(events.queue.get_nowait().type)
    assert "assembly_changed" in types and "agent_announce" not in types


async def test_a_broken_mapper_never_fails_the_call(rt, monkeypatch):
    lead = await _run(rt)
    rt.store.register_part("arm", rt.store.get_run(lead).project_id)
    monkeypatch.setitem(announce.MAPPERS, "assembly_add_part",
                        lambda a, d: (_ for _ in ()).throw(RuntimeError("boom")))
    out = await rt.gate.call(lead, "assembly_add_part", {"part_id": "arm"}, call_id="c1",
                             workdir=rt.workdir(lead))
    assert out.ok


def test_an_auto_placed_part_is_announced_as_added_too():
    """Plan 9 D10: one create result → part_created (with its render) + assembly_added."""
    inst = {"id": "i1", "part_id": "plate", "name": "plate", "fixed": True}
    got = announce.all_from_tool_result(
        "mirror_part_create", {"part_id": "plate"},
        ok(part_id="plate", version=1, placed={"project_id": "b1", "instance": inst}))
    assert [a["kind"] for a in got] == ["part_created", "assembly_added"]
    assert got[0]["ref"]["thumb_url"] == "/parts/plate/thumb.png"
    assert got[1]["ref"] == {"project_id": "b1", "instance_id": "i1", "part_id": "plate"}
    # not placed (place: false, or a rebuild): only the part card
    assert [a["kind"] for a in announce.all_from_tool_result(
        "mirror_part_create", {}, ok(part_id="plate", version=1))] == ["part_created"]
    assert announce.all_from_tool_result("mirror_part_create", {},
                                         ToolOutput(ok=False, summary="x")) == []


BOX = "VARS = {'s': 10.0}\n\ndef build(v):\n    return Box(v['s'], v['s'], v['s'])\n"


@pytest.mark.slow
@pytest.mark.timeout(240)
async def test_an_agents_new_part_lands_in_the_assembly(rt):
    lead = await _run(rt)
    project = rt.store.get_run(lead).project_id
    events = rt.events.subscribe()
    for pid in ("cube_a", "cube_b"):
        out = await rt.gate.call(lead, "mirror_part_create", {"part_id": pid, "script": BOX},
                                 call_id=f"c-{pid}", workdir=rt.workdir(lead))
        assert out.ok, out.summary
        assert out.data["placed"]["project_id"] == project
    asm = rt.assembly_store.get(project)
    assert [i.part_id for i in asm.instances] == ["cube_a", "cube_b"]
    assert asm.instances[0].fixed and not asm.instances[1].fixed
    assert asm.instances[1].transform[0][3] >= 10        # beside the first cube, not inside it
    kinds, changed = [], 0
    while not events.queue.empty():
        ev = events.queue.get_nowait()
        if ev.type == "agent_announce":
            kinds.append(ev.data["kind"])
        changed += ev.type == "assembly_changed"
    assert kinds == ["part_created", "assembly_added"] * 2 and changed == 2
    # place: false opts out
    out = await rt.gate.call(lead, "mirror_part_create",
                             {"part_id": "cube_c", "script": BOX, "place": False},
                             call_id="c3", workdir=rt.workdir(lead))
    assert out.ok and "placed" not in out.data
    assert len(rt.assembly_store.get(project).instances) == 2
