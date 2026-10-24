"""Plan schema and validation. A plan is a DAG of tasks, each owned by one agent."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class PlanTask(BaseModel):
    id: str
    title: str
    agent: str
    prompt: str
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    goal: str
    tasks: list[PlanTask]


def _find_cycle(tasks: dict[str, PlanTask]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    path: list[str] = []

    def visit(tid: str) -> list[str] | None:
        state[tid] = 1
        path.append(tid)
        for dep in tasks[tid].depends_on:
            if dep not in tasks or dep == tid:
                continue
            if state.get(dep) == 1:
                return path[path.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        state[tid] = 2
        return None

    for tid in tasks:
        if tid not in state:
            found = visit(tid)
            if found:
                return found
    return None


def validate_plan(plan: Plan, known_agents: set[str], max_tasks: int = 12) -> list[str]:
    """Return every problem found; an empty list means the plan can run."""
    if not plan.tasks:
        return ["plan is empty: it needs at least one task"]
    errors: list[str] = []
    if len(plan.tasks) > max_tasks:
        errors.append(f"too many tasks: {len(plan.tasks)} (maximum {max_tasks})")
    seen: dict[str, PlanTask] = {}
    for t in plan.tasks:
        if not ID_RE.match(t.id):
            errors.append(f"task id {t.id!r} is invalid: use {ID_RE.pattern}")
        if t.id in seen:
            errors.append(f"duplicate task id: {t.id}")
        else:
            seen[t.id] = t
        if t.agent not in known_agents:
            errors.append(f"task {t.id}: unknown agent {t.agent!r} "
                          f"(known: {', '.join(sorted(known_agents))})")
    for t in plan.tasks:
        for dep in t.depends_on:
            if dep == t.id:
                errors.append(f"task {t.id} depends on itself")
            elif dep not in seen:
                errors.append(f"task {t.id}: unknown dependency {dep!r}")
    cycle = _find_cycle(seen)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    return errors
