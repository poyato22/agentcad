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
        await rt.send(rid, "hello again", effort="high")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.final_text == "Hello there"
        step = [p for p in _effort_steps(rt, rid) if p.get("claude_code") == "effort"][-1]
        assert step["requested"] == "high" and step["applied"] is None
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


# ---- fix round 1: an older or stricter CLI never fails a run because of effort

async def test_a_cli_without_effort_gets_no_flag_and_the_level_per_turn(rt, claude_bin, signed_in,
                                                                         user_home):
    (user_home / "fake-claude-no-effort").write_text("1")
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet",
                                 effort="hard")
        res = await asyncio.wait_for(rt.wait(rid), 15)
    assert res.final_text == "Hello there"
    assert "--effort" not in _argvs(rt, rid)[0]
    assert _controls(rt, rid)[0] == {"subtype": "apply_flag_settings",
                                     "settings": {"effortLevel": "high"}}
    assert _turn_efforts(rt, rid) == ["high"]
    notes = [s.payload for s in rt.store.steps(rid, [StepKind.DECISION])
             if s.payload.get("claude_code") == "effort_flag_unsupported"]
    assert len(notes) == 1


async def test_a_cli_that_rejects_effort_is_retried_once_without_it(rt, claude_bin, signed_in,
                                                                    user_home):
    (user_home / "fake-claude-reject-effort").write_text("1")
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet",
                                 effort="high")
        res = await asyncio.wait_for(rt.wait(rid), 20)
    assert res.stop_reason == "end_turn" and res.final_text == "Hello there"
    assert rt.store.get_run(rid).status == "done"
    argvs = _argvs(rt, rid)                       # the rejected start never got to log argv
    assert argvs and all("--effort" not in a for a in argvs)
    rejected = [s.payload for s in rt.store.steps(rid, [StepKind.DECISION])
                if s.payload.get("claude_code") == "effort_flag_rejected"]
    assert rejected and "unknown option" in rejected[0]["stderr_tail"]
    assert _turn_efforts(rt, rid) == ["high"]      # applied over the control protocol


async def test_an_unanswered_effort_change_times_out_and_the_turn_runs(rt, claude_bin, signed_in,
                                                                      monkeypatch):
    from mises.claude_code import session as session_mod
    monkeypatch.setattr(session_mod, "CONTROL_TIMEOUT_S", 0.3)
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        (rt.workdir(rid) / "fake_effort_silent").write_text("1")
        await rt.send(rid, "hello again", effort="high")
        res = await asyncio.wait_for(rt.wait(rid), 15)
    assert res.final_text == "Hello there"
    step = [p for p in _effort_steps(rt, rid) if p.get("claude_code") == "effort"][-1]
    assert step["applied"] is None and "did not answer" in step["error"]


# ---- G8: the user's level (Auto · Low · Medium · High · Max), and Max's deliberate loop

def _text(content) -> str:
    return content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content if isinstance(b, dict))


@pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
async def test_each_level_reaches_the_cli_as_its_effort_flag(rt, claude_bin, signed_in, level):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet",
                                 effort=level)
        await asyncio.wait_for(rt.wait(rid), 15)
    argv = _argvs(rt, rid)[0]
    assert argv[argv.index("--effort") + 1] == level
    assert _turn_efforts(rt, rid) == [level]
    assert "MAX_THINKING_TOKENS" not in json.dumps(_log(rt, rid))    # thinking stays adaptive
    sent = _text(_sent(rt, rid)[0])
    # only a Max the user chose runs the plan -> build -> review -> revise loop
    assert ("Plan first" in sent and "Review independently" in sent and "Revise" in sent) \
        == (level == "max")


async def test_max_on_a_follow_up_and_from_the_profile_default(rt, claude_bin, signed_in):
    from mises.profile import load_profile, save_profile
    save_profile(rt.home, load_profile(rt.home).model_copy(update={"default_effort": "max"}))
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        await rt.send(rid, "hello again", effort="low")
        await asyncio.wait_for(rt.wait(rid), 15)
    assert _turn_efforts(rt, rid) == ["max", "low"]
    first, second = (_text(s) for s in _sent(rt, rid)[:2])
    assert "Plan first" in first and "Plan first" not in second
    d = [p["effort"] for p in _effort_steps(rt, rid) if "effort" in p]
    assert [(x["level"], x["source"]) for x in d] == [("max", "profile"), ("low", "user")]
    # the thread keeps the user's own words, not the loop's instructions
    users = [s.payload for s in rt.store.steps(rid, [StepKind.MESSAGE])
             if s.payload.get("role") == "user"]
    assert users[0]["text"] == "hello"
