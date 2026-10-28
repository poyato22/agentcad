"""Claude Code runs take parallel-agents slots too (fake claude)."""
import asyncio

from mises.profile import Profile, save_profile

from .test_runtime_wiring import _sent, _wait_for, needs_sandbox, served, signed_in  # noqa: F401


@needs_sandbox
async def test_claude_run_beyond_the_limit_queues_then_runs(rt, claude_bin, signed_in):
    save_profile(rt.home, Profile(max_parallel_agents=1))
    async with served(rt):
        sub = rt.events.subscribe()
        a = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        rt._claude[a].session.term_grace_s = 0.5
        b = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        assert rt.store.get_run(b).status == "queued"
        await _wait_for(lambda: _sent(rt, a))
        await asyncio.sleep(0.2)
        assert _sent(rt, b) == []                  # not prompted while queued
        types = []
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev.run_id == b:
                types.append(ev.type)
        assert types == ["run_queued"]
        rt.cancel(a)
        res = await asyncio.wait_for(rt.wait(b), 15)
        assert res.final_text == "Hello there"
        assert rt.store.get_run(b).status == "done"
        assert rt.parallel.active() == set()


@needs_sandbox
async def test_cancel_a_queued_claude_run(rt, claude_bin, signed_in):
    save_profile(rt.home, Profile(max_parallel_agents=1))
    async with served(rt):
        a = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        rt._claude[a].session.term_grace_s = 0.5
        b = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        rt.cancel(b)
        res = await asyncio.wait_for(rt.wait(b), 10)
        assert res.stop_reason == "cancelled"
        assert rt.store.get_run(b).status == "cancelled"
        assert _sent(rt, b) == []
        rt.cancel(a)
        await asyncio.wait_for(rt.wait(a), 10)
        assert rt.parallel.active() == set() and rt.parallel.queued() == []


@needs_sandbox
async def test_claude_follow_up_queues_and_returns_to_done(rt, claude_bin, signed_in):
    save_profile(rt.home, Profile(max_parallel_agents=1))
    async with served(rt):
        a = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(a), 15)
        b = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        rt._claude[b].session.term_grace_s = 0.5
        await _wait_for(lambda: _sent(rt, b))
        await rt.send(a, "second message")
        assert rt.store.get_run(a).status == "queued"
        rt.cancel(b)
        res = await asyncio.wait_for(rt.wait(a), 15)
        assert res.final_text == "echo: second message"
        assert rt.store.get_run(a).status == "done"


@needs_sandbox
async def test_cancel_a_queued_claude_follow_up_keeps_its_last_status(rt, claude_bin, signed_in):
    save_profile(rt.home, Profile(max_parallel_agents=1))
    async with served(rt):
        a = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(a), 15)
        b = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        rt._claude[b].session.term_grace_s = 0.5
        await _wait_for(lambda: _sent(rt, b))
        await rt.send(a, "second message")
        assert rt.store.get_run(a).status == "queued"
        rt.cancel(a)
        res = await asyncio.wait_for(rt.wait(a), 10)
        assert res.stop_reason == "cancelled"
        assert rt.store.get_run(a).status == "done"
        rt.cancel(b)
        await asyncio.wait_for(rt.wait(b), 10)
