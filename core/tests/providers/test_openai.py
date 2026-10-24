import json

import httpx
import pytest

from mises.providers.base import (Done, ModelRequest, TextDelta, ToolCall, ToolSchema, Usage)
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import (ErrorCode, ImageBlock, Message, MisesError, TextBlock, ToolResultBlock,
                         ToolUseBlock)


def chunk(delta=None, finish=None, usage=None):
    d = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m",
         "choices": [] if delta is None else [{"index": 0, "delta": delta,
                                               "finish_reason": finish}]}
    if usage:
        d["usage"] = usage
    return d


def sse(*chunks) -> bytes:
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def tc(index, id=None, name=None, args=None):
    f = {}
    if name is not None:
        f["name"] = name
    if args is not None:
        f["arguments"] = args
    d = {"index": index, "function": f}
    if id:
        d.update(id=id, type="function")
    return {"tool_calls": [d]}


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("openai", "sk", None, http_client=client, retry_base_delay=0, **kw)


def respond(body):
    return lambda request: httpx.Response(200, content=body,
                                          headers={"content-type": "text/event-stream"})


def req(**kw):
    base = dict(model="gpt-x", system="sys",
                messages=[Message(role="user", content=[TextBlock(text="hi")])])
    base.update(kw)
    return ModelRequest(**base)


USAGE = {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13,
         "prompt_tokens_details": {"cached_tokens": 3}}


async def test_openai_tool_call_accumulates_deltas():
    body = sse(chunk({"role": "assistant", "content": "ok "}),
               chunk(tc(0, id="call_1", name="make_box", args="")),
               chunk(tc(0, args='{"w": ')), chunk(tc(0, args="1, ")), chunk(tc(0, args='"h": 2}')),
               chunk({}, finish="tool_calls"), chunk(usage=USAGE))
    evs = [e async for e in make(respond(body)).stream(req())]
    assert evs == [TextDelta(text="ok "),
                   ToolCall(id="call_1", name="make_box", input={"w": 1, "h": 2}),
                   Usage(input_tokens=9, output_tokens=4, cache_read_tokens=3),
                   Done(stop_reason="tool_use")]


async def test_openai_two_parallel_tool_calls_by_index():
    body = sse(chunk(tc(0, id="a", name="x", args='{"p":')), chunk(tc(1, id="b", name="y", args="{}")),
               chunk(tc(0, args="1}")), chunk({}, finish="tool_calls"))
    evs = [e async for e in make(respond(body)).stream(req())]
    calls = [e for e in evs if isinstance(e, ToolCall)]
    assert [(c.id, c.name, c.input) for c in calls] == [("a", "x", {"p": 1}), ("b", "y", {})]


async def test_openai_bad_json_args():
    body = sse(chunk(tc(0, id="c1", name="t", args='{"w": ')), chunk({}, finish="tool_calls"))
    evs = [e async for e in make(respond(body)).stream(req())]
    assert ToolCall(id="c1", name="t", input={}, raw='{"w": ') in evs


@pytest.mark.parametrize("fr,sr", [("stop", "end_turn"), ("length", "max_tokens"),
                                   ("content_filter", "refusal")])
async def test_openai_finish_reasons(fr, sr):
    body = sse(chunk({"content": "x"}, finish=fr))
    evs = [e async for e in make(respond(body)).stream(req())]
    assert evs[-1] == Done(stop_reason=sr)


async def test_openai_message_mapping():
    seen = {}

    def h(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse(chunk({"content": "k"}, finish="stop")),
                              headers={"content-type": "text/event-stream"})

    msgs = [
        Message(role="user", content=[TextBlock(text="look"),
                                      ImageBlock(media_type="image/png", data_b64="QUJD")]),
        Message(role="assistant", content=[TextBlock(text="ok"),
                                           ToolUseBlock(id="t1", name="a", input={"x": 1})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="t1", content="done")]),
    ]
    tools = [ToolSchema(name="a", description="A", input_schema={"type": "object"})]
    [e async for e in make(h).stream(req(messages=msgs, tools=tools))]
    m = seen["messages"]
    assert m[0] == {"role": "system", "content": "sys"}
    assert m[1]["content"][1] == {"type": "image_url",
                                  "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert m[2]["tool_calls"] == [{"id": "t1", "type": "function",
                                   "function": {"name": "a", "arguments": '{"x": 1}'}}]
    assert m[3] == {"role": "tool", "tool_call_id": "t1", "content": "done"}
    assert seen["tools"][0]["function"]["name"] == "a"


async def test_openai_supports_tools_false_omits_tools():
    seen = {}

    def h(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse(chunk({"content": "k"}, finish="stop")),
                              headers={"content-type": "text/event-stream"})

    tools = [ToolSchema(name="a", description="A", input_schema={"type": "object"})]
    [e async for e in make(h, supports_tools=False).stream(req(tools=tools))]
    assert "tools" not in seen


async def test_openai_500_transient():
    n = []

    def h(request):
        n.append(1)
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(MisesError) as ei:
        [e async for e in make(h).stream(req())]
    assert ei.value.code == ErrorCode.TRANSIENT and len(n) == 3


@pytest.mark.parametrize("status,code", [(401, ErrorCode.DENIED), (400, ErrorCode.INVALID_INPUT)])
async def test_openai_status_mapping(status, code):
    def h(request):
        return httpx.Response(status, json={"error": {"message": "no"}})

    with pytest.raises(MisesError) as ei:
        [e async for e in make(h).stream(req())]
    assert ei.value.code == code
    if code == ErrorCode.DENIED:
        assert ei.value.hint
