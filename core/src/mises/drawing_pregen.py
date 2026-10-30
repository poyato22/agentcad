"""Background drawing pre-generation.

When a part finishes building (`part_changed`), warm its 2D-drawing cache off the critical path
so that opening the drawing later is instant.

- **Never on an edit's critical path.** The part lock (which every build and edit takes) is held
  only to *snapshot* the part's model.step + metrics.json and its version, and later only to
  *install* the finished files — milliseconds each. The drawing itself renders, unlocked, from
  the snapshot. If the part moved on meanwhile (new version or new model.step) the result is
  dropped, so a stale drawing is never cached.
- **Superseded and bounded.** A newer change to a part cancels its in-flight pre-gen; a
  semaphore caps how many heavy sandboxed drawing workers run at once.
- **Sandboxed, no fork.** Each drawing runs in the sandboxed worker `_mirror_drawing` launches
  from the host; the snapshot lives under the part's own tmp/, which that sandbox can read and
  write. Nothing forks inside the sandbox.

Best-effort: a failure here is logged at debug level and swallowed; the on-demand request
reports the real error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
from collections.abc import Callable
from datetime import date as _date
from pathlib import Path

from .cad import safe_io
from .cad.tools import PART_ID_RE, _lock
from .drawing import _cached, _mirror_drawing
from .edits import read_version

log = logging.getLogger(__name__)

_SNAPSHOT_FILES = ("model.step", "metrics.json")
_OUTPUT_FILES = ("drawing.svg", "drawing.dxf")      # drawing.json goes last: it marks the cache


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
            # the id becomes a path component: never trust it, even from our own publishers
            if isinstance(part_id, str) and PART_ID_RE.fullmatch(part_id):
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
            try:
                await self._render(part_id)
            except asyncio.CancelledError:
                raise                               # superseded or shutting down
            except Exception:                       # noqa: BLE001 - best-effort warm-up
                log.debug("drawing pre-generation for %s failed", part_id, exc_info=True)

    async def _render(self, part_id: str) -> None:
        part_dir = safe_io.real_dir(self._home / "parts" / part_id)
        material = self._material() if callable(self._material) else self._material
        want = {"label": part_id, "material": material, "date": _date.today().isoformat()}

        # 1. snapshot under the lock (fast: two small reads, two writes)
        async with _lock(part_dir):
            if not safe_io.exists(part_dir, "model.step"):
                return                              # not built (or deleted): nothing to draw
            if _cached(part_dir, want) is not None:
                return                              # already warm
            version = read_version(part_dir)
            step_ns = os.lstat(part_dir / "model.step").st_mtime_ns
            safe_io.ensure_dir(part_dir / "tmp")
            snap = part_dir / "tmp" / f"pregen-{secrets.token_hex(6)}"
            safe_io.ensure_dir(snap)
            for name in _SNAPSHOT_FILES:
                if safe_io.copy_out(part_dir, name, snap) is None:
                    shutil.rmtree(snap, ignore_errors=True)
                    return
        try:
            # 2. render from the snapshot with no lock held: edits and builds go ahead
            drawing = await _mirror_drawing(snap, **want, timeout_s=60.0)
            outputs = {n: safe_io.read_bytes(snap, n) for n in _OUTPUT_FILES}
            if any(v is None for v in outputs.values()):
                return
            # 3. install only if the part is still the one we drew (milliseconds under lock)
            async with _lock(part_dir):
                if not safe_io.exists(part_dir, "model.step"):
                    return
                if (read_version(part_dir) != version
                        or os.lstat(part_dir / "model.step").st_mtime_ns != step_ns):
                    log.debug("drawing pre-generation for %s dropped: the part changed",
                              part_id)
                    return
                safe_io.unlink(part_dir, "drawing.json")     # no half-installed cache marker
                for name, data in outputs.items():
                    safe_io.write_bytes(part_dir, name, data)
                # the snapshot's own mtime is what the worker saw; the cache keys on the part's
                info = {**drawing.info, "step_mtime_ns": step_ns}
                safe_io.write_text(part_dir, "drawing.json", json.dumps(info))
        finally:
            shutil.rmtree(snap, ignore_errors=True)  # our own snapshot dir

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
