"""E20: after an agent finishes you can talk to it again, with its context.

- a Claude Code lead is never retired by _spent (it resumes; its window resets)
- a forced retire inherits the old lead's model and the gist of its thread
- a direct user message to a sub-agent opens a fresh dollar window for it
- a spawned child's dollar cap is floored when the family budget is nearly spent
"""
from __future__ import annotations

import pytest

import mises.runtime as runtime_mod
from mises import budget
from mises.orchestrator import lead as lead_mod
from mises.orchestrator.orchestrator import CHILD_USD_FLOOR, child_policy
from mises.runtime import CLAUDE_CODE, Runtime
from mises.store import Store
from mises.types import Caps, SafetyClass as C


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))


def test_spent_never_retires_a_claude_code_lead(rt):
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", CLAUDE_CODE, "sonnet", "go", project_id=pid,
                               external=True)
    rt.store.update_run(lead.id, role="lead", status="failed")
    assert rt._is_claude_run(lead.id) is True
    assert lead_mod._spent(rt, rt.store.get_run(lead.id)) is False


def test_spent_still_retires_a_failed_native_lead(rt):
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    rt.store.update_run(lead.id, role="lead", status="failed")
    assert lead_mod._spent(rt, rt.store.get_run(lead.id)) is True


def test_spent_does_not_retire_on_dollar_cap_alone(rt):
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    rt.store.update_run(lead.id, role="lead", status="done")
    rt.store.create_policy(lead.id, ["*"], ["*"], Caps(usd_run=5.0), list(C))
    budget.start_turn(rt.store, lead.id)
    rt.store.add_usage(lead.id, usd=6.0)
    assert budget.usd_exhausted(rt.store, lead.id) is not None
    assert lead_mod._spent(rt, rt.store.get_run(lead.id)) is False


def test_retire_context_carries_last_assistant_text(rt):
    from mises.types import StepKind
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", CLAUDE_CODE, "sonnet", "go", project_id=pid)
    rt.store.append_step(lead.id, StepKind.MESSAGE, {"role": "assistant", "text": "Built the arm."})
    ctx = lead_mod._retire_context(rt, rt.store.get_run(lead.id))
    assert ctx == {"previous_lead": "Built the arm."}


def test_open_sub_window_raises_a_spent_subs_cap(rt):
    pid = rt.store.create_project("Robot").id
    lead = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    rt.store.update_run(lead.id, role="lead")
    rt.store.create_policy(lead.id, ["*"], ["*"], Caps(usd_run=5.0), list(C))
    sub = rt.store.create_run("designer", "fake", "m", "t", parent_id=lead.id, project_id=pid)
    rt.store.create_policy(sub.id, ["*"], ["*"], Caps(usd_run=5.0), list(C))
    rt.store.add_usage(sub.id, usd=5.0)
    assert budget.usd_exhausted(rt.store, sub.id) is not None
    rt._open_sub_window(sub.id)
    assert budget.usd_exhausted(rt.store, sub.id) is None


def test_child_cap_is_floored_when_family_nearly_spent():
    s = Store(":memory:")
    lead = s.create_run("engineer", "fake", "m", "go").id
    s.create_policy(lead, ["*"], ["*"], Caps(usd_run=5.0), list(C))
    s.update_run(lead, role="lead")
    budget.start_turn(s, lead)
    s.add_usage(lead, usd=4.8)         # only $0.20 left in the family window

    class FakeRT:
        store = s
        definitions = {"designer": type("D", (), {"tools": None, "model": None})()}

        class gate:
            user = "local"
    pol = child_policy(FakeRT, lead, "designer")
    assert pol["caps"].usd_run == pytest.approx(CHILD_USD_FLOOR)
