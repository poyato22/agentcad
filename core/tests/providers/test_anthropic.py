import json

import httpx2 as httpx
import pytest

from mises.providers.anthropic_provider import AnthropicProvider
from mises.providers.base import (Done, ModelRequest, TextDelta, ToolCall, ToolSchema, Usage)
from mises.types import (ErrorCode, ImageBlock, Message, MisesError, TextBlock, ToolResultBlock,
                         ToolUseBlock)


def sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()


BODY = sse(
    ("message_start", {"type": "message_start", "message": {
        "id": "m1", "type": "message", "role": "assistant", "model": "x", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 1, "cache_read_input_tokens": 5}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "Hel"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "lo"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {
        "type": "tool_use", "id": "tu_1", "name": "make_box", "input": {}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {
        "type": "input_json_delta", "partial_json": '{"w": 1'}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {
        "type": "input_json_delta", "partial_json": ', "h": 2}'}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                       "usage": {"output_tokens": 42}}),
    ("message_stop", {"type": "message_stop"}),
)


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicProvider("sk-test", http_client=client, retry_base_delay=0, **kw)


def req(**kw):
    base = dict(model="claude-sonnet-5-5", system="be brief",
                messages=[Message(role="user", content=[TextBlock(text="hi")])])
    base.update(kw)
    return ModelRequest(**base)


def ok(request):
    return httpx.Response(200, content=BODY, headers={"content-type": "text/event-stream"})


async def test_anthropic_text_and_tool_call():
    p = make(ok)
    evs = [e async for e in p.stream(req())]
    assert evs == [
        TextDelta(text="Hel"), TextDelta(text="lo"),
        ToolCall(id="tu_1", name="make_box", input={"w": 1, "h": 2}),
        Usage(input_tokens=11, output_tokens=42, cache_read_tokens=5),
        Done(stop_reason="tool_use"),
    ]


async def test_anthropic_429_is_transient():
    calls = []

    def h(request):
        calls.append(1)
        return httpx.Response(429, json={"type": "error", "error": {
            "type": "rate_limit_error", "message": "slow down"}})

    with pytest.raises(MisesError) as ei:
        [e async for e in make(h).stream(req())]
    assert ei.value.code == ErrorCode.TRANSIENT
    assert len(calls) == 3  # retried up to 3 attempts


async def test_anthropic_retry_then_success():
    n = []

    def h(request):
        n.append(1)
        if len(n) < 3:
            return httpx.Response(500, json={"type": "error", "error": {
                "type": "api_error", "message": "boom"}})
        return ok(request)

    evs = [e async for e in make(h).stream(req())]
    assert evs[-1] == Done(stop_reason="tool_use") and len(n) == 3


@pytest.mark.parametrize("status,code", [(401, ErrorCode.DENIED), (403, ErrorCode.DENIED),
                                         (400, ErrorCode.INVALID_INPUT)])
async def test_anthropic_status_mapping(status, code):
    def h(request):
        return httpx.Response(status, json={"type": "error", "error": {
            "type": "x", "message": "nope"}})

    with pytest.raises(MisesError) as ei:
        [e async for e in make(h).stream(req())]
    assert ei.value.code == code
    if code == ErrorCode.DENIED:
        assert "ANTHROPIC_API_KEY" in ei.value.hint


async def test_anthropic_connection_error_transient():
    def h(request):
        raise httpx.ConnectError("down")

    with pytest.raises(MisesError) as ei:
        [e async for e in make(h).stream(req())]
    assert ei.value.code == ErrorCode.TRANSIENT


async def test_anthropic_request_has_cache_control():
    seen = {}

    def h(request):
        seen.update(json.loads(request.content))
        return ok(request)

    tools = [ToolSchema(name="a", description="A", input_schema={"type": "object"}),
             ToolSchema(name="b", description="B", input_schema={"type": "object"})]
    msgs = [
        Message(role="user", content=[TextBlock(text="look"),
                                      ImageBlock(media_type="image/png", data_b64="QUJD")]),
        Message(role="assistant", content=[ToolUseBlock(id="t1", name="a", input={"x": 1})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="t1", content="r",
                                                      is_error=True)]),
    ]
    [e async for e in make(h).stream(req(tools=tools, messages=msgs, temperature=0.2))]
    assert seen["system"] == [{"type": "text", "text": "be brief",
                               "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in seen["tools"][0]
    assert seen["tools"][1]["cache_control"] == {"type": "ephemeral"}
    assert seen["messages"][0]["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assert seen["messages"][2]["content"][0]["is_error"] is True
    assert "temperature" not in seen and seen["stream"] is True
