"""Design studies: search a parametric part's variables for the best variant that still meets
the spec.

The search is deliberately cheap and legible: coarse Latin-hypercube sampling to find the
feasible basin, then coordinate refinement around the best feasible point. The evaluator is
injected (the real one regenerates a scratch copy of the part and runs a stress study per load
case; tests pass a fake), so this module has no CAD or solver dependency and is deterministic
given a seed.

Nothing here touches the store or the event bus: callers pass `on_iteration`/`is_cancelled`
callbacks and persist as iterations land. Evaluations run concurrently up to `max_parallel`,
and each one gets a deadline of the study's remaining time, so a slow solve cannot blow the
budget. An evaluation that errors or times out is recorded and is never feasible.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

MAX_EVALS = 24
MAX_MINUTES = 20.0

Metrics = dict
Evaluate = Callable[[dict], Awaitable[Metrics]]
# Status of a finished study: ran to its eval budget / converged, cancelled by the user, or
# stopped by its time budget. Partial results are kept in every case.
DONE, CANCELLED, BUDGET_EXHAUSTED = "done", "cancelled", "budget_exhausted"
_ERROR_KEYS = ("eval_error", "build_error")


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


@dataclass(frozen=True)
class Weight:
    """One term of a weighted objective: `goal` "min" or "max" of a metric, with a weight."""
    metric: str
    goal: str = "min"
    weight: float = 1.0


def latin_hypercube(variables: list[Variable], n: int, rng: random.Random) -> list[dict]:
    cols: list[list[float]] = []
    for v in variables:
        perm = list(range(n))
        rng.shuffle(perm)
        cols.append([v.lo + (perm[i] + rng.random()) / n * (v.hi - v.lo) for i in range(n)])
    return [{v.name: cols[j][i] for j, v in enumerate(variables)} for i in range(n)]


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def min_safety_factor(metrics: Metrics) -> float | None:
    s = _num(metrics.get("min_sf"))
    if s is not None:
        return s
    vals = [_num(v) for v in (metrics.get("sf_by_case") or {}).values()]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


# ---- objectives
# Kept metric-key driven so later study types (C7: drag, modal, thermal, ...) can optimise their
# own metric without touching this search. Aliases name the common ones; "min:<key>" /
# "max:<key>" cover the rest (e.g. "max:min_sf" for strength-first, "min:max_disp_mm" for
# stiffness-first). "weighted" combines several, each normalised by the first value seen.
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
    raise ValueError(f"unknown objective {objective!r} (use 'min_mass', 'min:<key>', "
                     "'max:<key>' or 'weighted' with weights)")


@dataclass
class Objective:
    """Smaller value() is better. Missing metrics score +inf (never preferred)."""
    objective: str = "min_mass"
    weights: tuple[Weight, ...] = ()
    _scales: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.objective == "weighted":
            if not self.weights:
                raise ValueError("a weighted objective needs weights")
            for w in self.weights:
                if w.goal not in ("min", "max") or not (w.weight > 0):
                    raise ValueError(f"bad weight {w}")
        else:
            _resolve_objective(self.objective)

    def value(self, metrics: Metrics) -> float:
        if self.objective != "weighted":
            key, sign = _resolve_objective(self.objective)
            v = _num(min_safety_factor(metrics) if key == "min_sf" else metrics.get(key))
            return float("inf") if v is None else sign * v
        total = 0.0
        for w in self.weights:
            v = _num(min_safety_factor(metrics) if w.metric == "min_sf"
                     else metrics.get(w.metric))
            if v is None:
                return float("inf")
            scale = self._scales.setdefault(w.metric, abs(v) or 1.0)
            total += w.weight * (1 if w.goal == "min" else -1) * v / scale
        return total


# Priority words from the interview -> the metric each one optimises.
PRIORITY_METRICS: dict[str, tuple[str, str]] = {
    "weight": ("mass_g", "min"),
    "impact strength": ("min_sf", "max"),
    "strength": ("min_sf", "max"),
    "stiffness": ("max_disp_mm", "min"),
    "print time": ("mass_g", "min"),  # printed mass is the honest proxy we can measure
}


def weights_from_priorities(ranking: list[str]) -> tuple[Weight, ...]:
    """A ranked priority list (first = most important) -> weights n, n-1, ... merged per metric."""
    known = [p.strip().lower() for p in ranking if p.strip().lower() in PRIORITY_METRICS]
    acc: dict[tuple[str, str], float] = {}
    for i, p in enumerate(known):
        acc[PRIORITY_METRICS[p]] = acc.get(PRIORITY_METRICS[p], 0.0) + float(len(known) - i)
    return tuple(Weight(m, g, w) for (m, g), w in acc.items())


# ---- results

def is_feasible_iteration(metrics: Metrics, feasible: Callable[[Metrics], bool]) -> bool:
    if any(metrics.get(k) for k in _ERROR_KEYS):
        return False
    try:
        return bool(feasible(metrics))
    except Exception:  # noqa: BLE001  a broken check is never a pass
        return False


def compute_pareto(iterations: list[dict]) -> None:
    """Mark each feasible iteration pareto-optimal on (mass minimise, min safety factor
    maximise). Mutates the iterations in place; infeasible points are never on the front."""
    feas = [it for it in iterations if it["feasible"]]
    for it in iterations:
        it["pareto"] = False
    for it in feas:
        m, s = _num(it["metrics"].get("mass_g")), min_safety_factor(it["metrics"])
        if m is None or s is None:
            continue
        dominated = False
        for other in feas:
            if other is it:
                continue
            om, os_ = _num(other["metrics"].get("mass_g")), min_safety_factor(other["metrics"])
            if om is None or os_ is None:
                continue
            if om <= m and os_ >= s and (om < m or os_ > s):
                dominated = True
                break
        it["pareto"] = not dominated


def best_feasible(iterations: list[dict], objective: Objective) -> dict | None:
    pool = [it for it in iterations if it["feasible"]
            and math.isfinite(objective.value(it["metrics"]))]
    return min(pool, key=lambda it: objective.value(it["metrics"]), default=None)


def finalize(iterations: list[dict], objective: Objective) -> int | None:
    """Pareto flags (in place) and the best feasible iteration's n. Safe on partial results."""
    compute_pareto(iterations)
    b = best_feasible(iterations, objective)
    return b["n"] if b else None


