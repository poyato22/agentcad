"""Plan 5 final fix I-8: the per-run dollar cap is one family budget over a run and all its
descendants (like the Onshape family cap). Children get what is left, never a fresh copy;
Claude Code cost comes from stream-json total_cost_usd; unknown cost counts $0 with a note."""
import pytest
from pydantic import BaseModel

import mises.runtime as runtime_mod
from mises import budget, projects
from mises.agent.definitions import AgentDefinition
from mises.agent.loop import AgentLoop, LoopConfig
from mises.approvals import ApprovalService
from mises.claude_code.detect import ClaudeInfo
from mises.claude_code.session import ClaudeCodeSession
from mises.events import EventBus
from mises.gate import Gate
from mises.orchestrator.orchestrator import child_policy
from mises.providers.base import Done, TextDelta, ToolCall, Usage
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.store import Store
from mises.tools.registry import ToolRegistry, tool
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C, StepKind, ToolOutput


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    # G3: dollar caps are off by default; these tests are about the caps, so they turn them on
    monkeypatch.setattr(budget, "profile_caps_on", lambda store: True)


def _run(store, cap=5.0, parent=None, provider="fake"):
    rid = store.create_run("designer", provider, "m", "t", parent_id=parent).id
    store.create_policy(rid, ["*"], ["*"], Caps(usd_run=cap), list(C))
    return rid


# ---- family accounting

def test_family_cap_counts_root_and_every_descendant():
    s = Store(":memory:")
    root = _run(s, 5.0)
    a = _run(s, 5.0, parent=root)
    b = _run(s, 5.0, parent=a)
    s.add_usage(root, usd=1.0)
    s.add_usage(a, usd=1.5)
    s.add_usage(b, usd=2.0)
    assert budget.family_spend(s, b)[2] == pytest.approx(4.5)
    assert budget.usd_exhausted(s, b) is None
    s.add_usage(b, usd=0.6)                 # family 5.1 >= root cap 5.0; b alone is only 2.6
    for rid in (root, a, b):
        err = budget.usd_exhausted(s, rid)
        assert err is not None and err.code == ErrorCode.BUDGET_EXHAUSTED
        assert "across it and its subagents" in err.message


def test_own_cap_and_nothing_spent_never_exhausted():
    s = Store(":memory:")
    r = _run(s, 0.0)
    assert budget.usd_exhausted(s, r) is None      # $0 spent (unknown cost) is not "over"
    s.add_usage(r, usd=0.01)
    assert "for this run" in budget.usd_exhausted(s, r).message


def test_child_gets_what_the_family_has_left_not_a_fresh_copy(tmp_path):
    rt = Runtime(tmp_path / "home")
    parent = _run(rt.store, 5.0)
    sibling = _run(rt.store, 5.0, parent=parent)
    rt.store.add_usage(parent, usd=1.0)
    rt.store.add_usage(sibling, usd=2.5)
    kw = child_policy(rt, parent, "designer")
    assert kw["caps"].usd_run == pytest.approx(1.5)
    # a grandchild of the sibling: bounded by the sibling's own cap and the family's rest
    assert child_policy(rt, sibling, "designer")["caps"].usd_run == pytest.approx(1.5)
    rt.store.add_usage(sibling, usd=1.5)           # family now at 5.0
    with pytest.raises(MisesError) as e:
        child_policy(rt, parent, "designer")
    assert e.value.code == ErrorCode.BUDGET_EXHAUSTED


class In(BaseModel):
    n: int = 0


@tool("noop", description="d", safety=C.READ)
async def noop(ctx, args: In) -> ToolOutput:
    return ToolOutput(summary="ran")


def _env(store):
    reg = ToolRegistry()
    reg.register(noop)
    bus = EventBus()
    return reg, bus, Gate(reg, store, ApprovalService(store, bus), bus)


async def test_gate_refuses_tool_calls_once_the_family_budget_is_spent(tmp_path):
    s = Store(":memory:")
    _reg, _bus, gate = _env(s)
    root = _run(s, 1.0)
    child = _run(s, 1.0, parent=root)
    await gate.call(child, "noop", {}, call_id="c1", workdir=tmp_path)
    s.add_usage(root, usd=1.0)                     # the parent spent the family's dollars
    with pytest.raises(MisesError) as e:
        await gate.call(child, "noop", {}, call_id="c2", workdir=tmp_path)
    assert e.value.code == ErrorCode.BUDGET_EXHAUSTED


