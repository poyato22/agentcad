"""The render worker: renders run in a sandboxed, time- and memory-limited subprocess, the input
is bounded, and memory does not grow with how many layers a (hostile) mesh stacks per pixel."""
from __future__ import annotations

import asyncio
import os
import time

import numpy as np
import pytest

from mises.cad import render
from mises.cad.render_job import MeshIn, run_render_job
from mises.types import ErrorCode, MisesError

from .test_render import _binary_stl, _box_tris, blended_wing_tris

pytestmark = pytest.mark.slow          # spawns real sandboxed subprocesses


def _part(tmp_path, name: str, tris) -> MeshIn:
    d = tmp_path / "parts" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.stl").write_bytes(tris if isinstance(tris, bytes) else _binary_stl(tris))
    return MeshIn(d, name, (0.6, 0.6, 0.7))


def _full_frame_stack(n_tris: int) -> np.ndarray:
    """n_tris triangles, each pair a full-frame quad seen face-on from the front: every pixel
    of the front view is covered by every triangle pair (the hostile overdraw case)."""
    quads = []
    for i in range(n_tris // 2):
        a, b, c, d = (-50, i, -50), (50, i, -50), (50, i, 50), (-50, i, 50)
        quads += [[a, b, c], [a, c, d]]
    return np.array(quads, dtype=np.float64)


async def test_hostile_full_frame_stack_is_refused_fast_and_small(tmp_path):
    m = _part(tmp_path, "hostile", _full_frame_stack(1500))     # ~75 KB of STL
    t0 = time.perf_counter()
    with pytest.raises(MisesError) as e:
        await run_render_job(tmp_path / "jobs", [m], timeout_s=30, memory_mb=1024, renders=[
            {"key": "k", "views": ["front", "top", "side", "iso"], "size": 512}])
    assert e.value.code == ErrorCode.INVALID_INPUT and "too complex" in e.value.message
    assert time.perf_counter() - t0 < 30


async def test_hostile_stack_streams_in_bounded_memory_when_rendered(tmp_path):
    """With the complexity refusal lifted, the same stack is rasterised in full: memory stays
    bounded by the batch size (a streaming z-buffer), under 1 GB, within the time limit."""
    m = _part(tmp_path, "hostile", _full_frame_stack(1500))
    out = await run_render_job(tmp_path / "jobs", [m], timeout_s=120, memory_mb=1024,
                               max_layers=1e9, renders=[
                                   {"key": "k", "views": ["front"], "size": 256}])
    assert out.pngs["k"]["front"].startswith(b"\x89PNG")
    assert out.peak_rss_mb < 1024, out.peak_rss_mb


async def test_non_finite_and_outlier_vertices_are_dropped(tmp_path):
    cube = _box_tris(20, 20, 20)
    bad = np.array([[[np.nan, 0, 0], [1, 0, 0], [0, 1, 0]],
                    [[np.inf, 0, 0], [1, 0, 0], [0, 1, 0]],
                    [[1e38, 0, 0], [1, 0, 0], [0, 1, 0]],            # sets no framing any more
                    [[5e5, 5e5, 0], [5e5 + 1, 5e5, 0], [5e5, 5e5 + 1, 0]]])
    m = _part(tmp_path, "cube", np.concatenate([cube, bad]))
    out = await run_render_job(tmp_path / "jobs", [m], masks=[
        {"key": "top", "view": "top", "size": 128}])
    assert out.dropped == 4
    assert out.masks["top"].mean() > 0.5                         # the cube fills the frame


async def test_nothing_finite_is_a_clear_refusal(tmp_path):
    m = _part(tmp_path, "nan", np.full((4, 3, 3), np.nan))
    with pytest.raises(MisesError) as e:
        await run_render_job(tmp_path / "jobs", [m], renders=[
            {"key": "k", "views": ["iso"], "size": 128}])
    assert "nothing finite" in e.value.message


async def test_oversize_stl_refused_before_reading(tmp_path):
    d = tmp_path / "parts" / "huge"
    d.mkdir(parents=True)
    with open(d / "model.stl", "wb") as f:
        f.truncate(render.MAX_STL_BYTES + 1)                      # sparse: no real 50 MB write
    with pytest.raises(MisesError) as e:
        await run_render_job(tmp_path / "jobs", [MeshIn(d, "huge", (0.5, 0.5, 0.5))], renders=[
            {"key": "k", "views": ["iso"], "size": 128}])
    assert e.value.code == ErrorCode.INVALID_INPUT and "render limit" in e.value.message


async def test_triangle_cap_refused(tmp_path):
    header = b"\0" * 80 + (render.MAX_TRIANGLES + 1).to_bytes(4, "little")
    m = _part(tmp_path, "many", header + b"\0" * 200)
    with pytest.raises(MisesError) as e:
        await run_render_job(tmp_path / "jobs", [m], renders=[
            {"key": "k", "views": ["iso"], "size": 128}])
    assert "triangles" in e.value.message


async def test_render_does_not_block_the_event_loop(tmp_path):
    m = _part(tmp_path, "wing", blended_wing_tris())
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        await run_render_job(tmp_path / "jobs", [m], renders=[
            {"key": "k", "views": ["front", "top", "side", "iso"], "size": 512}])
    finally:
        t.cancel()
    assert ticks >= 10                    # the loop kept running during the render


async def test_job_dir_is_cleaned_up(tmp_path):
    m = _part(tmp_path, "cube", _box_tris(10, 10, 10))
    await run_render_job(tmp_path / "jobs", [m], renders=[{"key": "k", "views": ["iso"],
                                                           "size": 128}])
    assert not any((tmp_path / "jobs").iterdir())
