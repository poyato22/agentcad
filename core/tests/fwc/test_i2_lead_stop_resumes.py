"""FWC I2 (E20): a native lead that stops on a limit (budget, max_turns, circuit_open) is left
`failed` but is resumed by the next message, on the same run with its thread; only a crash
retires it."""
import pytest

import mises.runtime as runtime_mod
from mises.orchestrator import lead as lead_mod
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.types import StepKind


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return tmp_path / "home"


def text(t):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


def tool_turn(i):
    return [ToolCall(id=f"t{i}", name="no_such_tool", input={}), Done(stop_reason="tool_use")]


class Costly:
    """The first model call costs more than the lead's $5 window, so the loop's next turn stops
    on `budget` (the real stop, not a status set by hand)."""
    name = "fake"

    def __init__(self, box, script):
        self.box, self.inner, self.n = box, FakeProvider(script), 0

    async def stream(self, req):
        rt = self.box[0]
        self.n += 1
        if self.n == 1:
            lead = lead_mod.find_lead(rt.store, rt.active_project_id())
            rt.store.add_usage(lead.id, usd=6.0)
        async for ev in self.inner.stream(req):
            yield ev


async def test_a_lead_stopped_on_budget_is_resumed_not_archived(home, monkeypatch):
    from mises import budget
    monkeypatch.setattr(budget, "profile_caps_on", lambda store: True)     # G3: caps are opt-in
    box = []
    prov = Costly(box, [tool_turn(0), text("resumed with my thread")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    box.append(rt)
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    res = await rt.wait(first)
    assert res.stop_reason == "budget"
    assert rt.store.get_run(first).status == "failed"
    assert lead_mod.stopped(rt, first) and lead_mod.stopped(rt.store, first)
    out = await lead_mod.submit(rt, "carry on", project_id=pid)
    assert out["new_lead"] is False and out["run_id"] == first
    assert rt.store.get_run(first).archived is False
    res = await rt.wait(first)
    assert res.stop_reason == "end_turn" and res.final_text == "resumed with my thread"
    # the thread carried over: the first prompt is still in the history the model saw
    assert any("Build me a drone" in str(m.content) for m in res.messages)


async def test_a_lead_stopped_on_max_turns_is_resumed(home, monkeypatch):
    prov = FakeProvider([tool_turn(0), tool_turn(1), text("next")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    for d in rt.definitions.values():
        monkeypatch.setattr(d, "max_turns", 1)
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    assert (await rt.wait(first)).stop_reason == "max_turns"
    out = await lead_mod.submit(rt, "keep going", project_id=pid)
    assert out["new_lead"] is False and out["run_id"] == first


async def test_a_crashed_lead_is_still_replaced(home):
    prov = FakeProvider([text("one"), text("two")])
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    await rt.wait(first)
    rt.store.append_step(first, StepKind.ERROR, {"stop_reason": "error", "message": "boom"})
    rt.store.update_run(first, status="failed")
    rt._runs[first].result.stop_reason = "error"            # as a crash leaves it
    assert not lead_mod.stopped(rt, first) and not lead_mod.stopped(rt.store, first)
    out = await lead_mod.submit(rt, "again", project_id=pid)
    assert out["new_lead"] is True and out["run_id"] != first
    assert rt.store.get_run(first).archived is True
    await rt.wait(out["run_id"])


def test_a_stop_before_the_last_user_message_does_not_count(home):
    from mises.store import Store
    s = Store(":memory:")
    rid = s.create_run("engineer", "fake", "m", "t").id
    s.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "a"})
    s.append_step(rid, StepKind.ERROR, {"stop_reason": "budget"})
    assert lead_mod.stopped(s, rid)
    s.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "b"})
    s.append_step(rid, StepKind.ERROR, {"stop_reason": "error"})
    assert not lead_mod.stopped(s, rid)


async def test_a_replacement_lead_drops_an_inherited_ref_that_no_longer_resolves(home):
    """Retired-lead inheritance: a key-less ref is not passed on as an explicit choice, so the
    replacement goes through the implicit default (and E34's fallback) instead."""
    from mises.types import ErrorCode, MisesError
    prov = FakeProvider([text("one"), text("two")])
    keyless = {"on": False}

    def resolver(ref):
        if keyless["on"]:
            raise MisesError(ErrorCode.DENIED, "no API key for fake")
        return prov, "m"
    rt = Runtime(home, provider_resolver=resolver)
    pid = rt.active_project_id()
    first = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    await rt.wait(first)
    lead = rt.store.get_run(first)
    assert lead_mod._inherited_ref(rt, lead) == f"{lead.provider}/{lead.model}"
    keyless["on"] = True
    assert lead_mod._inherited_ref(rt, lead) is None
