from mises.orchestrator.plan import Plan, PlanTask, validate_plan

AGENTS = {"designer", "verifier"}


def T(id, deps=(), agent="designer"):
    return PlanTask(id=id, title=id, agent=agent, prompt="p", depends_on=list(deps))


def check(tasks, **kw):
    return validate_plan(Plan(goal="g", tasks=tasks), AGENTS, **kw)


def test_valid_plan():
    assert check([T("a"), T("b", ["a"], "verifier")]) == []


def test_empty():
    assert any("empty" in e for e in check([]))


def test_cycle_names_both():
    errs = check([T("a", ["b"]), T("b", ["a"])])
    assert any("cycle" in e and "a" in e and "b" in e for e in errs)


def test_unknown_agent_dep_self_dup_and_bad_id():
    errs = " | ".join(check([T("a", ["zzz"], "ghost"), T("a"), T("b", ["b"]), T("Bad Id")]))
    assert "unknown agent 'ghost'" in errs
    assert "unknown dependency 'zzz'" in errs
    assert "duplicate task id: a" in errs
    assert "depends on itself" in errs
    assert "'Bad Id' is invalid" in errs


def test_too_many_tasks():
    assert any("too many" in e for e in check([T(f"t{i}") for i in range(13)]))
    assert check([T(f"t{i}") for i in range(12)]) == []
