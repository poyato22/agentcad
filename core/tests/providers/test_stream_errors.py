"""Adapter-level mid-stream failure handling (real adapters over MockTransport)."""
import httpx
import httpx2
import pytest

from mises.providers.base import Done, ModelRequest
from mises.providers.openai_provider import OpenAICompatProvider
from mises.providers.anthropic_provider import AnthropicProvider
from mises.types import ErrorCode, Message, MisesError, TextBlock
from .test_anthropic import BODY as A_BODY, sse as a_sse
from .test_openai import chunk, sse as o_sse

REQ = ModelRequest(model="m", system="s",
                   messages=[Message(role="user", content=[TextBlock(text="hi")])])
SSE = {"content-type": "text/event-stream"}


class Truncated(httpx.AsyncByteStream, httpx2.AsyncByteStream):
    """Yields some bytes then raises a transport error, like a dropped connection."""
    def __init__(self, data, exc):
        self.data, self.exc = data, exc

    async def __aiter__(self):
        yield self.data
        raise self.exc


def a_provider(handler):
    return AnthropicProvider("k", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler)), retry_base_delay=0)


def o_provider(handler):
    return OpenAICompatProvider("openai", "k", None, http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler)), retry_base_delay=0)


A_START = a_sse(("message_start", {"type": "message_start", "message": {
    "id": "m", "type": "message", "role": "assistant", "model": "x", "content": [],
    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}))
A_TEXT = a_sse(
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "Hi"}}))
A_ERR = a_sse(("error", {"type": "error", "error": {"type": "overloaded_error",
                                                    "message": "Overloaded"}}))


def _seq(*bodies):
    """Handler returning each body in turn (last repeats). Body may be bytes or stream."""
    n = []

    def h(request):
        b = bodies[min(len(n), len(bodies) - 1)]
        n.append(1)
        return _resp(request, b)
    h.calls = n
    return h


def _resp(request, b):
    mod = httpx2 if "httpx2" in type(request).__module__ else httpx
    if isinstance(b, bytes):
        return mod.Response(200, content=b, headers=SSE)
    return mod.Response(200, stream=b, headers=SSE)


async def collect(p):
    return [e async for e in p.stream(REQ)]


# ---- Anthropic ----
async def test_anthropic_truncated_before_first_event_retried():
    h = _seq(Truncated(b"", httpx2.ReadError("cut")), A_BODY)
    evs = await collect(a_provider(h))
    assert evs[-1] == Done(stop_reason="tool_use") and len(h.calls) == 2


async def test_anthropic_error_frame_before_first_event_retried():
    h = _seq(A_START + A_ERR, A_BODY)
    evs = await collect(a_provider(h))
    assert evs[-1] == Done(stop_reason="tool_use") and len(h.calls) == 2


async def test_anthropic_truncated_after_first_event_is_transient():
    h = _seq(Truncated(A_START + A_TEXT, httpx2.ReadError("cut")))
    got = []
    with pytest.raises(MisesError) as ei:
        async for e in a_provider(h).stream(REQ):
            got.append(e)
    assert ei.value.code == ErrorCode.TRANSIENT and len(got) == 1 and len(h.calls) == 1


async def test_anthropic_error_frame_after_first_event_is_transient():
    h = _seq(A_START + A_TEXT + A_ERR)
    got = []
    with pytest.raises(MisesError) as ei:
        async for e in a_provider(h).stream(REQ):
            got.append(e)
    assert ei.value.code == ErrorCode.TRANSIENT and len(got) == 1 and len(h.calls) == 1


@pytest.mark.parametrize("etype,code", [
    ("invalid_request_error", ErrorCode.INVALID_INPUT),
    ("authentication_error", ErrorCode.DENIED), ("permission_error", ErrorCode.DENIED),
    ("rate_limit_error", ErrorCode.TRANSIENT), ("api_error", ErrorCode.TRANSIENT)])
async def test_anthropic_error_frame_types(etype, code):
    body = A_START + a_sse(("error", {"type": "error", "error": {"type": etype, "message": "m"}}))
    with pytest.raises(MisesError) as ei:
        await collect(a_provider(_seq(body)))
    assert ei.value.code == code


# ---- OpenAI-compatible ----
O_OK = o_sse(chunk({"content": "ok"}, finish="stop"))
O_FIRST = ("data: " + __import__("json").dumps(chunk({"content": "Hi"})) + "\n\n").encode()


async def test_openai_truncated_before_first_event_retried():
    h = _seq(Truncated(b"", httpx.ReadError("cut")), O_OK)
    evs = await collect(o_provider(h))
    assert evs[-1] == Done(stop_reason="end_turn") and len(h.calls) == 2


async def test_openai_error_chunk_before_first_event_retried():
    err = b'data: {"error": {"message": "overloaded", "type": "server_error"}}\n\n'
    h = _seq(err, O_OK)
    evs = await collect(o_provider(h))
    assert evs[-1] == Done(stop_reason="end_turn") and len(h.calls) == 2


async def test_openai_truncated_after_first_event_is_transient():
    h = _seq(Truncated(O_FIRST, httpx.RemoteProtocolError("cut")))
    got = []
    with pytest.raises(MisesError) as ei:
        async for e in o_provider(h).stream(REQ):
            got.append(e)
    assert ei.value.code == ErrorCode.TRANSIENT and len(got) == 1 and len(h.calls) == 1
