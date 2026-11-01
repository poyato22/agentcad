"""P11 F9 (fake claude): a Claude Code run recovers by itself from run-level failures (the process
exiting, error_during_execution, the Mises tools not connecting, a network hiccup), at most 3
times per user turn with a visible note each time; once that budget is spent it stops with one
clear card (what failed, what was tried, Retry / Continue). A config failure is not retried."""
import asyncio
import os

import pytest

from mises.runtime import Runtime
from mises.types import StepKind

from .test_runtime_wiring import _argvs, served, signed_in  # noqa: F401

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(Runtime, "CLAUDE_RECOVERY_BACKOFF_S", 0.0)


def _notes(rt, rid):
    return [s.payload["recovery"] for s in rt.store.steps(rid, [StepKind.DECISION])
            if "recovery" in s.payload]


def _users(rt, rid):
    return [s.payload["text"] for s in rt.store.steps(rid, [StepKind.MESSAGE])
            if s.payload["role"] == "user"]


async def _run(rt, prompt):
    rid = await rt.start_run("designer", prompt, model_ref="claude-code/sonnet")
    return rid, await asyncio.wait_for(rt.wait(rid), 30)


@pytest.mark.parametrize("mode, n, resumed", [
    ("exit", 1, True), ("during", 1, True), ("net", 2, True), ("mcp", 1, False)])
async def test_a_run_level_failure_recovers_with_a_visible_note(rt, claude_bin, signed_in,
                                                                mode, n, resumed):
    async with served(rt):
        rid, res = await _run(rt, f"flaky:{mode}:{n}")
    assert res.stop_reason == "end_turn", res.error
    assert res.final_text.startswith(f"recovered after {n}")
    assert "Mises note: your previous session stopped" in res.final_text   # told to carry on
    notes = _notes(rt, rid)
    assert [x["attempt"] for x in notes] == list(range(1, n + 1))
    assert notes[0]["text"].startswith("Recovered: ") and notes[0]["of"] == 3
    argvs = _argvs(rt, rid)
    assert len(argvs) == n + 1
    if resumed:     # Claude Code still has the conversation: resumed, not started over
        assert "--resume" in argvs[-1] and "resumed the session" in notes[0]["action"]
    else:           # it never started one: a fresh session with a recap of the thread
        assert "--session-id" in argvs[-1] and "recap" in notes[0]["action"]
    assert rt.store.get_run(rid).status == "done"
    assert _users(rt, rid) == [f"flaky:{mode}:{n}"]          # the user's message once
    failed = [s.payload for s in rt.store.steps(rid, [StepKind.ERROR])]
    assert failed and all(p.get("recoverable") for p in failed)


async def test_the_budget_spent_the_run_stops_with_one_clear_card(rt, claude_bin, signed_in):
    async with served(rt):
        rid, res = await _run(rt, "flaky:exit:9")
    assert res.stop_reason == "error"
    assert len(_notes(rt, rid)) == 3 and len(_argvs(rt, rid)) == 4
    cards = [s.payload for s in rt.store.steps(rid, [StepKind.ERROR]) if "recovery" in s.payload]
    assert len(cards) == 1
    card = cards[0]["recovery"]
    assert card["exhausted"] is True and card["actions"] == ["retry", "continue"]
    assert len(card["tried"]) == 3 and "exited" in card["cause"]
    assert cards[0]["message"].startswith("Stopped after 3 recoveries")
    assert rt.store.get_run(rid).status == "failed"


async def test_a_config_failure_is_not_retried(rt, claude_bin, signed_in):
    async with served(rt):
        rid, res = await _run(rt, "error")        # "Not logged in": the user's to fix
    assert res.stop_reason == "error" and _notes(rt, rid) == []
    assert len(_argvs(rt, rid)) == 1


async def test_a_restarted_session_runs_at_the_turns_effort(rt, claude_bin, signed_in):
    """Plan 11 F8 x F9: the first process starts with --effort; the session that replaces it
    (resumed, without the flag) gets the turn's level over the control protocol before it
    carries on."""
    from .test_runtime_wiring import _log
    async with served(rt):
        rid = await rt.start_run("designer", "flaky:exit:1", model_ref="claude-code/sonnet",
                                 effort="hard")
        res = await asyncio.wait_for(rt.wait(rid), 30)
    assert res.stop_reason == "end_turn", res.error
    argvs = _argvs(rt, rid)
    assert argvs[0][argvs[0].index("--effort") + 1] == "high"
    assert "--effort" not in argvs[1] and "--resume" in argvs[1]
    controls = [r["msg"]["request"] for r in _log(rt, rid) if r["event"] == "control"]
    assert {"subtype": "apply_flag_settings", "settings": {"effortLevel": "high"}} in controls
    assert len(_notes(rt, rid)) == 1


async def test_stop_during_the_recovery_backoff_ends_the_turn(rt, claude_bin, signed_in,
                                                              monkeypatch):
    """Fix round 1 (I1): Stop while a recovery waits to restart: no new session, no re-run."""
    monkeypatch.setattr(Runtime, "CLAUDE_RECOVERY_BACKOFF_S", 3.0)
    async with served(rt):
        rid = await rt.start_run("designer", "flaky:exit:1", model_ref="claude-code/sonnet")
        # the first process died and its error is in: the recovery is in its backoff
        for _ in range(200):
            if any(s.payload.get("recoverable") for s in rt.store.steps(rid, [StepKind.ERROR])):
                break
            await asyncio.sleep(0.05)
        rt.cancel(rid)
        res = await asyncio.wait_for(rt.wait(rid), 10)
        await asyncio.sleep(0.5)
    assert res.stop_reason == "cancelled"
    assert len(_argvs(rt, rid)) == 1 and _notes(rt, rid) == []
    assert rt.store.get_run(rid).status in ("failed", "cancelled")    # the crash, then Stop


async def test_a_recovery_cancels_the_dead_sessions_approval(rt, claude_bin, signed_in):
    """Fix round 1 (I2): an irreversible call waits on its card when Claude Code dies. The
    restart cancels that card (approving it later can run nothing), and the restarted session
    is told the approval was cancelled. The action never runs from the dead session."""
    from pydantic import BaseModel

    from mises.tools.registry import tool
    from mises.types import SafetyClass, ToolOutput
    ran = []

    class NoArgs(BaseModel):
        pass

    @tool("fake_merge", description="merge (test)", safety=SafetyClass.IRREVERSIBLE)
    async def fake_merge(ctx, args: NoArgs) -> ToolOutput:
        ran.append(ctx.run_id)
        return ToolOutput(summary="merged")
    rt.registry.register(fake_merge)
    async with served(rt):
        rid, res = await _run(rt, "flaky:approve=fake_merge:1")
        assert res.stop_reason == "end_turn", res.error
        rows = rt.store._all("SELECT status, decided_by FROM approvals WHERE run_id=?", (rid,))
        assert [tuple(r) for r in rows] == [("rejected", "system:cancel")]   # the card was up
        assert rt.store.pending_approvals(rid) == []
        assert "approval it was waiting for was cancelled" in res.final_text
        await asyncio.sleep(0.5)
    assert ran == []
