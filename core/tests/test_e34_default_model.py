"""E34: every model-choosing path picks a reachable model when there is no API key.

default_model_ref prefers the build's lead model, then the profile default, then the user's
Claude Code (signed in, no Anthropic key), then the Anthropic default. start_run on an
unreachable ref (its vendor has no key) with Claude Code signed in falls back to Claude Code
with a visible note, instead of raising "no API key".
"""
from __future__ import annotations

import time

import pytest

import mises.runtime as runtime_mod
from mises.claude_code import detect
from mises.providers.registry import DEFAULT_MODEL
from mises.runtime import CLAUDE_CODE, Runtime
from mises.types import ErrorCode, MisesError, StepKind


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    # no key for any vendor in the environment
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))


def _signed_in(rt, yes=True):
    info = detect.ClaudeInfo(path="/fake/claude", version="2.0.0")
    auth = detect.AuthStatus(signed_in=yes, auth_method="oauth" if yes else "none",
                             email=None, config_dir=None, raw_ok=True)
    rt._claude_status_cache = (time.monotonic(), (info, auth))


def test_no_key_signed_in_uses_claude_code(rt):
    _signed_in(rt, True)
    assert rt.default_model_ref() == f"{CLAUDE_CODE}/sonnet"


def test_no_key_not_signed_in_uses_anthropic_default(rt):
    _signed_in(rt, False)
    assert rt.default_model_ref() == DEFAULT_MODEL


def test_profile_default_wins_over_claude_fallback(rt, monkeypatch):
    _signed_in(rt, True)
    monkeypatch.setattr(runtime_mod, "keychain_get",
                        lambda realm, vendor: "k" if vendor == "openai" else None)
    prof = rt.profile().model_copy(update={"default_model": "openai/gpt-5"})
    assert rt.default_model_ref(prof=prof) == "openai/gpt-5"


def test_lead_model_wins(rt):
    _signed_in(rt, True)
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", CLAUDE_CODE, "opus", "go", project_id=pid)
    rt.store.update_run(lead.id, role="lead")
    assert rt.default_model_ref(pid) == f"{CLAUDE_CODE}/opus"


def test_claude_fallback_ref_only_when_unreachable(rt):
    _signed_in(rt, True)
    assert rt._claude_fallback_ref("anthropic/claude-sonnet-5-5") == f"{CLAUDE_CODE}/sonnet"
    assert rt._claude_fallback_ref(f"{CLAUDE_CODE}/opus") is None     # already Claude Code
    assert rt._claude_fallback_ref("ollama/qwen3") is None            # needs no key


def test_claude_fallback_ref_none_when_not_signed_in(rt):
    _signed_in(rt, False)
    assert rt._claude_fallback_ref("anthropic/claude-sonnet-5-5") is None


@pytest.mark.asyncio
async def test_start_run_falls_back_to_claude_with_note(rt, monkeypatch):
    _signed_in(rt, True)
    captured = {}

    async def fake_start_claude(agent, prompt, alias, **kw):
        rid = rt.create_external_run(agent, provider=CLAUDE_CODE, model=alias,
                                     project_id=kw.get("project_id"))
        captured["alias"] = alias
        captured["rid"] = rid
        return rid

    monkeypatch.setattr(rt, "_start_claude_run", fake_start_claude)
    pid = rt.store.create_project("Robot").id
    rid = await rt.start_run("engineer", "make a bracket", model_ref="anthropic/claude-sonnet-5-5",
                             project_id=pid)
    assert captured["alias"] == "sonnet"
    steps = rt.store.steps(rid)
    assert any(s.payload.get("model_fallback") for s in steps), \
        "a visible note records the switch to Claude Code"


@pytest.mark.asyncio
async def test_start_run_raises_when_not_signed_in(rt, monkeypatch):
    _signed_in(rt, False)

    def resolver(ref):
        raise MisesError(ErrorCode.DENIED, "no API key for anthropic")

    rt.provider_resolver = resolver
    pid = rt.store.create_project("Robot").id
    with pytest.raises(MisesError) as e:
        await rt.start_run("engineer", "x", model_ref="anthropic/claude-sonnet-5-5",
                           project_id=pid)
    assert e.value.code == ErrorCode.DENIED
