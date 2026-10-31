"""Plan 9 D29: agents delete parts with part_delete (Gate-classed; asks when the user edited it)."""
import asyncio
import json

import pytest

import mises.runtime as runtime_mod
from mises import edits
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


async def test_an_agent_deletes_a_part_and_its_instances(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)
    rt.assembly_store.add_instance(project, Instance(part_id="arm", name="arm", fixed=True))
    events = rt.events.subscribe()
    out = await rt.gate.call(lead, "part_delete", {"part_id": "arm"}, call_id="c1",
                             workdir=rt.workdir(lead))
    assert out.ok, out.summary
    assert out.data["deleted"] == "arm" and len(out.data["removed_instances"]) == 1
    assert rt.store.get_part("arm") is None and rt.assembly_store.get(project).instances == []
    types = []
    while not events.queue.empty():
        types.append(events.queue.get_nowait().type)
    assert "assembly_changed" in types and "part_changed" in types
    assert rt.store.pending_approvals(lead) == []


async def test_cascade_false_is_refused_while_the_assembly_uses_it(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)
    rt.assembly_store.add_instance(project, Instance(part_id="arm", name="arm", fixed=True))
    with pytest.raises(MisesError, match="in the assembly"):
        await rt.gate.call(lead, "part_delete", {"part_id": "arm", "cascade": False},
                           call_id="c1", workdir=rt.workdir(lead))
    assert rt.store.get_part("arm") is not None


async def test_a_part_the_user_edited_needs_approval(rt):
    lead = await _lead(rt)
    project = rt.store.get_run(lead).project_id
    _mk(rt, "arm", project)
    edits.log_user_edit(rt.home, "arm", version=2, summary="set wall=3", run_id=None)
    task = asyncio.ensure_future(rt.gate.call(lead, "part_delete", {"part_id": "arm"},
                                              call_id="c1", workdir=rt.workdir(lead)))
    pending = []
    for _ in range(300):
        pending = rt.store.pending_approvals(lead)
        if pending:
            break
        await asyncio.sleep(0.01)
    assert len(pending) == 1 and pending[0].action == "part_delete"
    assert "you edited" in pending[0].deltas["approval_reason"]
    assert pending[0].deltas["instances"] == 0
    assert rt.store.get_part("arm") is not None              # nothing happens before the answer
    rt.approvals.decide(pending[0].id, pending[0].payload_hash, False, decided_by="local-user")
    with pytest.raises(MisesError):
        await task
    assert rt.store.get_part("arm") is not None
