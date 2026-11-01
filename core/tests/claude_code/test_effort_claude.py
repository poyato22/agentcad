"""Plan 11 F8a: a Claude Code run's effort reaches the (fake) claude binary: --effort when the
session starts, then a stream-json apply_flag_settings {effortLevel} before a turn whose level
differs, confirmed with get_settings. Never the real binary, never the network."""
import asyncio
import json
import os

import pytest

from mises.types import StepKind

from .test_runtime_wiring import _argvs, _log, _sent, served

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


@pytest.fixture
def signed_in(auth_state):
    auth_state.write_text("signed_in")
    return auth_state


def _controls(rt, rid):
    return [r["msg"]["request"] for r in _log(rt, rid) if r["event"] == "control"]


def _turn_efforts(rt, rid):
    return [r["effort"] for r in _log(rt, rid) if r["event"] == "turn"]


def _effort_steps(rt, rid):
    return [s.payload for s in rt.store.steps(rid, [StepKind.DECISION])
            if "effort" in s.payload or s.payload.get("claude_code") == "effort"]


async def test_effort_flag_then_per_turn_control(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "Design a egg drop build no limits think of the "
                                 "best possible design", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        argv = _argvs(rt, rid)[0]
        assert argv[argv.index("--effort") + 1] == "max"         # auto: an open-ended challenge
        first = _effort_steps(rt, rid)[0]["effort"]
        assert (first["level"], first["source"], first["label"]) == ("max", "auto", "Max")
        assert first["control"]["value"] == "max"
        assert _controls(rt, rid) == []                           # the flag was enough
        # a routine follow-up drops to low: one control request, then the turn runs at low
        await rt.send(rid, "rename it to pod")
        await asyncio.wait_for(rt.wait(rid), 15)
        assert _controls(rt, rid) == [
            {"subtype": "apply_flag_settings", "settings": {"effortLevel": "low"}},
            {"subtype": "get_settings"}]
        assert _turn_efforts(rt, rid) == ["max", "low"]
        applied = [p for p in _effort_steps(rt, rid) if p.get("claude_code") == "effort"]
        assert applied == [{"claude_code": "effort", "requested": "low", "applied": "low"}]
        # the user's Think pick wins; the same level twice sends nothing new
        await rt.send(rid, "rename it to pod2", effort="hard")
        await asyncio.wait_for(rt.wait(rid), 15)
        await rt.send(rid, "now the lid", effort="hard")
        await asyncio.wait_for(rt.wait(rid), 15)
        sets = [c for c in _controls(rt, rid) if c["subtype"] == "apply_flag_settings"]
        assert [c["settings"]["effortLevel"] for c in sets] == ["low", "high"]
        assert _turn_efforts(rt, rid) == ["max", "low", "high", "high"]
        assert _sent(rt, rid)[-1] == "now the lid"


async def test_a_refused_effort_change_is_recorded_and_the_turn_still_runs(rt, claude_bin,
                                                                           signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        (rt.workdir(rid) / "fake_effort_error").write_text("1")
        await rt.send(rid, "hello again", effort="max")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.final_text == "Hello there"
        step = [p for p in _effort_steps(rt, rid) if p.get("claude_code") == "effort"][-1]
        assert step["requested"] == "max" and step["applied"] is None
        assert "effort is locked" in step["error"]


async def test_the_session_never_inherits_an_effort_env(rt, claude_bin, signed_in, monkeypatch):
    from mises.claude_code import session as session_mod
    real = session_mod.claude_environment
    monkeypatch.setattr(session_mod, "claude_environment",
                        lambda: {**real(), "CLAUDE_CODE_EFFORT_LEVEL": "low"})
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
    env_keys = [r["env_keys"] for r in _log(rt, rid) if r["event"] == "argv"][0]
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in env_keys
    assert json.dumps(_argvs(rt, rid)).count("--effort") == 1
