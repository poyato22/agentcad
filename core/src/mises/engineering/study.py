"""Design studies: search a parametric part's variables for the lightest variant that still
meets the spec.

The search is deliberately cheap and legible: coarse Latin-hypercube sampling to find the
feasible basin, then coordinate refinement around the best feasible point. The evaluator is
injected (the real one regenerates the part and runs a stress study per load case; tests pass a
fake), so this module has no CAD or solver dependency and is fully deterministic given a seed.

Nothing here touches the store or the event bus: callers pass `on_progress`/`is_cancelled`
callbacks. Evaluations run concurrently up to `max_parallel`; the real tool keeps that small so
the memory-hungry solver is not oversubscribed.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

MAX_EVALS = 24
MAX_MINUTES = 20.0

Metrics = dict
Evaluate = Callable[[dict], Awaitable[Metrics]]


@dataclass(frozen=True)
class Variable:
    name: str
    lo: float
    hi: float

    def __post_init__(self) -> None:
        if not (self.hi > self.lo):
            raise ValueError(f"variable {self.name!r}: hi must be greater than lo")

    def clamp(self, x: float) -> float:
        return min(self.hi, max(self.lo, x))


@dataclass(frozen=True)
class Budget:
    evals: int = MAX_EVALS
    minutes: float = MAX_MINUTES

    def clamped(self) -> "Budget":
        return Budget(evals=max(1, min(MAX_EVALS, int(self.evals))),
                      minutes=max(0.0, min(MAX_MINUTES, float(self.minutes))))


def latin_hypercube(variables: list[Variable], n: int, rng: random.Random) -> list[dict]:
    cols: list[list[float]] = []
    for v in variables:
        perm = list(range(n))
        rng.shuffle(perm)
        cols.append([v.lo + (perm[i] + rng.random()) / n * (v.hi - v.lo) for i in range(n)])
    return [{v.name: cols[j][i] for j, v in enumerate(variables)} for i in range(n)]


def min_safety_factor(metrics: Metrics) -> float | None:
    sf = metrics.get("sf_by_case")
    vals = [v for v in (sf or {}).values() if isinstance(v, (int, float))]
    return min(vals) if vals else None


# Objective is kept metric-key driven so later study types (C7: drag, modal, thermal, ...) can
# optimise their own metric without touching this search. Aliases name the common ones; the
# generic "min:<metric_key>" / "max:<metric_key>" forms cover the rest. Feasibility is a
# separate pluggable callback, so a constraint like "sf >= 2" composes with any objective.
_OBJ_ALIASES: dict[str, tuple[str, int]] = {"min_mass": ("mass_g", 1)}


def _resolve_objective(objective: str) -> tuple[str, int]:
    """(metric_key, sign): sign 1 means smaller is better, -1 means larger is better."""
    if objective in _OBJ_ALIASES:
        return _OBJ_ALIASES[objective]
    if ":" in objective:
        direction, key = objective.split(":", 1)
        key = key.strip()
        if key and direction in ("min", "max"):
            return key, 1 if direction == "min" else -1
    raise ValueError(f"unknown objective {objective!r} (use 'min_mass', 'min:<key>' or "
                     "'max:<key>')")


def _objective_value(objective: str, metrics: Metrics) -> float:
    key, sign = _resolve_objective(objective)
    v = metrics.get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return float("inf")
    return sign * float(v)


def compute_pareto(iterations: list[dict]) -> None:
    """Mark each feasible iteration pareto-optimal on (mass minimise, min safety factor
    maximise). Mutates the iterations in place; infeasible points are never on the front."""
    feas = [it for it in iterations if it["feasible"]]
    for it in iterations:
        it["pareto"] = False
    for it in feas:
        m, s = it["metrics"].get("mass_g"), min_safety_factor(it["metrics"])
        if not isinstance(m, (int, float)) or s is None:
            continue
        dominated = False
        for other in feas:
            if other is it:
                continue
            om, os_ = other["metrics"].get("mass_g"), min_safety_factor(other["metrics"])
            if not isinstance(om, (int, float)) or os_ is None:
                continue
            if om <= m and os_ >= s and (om < m or os_ > s):
                dominated = True
                break
        it["pareto"] = not dominated


async def run_study(variables: list[Variable], evaluate: Evaluate, *,
                    objective: str = "min_mass",
                    feasible: Callable[[Metrics], bool] = lambda _m: True,
                    budget: Budget | None = None,
                    on_progress: Callable[[int, int], None] | None = None,
                    is_cancelled: Callable[[], bool] | None = None,
                    max_parallel: int = 4, seed: int = 0) -> dict:
    """Search `variables` for the objective-best feasible variant within `budget`.

    Returns {variables, objective, status, iterations, best_id}, where status is "done" or
    "cancelled". Each iteration is {n, vars, metrics, feasible, pareto}; `best_id` is the n of
    the best feasible iteration, or None if none was feasible.
    """
    if not variables:
        raise ValueError("a study needs at least one variable")
    _resolve_objective(objective)  # reject an unknown objective up front
    budget = (budget or Budget()).clamped()
    rng = random.Random(seed)
    start = time.monotonic()
    iterations: list[dict] = []
    total = budget.evals
    cancelled = False
    sem = asyncio.Semaphore(max(1, max_parallel))

    def cancel_now() -> bool:
        return bool(is_cancelled and is_cancelled())

    def out_of_time() -> bool:
        return (time.monotonic() - start) >= budget.minutes * 60

    def record(vars: dict, metrics: Metrics) -> dict:
        it = {"n": len(iterations), "vars": vars, "metrics": metrics,
              "feasible": bool(feasible(metrics)), "pareto": False}
        iterations.append(it)
        if on_progress:
            on_progress(len(iterations), total)
        return it

    async def eval_point(vars: dict) -> Metrics:
        async with sem:
            return await evaluate(vars)

    # ---- coarse Latin-hypercube sweep, evaluated concurrently
    n_coarse = min(budget.evals, max(4, 2 * len(variables) + 2))
    candidates = latin_hypercube(variables, n_coarse, rng)
    ci = 0
    pending: dict[asyncio.Task, dict] = {}
    try:
        while ci < len(candidates) or pending:
            if cancel_now() or out_of_time():
                cancelled = cancel_now()
                break
            while (ci < len(candidates) and len(pending) < max_parallel
                   and len(iterations) + len(pending) < budget.evals):
                vars = candidates[ci]
                ci += 1
                pending[asyncio.create_task(eval_point(vars))] = vars
            if not pending:
                break
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                vars = pending.pop(t)
                record(vars, t.result())
    except BaseException:
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise

    # ---- coordinate refinement around the best feasible point found so far
    def best_iter() -> dict | None:
        feas = [it for it in iterations if it["feasible"]]
        pool = feas or iterations
        if not pool:
            return None
        return min(pool, key=lambda it: _objective_value(objective, it["metrics"]))

    seen = {tuple(round(it["vars"][v.name], 6) for v in variables) for it in iterations}
    delta = 0.25
    best = best_iter()
    while (not cancelled and best is not None and len(iterations) < budget.evals
           and not out_of_time()):
        if cancel_now():
            cancelled = True
            break
        improved = False
        for v in variables:
            for sign in (1, -1):
                if len(iterations) >= budget.evals or out_of_time() or cancel_now():
                    break
                cand = dict(best["vars"])
                cand[v.name] = v.clamp(best["vars"][v.name] + sign * delta * (v.hi - v.lo))
                key = tuple(round(cand[x.name], 6) for x in variables)
                if key in seen:
                    continue
                seen.add(key)
                it = record(cand, await eval_point(cand))
                if (it["feasible"] and _objective_value(objective, it["metrics"])
                        < _objective_value(objective, best["metrics"])):
                    best, improved = it, True
        cancelled = cancelled or cancel_now()
        if not improved:
            delta /= 2
            if delta < 0.02:
                break

    compute_pareto(iterations)
    best = best_iter() if not cancelled or iterations else best
    best_feasible = min((it for it in iterations if it["feasible"]),
                        key=lambda it: _objective_value(objective, it["metrics"]), default=None)
    return {
        "variables": [{"name": v.name, "lo": v.lo, "hi": v.hi} for v in variables],
        "objective": objective,
        "status": "cancelled" if cancelled else "done",
        "iterations": iterations,
        "best_id": best_feasible["n"] if best_feasible else None,
    }
