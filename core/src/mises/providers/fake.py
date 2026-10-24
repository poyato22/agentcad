"""Scripted provider for tests."""
from __future__ import annotations

from collections.abc import AsyncIterator

from mises.providers.base import Done, ModelRequest, StreamEvent
from mises.types import ErrorCode, MisesError


class FakeProvider:
    name = "fake"

    def __init__(self, turns: list[list[StreamEvent]]):
        self._turns = list(turns)
        self.requests: list[ModelRequest] = []

    async def stream(self, req: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(req)
        if not self._turns:
            raise MisesError(ErrorCode.TOOL_FAILED, "FakeProvider has no scripted turns left")
        events = list(self._turns.pop(0))
        for ev in events:
            yield ev
        if not any(isinstance(e, Done) for e in events):
            yield Done(stop_reason="end_turn")
