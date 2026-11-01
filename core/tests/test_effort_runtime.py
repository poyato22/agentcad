"""Plan 11 F8a end to end on a native run: the chat's Think choice, the build default, the
auto pick and a sub-agent's own effort reach the provider's ModelRequest.effort, and each turn
records its decision as a step."""
import time

import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.types import StepKind

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return tmp_path / "home"


class Capture:
    name = "anthropic"

    def __init__(self):
        self.reqs = []

    async def stream(self, req):
        self.reqs.append(req)
        async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                     ).stream(req):
            yield ev


def _idle(rt, rid, n=300):
    for _ in range(n):
        if rt.store.get_run(rid).status in ("done", "failed", "cancelled") \
                and not rt.run_busy(rid):
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _decisions(rt, rid):
    return [s.payload["effort"] for s in rt.store.steps(rid, [StepKind.DECISION])
            if "effort" in s.payload]


def test_chat_think_choice_build_default_and_auto(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "claude-opus-5-5"))
    with TestClient(create_app(rt, TOKEN)) as client:
        p = rt.store.ensure_default_project().id
        r = client.post(f"/projects/{p}/chat", headers=AUTH, json={
            "text": "Design a egg drop build no limits think of the best possible design"})
        lead = r.json()["run_id"]
        _idle(rt, lead)
        assert prov.reqs[-1].effort == "max"                     # auto: open-ended challenge
        client.post(f"/projects/{p}/chat", headers=AUTH, json={"text": "rename it to pod"})
        _idle(rt, lead)
        assert prov.reqs[-1].effort == "low"                     # auto: routine
        client.post(f"/projects/{p}/chat", headers=AUTH,
                    json={"text": "rename it to pod", "effort": "hard"})
        _idle(rt, lead)
        assert prov.reqs[-1].effort == "high"                    # the user's pick
        assert client.post(f"/projects/{p}/chat", headers=AUTH, json={
            "text": "x", "effort": "ultra"}).status_code == 422
        # the build default
        assert client.get(f"/projects/{p}/effort", headers=AUTH).json()["default"] == "auto"
        v = client.put(f"/projects/{p}/effort", headers=AUTH, json={"default": "max"}).json()
        assert v["default"] == "max" and v["levels"]["hard"] == "high"
        client.post(f"/projects/{p}/chat", headers=AUTH, json={"text": "rename it to pod"})
        _idle(rt, lead)
        assert prov.reqs[-1].effort == "max"
        d = _decisions(rt, lead)
        assert [(x["level"], x["source"]) for x in d] == [
            ("max", "auto"), ("low", "auto"), ("high", "user"), ("max", "build")]
        assert d[0]["control"] == {"runtime": "anthropic", "param": "output_config.effort",
                                   "value": "max"}
        assert any("open-ended" in why for why in d[0]["reasons"])


def test_a_queued_message_keeps_its_think_choice(home, monkeypatch):
    from mises import effort as effort_mod
    from mises.orchestrator import lead as lead_mod
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "claude-opus-5-5"))
    sent = []

    async def fake_send(run_id, text, **kw):
        sent.append(kw.get("effort"))
    lead_q = {"message_id": "m1", "text": "hi", "log": None, "shown": "hi", "effort": "max"}
    monkeypatch.setattr(rt, "send", fake_send)
    import asyncio

    async def go():
        rt.lead_queues["L"] = [lead_q]
        monkeypatch.setattr(rt, "wait", lambda rid: asyncio.sleep(0))
        monkeypatch.setattr(rt, "run_busy", lambda rid: False)
        rt.store.create_run("engineer", "fake", "m", "x")
        await lead_mod._drain(rt, "L")
    asyncio.run(go())
    assert sent == ["max"]
    assert effort_mod.REQUESTED.get() is None


async def test_a_sub_agent_gets_its_spawners_effort(home):
    prov = FakeProvider([
        [ToolCall(id="s1", name="agent_spawn", input={"agent": "designer", "prompt": "edit",
                                                       "effort": "low"}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="child done"), Done(stop_reason="end_turn")],
        [TextDelta(text="parent done"), Done(stop_reason="end_turn")]])
    seen = []

    class Spy:
        name = "anthropic"

        async def stream(self, req):
            seen.append((req.system.count("# Role: designer") > 0, req.effort))
            async for ev in prov.stream(req):
                yield ev
    rt = Runtime(home, provider_resolver=lambda ref: (Spy(), "claude-opus-5-5"))
    pid = await rt.start_run("designer", "Design a motor mount", effort="max")
    await rt.wait(pid)
    (child,) = [r for r in rt.store.list_runs() if r.parent_id == pid]
    assert _decisions(rt, pid)[0]["level"] == "max"
    assert _decisions(rt, child.id)[0] == {**_decisions(rt, child.id)[0], "level": "low",
                                           "source": "user"}
    assert [e for _, e in seen] == ["max", "low", "max"]
