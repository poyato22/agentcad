"""How many agent runs may work at once: slots, a FIFO queue, and per-model groups.

A run needs a slot to talk to its model. A run may start when fewer than `total` runs hold a
slot and, for a grouped model (one local model; all OpenRouter free models, which share one
20 requests/min account limit), fewer than `group_cap` runs of its group do. Waiters are
granted in order, but one that cannot start (its group is full) does not hold up the ones
behind it. A run that waits on its own subagents gives its slot up while it waits
(`yielding`), so a parent never deadlocks against the children it is waiting for, and takes it
back ahead of the queue.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    total: int
    group: str | None = None
    group_cap: int = 0


@dataclass
class _Waiter:
    run_id: str
    ref: str
    fut: asyncio.Future[bool]


class ParallelLimiter:
    def __init__(self, limits_for: Callable[[str], Limits]):
        self._limits_for = limits_for
        self._active: dict[str, tuple[str, str | None]] = {}   # run_id -> (ref, group)
        self._queue: list[_Waiter] = []
        self._refs: dict[str, str] = {}   # run_id -> ref, for runs that yielded their slot

    # ---- queries
    def active(self) -> set[str]:
        return set(self._active)

    def holds(self, run_id: str) -> bool:
        return run_id in self._active

    def queued(self) -> list[str]:
        return [w.run_id for w in self._queue]

    def position(self, run_id: str) -> int | None:
        for i, w in enumerate(self._queue, 1):
            if w.run_id == run_id:
                return i
        return None

    # ---- slots
    def _fits(self, ref: str) -> tuple[bool, str | None]:
        lim = self._limits_for(ref)
        if len(self._active) >= max(1, lim.total):
            return False, lim.group
        if lim.group is not None:
            used = sum(1 for _, g in self._active.values() if g == lim.group)
            if used >= max(1, lim.group_cap):
                return False, lim.group
        return True, lim.group

    def try_acquire(self, run_id: str, ref: str) -> bool:
        if run_id in self._active:
            return True
        self.pump()
        ok, group = self._fits(ref)
        if ok:
            self._active[run_id] = (ref, group)
        return ok

    def enqueue(self, run_id: str, ref: str, *, front: bool = False) -> asyncio.Future[bool]:
        """Wait for a slot: the future resolves True once the slot is held, False if withdrawn."""
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        w = _Waiter(run_id, ref, fut)
        if front:
            self._queue.insert(0, w)
        else:
            self._queue.append(w)
        self.pump()
        return fut

    def withdraw(self, run_id: str) -> bool:
        for w in self._queue:
            if w.run_id == run_id:
                self._queue.remove(w)
                if not w.fut.done():
                    w.fut.set_result(False)
                return True
        return False

    def release(self, run_id: str) -> None:
        if self._active.pop(run_id, None) is not None:
            self.pump()

    def pump(self) -> None:
        """Grant every waiter that fits now, in queue order."""
        for w in list(self._queue):
            if w.fut.done():           # cancelled by its waiter
                self._queue.remove(w)
                continue
            ok, group = self._fits(w.ref)
            if ok:
                self._queue.remove(w)
                self._active[w.run_id] = (w.ref, group)
                w.fut.set_result(True)

    async def acquire(self, run_id: str, ref: str, *, front: bool = False) -> bool:
        """try_acquire, else wait in the queue. False if withdrawn; a cancelled wait leaves
        the queue (and gives back a slot granted at the same moment)."""
        if not front and self.try_acquire(run_id, ref):
            return True
        if front and run_id not in self._active:
            ok, group = self._fits(ref)
            if ok:
                self._active[run_id] = (ref, group)
                return True
        fut = self.enqueue(run_id, ref, front=front)
        try:
            return await fut
        except asyncio.CancelledError:
            if not self.withdraw(run_id) and fut.done() and not fut.cancelled() and fut.result():
                self.release(run_id)
            raise

    @contextlib.asynccontextmanager
    async def yielding(self, run_id: str) -> AsyncIterator[None]:
        """Give the slot up while waiting on subagents; take it back (ahead of the queue)."""
        held = self._active.get(run_id)
        if held is None:
            yield
            return
        ref = held[0]
        self.release(run_id)
        try:
            yield
        except Exception:
            await self.acquire(run_id, ref, front=True)   # the run goes on after an error
            raise
        # a cancelled run is ending: it does not wait for its slot again
        await self.acquire(run_id, ref, front=True)
