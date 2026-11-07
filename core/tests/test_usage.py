"""Per-turn and per-build usage (tokens and dollars): every add_usage with tokens or dollars is
a usage event tagged with its root run and the root's turn, so a turn's figure includes its
sub-agents and a build's total is the sum of its turns. Persisted in the store (restarts)."""
import pytest

from mises import usage
from mises.store import Store


def _lead(store, project="p1", model="claude-opus-4-5"):
    rid = store.create_run("lead", "claude-code", model, "t", project_id=project).id
    store.update_run(rid, role="lead")
    return rid


def test_turn_includes_its_sub_agents(tmp_path):
    s = Store(tmp_path / "mises.db")
    lead = _lead(s)
    s.start_turn(lead, 100.0, 0.0)
    s.add_usage(lead, input_tokens=10, output_tokens=20, cache_read_tokens=1000,
                cache_write_tokens=50, usd=0.10)
    sub = s.create_run("designer", "claude-code", "claude-sonnet-4-5", "s", parent_id=lead,
                       project_id="p1").id
    s.add_usage(sub, input_tokens=5, output_tokens=7, usd=0.02)
    s.start_turn(lead, 200.0, 0.12)
    s.add_usage(lead, input_tokens=1, output_tokens=2, usd=0.01)
    turns = usage.run_turns(s, sub)          # any run of the family gives the root's turns
    assert [t["turn_ts"] for t in turns] == [100.0, 200.0]
    t1 = turns[0]
    assert t1["usd"] == pytest.approx(0.12)
    assert t1["tokens"] == 10 + 20 + 1000 + 50 + 5 + 7
    assert t1["cache_read_tokens"] == 1000 and t1["cache_write_tokens"] == 50
    by = {a["run_id"]: a for a in t1["agents"]}
    assert by[lead]["role"] == "lead" and by[lead]["model"] == "claude-opus-4-5"
    assert by[sub]["usd"] == pytest.approx(0.02) and by[sub]["model"] == "claude-sonnet-4-5"
    # the run row keeps its old meaning (Claude Code input counts cache tokens)
    assert s.get_run(lead).input_tokens == 10 + 1000 + 50 + 1


def test_build_total_is_the_sum_of_turns_and_survives_restart(tmp_path):
    db = tmp_path / "mises.db"
    s = Store(db)
    lead = _lead(s)
    s.start_turn(lead, 100.0, 0.0)
    s.add_usage(lead, input_tokens=100, output_tokens=50, usd=0.5)
    s.start_turn(lead, 200.0, 0.5)
    s.add_usage(lead, input_tokens=10, output_tokens=5, usd=0.25)
    other = _lead(s, project="p2")
    s.add_usage(other, input_tokens=1, output_tokens=1, usd=9.0)
    s.add_usage(lead, onshape_calls=1)       # Onshape calls are no usage event
    del s
    b = usage.build_usage(Store(db), "p1")
    assert len(b["turns"]) == 2
    assert b["usd"] == pytest.approx(sum(t["usd"] for t in b["turns"])) == pytest.approx(0.75)
    assert b["tokens"] == sum(t["tokens"] for t in b["turns"]) == 165
    assert b["unrecorded"] is None


def test_old_runs_are_not_recorded_per_turn(tmp_path):
    s = Store(tmp_path / "mises.db")
    lead = _lead(s)
    # a run from before usage events: only its row totals exist
    s._db.execute("UPDATE runs SET input_tokens=500, output_tokens=100, usd=1.5 WHERE id=?",
                  (lead,))
    assert usage.run_turns(s, lead) == []
    b = usage.build_usage(s, "p1")
    assert b["turns"] == [] and b["recorded"] is False
    assert b["unrecorded"] == {"runs": 1, "tokens": 600, "usd": pytest.approx(1.5)}


def test_api_runtime_is_priced_from_the_table_but_caps_see_only_reported(tmp_path):
    s = Store(tmp_path / "mises.db")
    rid = s.create_run("designer", "anthropic", "claude-sonnet-4-5", "t", project_id="p1").id
    s.add_usage(rid, input_tokens=1_000_000, output_tokens=100_000, usd=0.0, usd_known=False,
                priced_usd=usage.price("anthropic", "claude-sonnet-4-5", 1_000_000, 100_000))
    assert s.get_run(rid).usd == 0.0             # the dollar cap never counts an estimate
    t = usage.run_turns(s, rid)[0]
    assert t["usd"] == pytest.approx(3.0 + 1.5) and t["usd_source"] == "priced"
    assert usage.price("ollama", "llama3", 10, 10) is None


def test_unpriced_usage_says_so(tmp_path):
    s = Store(tmp_path / "mises.db")
    rid = s.create_run("designer", "ollama", "llama3", "t", project_id="p1").id
    s.add_usage(rid, input_tokens=10, output_tokens=10, usd_known=False)
    t = usage.run_turns(s, rid)[0]
    assert t["usd"] == 0.0 and t["usd_source"] == "unknown" and t["tokens"] == 20


def test_native_loop_unreported_cost_is_priced_by_the_store(tmp_path):
    s = Store(tmp_path / "mises.db")
    rid = s.create_run("designer", "anthropic", "claude-haiku-4-5", "t", project_id="p1").id
    s.add_usage(rid, input_tokens=1_000_000, output_tokens=0, usd_known=False)
    t = usage.run_turns(s, rid)[0]
    assert t["usd"] == pytest.approx(1.0) and t["usd_source"] == "priced"
    assert usage.build_summary(s, "p1") == {"recorded": True, "tokens": 1_000_000, "usd": 1.0}


def test_current_model_prices_match_the_most_specific_prefix():
    from mises import usage
    m = 1_000_000
    assert usage.price("anthropic", "claude-opus-5-5", m, m) == 24.0
    assert usage.price("anthropic", "claude-opus-5", m, m) == 30.0
    assert usage.price("anthropic", "claude-sonnet-5-5", m, m) == 12.0
    assert usage.price("anthropic", "claude-fable-5-1", m, m) == 60.0
    assert usage.price("anthropic", "claude-haiku-5-5", m, m) == 0.6
    assert usage.price("anthropic", "claude-opus-4-8", m, m) == 30.0
    assert usage.price("anthropic", "claude-opus-4-1", m, m) == 90.0
    assert usage.price("anthropic", "claude-opus-5-5", 0, 0, m, m) == 5.2
