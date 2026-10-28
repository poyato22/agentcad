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
    # local models: the memory this run's model needs, and what all local runs may use
    # together (a run is admitted while the sum stays within it, or when no local run works)
    local_gb: float | None = None
    budget_gb: float = 0.0


@dataclass
class _Waiter:
    run_id: str
    ref: str
    fut: asyncio.Future[bool]


class ParallelLimiter:
    def __init__(self, limits_for: Callable[[str], Limits]):
        self._limits_for = limits_for
        # run_id -> (ref, group, local_gb)
        self._active: dict[str, tuple[str, str | None, float | None]] = {}
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
    def _fits(self, ref: str) -> tuple[bool, tuple[str, str | None, float | None]]:
        lim = self._limits_for(ref)
        entry = (ref, lim.group, lim.local_gb)
        if len(self._active) >= max(1, lim.total):
            return False, entry
        if lim.group is not None:
            used = sum(1 for _, g, _ in self._active.values() if g == lim.group)
            if used >= max(1, lim.group_cap):
                return False, entry
        if lim.local_gb is not None:
            local = [gb for _, _, gb in self._active.values() if gb is not None]
            if local and sum(local) + lim.local_gb > lim.budget_gb:
                return False, entry
        return True, entry

    def try_acquire(self, run_id: str, ref: str) -> bool:
        if run_id in self._active:
            return True
        self.pump()
        ok, entry = self._fits(ref)
        if ok:
            self._active[run_id] = entry
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
            ok, entry = self._fits(w.ref)
            if ok:
                self._queue.remove(w)
                self._active[w.run_id] = entry
                w.fut.set_result(True)

    async def acquire(self, run_id: str, ref: str, *, front: bool = False) -> bool:
        """try_acquire, else wait in the queue. False if withdrawn; a cancelled wait leaves
        the queue (and gives back a slot granted at the same moment)."""
        if not front and self.try_acquire(run_id, ref):
            return True
        if front and run_id not in self._active:
            ok, entry = self._fits(ref)
            if ok:
                self._active[run_id] = entry
                return True
        fut = self.enqueue(run_id, ref, front=front)
        try:
            return await fut
        except asyncio.CancelledError:
            if not self.withdraw(run_id) and fut.done() and not fut.cancelled() and fut.result():
                self.release(run_id)
            raise

    @contextlib.asynccontextmanager
    async def yielding(self, run_id: str,
                       *, is_cancelling: Callable[[], bool] | None = None) -> AsyncIterator[None]:
        """Give the slot up while waiting on subagents; take it back (ahead of the queue).

        A cancelled wait still re-takes the slot so the run can unwind holding it, unless the
        run itself is being cancelled (`is_cancelling()` is true): then it is ending and never
        waits for a slot again.
        """
        held = self._active.get(run_id)
        if held is None:
            yield
            return
        ref = held[0]
        self.release(run_id)
        try:
            yield
        except asyncio.CancelledError:
            if not (is_cancelling is not None and is_cancelling()):
                await self.acquire(run_id, ref, front=True)
            raise
        except Exception:
            await self.acquire(run_id, ref, front=True)   # the run goes on after an error
            raise
        await self.acquire(run_id, ref, front=True)
