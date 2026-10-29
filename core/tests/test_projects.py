"""Plan 5 C1 business logic: reconcile, cycle-safe re-parenting, cleanup, run tree."""
import json

import pytest

from mises import projects
from mises.store import Store
from mises.types import ErrorCode, MisesError


@pytest.fixture
def store():
    return Store(":memory:")


def _write_part(home, part_id, vars_=None):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps(vars_ or {"length": 40.0}))
    (d / "metrics.json").write_text(json.dumps({"mass_g": 12.0}))


def test_reconcile_registers_disk_parts_into_active_build(store, tmp_path):
    _write_part(tmp_path, "base_plate")
    _write_part(tmp_path, "left_arm")
    (tmp_path / "parts" / "stray.txt").write_text("x")
    p = store.create_project("Drone")
    rows = projects.list_parts(store, tmp_path, p.id)
    assert [r["id"] for r in rows] == ["base_plate", "left_arm"]
    assert rows[0]["part_id"] == "base_plate"      # alias kept for older UI code
    assert rows[0]["vars"] == {"length": 40.0}
    assert rows[0]["metrics"] == {"mass_g": 12.0}
    assert rows[0]["kind"] == "part" and rows[0]["version"] == 1


def test_patch_color_and_label_validation(store, tmp_path):
    _write_part(tmp_path, "arm")
    p = store.create_project("B")
    projects.list_parts(store, tmp_path, p.id)
    projects.patch_part(store, tmp_path, "arm", label="Arm", color="#00ff88")
    assert store.get_part("arm").color == "#00ff88"
    with pytest.raises(MisesError) as e:
        projects.patch_part(store, tmp_path, "arm", color="red")
    assert e.value.code == ErrorCode.INVALID_INPUT


def test_reparent_rejects_cycle(store, tmp_path):
    for name in ("pa", "pb", "pc"):
        _write_part(tmp_path, name)
    p = store.create_project("B")
    projects.list_parts(store, tmp_path, p.id)
    projects.patch_part(store, tmp_path, "pb", parent_id="pa")
    projects.patch_part(store, tmp_path, "pc", parent_id="pb")
    with pytest.raises(MisesError) as e:  # a under c would close the loop a->b->c->a
        projects.patch_part(store, tmp_path, "pa", parent_id="pc")
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MisesError):
        projects.patch_part(store, tmp_path, "pa", parent_id="pa")  # self-parent


def test_reorder_is_stable_and_rejects_duplicates(store, tmp_path):
    for name in ("pa", "pb", "pc"):
        _write_part(tmp_path, name)
    p = store.create_project("B")
    projects.list_parts(store, tmp_path, p.id)
    out = projects.reorder_parts(store, tmp_path, p.id, None, ["pc", "pa", "pb"])
    assert [r["id"] for r in out] == ["pc", "pa", "pb"]
    assert [r["order"] for r in out] == [0, 1, 2]
    with pytest.raises(MisesError):
        projects.reorder_parts(store, tmp_path, p.id, None, ["pa", "pa"])


def test_trash_restore_empty(store):
    p = projects.create_project(store, "Drone")
    projects.trash_project(store, p["id"])
    assert projects.list_projects(store)[0]["trashed_at"] is not None
    projects.restore_project(store, p["id"])
    assert projects.list_projects(store)[0]["trashed_at"] is None
    projects.trash_project(store, p["id"])
    res = projects.empty_trash(store)
    assert res == {"purged": [p["id"]], "count": 1}
    assert projects.list_projects(store) == []


def test_delete_reason(store):
    p = store.create_project("B")
    running = store.create_run("designer", "fake", "m", "x", project_id=p.id)
    assert projects.delete_reason(store, running) == "running"
    done = store.create_run("designer", "fake", "m", "y", project_id=p.id)
    store.update_run(done.id, status="done")
    assert projects.delete_reason(store, store.get_run(done.id)) is None
    from mises.store import Approval
    store.insert_approval(Approval(id="ap", run_id=done.id, action="x", payload={},
                                   payload_hash="h", status="pending", expires_at=1e12))
    assert projects.delete_reason(store, store.get_run(done.id)) == "pending_approval"


def test_cleanup_excludes_running_and_respects_age(store):
    import time
    p = store.create_project("B")
    old = store.create_run("designer", "fake", "m", "old", project_id=p.id)
    store.update_run(old.id, status="done", last_activity_at=time.time() - 100 * 86400)
    recent = store.create_run("designer", "fake", "m", "recent", project_id=p.id)
    store.update_run(recent.id, status="done")
    store.create_run("designer", "fake", "m", "live", project_id=p.id)  # running: never cleaned
    ids = projects.cleanup_candidates(store, project_id=p.id, older_than_days=30)
    assert ids == [old.id]
    assert set(projects.cleanup_candidates(store, project_id=p.id)) == {old.id, recent.id}


def test_runs_tree_nests_by_parent(store):
    p = store.create_project("B")
    root = store.create_run("planner", "fake", "m", "orch", project_id=p.id)
    child = store.create_run("designer", "fake", "m", "worker", project_id=p.id,
                             parent_run_id=root.id)
    tree = projects.runs_tree(store, p.id)
    assert len(tree) == 1 and tree[0]["id"] == root.id
    assert [c["id"] for c in tree[0]["children"]] == [child.id]
    assert tree[0]["cost"] == {"usd": 0.0, "tokens": 0}
