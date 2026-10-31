"""One global cap on concurrent heavy worker processes (final review M9).

Renders (G1's render worker, thumbnails), measure workers (their start and the one-shot
fallback), drawings (agent and background pre-generation), CFD (the lattice worker, OpenFOAM
containers), the gmsh mesher and CalculiX each start a process that can take a core and
hundreds of MB to several GB. Each kind had its own limit (or none), so together they could
oversubscribe the Mac. Every such process now takes a slot here first; the wait for a slot
does not count against the job's own timeout.

The cap is sized from the hardware (hardware.machine()): half the performance cores, and no
more than one per 2 GB of usable memory, between 1 and MAX_SLOTS; MISES_HEAVY_WORKERS overrides
it. Slots are per event loop (the core runs one), like studies.semaphore.

Renders never starve (Plan 9 D4): RENDER_RESERVE slots are kept for render kinds (the render
worker, drawings). Every other kind (the solver, the mesher, CFD, measure) shares the rest, so
a study holding every solve slot still leaves a render slot free. A one-slot machine gets one
extra slot for renders rather than none.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import weakref
from collections.abc import AsyncIterator

MAX_SLOTS = 8
GB_PER_SLOT = 2.0
RENDER_RESERVE = 1
#: kinds that may use the reserved slot: what the user looks at (views, cards, drawings)
RENDER_KINDS = frozenset({"the render", "drawing"})

_Pair = tuple[asyncio.Semaphore, asyncio.Semaphore]
# per event loop: (every heavy slot, the slots non-render kinds may take)
_sems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Pair]" = \
    weakref.WeakKeyDictionary()
_busy: dict[str, int] = {}


def heavy_limit(machine_info: dict | None = None) -> int:
    env = os.environ.get("MISES_HEAVY_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    from . import hardware
    m = machine_info or hardware.machine()
    by_cpu = max(1, int(m.get("perf_cores") or m.get("cpu_cores") or 1) // 2)
    by_mem = max(1, int(hardware.usable_memory_gb(m) // GB_PER_SLOT))   # type: ignore[arg-type]
    return max(1, min(MAX_SLOTS, by_cpu, by_mem))


def solve_limit(machine_info: dict | None = None) -> int:
    """Slots every non-render kind shares: the cap less the render reserve, at least one."""
    return max(1, heavy_limit(machine_info) - RENDER_RESERVE)


def total_slots(machine_info: dict | None = None) -> int:
    """All heavy slots: the cap, grown so the render reserve exists beside the solve slots."""
    return max(heavy_limit(machine_info), solve_limit(machine_info) + RENDER_RESERVE)


def _sem() -> _Pair:
    loop = asyncio.get_running_loop()
    s = _sems.get(loop)
    if s is None:
        s = _sems[loop] = (asyncio.Semaphore(total_slots()), asyncio.Semaphore(solve_limit()))
    return s


@contextlib.asynccontextmanager
async def heavy_slot(kind: str) -> AsyncIterator[None]:
    """Hold one heavy-worker slot while the process runs. `kind` is for status / tests; a
    render kind may take the reserved slot, any other kind waits for a solve slot first."""
    total, solve = _sem()
    general = kind not in RENDER_KINDS
    if general:
        await solve.acquire()
    try:
        await total.acquire()
    except BaseException:
        if general:
            solve.release()
        raise
    _busy[kind] = _busy.get(kind, 0) + 1
    try:
        yield
    finally:
        _busy[kind] -= 1
        if not _busy[kind]:
            del _busy[kind]
        total.release()
        if general:
            solve.release()


def in_use() -> dict[str, int]:
    """Slots held now, by kind."""
    return dict(_busy)


def reset_for_tests() -> None:
    _sems.clear()
    _busy.clear()
