"""In-process event bus with per-subscriber bounded queues."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

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

    async def subscribe(self, run_id: str | None = None) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.QUEUE_MAX)
        entry = (run_id, q)
        self._subs.append(entry)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.remove(entry)
