import json

import httpx
import pytest

from mises.providers import ollama_provider
from mises.providers.base import Done, ModelRequest, ReasoningDelta, TextDelta, ToolCall, ToolSchema, Usage
from mises.providers.ollama_provider import OllamaProvider, native_root
from mises.types import (ErrorCode, ImageBlock, Message, MisesError, TextBlock, ToolResultBlock,
                         ToolUseBlock)


def ndjson(*chunks) -> bytes:
    return "".join(json.dumps(c) + "\n" for c in chunks).encode()


def make(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://localhost:11434/v1", http_client=client, retry_base_delay=0)


def req(**kw):
    return ModelRequest(model="qwen3:9b", system="SYS", max_tokens=512,
                        messages=[Message(role="user", content=[TextBlock(text="hi")])], **kw)


async def collect(p, r):
    return [e async for e in p.stream(r)]


def test_native_root():
    assert native_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert native_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert native_root(None) == "http://localhost:11434"


async def test_request_uses_native_chat_with_num_ctx_and_tools(monkeypatch):
    monkeypatch.delenv("MISES_OLLAMA_NUM_CTX", raising=False)
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=ndjson(
            {"message": {"role": "assistant", "content": "ok"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
             "prompt_eval_count": 4878, "eval_count": 3}))
    tools = [ToolSchema(name="mirror_measure", description="d", input_schema={"type": "object"})]
    ev = await collect(make(handler), req(tools=tools, temperature=0.2))
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["body"]["options"] == {"num_ctx": ollama_provider.DEFAULT_NUM_CTX, "num_predict": 512,
                                       "temperature": 0.2}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert seen["body"]["tools"][0]["function"]["name"] == "mirror_measure"
    assert ev == [TextDelta(text="ok"), Usage(input_tokens=4878, output_tokens=3),
                  Done(stop_reason="end_turn")]


async def test_num_ctx_env_override(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=ndjson({"message": {}, "done": True}))
    monkeypatch.setenv("MISES_OLLAMA_NUM_CTX", "32768")
    await collect(make(handler), req())
    assert seen["body"]["options"]["num_ctx"] == 32768
    monkeypatch.setenv("MISES_OLLAMA_NUM_CTX", "12")             # nonsense: the default
    await collect(make(handler), req())
    assert seen["body"]["options"]["num_ctx"] == ollama_provider.DEFAULT_NUM_CTX


async def test_tool_calls_thinking_and_stop():
    def handler(request):
        return httpx.Response(200, content=ndjson(
            {"message": {"thinking": "plan"}, "done": False},
            {"message": {"tool_calls": [{"function": {"name": "mirror_part_create",
                                                      "arguments": {"part_id": "p"}}}]}, "done": False},
            {"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{bad"}}]}, "done": False},
            {"message": {}, "done": True, "done_reason": "stop"}))
    ev = await collect(make(handler), req())
    assert ev[0] == ReasoningDelta(text="plan")
    calls = [e for e in ev if isinstance(e, ToolCall)]
    assert calls[0].name == "mirror_part_create" and calls[0].input == {"part_id": "p"}
    assert calls[1].raw == "{bad" and calls[1].input == {}
    assert ev[-1] == Done(stop_reason="tool_use")


async def test_length_is_max_tokens():
    def handler(request):
        return httpx.Response(200, content=ndjson({"message": {"content": "x"}, "done": True,
                                                   "done_reason": "length"}))
    assert (await collect(make(handler), req()))[-1] == Done(stop_reason="max_tokens")


async def test_history_mapping():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=ndjson({"message": {}, "done": True}))
    msgs = [
        Message(role="user", content=[TextBlock(text="look"), ImageBlock(media_type="image/png", data_b64="AAAA")]),
        Message(role="assistant", content=[TextBlock(text="measuring"),
                                           ToolUseBlock(id="c1", name="mirror_measure", input={"part_id": "p"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content="2 holes")]),
    ]
    await collect(make(handler), ModelRequest(model="m", system="S", messages=msgs, max_tokens=10))
    m = seen["body"]["messages"]
    assert m[1] == {"role": "user", "content": "look", "images": ["AAAA"]}
    assert m[2] == {"role": "assistant", "content": "measuring",
                    "tool_calls": [{"function": {"name": "mirror_measure", "arguments": {"part_id": "p"}}}]}
    assert m[3] == {"role": "tool", "content": "2 holes", "tool_name": "mirror_measure"}


@pytest.mark.parametrize("status,code", [(404, ErrorCode.NOT_FOUND), (401, ErrorCode.DENIED)])
async def test_http_errors(status, code):
    def handler(request):
        return httpx.Response(status, json={"error": "nope"})
    with pytest.raises(MisesError) as e:
        await collect(make(handler), req())
    assert e.value.code == code


async def test_connection_refused_is_transient():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    with pytest.raises(MisesError) as e:
        await collect(make(handler), req())
    assert e.value.code == ErrorCode.TRANSIENT
