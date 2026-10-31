"""Release merge (c), M9: one global cap on concurrent heavy workers across render, measure,
drawing, CFD and CalculiX / gmsh, sized from the hardware."""
import asyncio

import pytest

from mises import heavy
from mises.sim import proc


@pytest.fixture(autouse=True)
def _fresh():
    heavy.reset_for_tests()
    yield
    heavy.reset_for_tests()


def test_the_cap_is_sized_from_the_hardware(monkeypatch):
    monkeypatch.delenv("MISES_HEAVY_WORKERS", raising=False)
    m = lambda perf, ram: {"cpu_cores": perf * 2, "perf_cores": perf, "ram_gb": ram, "chip": "x"}  # noqa: E731
    assert heavy.heavy_limit(m(6, 24)) == 3          # M5 Pro: half the P-cores
    assert heavy.heavy_limit(m(12, 8)) == 1          # memory-bound: 8 GB - 5 reserved
    assert heavy.heavy_limit(m(1, 64)) == 1
    assert heavy.heavy_limit(m(24, 192)) == heavy.MAX_SLOTS
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "5")
    assert heavy.heavy_limit(m(6, 24)) == 5


async def test_render_measure_drawing_cfd_and_ccx_share_one_cap(monkeypatch):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "2")
    now, peak = [0], [0]

    async def job(kind):
        async with heavy.heavy_slot(kind):
            now[0] += 1
            peak[0] = max(peak[0], now[0])
            assert sum(heavy.in_use().values()) <= 2
            await asyncio.sleep(0.05)
            now[0] -= 1
    await asyncio.gather(*(job(k) for k in ("the render", "measure", "drawing", "OpenFOAM",
                                            "CalculiX", "gmsh meshing")))
    assert peak[0] == 2 and heavy.in_use() == {}


async def test_run_sandboxed_takes_a_slot_and_its_wait_is_not_its_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "1")
    seen = []

    async def fake(argv, job_dir, *, timeout_s, what, **kw):
        seen.append((what, dict(heavy.in_use())))
        await asyncio.sleep(0.05)
        return 0, "", ""
    monkeypatch.setattr(proc, "_run_sandboxed", fake)
    await asyncio.gather(proc.run_sandboxed(["x"], tmp_path, timeout_s=0.06, what="CalculiX"),
                         proc.run_sandboxed(["x"], tmp_path, timeout_s=0.06, what="gmsh meshing"))
    assert seen == [("CalculiX", {"CalculiX": 1}), ("gmsh meshing", {"gmsh meshing": 1})]


# ---- Plan 9 B3 (D4): renders never starve behind the solver

async def test_a_render_gets_a_slot_while_every_solve_slot_is_held(monkeypatch):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "3")
    release = asyncio.Event()
    holding = []

    async def solve(kind):
        async with heavy.heavy_slot(kind):
            holding.append(kind)
            await release.wait()

    solvers = [asyncio.create_task(solve(k)) for k in ("CalculiX", "gmsh meshing",
                                                         "the wind solver", "OpenFOAM")]
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(holding) == 2                      # 3 slots, one reserved for renders
    assert heavy.solve_limit() == 2 and heavy.total_slots() == 3
    async with asyncio.timeout(1):                # a render does not wait for the solves
        async with heavy.heavy_slot("the render"):
            assert heavy.in_use()["the render"] == 1
        async with heavy.heavy_slot("drawing"):
            pass
    release.set()
    await asyncio.gather(*solvers)
    assert heavy.in_use() == {}


async def test_renders_may_use_every_slot_and_measure_is_not_a_render(monkeypatch):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "3")
    now, peak = {"r": 0, "m": 0}, {"r": 0, "m": 0}

    async def job(kind, key):
        async with heavy.heavy_slot(kind):
            now[key] += 1
            peak[key] = max(peak[key], now[key])
            await asyncio.sleep(0.03)
            now[key] -= 1
    await asyncio.gather(*(job("the render", "r") for _ in range(5)))
    await asyncio.gather(*(job("measure", "m") for _ in range(5)))
    assert peak == {"r": 3, "m": 2}


async def test_one_slot_machines_still_keep_a_render_slot(monkeypatch):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "1")
    assert heavy.solve_limit() == 1 and heavy.total_slots() == 2
    release = asyncio.Event()

    async def solve():
        async with heavy.heavy_slot("CalculiX"):
            await release.wait()
    t = asyncio.create_task(solve())
    await asyncio.sleep(0)
    async with asyncio.timeout(1):
        async with heavy.heavy_slot("the render"):
            pass
    release.set()
    await t
