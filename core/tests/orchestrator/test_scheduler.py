import asyncio
import time

from mises.orchestrator.plan import Plan, PlanTask
from mises.orchestrator.scheduler import TaskResult, run_plan


def P(*specs):
    return Plan(goal="g", tasks=[PlanTask(id=i, title=i, agent="x", prompt="p", depends_on=d)
                                 for i, d in specs])


async def test_parallel_ready_tasks():
    seen = {}

    async def spawn(task, deps):
        seen[task.id] = dict(deps)
        await asyncio.sleep(0.2)
        return TaskResult(task_id=task.id, status="done", output=f"out-{task.id}")

    t0 = time.monotonic()
    res = await run_plan(P(("a", []), ("b", []), ("c", ["a", "b"])), spawn)
    # serial would be 0.6 s; parallel is about 0.4 s
    assert time.monotonic() - t0 < 0.55
    assert {r.status for r in res.values()} == {"done"}
    assert set(seen["c"]) == {"a", "b"} and seen["c"]["a"].output == "out-a"
    assert seen["a"] == {}


async def test_cascade_skip():
    async def spawn(task, deps):
        st = "failed" if task.id == "a" else "done"
        return TaskResult(task_id=task.id, status=st, output="x")

    res = await run_plan(P(("a", []), ("b", ["a"]), ("c", ["b"]), ("d", [])), spawn)
    assert [res[k].status for k in "abcd"] == ["failed", "skipped", "skipped", "done"]


async def test_spawn_exception_is_failure():
    async def spawn(task, deps):
        raise RuntimeError("boom")

    res = await run_plan(P(("a", []), ("b", ["a"])), spawn)
    assert res["a"].status == "failed" and "boom" in res["a"].output
    assert res["b"].status == "skipped"


async def test_max_parallel_respected():
    cur = peak = 0

    async def spawn(task, deps):
        nonlocal cur, peak
        cur += 1
        peak = max(peak, cur)
        await asyncio.sleep(0.05)
        cur -= 1
        return TaskResult(task_id=task.id, status="done")

    res = await run_plan(P(*[(f"t{i}", []) for i in range(6)]), spawn, max_parallel=2)
    assert peak == 2 and len(res) == 6


async def test_cancel_cancels_running():
    started = asyncio.Event()
    cancelled = []

    async def spawn(task, deps):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(task.id)
            raise

    t = asyncio.create_task(run_plan(P(("a", [])), spawn))
    await started.wait()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    assert cancelled == ["a"]