async def run_study(variables: list[Variable], evaluate: Evaluate, *,
                    objective: str | Objective = "min_mass",
                    feasible: Callable[[Metrics], bool] = lambda _m: True,
                    budget: Budget | None = None,
                    on_progress: Callable[[int, int], None] | None = None,
                    on_iteration: Callable[[dict, list[dict]], None] | None = None,
                    is_cancelled: Callable[[], bool] | None = None,
                    iterations: list[dict] | None = None,
                    max_parallel: int = 4, seed: int = 0) -> dict:
    """Search `variables` for the objective-best feasible variant within `budget`.

    Returns {variables, objective, status, iterations, best_id}; status is "done",
    "cancelled" or "budget_exhausted". Each iteration is {n, vars, metrics, feasible, pareto}.
    Pass your own `iterations` list to keep the partial results if the task is cancelled
    (it is filled in place, and `on_iteration` fires as each one lands).
    """
    if not variables:
        raise ValueError("a study needs at least one variable")
    obj = objective if isinstance(objective, Objective) else Objective(objective)
    budget = (budget or Budget()).clamped()
    rng = random.Random(seed)
    start = time.monotonic()
    iterations = iterations if iterations is not None else []
    total = budget.evals
    status = DONE
    sem = asyncio.Semaphore(max(1, max_parallel))

    def cancel_now() -> bool:
        return bool(is_cancelled and is_cancelled())

    def remaining() -> float:
        return budget.minutes * 60 - (time.monotonic() - start)

    def record(vars: dict, metrics: Metrics) -> dict:
        it = {"n": len(iterations), "vars": vars, "metrics": metrics,
              "feasible": is_feasible_iteration(metrics, feasible), "pareto": False}
        iterations.append(it)
        if on_progress:
            on_progress(len(iterations), total)
        if on_iteration:
            on_iteration(it, iterations)
        return it

    async def eval_point(vars: dict) -> Metrics:
        async with sem:
            left = remaining()
            if left <= 0:
                return {"eval_error": "time budget used up before this evaluation started"}
            try:
                return await asyncio.wait_for(evaluate(vars), timeout=left)
            except asyncio.TimeoutError:
                return {"eval_error": "evaluation hit the study's time budget"}
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  one bad variant must not end the study
                return {"eval_error": f"{type(e).__name__}: {e}"}

    def stop_reason() -> str | None:
        if cancel_now():
            return CANCELLED
        if remaining() <= 0:
            return BUDGET_EXHAUSTED
        return None

    # ---- coarse Latin-hypercube sweep, evaluated concurrently
    n_coarse = min(budget.evals, max(4, 2 * len(variables) + 2))
    candidates = latin_hypercube(variables, n_coarse, rng)
    ci = 0
    pending: dict[asyncio.Task, dict] = {}
    try:
        while ci < len(candidates) or pending:
            reason = stop_reason()
            if reason and not pending:
                status = reason
                break
            while (not reason and ci < len(candidates) and len(pending) < max_parallel
                   and len(iterations) + len(pending) < budget.evals):
                vars = candidates[ci]
                ci += 1
                pending[asyncio.create_task(eval_point(vars))] = vars
            if not pending:
                break
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                record(pending.pop(t), t.result())
    except BaseException:
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise

    # ---- coordinate refinement around the best feasible point found so far
    seen = {tuple(round(it["vars"][v.name], 6) for v in variables) for it in iterations}
    delta = 0.25
    best = best_feasible(iterations, obj) or (
        min(iterations, key=lambda it: obj.value(it["metrics"])) if iterations else None)
    while status == DONE and best is not None and len(iterations) < budget.evals:
        reason = stop_reason()
        if reason:
            status = reason
            break
        improved = False
        for v in variables:
            for sign in (1, -1):
                if len(iterations) >= budget.evals:
                    break
                reason = stop_reason()
                if reason:
                    status = reason
                    break
                cand = dict(best["vars"])
                cand[v.name] = v.clamp(best["vars"][v.name] + sign * delta * (v.hi - v.lo))
                key = tuple(round(cand[x.name], 6) for x in variables)
                if key in seen:
                    continue
                seen.add(key)
                it = record(cand, await eval_point(cand))
                if it["feasible"] and (not best["feasible"]
                                       or obj.value(it["metrics"]) < obj.value(best["metrics"])):
                    best, improved = it, True
            if status != DONE:
                break
        if status != DONE:
            break
        if not improved:
            delta /= 2
            if delta < 0.02:
                break

    if status == DONE and remaining() <= 0:
        status = BUDGET_EXHAUSTED  # the clock, not the search, ended it
    best_id = finalize(iterations, obj)
    return {
        "variables": [{"name": v.name, "lo": v.lo, "hi": v.hi} for v in variables],
        "objective": obj.objective,
        "status": status,
        "iterations": iterations,
        "best_id": best_id,
    }
