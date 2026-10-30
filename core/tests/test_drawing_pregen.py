"""U6: background drawing pre-generation on `part_changed`.

The real DrawingPregenerator, mirror_drawing/_mirror_drawing, part lock, nonce check and cache
all run; only the sandboxed worker *process* is replaced (mises.drawing.sandboxed_command now
launches a tiny fake worker that writes the three drawing files). The fake can block on a gate
file, so a test can act while a pre-gen is mid-render.
"""
import asyncio
import json
import sys
import time
from datetime import date

import pytest

from mises import drawing as drawing_mod
from mises.cad.tools import _lock
from mises.drawing import mirror_drawing
from mises.drawing_pregen import DrawingPregenerator
from mises.edits import read_version, write_version
from mises.events import Event, EventBus

FAKE_WORKER = r'''
import json, os, sys, time
from pathlib import Path
part_dir, args_path = Path(sys.argv[1]), Path(sys.argv[2])
marks, gate = Path({marks!r}), Path({gate!r})
with open(marks / "started.log", "a") as f:
    f.write(part_dir.name + "\n")
deadline = time.time() + 20
while not gate.exists() and time.time() < deadline:
    time.sleep(0.02)
args = json.loads(args_path.read_text())
(part_dir / "drawing.svg").write_text("<svg>" + args["material"] + "</svg>")
(part_dir / "drawing.dxf").write_text("0\nEOF\n")
info = {{"nonce": os.environ["MISES_NONCE"], "views": ["front", "top", "right", "iso"],
        "scale_text": "1:1", "step_mtime_ns": (part_dir / "model.step").stat().st_mtime_ns,
        **args}}
(part_dir / "drawing.json").write_text(json.dumps(info))
'''


@pytest.fixture
def worker(tmp_path, monkeypatch):
    """Swap the sandboxed worker process for the fake; returns (marks dir, gate path, calls)."""
    marks = tmp_path / "marks"
    marks.mkdir()
    gate = marks / "gate"
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER.format(marks=str(marks), gate=str(gate)))
    calls = []

    def fake_sandboxed_command(argv, write_dir, **_):
        calls.append(write_dir)
        # argv = [python, -I, WORKER, <dir>, <args.json>, --drawing]
        return [sys.executable, str(script), *argv[3:5]]
    monkeypatch.setattr(drawing_mod, "sandboxed_command", fake_sandboxed_command)
    return marks, gate, calls


def _started(marks):
    p = marks / "started.log"
    return p.read_text().split() if p.exists() else []


def _built_part(home, part_id, version=1):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "model.step").write_text("ISO-10303-21;")
    (d / "metrics.json").write_text(json.dumps({"bbox": {"size": [1, 1, 1]}}))
    write_version(d, version)
    return d


async def _until(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_part_changed_warms_the_cache_the_endpoint_reads(tmp_path, worker):
    marks, gate, calls = worker
    gate.touch()
    d = _built_part(tmp_path, "widget")
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material=lambda: "PETG")   # callable is resolved
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 1}))
    await _until(lambda: (d / "drawing.json").exists())
    await _until(lambda: not pg._tasks)
    await pg.aclose()

    info = json.loads((d / "drawing.json").read_text())
    assert info["material"] == "PETG" and "nonce" not in info
    assert info["step_mtime_ns"] == (d / "model.step").stat().st_mtime_ns
    assert not list((d / "tmp").glob("pregen-*"))          # the snapshot was cleaned up
    # the on-demand path (same label/material/date as drawing_api) is now a pure cache hit
    n = len(calls)
    res = await mirror_drawing(d, label="widget", material="PETG",
                               date=date.today().isoformat())
    assert len(calls) == n and res.svg_path.read_text() == "<svg>PETG</svg>"


async def test_edit_during_pregen_is_not_delayed_and_stale_result_is_dropped(tmp_path, worker):
    marks, gate, _ = worker                                # gate closed: the render blocks
    d = _built_part(tmp_path, "widget", version=1)
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 1}))
    await _until(lambda: len(_started(marks)) == 1)        # the worker is mid-render
    assert _started(marks)[0].startswith("pregen-")        # ... on the snapshot, not the part

    # an edit arriving now takes the part lock at once (pre-gen renders without holding it)
    t0 = time.monotonic()
    async with asyncio.timeout(0.5):
        async with _lock(d):
            write_version(d, 2)
            (d / "model.step").write_text("ISO-10303-21; edited")
    assert time.monotonic() - t0 < 0.5

    gate.touch()                                           # let the stale render finish
    await _until(lambda: not pg._tasks)
    await pg.aclose()
    assert read_version(d) == 2
    assert not (d / "drawing.json").exists()               # drawn from v1: never cached
    assert not list((d / "tmp").glob("pregen-*"))


async def test_newer_change_supersedes_older(tmp_path, worker):
    marks, gate, _ = worker
    d = _built_part(tmp_path, "widget")
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 1}))
    await _until(lambda: len(_started(marks)) == 1)
    first = pg._tasks["widget"]
    bus.publish(Event(type="part_changed", data={"part_id": "widget", "version": 2}))
    await _until(lambda: first.done())
    assert first.cancelled()                               # the stale render was cancelled
    await _until(lambda: len(_started(marks)) == 2)        # and a fresh one began
    gate.touch()
    await _until(lambda: (d / "drawing.json").exists())
    await pg.aclose()


async def test_concurrency_is_bounded(tmp_path, worker):
    marks, gate, _ = worker
    for i in range(4):
        _built_part(tmp_path, f"part{i}")
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA", max_concurrent=2)
    pg.start()
    for i in range(4):
        bus.publish(Event(type="part_changed", data={"part_id": f"part{i}"}))
    await _until(lambda: len(_started(marks)) == 2)
    await asyncio.sleep(0.5)
    assert len(_started(marks)) == 2                       # the other two wait their turn
    gate.touch()
    await _until(lambda: len(_started(marks)) == 4)
    await _until(lambda: not pg._tasks)
    await pg.aclose()


async def test_aclose_cancels_inflight_and_cleans_up(tmp_path, worker):
    marks, _gate, _ = worker
    d = _built_part(tmp_path, "widget")
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg.start()
    bus.publish(Event(type="part_changed", data={"part_id": "widget"}))
    await _until(lambda: len(_started(marks)) == 1)
    async with asyncio.timeout(5):
        await pg.aclose()                                  # kills the blocked worker
    assert pg._tasks == {}
    assert not (d / "drawing.json").exists()
    assert not list((d / "tmp").glob("pregen-*"))


async def test_unbuilt_and_bad_ids_never_reach_the_worker(tmp_path, worker):
    _marks, gate, calls = worker
    gate.touch()
    (tmp_path / "parts" / "empty").mkdir(parents=True)    # no model.step
    bus = EventBus()
    pg = DrawingPregenerator(tmp_path, bus, material="PLA")
    pg.start()
    for pid in ("empty", "missing", "../escape", "Bad-Id", 7):
        bus.publish(Event(type="part_changed", data={"part_id": pid}))
    await asyncio.sleep(0.3)
    await _until(lambda: not pg._tasks)
    await pg.aclose()
    assert calls == []
