"""Plan 5 C1 fix round 1: deleting runs never strands a live Claude Code session or races a
follow-up message; every run gets a build at creation."""
import asyncio

import pytest

from mises.orchestrator.orchestrator import child_policy
from mises.runtime import _ClaudeRun
from mises.types import ErrorCode, MisesError


class _FakeSession:
    def __init__(self, gate: asyncio.Event | None = None):
        self.closed = 0
        self.alive = True
        self.gate = gate

    async def close(self):
        if self.gate is not None:
            await self.gate.wait()
        self.closed += 1
        self.alive = False


def _finished_claude_run(rt) -> str:
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet", task="hi")
    rt.store.update_run(rid, status="done")      # a top-level run after its turn: session idle
    return rid


async def test_delete_closes_the_idle_claude_session_first(rt):
    rid = _finished_claude_run(rt)
    s = _FakeSession()
    rt._claude[rid] = _ClaudeRun(session=s)
    assert await rt.delete_run(rid) is None
    assert s.closed == 1 and rid not in rt._claude
    with pytest.raises(KeyError):
        rt.store.get_run(rid)


async def test_a_pending_turn_counts_as_running(rt):
    rid = _finished_claude_run(rt)
    turn = asyncio.get_running_loop().create_future()
    rt._claude[rid] = _ClaudeRun(session=_FakeSession(), task=turn)  # type: ignore[arg-type]
    assert await rt.delete_run(rid) == ("running", [])
    assert rt.store.get_run(rid)
    turn.cancel()


async def test_delete_refused_while_a_follow_up_is_still_starting(rt, monkeypatch):
    rid = _finished_claude_run(rt)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_send(run_id, text):      # the turn has not begun: status still "done"
        started.set()
        await release.wait()
    monkeypatch.setattr(rt, "_send", slow_send)
    sending = asyncio.create_task(rt.send(rid, "one more thing"))
    await started.wait()
    assert rt.store.get_run(rid).status == "done"
    assert await rt.delete_run(rid) == ("running", [])
    release.set()
    await sending
    assert rt.store.get_run(rid)


async def test_send_refused_while_the_run_is_being_deleted(rt):
    rid = _finished_claude_run(rt)
    gate = asyncio.Event()
    rt._claude[rid] = _ClaudeRun(session=_FakeSession(gate))
    deleting = asyncio.create_task(rt.delete_run(rid))
    await asyncio.sleep(0)                   # delete is now awaiting the session close
    with pytest.raises(MisesError) as e:
        await rt.send(rid, "hello?")
    assert e.value.code == ErrorCode.NOT_FOUND
    gate.set()
    assert await deleting is None


async def test_every_run_gets_a_build(rt):
    drone = rt.store.create_project("Drone")
    active = rt.active_project_id()
    # external, ui and CLI-style runs: the active build
    ext = rt.create_external_run("codex", provider="acp", model="codex")
    assert rt.store.get_run(ext).project_id == active
    assert rt.store.get_run(rt._ui_run_id()).project_id == active
    # an orchestrator's worker inherits its parent's build
    parent = rt.create_external_run("planner", provider="claude-code", model="sonnet",
                                    project_id=drone.id)
    assert child_policy(rt, parent, "designer")["project_id"] == drone.id
    child = rt.create_external_run("designer", provider="claude-code", model="sonnet",
                                   parent_id=parent)
    assert rt.store.get_run(child).project_id == drone.id


async def test_cleanup_releases_sessions(rt):
    import httpx
    from mises.server.app import create_app
    rid = _finished_claude_run(rt)
    s = _FakeSession()
    rt._claude[rid] = _ClaudeRun(session=s)
    app = create_app(rt, "tok")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core",
                                 headers={"Authorization": "Bearer tok"}) as c:
        sub = rt.events.subscribe()
        r = await c.post("/runs/cleanup", json={"dry_run": False})
    assert rid in r.json()["ids"]
    assert s.closed == 1 and rid not in rt._claude
    # QA C-6: other views drop the deleted run on this event
    gone = [e for e in iter(lambda: None if sub.queue.empty() else sub.queue.get_nowait(), None)
            if e.type == "run_deleted"]
    assert [e.run_id for e in gone] == [rid] and gone[0].data["run_id"] == rid


async def test_delete_forgets_the_runs_activity_tracker(rt):
    rid = _finished_claude_run(rt)
    rt.activity_tracker(rid).set("starting")
    assert rid in rt._activity
    assert await rt.delete_run(rid) is None
    assert rid not in rt._activity
