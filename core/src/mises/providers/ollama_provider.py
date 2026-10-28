"""Ollama adapter over its native `/api/chat` (not the OpenAI-compatible `/v1`).

Why native: Ollama's `/v1/chat/completions` ignores per-request options, so every request runs
at the server's default context (4096 tokens here) and a longer prompt is silently cut to its
last ~2k tokens. The designer's first request (system rules + 24 tool schemas) is ~4.9k
tokens, so a local model never saw most of its instructions and tools. `/api/chat` takes
`options.num_ctx`, so the whole prompt fits (measured: 2050 → 4878 prompt tokens).
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx

from mises.providers.base import (Done, ModelRequest, ReasoningDelta, StreamEvent, TextDelta,
                                  ToolCall, Usage, TRANSPORT_ERRORS, classify_status,
                                  retried_stream)
from mises.providers.openai_provider import _messages
from mises.types import ErrorCode, Message, MisesError

#: Context window asked of Ollama for every request (MISES_OLLAMA_NUM_CTX overrides). 16k fits
#: the rules, the tool schemas and a working conversation; compaction keeps runs under it.
DEFAULT_NUM_CTX = 16384


def num_ctx() -> int:
    try:
        v = int(os.environ.get("MISES_OLLAMA_NUM_CTX", ""))
    except ValueError:
        return DEFAULT_NUM_CTX
    return v if 2048 <= v <= 262144 else DEFAULT_NUM_CTX


def native_root(base_url: str | None) -> str:
    """http://localhost:11434/v1 → http://localhost:11434 (the native API's root)."""
    root = (base_url or "http://localhost:11434").rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def _native_messages(system: str, msgs: list[Message]) -> list[dict]:
    """The OpenAI-shaped messages, reshaped for /api/chat: tool-call arguments are objects,
    tool results carry `tool_name`, user images go in `images` (base64, no data: prefix)."""
    names: dict[str, str] = {}
    out: list[dict] = []
    for m in _messages(system, msgs):
        role = m["role"]
        if role == "assistant":
            msg: dict = {"role": "assistant", "content": m.get("content") or ""}
            calls = []
            for c in m.get("tool_calls") or []:
                names[c["id"]] = c["function"]["name"]
                calls.append({"function": {"name": c["function"]["name"],
                                           "arguments": json.loads(c["function"]["arguments"])}})
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "content": m["content"],
                        "tool_name": names.get(m["tool_call_id"], "")})
        elif role == "user" and isinstance(m["content"], list):
            text = "\n".join(p["text"] for p in m["content"] if p["type"] == "text")
            images = [p["image_url"]["url"].split(",", 1)[1] for p in m["content"]
                      if p["type"] == "image_url"]
            msg = {"role": "user", "content": text}
            if images:
                msg["images"] = images
            out.append(msg)
        else:
            out.append({"role": role, "content": m["content"]})
    return out


class OllamaProvider:
    def __init__(self, base_url: str | None = None, *, http_client: httpx.AsyncClient | None = None,
                 retry_base_delay: float = 1.0, attempts: int = 3, timeout_s: float = 600.0):
        self.name = "ollama"
        self.supports_tools = True
        self.reasoning_content = False
        self._url = native_root(base_url) + "/api/chat"
        self._client = http_client
        self._timeout = timeout_s
        self._delay = retry_base_delay
        self._attempts = attempts
        self._hint = "check that Ollama is running (`ollama serve`) and the model is pulled"

    def _body(self, req: ModelRequest) -> dict:
        options: dict = {"num_ctx": num_ctx(), "num_predict": req.max_tokens}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        body: dict = {"model": req.model, "stream": True, "options": options,
                      "messages": _native_messages(req.system, req.messages)}
        if req.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in req.tools]
        return body

    async def _events(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        calls: list[ToolCall] = []
        done_reason = None
        in_tok = out_tok = 0
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=5))
        try:
            async with client.stream("POST", self._url, json=self._body(req)) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    m = classify_status(r.status_code, key_hint=self._hint)
                    if r.status_code == 404:
                        m = MisesError(ErrorCode.NOT_FOUND, f"Ollama has no model {req.model!r}",
                                       hint=f"pull it: `ollama pull {req.model}`")
                    raise m or MisesError(ErrorCode.TOOL_FAILED, f"Ollama error {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise MisesError(ErrorCode.TOOL_FAILED, f"Ollama error: {chunk['error']}")
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        yield ReasoningDelta(text=msg["thinking"])
                    if msg.get("content"):
                        yield TextDelta(text=msg["content"])
                    for c in msg.get("tool_calls") or []:
                        f = c.get("function") or {}
                        args = f.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except ValueError:
                                calls.append(ToolCall(id=f"call_{uuid4().hex[:12]}",
                                                      name=f.get("name", ""), input={}, raw=args))
                                continue
                        calls.append(ToolCall(id=c.get("id") or f"call_{uuid4().hex[:12]}",
                                              name=f.get("name", ""),
                                              input=args if isinstance(args, dict) else {}))
                    if chunk.get("done"):
                        done_reason = chunk.get("done_reason")
                        in_tok = chunk.get("prompt_eval_count") or 0
                        out_tok = chunk.get("eval_count") or 0
        except MisesError:
            raise
        except (httpx.HTTPError, *TRANSPORT_ERRORS) as e:
            raise MisesError(ErrorCode.TRANSIENT, f"connection problem: {e}",
                             hint=self._hint) from e
        except ValueError as e:
            raise MisesError(ErrorCode.TRANSIENT, f"Ollama sent a bad stream line: {e}") from e
        finally:
            if self._client is None:
                await client.aclose()
        for c in calls:
            yield c
        yield Usage(input_tokens=in_tok, output_tokens=out_tok)
        if calls:
            stop = "tool_use"
        elif done_reason == "length":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        yield Done(stop_reason=stop)

    def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        return retried_stream(lambda: self._events(req), attempts=self._attempts,
                              base_delay=self._delay)
