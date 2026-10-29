"""Plan 5 C1 store layer: builds (projects), part-tree metadata, run management."""
import pytest

from mises.store import Approval, Store
from mises.types import StepKind


@pytest.fixture
def store():
    return Store(":memory:")


def _approval(run_id):
    return Approval(id="ap1", run_id=run_id, action="x", payload={}, payload_hash="h",
                    microversion=None, deltas={}, status="pending", expires_at=1e12,
                    decided_by=None, decided_at=None, edited_payload=None)


def test_migration_assigns_existing_runs_to_default(tmp_path):
    """A DB with runs from before builds existed reopens with every run in one Default build."""
    path = tmp_path / "m.db"
    s1 = Store(path)
    s1.create_run("designer", "fake", "m", "task one")
    s1.create_run("designer", "fake", "m", "task two")
    s1._db.close()
    s2 = Store(path)  # reopen: migration runs
    projects = s2.list_projects()
    assert [p.name for p in projects] == ["Default"]
    default = projects[0]
    runs = s2.list_runs()
    assert len(runs) == 2
    assert {r.project_id for r in runs} == {default.id}


def test_fresh_db_has_no_build_until_asked(store):
    assert store.list_projects() == []
    d = store.ensure_default_project()
    assert d.name == "Default"
    assert store.ensure_default_project().id == d.id  # created once, idempotent


def test_project_crud_and_trash(store):
    p = store.create_project("Drone")
    assert store.get_project(p.id).name == "Drone"
    store.update_project(p.id, name="Drone v2")
    assert store.get_project(p.id).name == "Drone v2"
    store.update_project(p.id, trashed_at=123.0)
    assert store.get_project(p.id).trashed_at == 123.0
    assert [x.id for x in store.list_projects(include_trashed=False)] == []
    store.update_project(p.id, trashed_at=None)  # restore
    assert [x.id for x in store.list_projects(include_trashed=False)] == [p.id]
    store.purge_project(p.id)
    with pytest.raises(KeyError):
        store.get_project(p.id)


def test_run_management_fields_and_filters(store):
    p = store.create_project("B")
    a = store.create_run("designer", "fake", "m", "a", project_id=p.id)
    store.create_run("designer", "fake", "m", "b")  # no project
    store.update_run(a.id, title="arm study", pinned=True, archived=True)
    r = store.get_run(a.id)
    assert (r.title, r.pinned, r.archived) == ("arm study", True, True)
    assert [x.id for x in store.list_runs(project_id=p.id)] == [a.id]
    assert [x.id for x in store.list_runs(archived=True)] == [a.id]
    assert store.project_run_count(p.id) == 1


def test_delete_run_removes_steps_and_approvals(store):
    r = store.create_run("designer", "fake", "m", "x")
    store.append_step(r.id, StepKind.MESSAGE, {"a": 1})
    store.insert_approval(_approval(r.id))
    store.delete_run(r.id)
    with pytest.raises(KeyError):
        store.get_run(r.id)
    assert store.steps(r.id) == []
    assert store.pending_approvals(r.id) == []


def test_part_register_reorder_and_update(store):
    p = store.create_project("B")
    store.register_part("base_plate", p.id)
    store.register_part("left_arm", p.id)
    store.register_part("right_arm", p.id)
    rows = store.list_part_rows(p.id)
    assert [x.id for x in rows] == ["base_plate", "left_arm", "right_arm"]
    assert [x.order for x in rows] == [0, 1, 2]
    store.reorder_parts(p.id, None, ["right_arm", "base_plate", "left_arm"])
    assert [x.id for x in store.list_part_rows(p.id)] == ["right_arm", "base_plate", "left_arm"]
    store.update_part("base_plate", label="Base", color="#ff8800")
    b = store.get_part("base_plate")
    assert (b.label, b.color) == ("Base", "#ff8800")
    assert store.bump_part_version("base_plate") == 2


def test_register_part_is_idempotent(store):
    p = store.create_project("B")
    store.register_part("arm", p.id, label="Arm")
    store.update_part("arm", color="#123456")
    store.register_part("arm", p.id)  # must not clobber the existing row
    assert store.get_part("arm").color == "#123456"
