"""FWC M7 (E33): the whole-assembly render waiting for the lead's next tool result expires (a
TTL, or a second turn start without a tool call), and a turn-end review never consumes a later
turn's assembly change."""
from mises.agent.loop import RunResult
from mises.types import ToolOutput

from .test_review import BAD, _patch, rt  # noqa: F401 - the fixture


async def _review(rt, monkeypatch, text="I moved the arm; next I will mate it."):  # noqa: F811
    pid = rt.active_project_id()
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    _patch(monkeypatch, BAD)
    rt.mark_assembly_changed(run.id, pid)
    await rt._after_turn(run.id, text)
    return run.id


async def test_a_stale_render_is_not_attached(rt, monkeypatch):  # noqa: F811
    rid = await _review(rt, monkeypatch)
    rt.pending_assembly_images[rid][0] -= rt.PENDING_IMAGES_TTL_S + 1     # long ago
    assert rt.gate._with_assembly_render(rid, ToolOutput(summary="x")).images == []


async def test_the_render_rides_the_next_turn_but_not_the_one_after(rt, monkeypatch):  # noqa: F811
    rid = await _review(rt, monkeypatch)
    rt.new_user_turn(rid)                     # the next turn: still waiting for its tool call
    assert rid in rt.pending_assembly_images
    rt.new_user_turn(rid)                     # that turn made no tool call: dropped
    assert rt.gate._with_assembly_render(rid, ToolOutput(summary="x")).images == []
    rid2 = await _review(rt, monkeypatch)
    rt.new_user_turn(rid2)
    assert len(rt.gate._with_assembly_render(rid2, ToolOutput(summary="x")).images) == 1


async def test_a_new_turns_change_survives_the_previous_review(rt, monkeypatch):  # noqa: F811
    pid = rt.active_project_id()
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    _patch(monkeypatch, BAD)
    rt.mark_assembly_changed(run.id, pid)
    ok = RunResult(final_text="next I will mate it", messages=[], stop_reason="end_turn")
    assert rt._schedule_after_turn(run.id, ok)
    rt.mark_assembly_changed(run.id, pid)     # the next turn changes it before the review runs
    for t in list(rt._after_turn_tasks):
        await t
    assert rt.assembly_touched.get(run.id) == pid     # kept for that turn's own review
