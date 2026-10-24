"""In-process event bus with per-subscriber bounded queues."""
from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel, Field


class Event(BaseModel):
    type: str
    run_id: str | None = None
    data: dict = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class EventBus:
    QUEUE_MAX = 1000

    def __init__(self) -> None:
        self._subs: list[tuple[str | None, asyncio.Queue[Event]]] = []

    def publish(self, event: Event) -> None:
        for run_id, q in list(self._subs):
            if run_id is not None and event.run_id != run_id:
                continue
            while True:
                try:
                    q.put_nowait(event)
                    break
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

    def subscribe(self, run_id: str | None = None) -> "Subscription":
        """Register eagerly; events published after this call are never lost."""
        sub = Subscription(self, run_id)
        self._subs.append((run_id, sub.queue))
        return sub


class Subscription:
    def __init__(self, bus: EventBus, run_id: str | None):
        self._bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=EventBus.QUEUE_MAX)
        self._entry = (run_id, self.queue)
        self._closed = False

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Event:
        if self._closed:
            raise StopAsyncIteration
        return await self.queue.get()

    async def aclose(self) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._bus._subs.remove(self._entry)
            except ValueError:
                pass

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()
