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


@pytest.mark.asyncio
async def test_fallback_only_on_no_key_when_signed_in(rt):
    _signed_in(rt, True)
    assert await rt._claude_fallback_for(
        MisesError(ErrorCode.DENIED, "no API key for anthropic")) == f"{CLAUDE_CODE}/sonnet"
    # a different DENIED (not a missing key) is not rerouted
    assert await rt._claude_fallback_for(MisesError(ErrorCode.DENIED, "out of scope")) is None


@pytest.mark.asyncio
async def test_no_fallback_when_not_signed_in(rt):
    _signed_in(rt, False)
    assert await rt._claude_fallback_for(
        MisesError(ErrorCode.DENIED, "no API key for anthropic")) is None


def _no_key(ref):
    raise MisesError(ErrorCode.DENIED, "no API key for anthropic")


def _set_default(rt, ref):
    from mises.profile import save_profile
    save_profile(rt.home, rt.profile().model_copy(update={"default_model": ref}))


@pytest.mark.asyncio
async def test_start_run_falls_back_to_claude_with_note(rt, monkeypatch):
    # the implicit default (an Anthropic profile default, no key) routes to Claude Code
    _signed_in(rt, True)
    _set_default(rt, "anthropic/claude-sonnet-5-5")
    rt.provider_resolver = _no_key
    captured = {}

    async def fake_start_claude(agent, prompt, alias, **kw):
        rid = rt.create_external_run(agent, provider=CLAUDE_CODE, model=alias,
                                     project_id=kw.get("project_id"))
        captured["alias"] = alias
        return rid

    monkeypatch.setattr(rt, "_start_claude_run", fake_start_claude)
    pid = rt.store.create_project("Robot").id
    rid = await rt.start_run("engineer", "make a bracket", project_id=pid)
    assert captured["alias"] == "sonnet"
    assert any(s.payload.get("model_fallback") for s in rt.store.steps(rid))


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["explicit_anthropic", "explicit_openai", "openai_default",
                                 "pinned"])
async def test_an_explicit_choice_is_never_overridden(rt, monkeypatch, how):
    # I3: an explicit model_ref, an agent pin or a non-Anthropic profile default keeps its key
    # error even with Claude Code signed in
    _signed_in(rt, True)
    rt.provider_resolver = lambda ref: (_ for _ in ()).throw(
        MisesError(ErrorCode.DENIED, f"no API key for {ref.split('/')[0]}"))

    async def must_not(*a, **k):
        raise AssertionError("rerouted to Claude Code")
    monkeypatch.setattr(rt, "_start_claude_run", must_not)
    kw = {}
    if how == "explicit_anthropic":
        kw["model_ref"] = "anthropic/claude-sonnet-5-5"
    elif how == "explicit_openai":
        kw["model_ref"] = "openai/gpt-5"
    elif how == "openai_default":
        _set_default(rt, "openai/gpt-5")
    else:
        rt.definitions["engineer"] = rt.definitions["engineer"].model_copy(
            update={"model": "openai/gpt-5"})
    pid = rt.store.create_project("Robot").id
    with pytest.raises(MisesError) as e:
        await rt.start_run("engineer", "x", project_id=pid, **kw)
    assert e.value.code == ErrorCode.DENIED


@pytest.mark.asyncio
async def test_start_run_raises_when_not_signed_in(rt):
    _signed_in(rt, False)
    _set_default(rt, "anthropic/claude-sonnet-5-5")
    rt.provider_resolver = _no_key
    pid = rt.store.create_project("Robot").id
    with pytest.raises(MisesError) as e:
        await rt.start_run("engineer", "x", project_id=pid)
    assert e.value.code == ErrorCode.DENIED
