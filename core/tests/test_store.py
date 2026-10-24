import pytest
from mises.store import Store, Approval
from mises.types import StepKind, Caps, SafetyClass


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def run(store):
    return store.create_run("designer", "fake", "m", "task")


def test_step_chain(store, run):
    s1 = store.append_step(run.id, StepKind.MESSAGE, {"a": 1})
    s2 = store.append_step(run.id, StepKind.TOOL_CALL, {"b": 2})
    store.append_step(run.id, StepKind.ERROR, {"c": 3})
    assert s1.prev_hash is None and s2.prev_hash == s1.evidence_hash
    assert store.verify_chain(run.id) is True
    store._db.execute("UPDATE steps SET payload='{}' WHERE id=?", (s2.id,))
    assert store.verify_chain(run.id) is False


def test_steps_filter(store, run):
    a = store.append_step(run.id, StepKind.MESSAGE, {})
    store.append_step(run.id, StepKind.ERROR, {})
    assert [s.kind for s in store.steps(run.id, kinds=[StepKind.ERROR])] == [StepKind.ERROR]
    assert len(store.steps(run.id, after_id=a.id)) == 1


def test_policy_versions(store, run):
    p1 = store.create_policy(run.id, ["a"], ["b"], Caps(), [SafetyClass.READ])
    p2 = store.create_policy(run.id, ["a"], ["b"], Caps(max_turns=3), [SafetyClass.READ])
    assert (p1.version, p2.version) == (1, 2)
    assert store.current_policy(run.id).caps.max_turns == 3


def test_run_updates_and_usage(store, run):
    store.add_usage(run.id, input_tokens=5, usd=0.5, onshape_calls=2)
    store.add_usage(run.id, output_tokens=3)
    r = store.update_run(run.id, status="done", branch="b")
    assert (r.input_tokens, r.output_tokens, r.usd, r.onshape_calls, r.status) == (5, 3, 0.5, 2, "done")
    assert store.list_runs()[0].id == run.id


def _approval(run_id):
    return Approval(id="ap1", run_id=run_id, action="x", payload={}, payload_hash="h",
                    microversion=None, deltas={}, status="pending", expires_at=1e12,
                    decided_by=None, decided_at=None, edited_payload=None)


def test_cas_approval_first_wins(store, run):
    store.insert_approval(_approval(run.id))
    assert len(store.pending_approvals(run.id)) == 1
    assert store.cas_approval("ap1", from_status="pending", to_status="approved", decided_by="u") is True
    assert store.cas_approval("ap1", from_status="pending", to_status="rejected") is False
    assert store.get_approval("ap1").status == "approved"
    assert store.pending_approvals() == []


def test_ledger_counts_only_2xx_3xx(store, run):
    es = [store.ledger_reserve("u", run.id, "/e", 1, f"k{i}") for i in range(3)]
    for e, code in zip(es, [200, 302, 500]):
        store.ledger_settle(e.id, code)
    assert store.ledger_used("u", since=0) == 2
    e4 = store.ledger_reserve("u", run.id, "/e", 4, "k4")
    assert store.ledger_used("u", since=0) == 6
    store.ledger_release(e4.id)
    assert store.ledger_used("u", since=0) == 2


def test_release_run_reservations(store, run):
    store.ledger_reserve("u", run.id, "/e", 1, "a")
    store.ledger_reserve("u", run.id, "/e", 1, "b")
    assert store.ledger_used("u", since=0, run_id=run.id) == 2
    assert store.release_run_reservations(run.id) == 2
    assert store.ledger_used("u", since=0) == 0


def test_external_flag_defaults_false_and_persists(store, run):
    assert run.external is False
    ext = store.create_run("codex", "acp", "codex", "external agent: codex", external=True)
    assert store.get_run(ext.id).external is True
    assert {r.id: r.external for r in store.list_runs()} == {run.id: False, ext.id: True}
    # status updates keep it
    assert store.update_run(ext.id, status="done").external is True


def test_external_column_migrates_old_databases(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, provider TEXT,"
        " model TEXT, task TEXT, status TEXT, branch TEXT, onshape_calls INTEGER,"
        " input_tokens INTEGER, output_tokens INTEGER, usd REAL, created_at REAL, updated_at REAL,"
        " owner_pid INTEGER);"
        "INSERT INTO runs VALUES ('old1', NULL, 'designer', 'acp', 'm', 't', 'done', NULL, 0, 0, 0,"
        " 0.0, 1.0, 1.0, NULL);")
    db.commit()
    db.close()
    s = Store(path)
    assert s.get_run("old1").external is False
    assert s.create_run("codex", "acp", "codex", "x", external=True).external is True


def test_cache(store):
    assert store.cache_get("k") is None
    store.cache_put("k", {"v": 1})
    assert store.cache_get("k") == {"v": 1}
