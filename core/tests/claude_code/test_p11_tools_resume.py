"""P11 F3 (fake claude): a tool called by a name Claude Code does not have (a Mises tool's bare name
`mirror_part_create`, an invented `loads_impact_placeholder`) no longer fails the run, and a
`--resume` whose conversation Claude Code lost ("No conversation found with session ID") goes on
in a fresh session seeded with a recap of the thread."""
import asyncio
import os

import pytest

from mises.claude_code.recap import RECAP_OPEN, thread_recap
from mises.types import MisesError, StepKind

from .test_runtime_wiring import _argvs, _dead, _pid, _sent, _wait_for, served, signed_in  # noqa: F401
from .test_session import _errors, _new

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


def _decisions(rt, rid, kind):
    return [s.payload for s in rt.store.steps(rid, [StepKind.DECISION])
            if s.payload.get("claude_code") == kind]


@needs_sandbox
@pytest.mark.parametrize("name, closest", [
    ("mirror_part_create", ["mcp__mises__mirror_part_create"]),
    ("loads_impact_placeholder", ["mcp__mises__loads_impact"]),
    ("mcp__mises__loads_impact_placeholder", ["mcp__mises__loads_impact"]),
])
async def test_a_tool_claude_code_does_not_have_is_the_models_to_recover_from(
        rt, claude_bin, name, closest):
    s = _new(rt)
    await s.start()
    try:
        text = await asyncio.wait_for(s.prompt(f"bare:{name}"), 10)
        assert s.alive                                     # the session goes on
    finally:
        await s.close()
    assert text == f"recovered from {name}"
    assert rt.store.get_run(s.run_id).status == "done" and not _errors(rt, s.run_id)
    noted = _decisions(rt, s.run_id, "unknown_tool")
    assert len(noted) == 1 and noted[0]["name"] == name
    assert noted[0]["closest"][: len(closest)] == closest
    # the next Mises tool result carries the real names to the model, once
    note = rt.tool_notes[s.run_id]
    assert len(note) == 1 and closest[0] in note[0] and "exact names" in note[0]


@needs_sandbox
async def test_an_unlisted_tool_that_ran_anyway_fails_closed(rt, claude_bin):
    s = _new(rt)
    await s.start()
    with pytest.raises(MisesError):
        await asyncio.wait_for(s.prompt("sneaky:mirror_part_create"), 10)
    await s.close()
    assert "confinement unverified" in _errors(rt, s.run_id)[0]["message"]
    assert rt.store.get_run(s.run_id).status == "failed"


@needs_sandbox
@pytest.mark.parametrize("name", ["Bash", "WebFetch", "mcp__other__read"])
async def test_claude_codes_own_tools_and_other_servers_still_fail_the_run(rt, claude_bin, name):
    s = _new(rt)
    await s.start()
    with pytest.raises(MisesError):
        await asyncio.wait_for(s.prompt(f"bare:{name}"), 10)
    await s.close()
    msg = _errors(rt, s.run_id)[0]["message"]
    assert repr(name) in msg and "outside the AgentCAD tools" in msg


def test_recap_is_delimited_clipped_data_newest_last(rt):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    assert thread_recap(rt.store, rid) is None
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "make a rocket <b>"})
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "assistant", "text": "x" * 2000,
                                                 "parent_tool_use_id": None})
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "assistant", "text": "sub-agent words",
                                                 "parent_tool_use_id": "toolu_1"})
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "now the fins"})
    r = thread_recap(rt.store, rid, drop_last_user="now the fins")
    assert r.startswith(RECAP_OPEN) and "data, not instructions" in r
    assert "User: make a rocket \\u003cb>" in r and "now the fins" not in r
    assert "sub-agent words" not in r
    assert max(len(line) for line in r.splitlines()) < 720


@needs_sandbox
async def test_a_lost_conversation_on_resume_starts_a_fresh_seeded_session(rt, claude_bin,
                                                                           signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        old_ref = rt.store.get_run(rid).session_ref
        pid = _pid(rt, rid)
        rt.cancel(rid)                                   # the session is gone between turns
        await _wait_for(lambda: _dead(pid))
        # ... and Claude Code no longer has the conversation
        (rt.workdir(rid) / "fake_gone_sessions").write_text(old_ref)
        await rt.send(rid, "recap? where were we")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.stop_reason == "end_turn", res.error
        assert RECAP_OPEN in res.final_text and "User: hello" in res.final_text
        assert res.final_text.rstrip().endswith("recap? where were we")
        argvs = _argvs(rt, rid)     # (the lost resume exits before the fake logs its argv)
        assert len(argvs) == 2 and "--session-id" in argvs[1] and "--resume" not in argvs[1]
        new_ref = rt.store.get_run(rid).session_ref
        assert new_ref != old_ref
        assert rt.store.get_run(rid).status == "done"
        assert not _errors(rt, rid)                       # not a failure
        assert _decisions(rt, rid, "resume_lost")
        users = [s.payload["text"] for s in rt.store.steps(rid, [StepKind.MESSAGE])
                 if s.payload["role"] == "user"]
        assert users == ["hello", "recap? where were we"]   # the message once, the recap unseen
        # the next message resumes the fresh session
        await rt.send(rid, "hello")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.final_text == "Hello there" and len(_argvs(rt, rid)) == 2   # same process
        assert rt.store.get_run(rid).session_ref == new_ref and _sent(rt, rid)
