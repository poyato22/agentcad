"""Background drawing pre-generation.

When a part finishes building (`part_changed`), warm its 2D-drawing cache off the critical path
so that opening the drawing later is instant. Each part's pre-gen is superseded by the next
change to the same part (the stale one is cancelled), total concurrency is bounded (so many
builds at once never spawn an unbounded fan of heavy sandboxed workers), and every drawing still
runs in the sandboxed worker `mirror_drawing` launches from the host — nothing forks in the
sandbox. Pre-generation is best-effort: a failure here is swallowed, and the on-demand request
reports the real error.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date as _date
from pathlib import Path

from .drawing import DrawingError, mirror_drawing

log = logging.getLogger(__name__)


class DrawingPregenerator:
    def __init__(self, home: Path, events, *, material: Callable[[], str] | str,
                 max_concurrent: int = 2):
        self._home = Path(home)
        self._events = events
        self._material = material
        self._sem = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: dict[str, asyncio.Task] = {}   # part_id -> its in-flight pre-gen task
        self._consumer: asyncio.Task | None = None
        self._sub = None
        self._closed = False

    def start(self) -> None:
        """Subscribe to the event bus and begin consuming `part_changed`. Needs a running loop;
        idempotent. Subscribe before the first await so no change is missed."""
        if self._consumer is not None or self._closed:
            return
        self._sub = self._events.subscribe()
        self._consumer = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        assert self._sub is not None
        async for ev in self._sub:
            if ev.type != "part_changed":
                continue
            part_id = ev.data.get("part_id")
            if isinstance(part_id, str) and part_id:
                self._schedule(part_id)

    def _schedule(self, part_id: str) -> None:
        """Start (or restart) pre-gen for one part; a newer change supersedes an older one."""
        if self._closed:
            return
        old = self._tasks.get(part_id)
        if old is not None and not old.done():
            old.cancel()
        task = self._tasks[part_id] = asyncio.create_task(self._generate(part_id))

        def _done(t: asyncio.Task, pid: str = part_id) -> None:
            if self._tasks.get(pid) is t:
                self._tasks.pop(pid, None)
        task.add_done_callback(_done)

    async def _generate(self, part_id: str) -> None:
        async with self._sem:                       # respect the concurrency (memory) budget
            part_dir = self._home / "parts" / part_id
            if not (part_dir / "model.step").is_file():
                return                              # not built (or deleted) — nothing to draw
            material = self._material() if callable(self._material) else self._material
            try:
                await mirror_drawing(part_dir, label=part_id, material=material,
                                     date=_date.today().isoformat())
            except asyncio.CancelledError:
                raise                               # superseded or shutting down
            except (DrawingError, OSError, ValueError):
                log.debug("drawing pre-generation for %s failed (best-effort)", part_id,
                          exc_info=True)

    async def aclose(self) -> None:
        """Stop consuming and cancel every in-flight pre-gen task."""
        self._closed = True
        if self._sub is not None:
            self._sub.close()
        pending = [t for t in [self._consumer, *self._tasks.values()]
                   if t is not None and not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._consumer = None
