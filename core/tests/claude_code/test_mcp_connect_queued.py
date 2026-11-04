"""The Mises tools connect to a Claude Code run that waits for a parallel-agents slot.

Root cause of "the Mises tools did not connect to Claude Code (mises server: failed)" on a
lead's fanned-out sub-agents: the `claude` process (and its MCP stdio proxy) starts as soon as
the run is created, but a run beyond the parallel limit is `queued`; the MCP endpoint served
only `running` runs, so the proxy's handshake got 409 and Claude Code marked the server failed.
"""
import asyncio

from mises.profile import Profile, save_profile
from mises.types import StepKind

from .test_runtime_wiring import _log, _sent, _wait_for, needs_sandbox, served, signed_in  # noqa: F401


def _inits(rt, rid):
    return [s.payload for s in rt.store.steps(rid, [StepKind.DECISION])
            if s.payload.get("claude_code") == "init"]


@needs_sandbox
async def test_a_queued_runs_tools_connect_while_it_waits(rt, claude_bin, signed_in, user_home):
    (user_home / "fake-claude-mcp-at-start").write_text("")
    save_profile(rt.home, Profile(max_parallel_agents=1))
    async with served(rt):
        a = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        rt._claude[a].session.term_grace_s = 0.5
        b = await rt.start_run("designer", "tool", model_ref="claude-code/sonnet")
        assert rt.store.get_run(b).status == "queued"
        await _wait_for(lambda: _sent(rt, a))
        # b's proxy connects while b is still queued (the real CLI connects at launch)
        await asyncio.sleep(3.0)
        assert rt.store.get_run(b).status == "queued"
        rt.cancel(a)
        res = await asyncio.wait_for(rt.wait(b), 20)
    assert [r for r in _log(rt, b) if r["event"] == "mcp_failed"] == []
    assert res.stop_reason == "end_turn", res.error
    assert res.final_text == "listed ok=True"
    assert [i["mcp_servers"] for i in _inits(rt, b)] == [["mises"]]
    assert not [s for s in rt.store.steps(b, [StepKind.ERROR])]
