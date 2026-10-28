"""Moonshot (Kimi): vendor wiring and the reasoning_content round trip.

Kimi K3 / K3.1 Pro always think; the API streams `reasoning_content` beside `content`, and the
docs require sending the complete assistant message (reasoning_content included) back on later
turns of a tool-call loop (platform.kimi.ai/docs/guide/kimi-k3-1-pro-quickstart).
"""
import json

import httpx
import pytest

from mises.providers import registry
from mises.providers.base import Done, ModelRequest, ReasoningDelta, TextDelta, ToolCall
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import Message, TextBlock, ToolResultBlock, ToolUseBlock

from .test_openai import chunk, make, respond, sse, tc


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setattr(registry, "keychain_get", lambda s, a: None)


def req(**kw):
    base = dict(model="kimi-k3.1-pro", system="sys",
                messages=[Message(role="user", content=[TextBlock(text="hi")])])
    base.update(kw)
    return ModelRequest(**base)


def kimi(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("moonshot", "sk", "https://api.moonshot.ai/v1", http_client=client,
                                retry_base_delay=0, reasoning_content=True)


def test_moonshot_vendor_resolves(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    p, m = registry.resolve("moonshot/kimi-k3.1-pro")
    assert isinstance(p, OpenAICompatProvider) and p.name == "moonshot" and m == "kimi-k3.1-pro"
    assert str(p._client.base_url) == "https://api.moonshot.ai/v1/"
    assert p.reasoning_content is True
    assert registry.VENDORS["moonshot"].env_key == "MOONSHOT_API_KEY"
    assert registry.MOONSHOT_MODELS == ("moonshot/kimi-k3.1-pro",
                                        "moonshot/kimi-k3.1-pro-highspeed", "moonshot/kimi-k3")


def test_moonshot_needs_a_key():
    with pytest.raises(Exception) as e:
        registry.resolve("moonshot/kimi-k3")
    assert "MOONSHOT_API_KEY" in (e.value.hint or "")


def test_other_vendors_do_not_round_trip_reasoning(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    p, _ = registry.resolve("openai/gpt-x")
    assert p.reasoning_content is False


async def test_kimi_streams_reasoning_separately_from_text():
    body = sse(chunk({"role": "assistant", "reasoning_content": "think "}),
               chunk({"reasoning_content": "more"}), chunk({"content": "answer"}),
               chunk(tc(0, id="c1", name="a", args="{}")), chunk({}, finish="tool_calls"))
    evs = [e async for e in kimi(respond(body)).stream(req())]
    assert evs[:3] == [ReasoningDelta(text="think "), ReasoningDelta(text="more"),
                       TextDelta(text="answer")]
    assert isinstance(evs[3], ToolCall) and evs[-1] == Done(stop_reason="tool_use")


async def test_kimi_request_mapping_returns_reasoning_content():
    seen = {}

    def h(request):
        seen["url"] = str(request.url)
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse(chunk({"content": "k"}, finish="stop")),
                              headers={"content-type": "text/event-stream"})

    msgs = [Message(role="user", content=[TextBlock(text="go")]),
            Message(role="assistant", reasoning="I should call a.",
                    content=[ToolUseBlock(id="t1", name="a", input={})]),
            Message(role="user", content=[ToolResultBlock(tool_use_id="t1", content="ok")])]
    [e async for e in kimi(h).stream(req(messages=msgs))]
    assert seen["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert seen["model"] == "kimi-k3.1-pro"
    a = seen["messages"][2]
    assert a["role"] == "assistant" and a["reasoning_content"] == "I should call a."
    assert a["tool_calls"][0]["id"] == "t1" and a["content"] is None
    assert seen["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}
    assert "thinking" not in seen  # the docs: do not pass `thinking` to K3.1 Pro


async def test_other_vendors_never_send_or_emit_reasoning_content():
    seen = {}

    def h(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse(chunk({"reasoning_content": "x"}),
                                               chunk({"content": "k"}, finish="stop")),
                              headers={"content-type": "text/event-stream"})

    msgs = [Message(role="user", content=[TextBlock(text="go")]),
            Message(role="assistant", reasoning="secret", content=[TextBlock(text="hi")]),
            Message(role="user", content=[TextBlock(text="again")])]
    evs = [e async for e in make(h).stream(req(model="gpt-x", messages=msgs))]
    assert not any(isinstance(e, ReasoningDelta) for e in evs)
    assert "reasoning_content" not in seen["messages"][2]


def test_message_without_reasoning_serializes_as_before():
    m = Message(role="user", content=[TextBlock(text="x")])
    assert m.reasoning is None
    assert "reasoning" not in m.model_dump(exclude_none=True)
