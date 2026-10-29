import pytest

from mises.engineering.study import (Budget, Variable, compute_pareto, min_safety_factor,
                                     run_study)


async def _bowl(vars: dict) -> dict:
    """A convex bowl in wall thickness: mass is least at wall=5 mm; safety factor grows with it."""
    x = vars["wall"]
    return {"mass_g": (x - 5.0) ** 2 + 10.0, "sf_by_case": {"drop": x / 2.0}}


async def test_study_finds_feasible_optimum_within_10pct():
    variables = [Variable("wall", 2.0, 9.0)]

    def feasible(m: dict) -> bool:
        return (min_safety_factor(m) or 0.0) >= 1.5  # wall >= 3 mm

    res = await run_study(variables, _bowl, objective="min_mass", feasible=feasible,
                          budget=Budget(evals=24, minutes=20), max_parallel=1, seed=1)
    assert res["status"] == "done"
    assert len(res["iterations"]) <= 24
    assert res["best_id"] is not None
    best = next(it for it in res["iterations"] if it["n"] == res["best_id"])
    assert best["feasible"]
    # the feasible optimum is mass 10 g at wall=5 mm; get within 10%
    assert best["metrics"]["mass_g"] <= 10.0 * 1.10


async def test_study_respects_eval_budget():
    variables = [Variable("a", 0.0, 10.0), Variable("b", 0.0, 10.0)]
    calls = 0

    async def ev(v: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"mass_g": v["a"] + v["b"], "sf_by_case": {"c": 3.0}}

    res = await run_study(variables, ev, budget=Budget(evals=6, minutes=20), max_parallel=1, seed=2)
    assert calls <= 6 and len(res["iterations"]) <= 6


async def test_study_cancels_early():
    variables = [Variable("a", 0.0, 10.0)]
    n = 0

    async def ev(v: dict) -> dict:
        nonlocal n
        n += 1
        return {"mass_g": v["a"], "sf_by_case": {"c": 3.0}}

    res = await run_study(variables, ev, budget=Budget(evals=24, minutes=20),
                          is_cancelled=lambda: n >= 3, max_parallel=1, seed=3)
    assert res["status"] == "cancelled"
    assert len(res["iterations"]) < 24


async def test_budget_is_capped():
    b = Budget(evals=999, minutes=999).clamped()
    assert b.evals == 24 and b.minutes == 20.0


async def test_generic_objective_key():
    variables = [Variable("a", 0.0, 10.0)]

    async def ev(v: dict) -> dict:
        return {"drag_n": v["a"] + 1.0, "sf_by_case": {"c": 3.0}}

    res = await run_study(variables, ev, objective="min:drag_n", max_parallel=1,
                          budget=Budget(evals=8, minutes=20), seed=4)
    assert res["best_id"] is not None
    best = next(it for it in res["iterations"] if it["n"] == res["best_id"])
    assert best["metrics"]["drag_n"] <= min(it["metrics"]["drag_n"]
                                            for it in res["iterations"]) + 1e-9


def test_pareto_flags_are_correct():
    iters = [
        {"n": 0, "vars": {}, "metrics": {"mass_g": 10, "sf_by_case": {"c": 2.0}},
         "feasible": True, "pareto": False},
        {"n": 1, "vars": {}, "metrics": {"mass_g": 12, "sf_by_case": {"c": 3.0}},
         "feasible": True, "pareto": False},
        {"n": 2, "vars": {}, "metrics": {"mass_g": 11, "sf_by_case": {"c": 1.5}},
         "feasible": True, "pareto": False},   # dominated by n=0
        {"n": 3, "vars": {}, "metrics": {"mass_g": 9, "sf_by_case": {"c": 1.0}},
         "feasible": False, "pareto": False},  # infeasible, never on the front
    ]
    compute_pareto(iters)
    assert {it["n"]: it["pareto"] for it in iters} == {0: True, 1: True, 2: False, 3: False}
