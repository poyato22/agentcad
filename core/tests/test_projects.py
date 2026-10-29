"""Plan 5 C1 business logic: part rows at build time, cycle-safe re-parenting, delete safety,
cleanup, the run tree and the active build."""
import json
import time

import pytest

from mises import projects
from mises.store import Approval, Store
from mises.types import ErrorCode, MisesError


@pytest.fixture
def store():
    return Store(":memory:")


def _write_part(home, part_id, vars_=None):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps(vars_ or {"length": 40.0}))
    (d / "metrics.json").write_text(json.dumps({"mass_g": 12.0}))


def _built(store, home, project_id, *names):
    """Parts built by a run in `project_id` (the cad tool's register_built_part path)."""
    run = store.create_run("designer", "fake", "m", "build", project_id=project_id)
    for n in names:
        _write_part(home, n)
        projects.register_built_part(store, run.id, n)
    return run


def _approval(run_id, aid="ap"):
    return Approval(id=aid, run_id=run_id, action="x", payload={}, payload_hash="h",
                    status="pending", expires_at=1e12)


# ---- part rows

def test_built_part_goes_to_the_creating_runs_build(store, tmp_path):
    a = store.create_project("Drone")
    b = store.create_project("Rover")
    _built(store, tmp_path, a.id, "left_arm")
    # listing another build first must not claim it
    assert projects.list_parts(store, tmp_path, b.id) == []
    rows = projects.list_parts(store, tmp_path, a.id)
    assert [r["id"] for r in rows] == ["left_arm"]
    assert rows[0]["part_id"] == "left_arm" and rows[0]["vars"] == {"length": 40.0}
    assert rows[0]["metrics"] == {"mass_g": 12.0}
    assert rows[0]["kind"] == "part" and rows[0]["version"] == 1


def test_rebuild_keeps_the_row_and_its_build(store, tmp_path):
    a = store.create_project("Drone")
    b = store.create_project("Rover")
    _built(store, tmp_path, a.id, "left_arm")
    store.update_part("left_arm", color="#123456")
    other = store.create_run("designer", "fake", "m", "rebuild", project_id=b.id)
    projects.register_built_part(store, other.id, "left_arm")
    row = store.get_part("left_arm")
    assert row.project_id == a.id and row.color == "#123456"


def test_legacy_parts_go_to_default_once(store, tmp_path):
    _write_part(tmp_path, "old_plate")
    (tmp_path / "parts" / "stray.txt").write_text("x")
    store.create_project("Drone")          # listed first: must not get the legacy part
    assert projects.register_legacy_parts(store, tmp_path) == 1
    default = store.default_project_id()
    assert store.get_part("old_plate").project_id == default
    _write_part(tmp_path, "later_plate")   # built after: never claimed by the one-shot
    assert projects.register_legacy_parts(store, tmp_path) == 0
    assert store.get_part("later_plate") is None


def test_ghost_part_rows_are_hidden(store, tmp_path):
    p = store.create_project("B")
    _built(store, tmp_path, p.id, "gone_part", "kept_part")
    (tmp_path / "parts" / "gone_part" / "vars.json").unlink()
    (tmp_path / "parts" / "gone_part" / "metrics.json").unlink()
    (tmp_path / "parts" / "gone_part").rmdir()
    store.register_part("arm_group", p.id, kind="group")   # groups have no build dir
    assert [r["id"] for r in projects.list_parts(store, tmp_path, p.id)] == ["kept_part",
                                                                            "arm_group"]


def test_bump_version_on_unknown_part_fails_loudly(store):
    with pytest.raises(KeyError):
        store.bump_part_version("never_built")
    with pytest.raises(KeyError):
        store.update_part("never_built", color="#000000")


def test_patch_color_and_label_validation(store, tmp_path):
    p = store.create_project("B")
    _built(store, tmp_path, p.id, "arm")
    projects.patch_part(store, tmp_path, "arm", label="Arm", color="#00ff88")
    assert store.get_part("arm").color == "#00ff88"
    with pytest.raises(MisesError) as e:
        projects.patch_part(store, tmp_path, "arm", color="red")
    assert e.value.code == ErrorCode.INVALID_INPUT


def test_reparent_rejects_cycle_and_other_build(store, tmp_path):
    p = store.create_project("B")
    q = store.create_project("Q")
    _built(store, tmp_path, p.id, "pa", "pb", "pc")
    _built(store, tmp_path, q.id, "qa")
    projects.patch_part(store, tmp_path, "pb", parent_id="pa")
    projects.patch_part(store, tmp_path, "pc", parent_id="pb")
    with pytest.raises(MisesError) as e:  # pa under pc would close the loop
        projects.patch_part(store, tmp_path, "pa", parent_id="pc")
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MisesError):
        projects.patch_part(store, tmp_path, "pa", parent_id="pa")
    with pytest.raises(MisesError):
        projects.patch_part(store, tmp_path, "pa", parent_id="qa")   # another build


def test_reorder_is_stable_and_validates(store, tmp_path):
    p = store.create_project("B")
    q = store.create_project("Q")
    _built(store, tmp_path, p.id, "pa", "pb", "pc")
    _built(store, tmp_path, q.id, "qa")
    out = projects.reorder_parts(store, tmp_path, p.id, None, ["pc", "pa", "pb"])
    assert [r["id"] for r in out] == ["pc", "pa", "pb"]
    assert [r["order"] for r in out] == [0, 1, 2]
    # siblings left out keep their order after the listed ones (no ord collisions)
    out = projects.reorder_parts(store, tmp_path, p.id, None, ["pb"])
    assert [(r["id"], r["order"]) for r in out] == [("pb", 0), ("pc", 1), ("pa", 2)]
    with pytest.raises(MisesError):
        projects.reorder_parts(store, tmp_path, p.id, None, ["pa", "pa"])
    with pytest.raises(MisesError):        # an id from another build
        projects.reorder_parts(store, tmp_path, p.id, None, ["pa", "qa"])
    with pytest.raises(MisesError) as e:   # a missing parent
        projects.reorder_parts(store, tmp_path, p.id, "no_such_parent", ["pa"])
    assert e.value.code == ErrorCode.NOT_FOUND
    assert store.get_part("qa").project_id == q.id


