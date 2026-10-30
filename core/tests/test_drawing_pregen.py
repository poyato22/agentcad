"""U6: background drawing pre-generation on `part_changed` — bounded, cancellable, no fork."""
import asyncio

import pytest

from mises import drawing_pregen
from mises.drawing_pregen import DrawingPregenerator
from mises.events import Event, EventBus


def _built_part(home, part_id):
    d = home / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.step").write_text("ISO-10303-21;")   # presence is all the pregen checks
    return d


async def test_part_changed_triggers_pregen(tmp_path):
    seen = []
    done = asyncio.Event()

    async def fake(part_dir, *, label, material, date):
        seen.append((part_dir.name, label, material))
        done.set()

    bus = EventBus()
    _built_part(tmp_path, "widget")
    pg = DrawingPregenerator(tmp_path, bus, material="PETG")
    pg._generate = _wrap(fake, pg)          # keep the semaphore, swap the drawing call
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 2}))
    await asyncio.wait_for(done.wait(), 2)
    await pg.aclose()
    assert seen == [("widget", "widget", "PETG")]


async def test_unbuilt_part_is_skipped(tmp_path):
    calls = []

    async def fake(part_dir, **k):
        calls.append(part_dir.name)

    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg._generate = _wrap(fake, pg)
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "missing"}))
    await asyncio.sleep(0.1)
    await pg.aclose()
    # the real _generate returns early when model.step is absent; our wrapper mirrors that
    assert calls == []


async def test_newer_change_supersedes_older(tmp_path):
    started, cancelled = [], []
    release = asyncio.Event()

    async def fake(part_dir, **k):
        started.append(part_dir.name)
        try:
            await release.wait()            # block so the second change can supersede
        except asyncio.CancelledError:
            cancelled.append(part_dir.name)
            raise

    bus = EventBus()
    _built_part(tmp_path, "widget")
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg._generate = _wrap(fake, pg)
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 1}))
    await asyncio.sleep(0.05)
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 2}))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.sleep(0.05)
    await pg.aclose()
    assert cancelled == ["widget"]          # the first run was superseded (cancelled)
    assert started.count("widget") == 2     # and a fresh one started


async def test_concurrency_is_bounded(tmp_path):
    peak = 0
    current = 0
    gate = asyncio.Event()

    async def fake(part_dir, **k):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        try:
            await gate.wait()
        finally:
            current -= 1

    bus = EventBus()
    for i in range(5):
        _built_part(tmp_path, f"p{i}")
    pg = DrawingPregenerator(tmp_path, bus, material="PLA", max_concurrent=2)
    pg._generate = _wrap(fake, pg)
    pg.start()
    for i in range(5):
        bus.publish(Event(type="part_changed", data={"part_id": f"p{i}"}))
    await asyncio.sleep(0.1)
    assert peak <= 2
    gate.set()
    await pg.aclose()


async def test_aclose_cancels_inflight(tmp_path):
    entered = asyncio.Event()

    async def fake(part_dir, **k):
        entered.set()
        await asyncio.sleep(100)

    bus = EventBus()
    _built_part(tmp_path, "widget")
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg._generate = _wrap(fake, pg)
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget"}))
    await asyncio.wait_for(entered.wait(), 2)
    await pg.aclose()                       # must return promptly, cancelling the task
    assert pg._tasks == {}


def _wrap(fake, pg):
    """Replace only the drawing call, keeping the semaphore + model.step gate from _generate."""
    async def _generate(part_id):
        async with pg._sem:
            part_dir = pg._home / "parts" / part_id
            if not (part_dir / "model.step").is_file():
                return
            await fake(part_dir, label=part_id, material=pg._material, date="2026-10-30")
    return _generate
