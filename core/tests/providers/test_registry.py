import pytest

from mises.providers import registry
from mises.providers.anthropic_provider import AnthropicProvider
from mises.providers.base import Done, ModelRequest, with_retries
from mises.providers.ollama_provider import OllamaProvider
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import ErrorCode, MisesError

KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(registry, "keychain_get", lambda s, a: None)


def test_resolve_refs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    p, m = registry.resolve("ollama/qwen3")
    # Ollama: the native /api/chat (the only Ollama API that honours num_ctx)
    assert isinstance(p, OllamaProvider) and m == "qwen3" and p.name == "ollama"
    assert p._url == "http://localhost:11434/api/chat"
    p, m = registry.resolve("lmstudio/qwen3")
    assert isinstance(p, OpenAICompatProvider) and "localhost:1234" in str(p._client.base_url)
    p, m = registry.resolve("claude-haiku-5-5")
    assert isinstance(p, AnthropicProvider) and m == "claude-haiku-5-5"
    p, m = registry.resolve(registry.DEFAULT_MODEL)
    assert isinstance(p, AnthropicProvider) and m == "claude-sonnet-5-5"


def test_ollama_honours_profile_local_endpoint():
    # C7: an Ollama run uses the profile's configured ollama base URL, not the default port.
    eps = {"ollama": "http://127.0.0.1:12345/v1"}
    p, m = registry.resolve("ollama/qwen3", local_endpoints=eps)
    assert isinstance(p, OllamaProvider) and m == "qwen3"
    assert p._url == "http://127.0.0.1:12345/api/chat"
    # lmstudio endpoint override too, and no override falls back to the default
    p2, _ = registry.resolve("lmstudio/qwen3",
                             local_endpoints={"lmstudio": "http://127.0.0.1:4321/v1"})
    assert "127.0.0.1:4321" in str(p2._client.base_url)
    p3, _ = registry.resolve("ollama/qwen3")
    assert p3._url == "http://localhost:11434/api/chat"


def test_openrouter_model_keeps_inner_slash(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    p, m = registry.resolve("openrouter/anthropic/claude-x")
    assert m == "anthropic/claude-x" and "openrouter.ai" in str(p._client.base_url)


def test_key_from_keychain(monkeypatch):
    monkeypatch.setattr(registry, "keychain_get",
                        lambda s, a: "kc" if (s, a) == ("mises", "openai") else None)
    p, _ = registry.resolve("openai/gpt-x")
    assert p._client.api_key == "kc"


def test_missing_key_denied_with_hint():
    with pytest.raises(MisesError) as ei:
        registry.resolve("anthropic/claude-sonnet-5-5")
    assert ei.value.code == ErrorCode.DENIED
    assert ei.value.hint == ("add a key in Profile → Model (or set ANTHROPIC_API_KEY, or run "
                             "`mises keys set anthropic`)")  # QA B1-14: app-first advice


@pytest.mark.parametrize("ref", ["nope/model", "gpt-4", "ollama/"])
def test_bad_refs_invalid_input(ref):
    with pytest.raises(MisesError) as ei:
        registry.resolve(ref)
    assert ei.value.code == ErrorCode.INVALID_INPUT


async def test_with_retries_gives_up_after_3():
    n = []

    async def fn():
        n.append(1)
        raise MisesError(ErrorCode.TRANSIENT, "x")

    with pytest.raises(MisesError):
        await with_retries(fn, base_delay=0)
    assert len(n) == 3


async def test_with_retries_does_not_retry_other_errors():
    n = []

    async def fn():
        n.append(1)
        raise MisesError(ErrorCode.DENIED, "x")

    with pytest.raises(MisesError):
        await with_retries(fn, base_delay=0)
    assert len(n) == 1


async def test_with_retries_returns_after_transient():
    n = []

    async def fn():
        n.append(1)
        if len(n) < 2:
            raise MisesError(ErrorCode.TRANSIENT, "x")
        return "ok"

    assert await with_retries(fn, base_delay=0) == "ok"


async def test_mid_stream_drop_is_not_replayed():
    from mises.providers.base import TextDelta, retried_stream
    opens = []

    async def gen():
        opens.append(1)
        yield TextDelta(text="a")
        raise MisesError(ErrorCode.TRANSIENT, "dropped")

    got = []
    with pytest.raises(MisesError) as ei:
        async for e in retried_stream(gen, attempts=3, base_delay=0):
            got.append(e)
    assert ei.value.code == ErrorCode.TRANSIENT and len(opens) == 1 and len(got) == 1
