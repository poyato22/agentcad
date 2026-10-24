"""Provider seam: request/stream-event types and the retry helper."""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal, Protocol

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


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
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


class Done(BaseModel):
    type: Literal["done"] = "done"
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "refusal", "error"]


StreamEvent = TextDelta | ToolCall | Usage | Done


class ModelProvider(Protocol):
    name: str

    def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        """Yield events; a successful stream always ends with exactly one Done."""
        ...


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

