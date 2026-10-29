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


# The schema at the Plan 5 base commit (980d0be), as a Plan 4 Mises home has it on disk: the
# CREATE statements plus the three run columns that version added by ALTER TABLE.
BASE_COMMIT_DDL = """
CREATE TABLE runs (
  id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, provider TEXT, model TEXT, task TEXT,
  status TEXT, branch TEXT, onshape_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  usd REAL, created_at REAL, updated_at REAL);
CREATE TABLE policies (
  run_id TEXT, version INTEGER, edit_scope TEXT, read_scope TEXT, caps TEXT,
  allowed_classes TEXT, created_at REAL, PRIMARY KEY (run_id, version));
CREATE TABLE steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, kind TEXT, payload TEXT,
  evidence_hash TEXT, prev_hash TEXT, collapsed INTEGER);
CREATE INDEX steps_run ON steps(run_id, id);
CREATE TABLE approvals (
  id TEXT PRIMARY KEY, run_id TEXT, action TEXT, payload TEXT, payload_hash TEXT,
  microversion TEXT, deltas TEXT, status TEXT, expires_at REAL, decided_by TEXT,
  decided_at REAL, edited_payload TEXT);
CREATE TABLE ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, run_id TEXT, endpoint TEXT, estimate INTEGER,
  idempotency_key TEXT, status_code INTEGER, counted INTEGER, state TEXT, ts REAL);
CREATE TABLE tool_cache (key TEXT PRIMARY KEY, value TEXT);
ALTER TABLE runs ADD COLUMN owner_pid INTEGER;
ALTER TABLE runs ADD COLUMN external INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN session_ref TEXT;
"""


@pytest.fixture
def base_commit_db(tmp_path):
    """A database written by the base-commit Store: an orchestrator, its worker, and steps."""
    import sqlite3
    path = tmp_path / "mises.db"
    db = sqlite3.connect(path)
    db.executescript(BASE_COMMIT_DDL)
    db.executescript(
        "INSERT INTO runs VALUES ('orch1', NULL, 'planner', 'fake', 'm', 'plan', 'done', NULL,"
        " 0, 10, 5, 0.1, 1.0, 2.0, NULL, 0, NULL);"
        "INSERT INTO runs VALUES ('work1', 'orch1', 'designer', 'fake', 'm', 'arm', 'done', NULL,"
        " 0, 3, 2, 0.0, 1.5, 2.5, NULL, 0, NULL);"
        "INSERT INTO steps (run_id, ts, kind, payload, evidence_hash, prev_hash, collapsed)"
        " VALUES ('work1', 1.6, 'message', '{}', 'h1', NULL, 0);")
    db.commit()
    db.close()
    return path


def test_migration_from_base_commit_schema(base_commit_db):
    """Every ALTER applies; both runs are kept, keep their parent and steps, and land in one
    Default build."""
    s = Store(base_commit_db)
    assert [p.name for p in s.list_projects()] == ["Default"]
    default = s.default_project_id()
    runs = {r.id: r for r in s.list_runs()}
    assert set(runs) == {"orch1", "work1"}
    assert {r.project_id for r in runs.values()} == {default}
    assert runs["work1"].parent_id == "orch1"
    assert (runs["orch1"].title, runs["orch1"].pinned, runs["orch1"].archived) == (None, False,
                                                                                  False)
    assert len(s.steps("work1")) == 1


def test_migration_runs_once(base_commit_db):
    """Reopening never sweeps again: a later run without a build is not moved, and a renamed
    Default is not duplicated (Default is found by id, not name)."""
    s1 = Store(base_commit_db)
    default = s1.default_project_id()
    s1.update_project(default, name="Robot arm")
    stray = s1.create_run("designer", "fake", "m", "no build")   # direct store call, no build
    s1._db.close()
    s2 = Store(base_commit_db)
    assert [p.name for p in s2.list_projects()] == ["Robot arm"]
    assert s2.get_run(stray.id).project_id is None
    assert s2.ensure_default_project().id == default


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