# ---- builds

def test_trash_restore_empty_removes_part_rows(store, tmp_path):
    p = projects.create_project(store, "Drone")
    _built(store, tmp_path, p["id"], "frame_plate")
    projects.trash_project(store, p["id"])
    assert projects.list_projects(store)[0]["trashed_at"] is not None
    projects.restore_project(store, p["id"])
    assert projects.list_projects(store)[0]["trashed_at"] is None
    projects.trash_project(store, p["id"])
    assert projects.empty_trash(store) == {"purged": [p["id"]], "count": 1}
    assert projects.list_projects(store) == []
    assert store.get_part("frame_plate") is None
    assert (tmp_path / "parts" / "frame_plate" / "vars.json").exists()   # files stay


def test_active_build_skips_trashed_and_checks_ids(store):
    default = store.ensure_default_project()
    drone = store.create_project("Drone")
    assert projects.active_project_id(store, drone.id) == drone.id
    store.update_project(drone.id, trashed_at=time.time())
    assert projects.active_project_id(store, drone.id) == default.id
    assert projects.active_project_id(store, "no_such_build") == default.id
    with pytest.raises(MisesError) as e:
        projects.check_project(store, drone.id)
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MisesError) as e:
        projects.check_project(store, "no_such_build")
    assert e.value.code == ErrorCode.NOT_FOUND


def test_renamed_default_is_still_default(store):
    d = store.ensure_default_project()
    store.update_project(d.id, name="Robot arm")
    store.create_project("Default")        # a user build that happens to be named Default
    assert store.ensure_default_project().id == d.id
    assert projects.active_project_id(store, None) == d.id


# ---- delete safety, cleanup, tree

def test_delete_reason(store):
    p = store.create_project("B")
    running = store.create_run("designer", "fake", "m", "x", project_id=p.id)
    assert projects.delete_reason(store, running) == ("running", [])
    done = store.create_run("designer", "fake", "m", "y", project_id=p.id)
    store.update_run(done.id, status="done")
    assert projects.delete_reason(store, store.get_run(done.id)) is None
    # a live turn the status column does not show yet counts as running
    assert projects.delete_reason(store, store.get_run(done.id),
                                  busy=lambda rid: rid == done.id) == ("running", [])
    store.insert_approval(_approval(done.id))
    assert projects.delete_reason(store, store.get_run(done.id)) == ("pending_approval", [])


def test_delete_refused_while_a_descendant_works(store):
    p = store.create_project("B")
    parent = store.create_run("planner", "fake", "m", "orch", project_id=p.id)
    store.update_run(parent.id, status="cancelled")
    child = store.create_run("designer", "fake", "m", "w", project_id=p.id, parent_id=parent.id)
    grandchild = store.create_run("sim", "fake", "m", "g", project_id=p.id,
                                  parent_id=child.id)
    store.update_run(child.id, status="done")
    assert projects.delete_reason(store, store.get_run(parent.id)) == ("running", [grandchild.id])
    store.update_run(grandchild.id, status="done")
    store.insert_approval(_approval(grandchild.id))
    assert projects.delete_reason(store, store.get_run(parent.id)) == ("running", [grandchild.id])
    store.cas_approval("ap", from_status="pending", to_status="rejected")
    assert projects.delete_reason(store, store.get_run(parent.id)) is None


def test_cleanup_excludes_running_and_respects_age(store):
    p = store.create_project("B")
    old = store.create_run("designer", "fake", "m", "old", project_id=p.id)
    store.update_run(old.id, status="done", last_activity_at=time.time() - 100 * 86400)
    recent = store.create_run("designer", "fake", "m", "recent", project_id=p.id)
    store.update_run(recent.id, status="done")
    store.create_run("designer", "fake", "m", "live", project_id=p.id)   # running
    assert projects.cleanup_candidates(store, project_id=p.id, older_than_days=30) == [old.id]
    assert set(projects.cleanup_candidates(store, project_id=p.id)) == {old.id, recent.id}
    assert projects.cleanup_candidates(store, project_id=p.id, status_in=[]) == []
    assert projects.cleanup_candidates(store, project_id=p.id, status_in=["running"]) == []
    assert projects.cleanup_candidates(store, project_id=p.id,
                                       busy=lambda rid: rid == old.id) == [recent.id]


def test_cleanup_skips_a_parent_with_a_working_child(store):
    p = store.create_project("B")
    parent = store.create_run("planner", "fake", "m", "orch", project_id=p.id)
    store.update_run(parent.id, status="done")
    store.create_run("designer", "fake", "m", "w", project_id=p.id, parent_id=parent.id)
    assert projects.cleanup_candidates(store, project_id=p.id) == []


def test_runs_tree_nests_by_parent(store):
    p = store.create_project("B")
    root = store.create_run("planner", "fake", "m", "orch", project_id=p.id)
    child = store.create_run("designer", "fake", "m", "worker", project_id=p.id,
                             parent_id=root.id)
    tree = projects.runs_tree(store, p.id)
    assert len(tree) == 1 and tree[0]["id"] == root.id
    assert [c["id"] for c in tree[0]["children"]] == [child.id]
    assert tree[0]["cost"] == {"usd": 0.0, "tokens": 0}
