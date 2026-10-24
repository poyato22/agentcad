"""DAG scheduler: run ready tasks in parallel, skip everything downstream of a failure."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel

from .plan import Plan, PlanTask


class TaskResult(BaseModel):
    task_id: str
    status: Literal["done", "failed", "skipped", "cancelled"]
    output: str = ""
    run_id: str | None = None


Spawn = Callable[[PlanTask, dict[str, TaskResult]], Awaitable[TaskResult]]


async def run_plan(plan: Plan, spawn: Spawn, *, max_parallel: int = 3) -> dict[str, TaskResult]:
    """Run a validated plan. `spawn` receives a task and the results of its dependencies.

    A task is ready when all its dependencies are done. If any dependency ended in another
    state, the task is skipped without running (and so, transitively, is everything after it).
    A spawn that raises counts as a failed task. If the caller is cancelled, running tasks are
    cancelled too and the cancellation propagates.
    """
    max_parallel = max(1, max_parallel)
    pending = {t.id: t for t in plan.tasks}
    results: dict[str, TaskResult] = {}
    running: dict[asyncio.Task[TaskResult], PlanTask] = {}

    async def guarded(task: PlanTask, deps: dict[str, TaskResult]) -> TaskResult:
        try:
            res = await spawn(task, deps)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return TaskResult(task_id=task.id, status="failed", output=f"{type(e).__name__}: {e}")
        return res.model_copy(update={"task_id": task.id})

    try:
        while pending or running:
            progressed = True
            while progressed:
                progressed = False
                for tid, task in list(pending.items()):
                    dep_results = {d: results[d] for d in task.depends_on if d in results}
                    if len(dep_results) < len(task.depends_on):
                        continue  # a dependency is still pending or running
                    bad = [d for d, r in dep_results.items() if r.status != "done"]
                    if bad:
                        del pending[tid]
                        results[tid] = TaskResult(
                            task_id=tid, status="skipped",
                            output=f"skipped because dependency {', '.join(bad)} did not finish")
                        progressed = True
                    elif len(running) < max_parallel:
                        del pending[tid]
                        running[asyncio.create_task(guarded(task, dep_results))] = task
            if not running:
                break
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                task = running.pop(fut)
                results[task.id] = fut.result()
    except BaseException:
        for fut in running:
            fut.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        raise
    for tid in pending:  # only reachable with an invalid (cyclic) plan; never run those
        results[tid] = TaskResult(task_id=tid, status="skipped", output="unreachable dependencies")
    return {t.id: results[t.id] for t in plan.tasks}
