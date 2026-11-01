"""Plan 11 F8a: each native adapter maps ModelRequest.effort to its vendor's real control."""
import json

import httpx
import httpx2
import pytest

from mises.providers.anthropic_provider import AnthropicProvider
from mises.providers.base import ModelRequest
from mises.providers.openai_provider import OpenAICompatProvider
from mises.types import Message, TextBlock

from .test_anthropic import BODY


def _req(model, effort):
    return ModelRequest(model=model, system="s", effort=effort,
                        messages=[Message(role="user", content=[TextBlock(text="hi")])])


async def _anthropic_body(model, effort) -> dict:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, content=BODY, headers={"content-type": "text/event-stream"})
    p = AnthropicProvider("sk-test", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler)), retry_base_delay=0)
    [e async for e in p.stream(_req(model, effort))]
    return seen["body"]


@pytest.mark.parametrize("model,effort,sent", [
    ("claude-opus-5-5", "max", {"effort": "max"}),
    ("claude-sonnet-5-5", "low", {"effort": "low"}),
    ("claude-opus-4-5", "max", {"effort": "high"}),
    ("claude-haiku-4-5", "high", None),          # effort is a 400 there: never sent
    ("claude-opus-5-5", None, None),             # no decision: the API default
])
async def test_anthropic_output_config_effort(model, effort, sent):
    body = await _anthropic_body(model, effort)
    assert body.get("output_config") == sent
    assert "thinking" not in body                # adaptive is the models' own default


async def _openai_body(name, model, effort) -> dict:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})
    p = OpenAICompatProvider(name, "sk", None, retry_base_delay=0,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        [e async for e in p.stream(_req(model, effort))]
    except Exception:  # noqa: BLE001 - an empty stream may end without a Done; the body is all
        pass
    return seen["body"]


@pytest.mark.parametrize("name,model,effort,sent", [
    ("openai", "gpt-5.1", "max", "high"),
    ("openai", "o4-mini", "medium", "medium"),
    ("openai", "gpt-4o", "high", None),
    ("openrouter", "gpt-5.1", "high", None),      # only OpenAI's own API takes the field
])
async def test_openai_reasoning_effort(name, model, effort, sent):
    body = await _openai_body(name, model, effort)
    assert body.get("reasoning_effort") == sent
