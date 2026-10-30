"""Final review C1 / I3: a build's lead is one long-lived run, so its per-run caps (dollars,
Onshape calls, web fetches, spawned sub-agents) count per user message, with the sub-agents
spawned during a turn counted within it. A lead that failed or spent its cap is replaced."""
import time

import pytest

import mises.runtime as runtime_mod
from mises import budget
from mises.browser.snapshot import SnapshotStore
from mises.orchestrator import lead as lead_mod
from mises.orchestrator.orchestrator import child_policy
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.store import Store
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return tmp_path / "home"


def text(t):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


class Costly:
    """Every model call of the lead costs `usd` (as a cost-reporting provider would add)."""
    name = "fake"

    def __init__(self, rt_box, usd, script):
        self.rt_box, self.usd = rt_box, usd
        self.inner = FakeProvider(script)

    async def stream(self, req):
        rt = self.rt_box[0]
        lead = lead_mod.find_lead(rt.store, rt.active_project_id())
        rt.store.add_usage(lead.id, usd=self.usd)
        async for ev in self.inner.stream(req):
            yield ev


async def test_three_two_dollar_requests_on_one_lead_all_run(home):
    box = []
    prov = Costly(box, 2.0, [text("one"), text("two"), text("three")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    box.append(rt)
    pid = rt.active_project_id()
    ids = []
    for msg in ("Build me a drone", "now add a camera mount", "and a battery strap"):
        out = await lead_mod.submit(rt, msg, project_id=pid)
        ids.append(out["run_id"])
        res = await rt.wait(out["run_id"])
        assert res.stop_reason == "end_turn", res.error
    assert len(set(ids)) == 1                       # the same lead took all three
    run = rt.store.get_run(ids[0])
    assert run.usd == pytest.approx(6.0) and run.status == "done"   # $6 over a $5 cap


async def test_a_lead_that_spent_its_cap_in_one_message_is_replaced(home):
    box = []
    prov = Costly(box, 5.01, [text("one"), text("two")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    box.append(rt)
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    await rt.wait(first)
    assert budget.usd_exhausted(rt.store, first) is not None
    out = await lead_mod.submit(rt, "now add a camera mount", project_id=pid)
    assert out["new_lead"] is True and out["run_id"] != first
    assert rt.store.get_run(first).archived is True
    await rt.wait(out["run_id"])


async def test_a_failed_lead_is_replaced(home):
    prov = FakeProvider([text("one"), text("two")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    await rt.wait(first)
    rt.store.update_run(first, status="failed")
    out = await lead_mod.submit(rt, "again", project_id=pid)
    assert out["new_lead"] is True and out["run_id"] != first
    await rt.wait(out["run_id"])


# ---- budget windows, unit level
def _run(store, cap=5.0, parent=None, role=None):
    rid = store.create_run("designer", "fake", "m", "t", parent_id=parent).id
    store.create_policy(rid, ["*"], ["*"], Caps(usd_run=cap), list(C))
    if role:
        store.update_run(rid, role=role)
    return rid


def test_a_turn_resets_the_family_dollars_and_counts_new_children():
    s = Store(":memory:")
    lead = _run(s, role="lead")
    old = _run(s, parent=lead)
    s.add_usage(lead, usd=3.0)
    s.add_usage(old, usd=2.5)
    assert budget.usd_exhausted(s, lead) is not None
    budget.start_turn(s, lead)
    assert budget.usd_exhausted(s, lead) is None
    assert budget.family_spend(s, old)[2] == 0.0
    new = _run(s, parent=lead)                      # spawned this turn: counts in full
    s.add_usage(new, usd=4.0)
    s.add_usage(old, usd=1.0)                       # an older child still working: its new spend
    assert budget.usd_exhausted(s, new) is not None
    assert "this request" in budget.usd_exhausted(s, lead).message
    assert budget.spawned_this_turn(s, lead) == 1


def test_onshape_counts_per_turn_for_a_lead():
    s = Store(":memory:")
    lead = _run(s, role="lead")
    for i in range(3):
        e = s.ledger_reserve("u", lead, "GET /x", 1, f"k{i}")
        s.ledger_settle(e.id, 200)
        s.add_usage(lead, onshape_calls=1)
    assert budget.onshape_used(s, "u", lead) == 3
    time.sleep(0.01)
    budget.start_turn(s, lead)
    assert budget.onshape_used(s, "u", lead) == 0
    e = s.ledger_reserve("u", lead, "GET /y", 1, "k9")
    s.ledger_settle(e.id, 200)
    assert budget.onshape_used(s, "u", lead) == 1


def test_fetch_budget_is_the_familys_and_a_lead_turn_refills_it():
    s = Store(":memory:")
    snaps = SnapshotStore(s)
    lead = _run(s, role="lead")
    child = _run(s, parent=lead)
    root, _ = s.run_family(child)
    assert root == lead
    assert snaps.charge(root, 2, 3) == 2
    assert snaps.charge(root, 2, 3) is None         # the child cannot buy a fresh budget
    budget.start_turn(s, lead)
    assert snaps.charge(root, 3, 3) == 3


def test_child_onshape_headroom_is_this_turns(home):
    rt = Runtime(home)
    lead = _run(rt.store, role="lead")
    rt.store.update_run(lead, agent="engineer")
    for i in range(150):
        e = rt.store.ledger_reserve("local", lead, "GET /x", 1, f"k{i}")
        rt.store.ledger_settle(e.id, 200)
        rt.store.add_usage(lead, onshape_calls=1)
    assert child_policy(rt, lead, "designer")["caps"].onshape_calls_run == 50
    time.sleep(0.01)
    budget.start_turn(rt.store, lead)
    assert child_policy(rt, lead, "designer")["caps"].onshape_calls_run == 200


# ---- I3: a per-turn cap on spawned sub-agents
SPAWN = {"agent": "designer", "task": "Part", "prompt": "Make a 10 mm cube.", "wait": False}


async def test_spawns_per_turn_are_capped(home):
    from mises.profile import load_profile, save_profile
    home.mkdir(parents=True, exist_ok=True)
    prof = load_profile(home)
    save_profile(home, prof.model_copy(update={"max_spawns_per_turn": 2}))
    calls = [ToolCall(id=f"s{i}", name="agent_spawn", input=SPAWN) for i in range(3)]
    prov = FakeProvider([[*calls, Done(stop_reason="tool_use")], text("done")]
                        + [text("built")] * 3)
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    pid = rt.active_project_id()
    lead = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    await rt.wait(lead)
    kids = [r for r in rt.store.list_runs() if r.parent_id == lead]
    assert len(kids) == 2
    errs = [st.payload for st in rt.store.steps(lead) if st.payload.get("error")
            == ErrorCode.BUDGET_EXHAUSTED.value]
    assert errs and "2 sub-agents" in errs[0]["message"]
    for k in kids:
        await rt.wait(k.id)


def test_profile_spawn_cap_bounds():
    from mises.profile import Profile
    assert Profile().max_spawns_per_turn == 8
    with pytest.raises(ValueError):
        Profile(max_spawns_per_turn=0)


def test_usd_message_for_a_child_of_a_windowed_lead_hints_a_new_message():
    s = Store(":memory:")
    lead = _run(s, role="lead")
    budget.start_turn(s, lead)
    child = _run(s, cap=10.0, parent=lead)
    s.add_usage(child, usd=5.0)
    err = budget.usd_exhausted(s, child)
    assert err is not None and isinstance(err, MisesError)
    assert "next message" in (err.hint or "")


async def test_one_new_user_turn_hook_resets_the_critique_loop_and_the_lead_caps(home, monkeypatch):
    """Release merge: fix-bench (critique loop per user turn) and fix-final-core (lead caps per
    message) share Runtime.new_user_turn; a message joining a turn still running resets neither."""
    from mises.cad import views
    rt = Runtime(home, provider_resolver=lambda ref: (FakeProvider([]), "m"))
    lead = rt.store.create_run("engineer", "fake", "fake/model", "a Pi case").id
    rt.store.update_run(lead, role="lead")
    seen: list = []
    monkeypatch.setattr(views, "reset_loop", lambda rid: seen.append(("loop", rid)))
    monkeypatch.setattr(budget, "start_turn", lambda store, rid: seen.append(("caps", rid)))

    async def idle_send(run_id, text):
        return None
    monkeypatch.setattr(rt, "_send", idle_send)
    await rt.send(lead, "make the case")
    assert seen == [("loop", lead), ("caps", lead)]
    seen.clear()
    monkeypatch.setattr(rt, "run_busy", lambda rid: True)      # the lead is mid-turn
    await rt.send(lead, "and paint it red")
    assert seen == []
    # a plain (non-lead) run: a new turn resets its critique loop, no lead cap window
    plain = rt.store.create_run("designer", "fake", "fake/model", "a bracket").id
    rt.new_user_turn(plain)
    assert seen == [("loop", plain)]
    await rt.aclose()
