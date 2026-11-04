"""Mises carries a stopped run on by itself with an internal continuation: never the user's
message again, never a user bubble, budgets keep their window, bounded per user turn, and a
user Stop always wins."""
import asyncio
import os

import pytest

from mises.runtime import Runtime
from mises.types import MisesError, StepKind

from .test_runtime_wiring import _sent, served, signed_in  # noqa: F401

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(Runtime, "CLAUDE_RECOVERY_BACKOFF_S", 0.0)


def _users(rt, rid):
    return [s.payload["text"] for s in rt.store.steps(rid, [StepKind.MESSAGE])
            if s.payload["role"] == "user"]


def _continuations(rt, rid):
    return [s.payload["continuation"] for s in rt.store.steps(rid, [StepKind.DECISION])
            if "continuation" in s.payload]


async def test_continue_sends_an_internal_message_not_the_users_again(rt, claude_bin,
                                                                      signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "flaky:exit:9", model_ref="claude-code/sonnet")
        res = await asyncio.wait_for(rt.wait(rid), 30)
        assert res.stop_reason == "error"
        card = [s.payload for s in rt.store.steps(rid, [StepKind.ERROR]) if "recovery" in s.payload]
        assert card[-1]["recovery"]["actions"] == ["continue"]       # never "send it again"
        turn0 = rt.store.get_run(rid).turn_started_at
        await rt.continue_run(rid, "Claude Code exited (code 3)")
        res = await asyncio.wait_for(rt.wait(rid), 30)
    assert res.stop_reason == "end_turn", res.error
    assert res.final_text.startswith("echo: Mises note: your last turn stopped (Claude Code exited")
    assert _users(rt, rid) == ["flaky:exit:9"]                  # no user bubble for it
    assert "flaky:exit:9" not in _sent(rt, rid)[-1]
    assert _continuations(rt, rid) == [{"cause": "Claude Code exited (code 3)", "auto": False,
                                        "text": "Mises continued after: Claude Code exited "
                                                "(code 3)"}]
    assert rt.store.get_run(rid).turn_started_at == turn0       # budget window not reset
    assert rt.store.get_run(rid).status == "done"


async def test_automatic_continuations_are_bounded_and_stop_wins(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 30)
        for _ in range(Runtime.AUTO_CONTINUES):
            await rt.continue_run(rid, "the app restarted", auto=True)
            await asyncio.wait_for(rt.wait(rid), 30)
        assert "already continued" in rt.auto_continue_blocked(rid)
        with pytest.raises(MisesError):
            await rt.continue_run(rid, "the app restarted", auto=True)
        # the user's own Continue is not capped
        await rt.continue_run(rid, "the app restarted")
        await asyncio.wait_for(rt.wait(rid), 30)
        # a new user message opens a new allowance
        await rt.send(rid, "hello again")
        await asyncio.wait_for(rt.wait(rid), 30)
        assert rt.auto_continue_blocked(rid) is None
        # a user Stop always wins
        rt.stop_background(rid)
        assert rt.auto_continue_blocked(rid) == "the user stopped it"
