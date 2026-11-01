"""FWC M8 (E34): a drawing import with no API key resolves to the user's signed-in Claude Code
even when the sign-in cache is cold (startup, or after forget_claude_status)."""
import time

import pytest

import mises.runtime as runtime_mod
from mises.claude_code import detect
from mises.imports import drawing_import
from mises.runtime import Runtime


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    def no_key(ref):
        from mises.types import ErrorCode, MisesError
        raise MisesError(ErrorCode.DENIED, f"no API key for {ref}")
    rt = Runtime(home, provider_resolver=no_key)
    calls = []

    async def status(*, max_age=0.0):
        calls.append(max_age)
        info = detect.ClaudeInfo(path="/fake/claude", version="2.0.0")
        auth = detect.AuthStatus(signed_in=True, auth_method="oauth", email=None,
                                 config_dir=None, raw_ok=True)
        rt._claude_status_cache = (time.monotonic(), (info, auth))
        return info, auth
    monkeypatch.setattr(rt, "claude_status", status)
    rt._status_calls = calls
    return rt


async def test_a_cold_cache_is_warmed_before_the_vision_check(rt):
    rt.forget_claude_status()
    assert not rt._claude_signed_in_cached()
    await drawing_import._vision_check(rt, None)          # no "no API key"
    assert rt._status_calls and rt._claude_signed_in_cached()


async def test_an_explicit_model_is_not_second_guessed(rt):
    from mises.types import MisesError
    with pytest.raises(MisesError, match="no API key"):
        await drawing_import._vision_check(rt, "anthropic/claude-opus-5-5")
    assert rt._status_calls == []
