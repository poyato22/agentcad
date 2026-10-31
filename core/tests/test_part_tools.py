"""Plan 9 D29: agents delete parts with part_delete (Gate-classed; asks when the user edited it)."""
import asyncio
import json

import pytest

import mises.runtime as runtime_mod
from mises.assembly.model import Instance
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.types import MisesError, SafetyClass


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]] * 4)
    return Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "m"))


async def _lead(rt):
    rid = await rt.start_run("designer", "go")
    await rt.wait(rid)
    return rid


def _mk(rt, part_id, project_id):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mass_g": 1.0}))
    (d / "part.py").write_text("VARS = {}\n")
    rt.store.register_part(part_id, project_id)


def test_registered_as_a_local_mutation():
    from mises.part_tools import part_delete
    assert part_delete.safety == SafetyClass.LOCAL_MUTATE and part_delete.scope_arg == "part_id"


async def _approved(rt, run, args, approve=True):
    """Call part_delete, wait for its card (every agent delete asks), answer it."""
    task = asyncio.ensure_future(rt.gate.call(run, "part_delete", args, call_id="c1", workdir=rt.workdir(run)))
    pending = []
    for _ in range(300):
        pending = rt.store.pending_approvals(run)
        if pending:
            break
        await asyncio.sleep(0.01)
    assert len(pending) == 1 and pending[0].action == "part_delete"
    card = pending[0]
    rt.approvals.decide(card.id, card.payload_hash, approve, decided_by="local-user")
    return task, card


async def test_every_agent_delete_asks_and_then_deletes_with_its_instances_and_undo(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)                                   # an agent's own, untouched part: still asks
    rt.assembly_store.add_instance(project, Instance(part_id="arm", name="arm", fixed=True))
    events = rt.events.subscribe()
    task, card = await _approved(rt, lead, {"part_id": "arm"})
    assert card.deltas["instances"] == 1 and "delete the part arm" in card.deltas["approval_reason"]
    out = await task
    assert out.ok, out.summary
    assert out.data["deleted"] == "arm" and len(out.data["removed_instances"]) == 1
    assert rt.store.get_part("arm") is None and rt.assembly_store.get(project).instances == []
    got = []
    while not events.queue.empty():
        got.append(events.queue.get_nowait())
    assert "assembly_changed" in [e.type for e in got]
    deleted = [e for e in got if e.type == "part_deleted"]
    assert len(deleted) == 1 and deleted[0].data["undo"] == out.data["undo"] and deleted[0].data["project_id"] == project
    # the undo restores it
    from mises import projects
    projects.restore_part(rt.store, rt.home, "arm", out.data["undo"], assembly=rt.assembly_store)
    assert rt.store.get_part("arm") is not None and len(rt.assembly_store.get(project).instances) == 1


async def test_a_rejected_delete_never_runs(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)
    task, _ = await _approved(rt, lead, {"part_id": "arm"}, approve=False)
    with pytest.raises(MisesError):
        await task
    assert rt.store.get_part("arm") is not None


async def test_another_builds_part_is_out_of_reach(rt):
    lead = await _lead(rt)
    other = rt.store.create_project("Other")
    _mk(rt, "arm", other.id)
    task, _ = await _approved(rt, lead, {"part_id": "arm"})
    with pytest.raises(MisesError, match="not in this run's build"):
        await task
    assert rt.store.get_part("arm") is not None


async def test_cascade_false_is_refused_while_the_assembly_uses_it(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)
    rt.assembly_store.add_instance(project, Instance(part_id="arm", name="arm", fixed=True))
    task, _ = await _approved(rt, lead, {"part_id": "arm", "cascade": False})
    with pytest.raises(MisesError, match="in the assembly"):
        await task
    assert rt.store.get_part("arm") is not None