async def test_native_loop_counts_reported_cost_and_stops_cleanly(tmp_path):
    s = Store(":memory:")
    reg, bus, gate = _env(s)
    rid = _run(s, 0.5)
    provider = FakeProvider([
        [ToolCall(id="t1", name="noop", input={}), Usage(input_tokens=5, output_tokens=5,
                                                         usd=0.6), Done(stop_reason="tool_use")],
        [TextDelta(text="never reached"), Done(stop_reason="end_turn")]])
    loop = AgentLoop(run_id=rid, defn=AgentDefinition(name="t", description="d", body="b"),
                     provider=provider, model="m", gate=gate, registry=reg, store=s,
                     events=bus, workdir=tmp_path, config=LoopConfig(retry_base_delay=0))
    r = await loop.run("go")
    assert r.stop_reason == "budget" and "dollar cap reached" in r.error
    assert s.get_run(rid).usd == pytest.approx(0.6)
    errs = s.steps(rid, [StepKind.ERROR])
    assert errs[-1].payload["stop_reason"] == "budget"
    assert runtime_mod._STATUS["budget"] == "failed"


def test_unknown_cost_is_zero_with_a_note():
    s = Store(":memory:")
    r = s.get_run(_run(s))
    assert projects.run_view(r)["cost"]["usd_note"] == budget.UNKNOWN_NOTE
    cc = s.get_run(_run(s, provider="claude-code"))
    assert "usd_note" not in projects.run_view(cc)["cost"]


# ---- Claude Code: total_cost_usd

def _session(rt, cap=5.0, parent=None):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet",
                                 caps=Caps(usd_run=cap), parent_id=parent)
    s = ClaudeCodeSession(rt, rid, model_alias="sonnet", info=ClaudeInfo(path=None, version=None),
                          mcp_url="http://127.0.0.1/mcp", mcp_token="t")
    s._init_ok = True
    return s


def _result(cost=None):
    msg = {"type": "result", "subtype": "success", "usage": {"input_tokens": 1,
                                                             "output_tokens": 1}}
    if cost is not None:
        msg["total_cost_usd"] = cost
    return msg


def test_claude_code_counts_total_cost_deltas(tmp_path):
    rt = Runtime(tmp_path / "home")
    s = _session(rt)
    s._on_result(_result(0.25))
    s._on_result(_result(0.40))                   # the process's running total: +0.15
    assert rt.store.get_run(s.run_id).usd == pytest.approx(0.40)
    s._on_result(_result())                       # no field: $0, with a note
    assert rt.store.get_run(s.run_id).usd == pytest.approx(0.40)
    notes = [st.payload for st in rt.store.steps(s.run_id, [StepKind.DECISION])
             if st.payload.get("claude_code") == "cost_unknown"]
    assert notes and "counted as $0" in notes[0]["note"]


def test_claude_code_is_a_subscription_never_dollar_capped_even_when_caps_are_on(tmp_path):
    """G3: past its 'cap' a Claude Code run keeps its status and spend is only recorded."""
    rt = Runtime(tmp_path / "home")
    s = _session(rt, cap=1.0)
    s._on_result(_result(1.2))
    run = rt.store.get_run(s.run_id)
    assert run.status != "failed" and run.usd == pytest.approx(1.2)
    assert not [e for e in rt.store.steps(s.run_id, [StepKind.ERROR])
                if e.payload.get("error") == "budget_exhausted"]
    assert budget.usd_exhausted(rt.store, s.run_id) is None


def test_claude_code_family_spend_never_caps_its_children(tmp_path):
    rt = Runtime(tmp_path / "home")
    parent = _session(rt, cap=1.0)
    child = _session(rt, cap=1.0, parent=parent.run_id)
    child._on_result(_result(0.7))
    parent._on_result(_result(0.4))               # 1.1 across the family, a cap of 1.0
    assert rt.store.get_run(parent.run_id).status != "failed"
    assert budget.usd_exhausted(rt.store, child.run_id) is None


def test_dollar_caps_are_off_by_default_and_a_profile_setting(tmp_path, monkeypatch):
    """G3: no dollar cap applies unless the profile turns them on (any runtime)."""
    monkeypatch.undo()                                    # the real profile switch
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home")
    rid = _run(rt.store, cap=1.0)
    rt.store.add_usage(rid, usd=3.0)
    assert budget.usd_exhausted(rt.store, rid) is None
    from mises.profile import load_profile, save_profile
    prof = load_profile(rt.home)
    save_profile(rt.home, prof.model_copy(update={"dollar_caps": True}))
    assert budget.usd_exhausted(rt.store, rid) is not None
