"""A Claude Code run the app's restart interrupted mid-turn carries on by itself on the next
start: resumed from its stored session with an internal continuation (no user bubble),
sub-agents first, and the lead told its sub-agents were continued."""
import asyncio
import os
import subprocess

import pytest

from mises.runtime import Runtime
from mises.types import StepKind

from .test_runtime_wiring import _argvs, _sent, served, signed_in  # noqa: F401

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


def _dead_pid() -> int:
    p = subprocess.Popen(["/usr/bin/true"])
    p.wait()
    return p.pid


def _users(rt, rid):
    return [s.payload["text"] for s in rt.store.steps(rid, [StepKind.MESSAGE])
            if s.payload["role"] == "user"]


async def _orphan(rt, *prompts):
    """Runs left 'running' by a process that died (the app quit mid-turn)."""
    ids = []
    async with served(rt):
        for p in prompts:
            rid = await rt.start_run("designer", p, model_ref="claude-code/sonnet")
            await asyncio.wait_for(rt.wait(rid), 30)
            ids.append(rid)
        await rt.close_claude_sessions()
    pid = _dead_pid()
    for rid in ids:
        rt.store._db.execute("UPDATE runs SET status='running', owner_pid=? WHERE id=?",
                             (pid, rid))
    return ids


async def _until(pred, timeout=30.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_interrupted_runs_continue_on_the_next_start(rt, claude_bin, signed_in):
    lead, child = await _orphan(rt, "hello", "hello")
    rt.store._db.execute("UPDATE runs SET parent_id=? WHERE id=?", (lead, child))
    rt2 = Runtime(rt.home, provider_resolver=rt.provider_resolver)
    assert set(rt2.interrupted) == {lead, child}
    async with served(rt2):
        await _until(lambda: all(rt2.store.get_run(r).status == "done"
                                 and any("continuation" in s.payload for s in
                                         rt2.store.steps(r, [StepKind.DECISION]))
                                 for r in (lead, child)))
    for rid in (lead, child):
        assert "--resume" in _argvs(rt2, rid)[-1]          # its stored session, not a new one
        assert _users(rt2, rid) == ["hello"]                # no user bubble for the continuation
        sent = _sent(rt2, rid)[-1]
        assert sent.startswith("AgentCAD note: your last turn stopped (the app restarted")
        note = [s.payload["continuation"] for s in rt2.store.steps(rid, [StepKind.DECISION])
                if "continuation" in s.payload]
        assert note == [{"cause": Runtime.RESTART_CAUSE, "auto": True,
                         "text": f"AgentCAD continued after: {Runtime.RESTART_CAUSE}"}]
    assert f"sub-agents {child}" in _sent(rt2, lead)[-1]
    assert "sub-agents" not in _sent(rt2, child)[-1]


async def test_an_old_or_stopped_interruption_is_not_continued(rt, claude_bin, signed_in):
    old, stopped = await _orphan(rt, "hello", "hello")
    # it last worked two days ago (no last_activity_at: updated_at, which the restart bumps)
    rt.store._db.execute("UPDATE runs SET last_activity_at=NULL, updated_at=? WHERE id=?",
                         (1.0, old))
    rt2 = Runtime(rt.home, provider_resolver=rt.provider_resolver)
    rt2.note_hold.add(stopped)                              # the user's Stop wins
    async with served(rt2):
        await asyncio.sleep(1.0)
        assert rt2.interrupted == [] or set(rt2.interrupted) == {old, stopped}
    for rid in (old, stopped):
        assert rt2.store.get_run(rid).status == "failed"
        assert len(_argvs(rt2, rid)) == 1                   # never respawned
