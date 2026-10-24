"""Anthropic Messages API adapter."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import anthropic
import httpx2 as httpx

from mises.providers.base import (Done, ModelRequest, StreamEvent, TextDelta, ToolCall, Usage,
                                  classify_status, retried_stream)
from mises.types import ErrorCode, ImageBlock, Message, MisesError, TextBlock, ToolResultBlock, \
    ToolUseBlock

KEY_HINT = "set ANTHROPIC_API_KEY or run `mises keys set anthropic`"
_STOP = {"tool_use": "tool_use", "end_turn": "end_turn", "max_tokens": "max_tokens",
         "refusal": "refusal"}


def _block(b) -> dict:
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ImageBlock):
        return {"type": "image",
                "source": {"type": "base64", "media_type": b.media_type, "data": b.data_b64}}
    if isinstance(b, ToolUseBlock):
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if isinstance(b, ToolResultBlock):
        return {"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content,
                "is_error": b.is_error}
    raise ValueError(f"unsupported block {b!r}")


def _messages(msgs: list[Message]) -> list[dict]:
    return [{"role": m.role, "content": [_block(b) for b in m.content]} for m in msgs]


def map_error(e: Exception, key_hint: str = KEY_HINT) -> MisesError:
    if isinstance(e, MisesError):
        return e
    if isinstance(e, anthropic.APIConnectionError):  # includes timeouts
        return MisesError(ErrorCode.TRANSIENT, f"connection problem: {e}")
    if isinstance(e, anthropic.APIStatusError):
        m = classify_status(e.status_code, key_hint=key_hint)
        if m:
            m.message = f"{m.message}: {e.message}"
            return m
    return MisesError(ErrorCode.TOOL_FAILED, f"provider error: {e}")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None, *,
                 http_client: httpx.AsyncClient | None = None, retry_base_delay: float = 1.0,
                 attempts: int = 3):
        # SDK retries off: with_retries owns backoff so behaviour is uniform across vendors.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url,
                                                max_retries=0, http_client=http_client)
        self._delay = retry_base_delay
        self._attempts = attempts

    def _params(self, req: ModelRequest) -> dict:
        p: dict = {
            "model": req.model, "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": _messages(req.messages),
        }
        if req.tools:
            tools = [{"name": t.name, "description": t.description,
                      "input_schema": t.input_schema} for t in req.tools]
            tools[-1]["cache_control"] = {"type": "ephemeral"}
            p["tools"] = tools
        # req.temperature is deliberately not sent: the Anthropic API rejects sampling
        # parameters on current models and the 2.x SDK no longer accepts them.
        return p

    async def _events(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        in_tok = out_tok = cache_read = 0
        stop = "end_turn"
        try:
            async with self._client.messages.stream(**self._params(req)) as s:
                async for ev in s:
                    t = ev.type
                    if t == "message_start":
                        u = ev.message.usage
                        in_tok = u.input_tokens or 0
                        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
                        out_tok = u.output_tokens or 0
                    elif t == "content_block_delta" and ev.delta.type == "text_delta":
                        yield TextDelta(text=ev.delta.text)
                    elif t == "content_block_stop" and ev.content_block.type == "tool_use":
                        cb = ev.content_block
                        inp = cb.input if isinstance(cb.input, dict) else {}
                        yield ToolCall(id=cb.id, name=cb.name, input=inp)
                    elif t == "message_delta":
                        if ev.delta.stop_reason:
                            stop = _STOP.get(ev.delta.stop_reason, "end_turn")
                        out_tok = ev.usage.output_tokens or out_tok
        except MisesError:
            raise
        except (anthropic.APIError, httpx.HTTPError, json.JSONDecodeError) as e:
            raise map_error(e) from e
        yield Usage(input_tokens=in_tok, output_tokens=out_tok, cache_read_tokens=cache_read)
        yield Done(stop_reason=stop)

    def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        return retried_stream(lambda: self._events(req), attempts=self._attempts,
                              base_delay=self._delay)
