import pytest

from mises.engineering.tools import (DEFAULT_PROJECT, LoadsImpactArgs, ProjectArg, SpecCheckArgs,
                                     SpecSetArgs, StudyGetArgs, loads_impact, spec_check, spec_get,
                                     spec_set, study_get)
from mises.events import EventBus
from mises.store import AgentPolicy, Store
from mises.tools.registry import ToolContext
from mises.types import Caps, ErrorCode, MisesError, SafetyClass

pytestmark = pytest.mark.asyncio


def _ctx(tmp_path, store=None, events=None):
    store = store or Store(":memory:")
    run = store.create_run("engineer", "fake", "m", "task")
    policy = AgentPolicy(run_id=run.id, version=1, edit_scope=["*"], read_scope=["*"],
                         caps=Caps(), allowed_classes=[SafetyClass.READ, SafetyClass.LOCAL_MUTATE],
                         created_at=0.0)
    return ToolContext(run_id=run.id, user="u", store=store, events=events or EventBus(),
                       workdir=tmp_path, policy=policy, call_id="c1")


DRONE = dict(
    goal="5 inch drone frame",
    requirements=[
        {"id": "mass_budget", "kind": "max_mass_g", "target": 120, "priority": 5,
         "source": "user"},
        {"id": "arm_safety", "kind": "min_safety_factor", "target": 2.0,
         "load_case_id": "crash_drop", "priority": 4},
        {"id": "frame_envelope", "kind": "envelope_mm", "target": [220, 220, 40]},
    ],
    load_cases=[
        {"id": "hover_thrust", "name": "hover", "fix": "bottom", "load": "top",
         "newtons": 40, "direction": "-z"},
        {"id": "crash_drop", "name": "crash drop", "fix": "bottom", "load": "top",
         "newtons": 120, "direction": "-z", "estimate": True},
    ],
)


async def test_spec_set_and_get_roundtrip(tmp_path):
    ctx = _ctx(tmp_path)
    out = await spec_set.handler(ctx, SpecSetArgs(**DRONE))
    assert out.ok and out.data["project_id"] == DEFAULT_PROJECT
    got = await spec_get.handler(ctx, ProjectArg())
    spec = got.data["spec"]
    assert len(spec["requirements"]) == 3 and len(spec["load_cases"]) == 2
    assert ctx.store.get_spec(DEFAULT_PROJECT) is not None


async def test_spec_set_refuses_to_overwrite_an_unseen_user_edit(tmp_path):
    """I6: the agent's spec_set is checked against the spec it last read; a user edit made in
    between is a structured conflict with the current spec, never a silent overwrite."""
    import time
    ctx = _ctx(tmp_path)
    assert (await spec_set.handler(ctx, SpecSetArgs(**DRONE))).ok   # first write: no read needed
    got = await spec_get.handler(ctx, ProjectArg())
    assert got.data["updated_at"] is not None
    time.sleep(0.01)
    user = {**DRONE, "goal": "user's goal SF>=3"}
    ctx.store.put_spec(DEFAULT_PROJECT, {**got.data["spec"], "goal": user["goal"]})  # user edit
    out = await spec_set.handler(ctx, SpecSetArgs(**{**DRONE, "goal": "agent's goal"}))
    assert not out.ok and out.data["error"] == "stale_version"
    assert out.data["spec"]["goal"] == user["goal"]                       # current spec returned
    assert ctx.store.get_spec(DEFAULT_PROJECT)["goal"] == user["goal"]    # not overwritten
    # having seen the conflict, a retry on top of it goes through
    assert (await spec_set.handler(ctx, SpecSetArgs(**{**DRONE, "goal": "merged"}))).ok


async def test_spec_set_emits_spec_changed(tmp_path):
    bus = EventBus()
    sub = bus.subscribe()
    ctx = _ctx(tmp_path, events=bus)
    await spec_set.handler(ctx, SpecSetArgs(**DRONE))
    ev = await sub.queue.get()
    assert ev.type == "spec_changed" and ev.data["project_id"] == DEFAULT_PROJECT


async def test_spec_set_rejects_dangling_load_case(tmp_path):
    ctx = _ctx(tmp_path)
    bad = {**DRONE, "load_cases": []}  # requirement arm_safety now references a missing case
    with pytest.raises(MisesError) as e:
        await spec_set.handler(ctx, SpecSetArgs(**bad))
    assert e.value.code == ErrorCode.INVALID_INPUT


async def test_loads_impact_tool(tmp_path):
    ctx = _ctx(tmp_path)
    out = await loads_impact.handler(ctx, LoadsImpactArgs(mass_kg=0.5, drop_m=1.5, k=10))
    assert out.data["estimate"] is True and out.data["newtons"] > 0


async def test_study_get_missing(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(MisesError) as e:
        await study_get.handler(ctx, StudyGetArgs(study_id="study_000000000000"))
    assert e.value.code == ErrorCode.NOT_FOUND


async def test_spec_check_needs_a_built_part(tmp_path):
    ctx = _ctx(tmp_path)
    await spec_set.handler(ctx, SpecSetArgs(**DRONE))
    with pytest.raises(MisesError) as e:
        await spec_check.handler(ctx, SpecCheckArgs(part_id="missing_part"))
    assert e.value.code == ErrorCode.NOT_FOUND
