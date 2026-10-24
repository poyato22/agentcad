"""OpenAI-compatible chat.completions adapter (OpenAI, Gemini, OpenRouter, Ollama, ...)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import openai

from mises.providers.base import (Done, ModelRequest, StreamEvent, TextDelta, ToolCall, Usage,
                                  TRANSPORT_ERRORS, classify_status, classify_stream_error_type,
                                  retried_stream)
from mises.types import ErrorCode, ImageBlock, Message, MisesError, TextBlock, ToolResultBlock, \
    ToolUseBlock

_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens",
         "content_filter": "refusal"}


def _messages(system: str, msgs: list[Message]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in msgs:
        if m.role == "assistant":
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            calls = [{"id": b.id, "type": "function",
                      "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                     for b in m.content if isinstance(b, ToolUseBlock)]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # Tool results become their own role "tool" messages, placed first.
        for b in m.content:
            if isinstance(b, ToolResultBlock):
                content = f"ERROR: {b.content}" if b.is_error else b.content
                out.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": content})
        parts: list[dict] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{b.media_type};base64,{b.data_b64}"}})
        if parts:
            out.append({"role": "user", "content": parts})
    return out


def map_error(e: Exception, key_hint: str) -> MisesError:
    if isinstance(e, MisesError):
        return e
    if isinstance(e, openai.APIConnectionError) or isinstance(e, TRANSPORT_ERRORS):
        return MisesError(ErrorCode.TRANSIENT, f"connection problem: {e}")
    if isinstance(e, openai.APIStatusError):
        m = classify_status(e.status_code, key_hint=key_hint)
        if m:
            m.message = f"{m.message}: {e.message}"
            return m
    if isinstance(e, openai.APIError):  # in-stream error chunk: no HTTP status
        m = classify_stream_error_type(getattr(e, "type", None) or getattr(e, "code", None),
                                       key_hint=key_hint)
        if m:
            return m
    return MisesError(ErrorCode.TOOL_FAILED, f"provider error: {e}")


class OpenAICompatProvider:
    def __init__(self, name: str, api_key: str | None, base_url: str | None, *,
                 supports_tools: bool = True, http_client: httpx.AsyncClient | None = None,
                 retry_base_delay: float = 1.0, attempts: int = 3,
                 key_hint: str | None = None):
        self.name = name
        self.supports_tools = supports_tools
        self._hint = key_hint or f"check the {name} API key (env var or `mises keys set {name}`)"
        # Local servers (Ollama, LM Studio) need no key but the SDK demands a non-empty one.
        self._client = openai.AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url,
                                          max_retries=0, http_client=http_client)
        self._delay = retry_base_delay
        self._attempts = attempts

    def _params(self, req: ModelRequest) -> dict:
        p: dict = {"model": req.model, "messages": _messages(req.system, req.messages),
                   "max_tokens": req.max_tokens, "stream": True,
                   "stream_options": {"include_usage": True}}
        if req.tools and self.supports_tools:
            p["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in req.tools]
        if req.temperature is not None:
            p["temperature"] = req.temperature
        return p

    async def _events(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        acc: dict[int, dict] = {}
        finish: str | None = None
        in_tok = out_tok = cache_read = 0
        try:
            stream = await self._client.chat.completions.create(**self._params(req))
            async for chunk in stream:
                if chunk.usage:
                    in_tok = chunk.usage.prompt_tokens or 0
                    out_tok = chunk.usage.completion_tokens or 0
                    d = chunk.usage.prompt_tokens_details
                    cache_read = (d.cached_tokens or 0) if d else 0
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        yield TextDelta(text=delta.content)
                    for tc in delta.tool_calls or []:
                        slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            slot["name"] += tc.function.name or ""
                            slot["args"] += tc.function.arguments or ""
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except MisesError:
            raise
        except (openai.OpenAIError, httpx.HTTPError, *TRANSPORT_ERRORS) as e:
            raise map_error(e, self._hint) from e
        for i in sorted(acc):
            slot = acc[i]
            raw = slot["args"]
            try:
                parsed = json.loads(raw) if raw.strip() else {}
                if not isinstance(parsed, dict):
                    raise ValueError("arguments are not an object")
                yield ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"], input=parsed)
            except ValueError:
                yield ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"], input={}, raw=raw)
        yield Usage(input_tokens=in_tok, output_tokens=out_tok, cache_read_tokens=cache_read)
        stop = _STOP.get(finish or "stop", "end_turn")
        if acc and stop == "end_turn":
            stop = "tool_use"  # some local servers report "stop" even with tool calls
        yield Done(stop_reason=stop)

    def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        return retried_stream(lambda: self._events(req), attempts=self._attempts,
                              base_delay=self._delay)
