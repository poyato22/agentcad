"""Provider seam: request/stream-event types and the retry helper."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, Protocol

import httpx
import httpx2

from pydantic import BaseModel, Field

from mises.types import ErrorCode, Message, MisesError


class ToolSchema(BaseModel):
    name: str
    description: str
    input_schema: dict


class ModelRequest(BaseModel):
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSchema] = Field(default_factory=list)
    max_tokens: int = 8192
    temperature: float | None = None
    # Plan 11 F8: the turn's reasoning effort (low|medium|high|max); each adapter maps it to
    # its vendor's control (mises.effort.control), or leaves it out where there is none
    effort: str | None = None


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDelta(BaseModel):
    """Model thinking streamed beside the answer (Kimi's `reasoning_content`). Not answer text:
    the loop keeps it on the assistant message so it can be sent back on later turns."""
    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict = Field(default_factory=dict)
    raw: str | None = None  # unparsable args string; input is {} then


class Usage(BaseModel):
    type: Literal["usage"] = "usage"
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    usd: float | None = None     # the request's dollar cost, when the provider reports it


class Done(BaseModel):
    type: Literal["done"] = "done"
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "refusal", "error"]


StreamEvent = TextDelta | ReasoningDelta | ToolCall | Usage | Done


class ModelProvider(Protocol):
    name: str

    def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Yield events; a successful stream always ends with exactly one Done.

        Providers never emit Done("error"): failures are raised as MisesError (the
        exception is the error channel); the literal exists only for consumers that
        synthesize a terminal event themselves."""
        ...


class RateLimiter:
    """At most `max_calls` request starts per sliding `period` seconds, shared by every provider
    that holds it (OpenRouter's free models: 20 requests/min per account). acquire() waits."""

    def __init__(self, max_calls: int, period: float, *,
                 clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None):
        self.max_calls, self.period = max_calls, period
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._starts: list[float] = []
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while True:
                now = self._clock()
                self._starts = [t for t in self._starts if now - t < self.period]
                if len(self._starts) < self.max_calls:
                    self._starts.append(now)
                    return
                await self._sleep(self.period - (now - self._starts[0]) + 0.01)


async def with_retries[T](fn: Callable[[], Awaitable[T]], *, attempts: int = 3,
                          base_delay: float = 1.0) -> T:
    """Call fn, retrying MisesError(TRANSIENT) with exponential backoff and jitter."""
    for i in range(attempts):
        try:
            return await fn()
        except MisesError as e:
            if e.code != ErrorCode.TRANSIENT or i == attempts - 1:
                raise
            await asyncio.sleep(base_delay * (2 ** i) * (0.5 + random.random()))
    raise AssertionError("unreachable")  # pragma: no cover


async def retried_stream(open_fn: Callable[[], AsyncIterator[StreamEvent]], *,
                         attempts: int, base_delay: float) -> AsyncIterator[StreamEvent]:
    """Retry opening a stream up to its first event. After that, errors propagate
    (a mid-stream drop is a MisesError(TRANSIENT) from the inner iterator; no replay)."""
    async def start():
        it = open_fn()
        try:
            first = await anext(it)
        except StopAsyncIteration:
            return it, None
        except BaseException:
            await it.aclose()  # type: ignore[attr-defined]
            raise
        return it, first

    it, first = await with_retries(start, attempts=attempts, base_delay=base_delay)
    try:
        if first is not None:
            yield first
            async for ev in it:
                yield ev
    finally:
        await it.aclose()  # type: ignore[attr-defined]


def classify_status(status: int, *, key_hint: str) -> MisesError | None:
    """Shared HTTP-status -> MisesError mapping used by both adapters."""
    if status in (401, 403):
        return MisesError(ErrorCode.DENIED, f"provider rejected credentials (HTTP {status})",
                          hint=key_hint)
    if status == 404:
        return MisesError(ErrorCode.NOT_FOUND, "model or endpoint not found (HTTP 404)")
    if status in (408, 409, 429) or status >= 500:
        return MisesError(ErrorCode.TRANSIENT, f"provider unavailable (HTTP {status})")
    if 400 <= status < 500:
        return MisesError(ErrorCode.INVALID_INPUT, f"provider rejected the request (HTTP {status})")
    return None



TRANSPORT_ERRORS = (httpx.TransportError, httpx2.TransportError)  # incl. timeouts, read errors


def classify_stream_error_type(err_type: str | None, *, key_hint: str) -> MisesError | None:
    """Map a provider's in-stream error type string (e.g. Anthropic `event: error`)."""
    if err_type in ("overloaded_error", "api_error", "rate_limit_error", "timeout_error",
                    "server_error", "rate_limit_exceeded", "service_unavailable"):
        return MisesError(ErrorCode.TRANSIENT, f"provider error in stream: {err_type}")
    if err_type in ("invalid_request_error", "request_too_large"):
        return MisesError(ErrorCode.INVALID_INPUT, f"provider error in stream: {err_type}")
    if err_type in ("authentication_error", "permission_error"):
        return MisesError(ErrorCode.DENIED, f"provider error in stream: {err_type}",
                          hint=key_hint)
    if err_type == "not_found_error":
        return MisesError(ErrorCode.NOT_FOUND, f"provider error in stream: {err_type}")
    return None
