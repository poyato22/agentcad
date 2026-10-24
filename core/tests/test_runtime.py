import asyncio
import json

import pytest
from pydantic import BaseModel

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.tools.registry import tool
from mises.types import (Caps, ErrorCode, MisesError, SafetyClass as C, StepKind, ToolOutput,
                         ToolResultBlock)


class In(BaseModel):
    n: int = 0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


def make(tmp_path, turns):
    fake = FakeProvider(turns)
    refs = []

    def resolver(ref):
        refs.append(ref)
        return fake, "fake-model"
    rt = Runtime(tmp_path / "home", provider_resolver=resolver)
    return rt, fake, refs


async def next_event(sub, type_):
    while True:
        ev = await asyncio.wait_for(sub.queue.get(), 5)
        if ev.type == type_:
            return ev


async def test_start_run_records_policy_and_steps(tmp_path):
    rt, fake, refs = make(tmp_path, [[TextDelta(text="hello"), Done(stop_reason="end_turn")]])
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "make a bracket", edit_scope=("part_a",),
                             read_scope=("part_b",), caps=Caps(max_turns=7))
    started = await next_event(sub, "run_started")
    assert started.run_id == rid
    r = await rt.wait(rid)
    assert r.stop_reason == "end_turn" and r.final_text == "hello"
    finished = await next_event(sub, "run_finished")
    assert finished.data["status"] == "done"
    run = rt.store.get_run(rid)
    assert (run.status, run.agent, run.provider, run.model, run.task) == (
        "done", "designer", "fake", "fake-model", "make a bracket")
    assert refs == ["anthropic/claude-sonnet-5-5"]
    pol = rt.store.current_policy(rid)
    assert pol.edit_scope == ["part_a"] and pol.read_scope == ["part_b"]
    assert pol.caps.max_turns == 7 and set(pol.allowed_classes) == set(C)
    msgs = rt.store.steps(rid, [StepKind.MESSAGE])
    assert [s.payload["role"] for s in msgs] == ["user", "assistant"]
    assert rt.store.verify_chain(rid)
    wd = rt.workdir(rid)
    assert wd == tmp_path / "home" / "runs" / rid and wd.is_dir()
    assert (wd / "parts").is_symlink() and (wd / "parts").resolve() == (tmp_path / "home" / "parts").resolve()
    # tools offered include mirror, onshape and ui tools
    names = {t.name for t in fake.requests[0].tools}
    assert {"mirror_part_list", "ui_ask_user", "ui_note"} <= names
    assert any(n.startswith("onshape_") for n in names)
    assert "onshape" not in rt.gate.services and rt.gate.services["runtime"] is rt


async def test_unknown_agent_rejected(tmp_path):
    rt, _, _ = make(tmp_path, [])
    with pytest.raises(MisesError) as e:
        await rt.start_run("nobody", "x")
    assert e.value.code == ErrorCode.NOT_FOUND


async def test_onshape_service_from_keychain(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_mod, "keychain_get",
                        lambda s, a: "ak:sk" if (s, a) == ("mises", "onshape") else None)
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (FakeProvider([]), "m"))
    ad = rt.gate.services["onshape"]
    assert ad.client._ak == "ak" and ad.client._sk == "sk" and ad.client.user == "local"
    await rt.aclose()


async def test_irreversible_tool_blocks_until_decided(tmp_path):
    rt, fake, _ = make(tmp_path, [
        [ToolCall(id="c1", name="danger_merge", input={"n": 1}), Done(stop_reason="tool_use")],
        [TextDelta(text="merged"), Done(stop_reason="end_turn")]])
    ran = []

    @tool("danger_merge", description="merge", safety=C.IRREVERSIBLE)
    async def merge(ctx, args: In) -> ToolOutput:
        ran.append(args.n)
        return ToolOutput(summary="merged")
    rt.registry.register(merge)
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "merge it")
    ev = await next_event(sub, "approval_requested")
    await asyncio.sleep(0.05)
    assert ran == [] and rt.store.get_run(rid).status == "running"
    rt.approvals.decide(ev.data["id"], ev.data["payload_hash"], True, "human")
    r = await rt.wait(rid)
    assert r.stop_reason == "end_turn" and ran == [1]


async def test_cancel_run_rejects_approval_and_marks_cancelled(tmp_path):
    rt, fake, _ = make(tmp_path, [
        [ToolCall(id="c1", name="danger_merge", input={}), Done(stop_reason="tool_use")]])

    @tool("danger_merge", description="merge", safety=C.IRREVERSIBLE)
    async def merge(ctx, args: In) -> ToolOutput:
        return ToolOutput(summary="merged")
    rt.registry.register(merge)
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "merge it")
    await next_event(sub, "approval_requested")
    rt.cancel(rid)
    r = await rt.wait(rid)
    assert r.stop_reason == "cancelled"
    assert rt.store.get_run(rid).status == "cancelled"
    assert rt.store.pending_approvals(rid) == []


async def test_send_continues_history(tmp_path):
    rt, fake, _ = make(tmp_path, [[TextDelta(text="first answer"), Done(stop_reason="end_turn")],
                                  [TextDelta(text="second answer"), Done(stop_reason="end_turn")]])
    rid = await rt.start_run("designer", "first question")
    await rt.wait(rid)
    await rt.send(rid, "follow up")
    r = await rt.wait(rid)
    assert r.final_text == "second answer"
    texts = [m.content[0].text for m in fake.requests[1].messages]
    assert texts == ["first question", "first answer", "follow up"]
    assert rt.store.get_run(rid).status == "done"


async def test_send_while_running_is_refused(tmp_path):
    rt, fake, _ = make(tmp_path, [])
    gate = asyncio.Event()

    class Slow(FakeProvider):
        async def stream(self, req):
            await gate.wait()
            yield TextDelta(text="x")
            yield Done(stop_reason="end_turn")
    rt.provider_resolver = lambda ref: (Slow([]), "m")
    rid = await rt.start_run("designer", "q")
    with pytest.raises(MisesError) as e:
        await rt.send(rid, "again")
    assert e.value.code == ErrorCode.INVALID_INPUT
    gate.set()
    await rt.wait(rid)


async def test_ui_ask_user_roundtrip(tmp_path):
    rt, fake, _ = make(tmp_path, [
        [ToolCall(id="c1", name="ui_ask_user",
                  input={"question": "Which colour?", "options": ["red", "blue"]}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="blue it is"), Done(stop_reason="end_turn")]])
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "paint it")
    q = await next_event(sub, "question_asked")
    assert q.run_id == rid and q.data["question"] == "Which colour?"
    assert q.data["options"] == ["red", "blue"]
    rt.answer(q.data["question_id"], "blue")
    r = await rt.wait(rid)
    assert r.final_text == "blue it is"
    (res,) = [b for b in fake.requests[1].messages[2].content if isinstance(b, ToolResultBlock)]
    assert not res.is_error and json.loads(res.content)["data"]["answer"] == "blue"
    with pytest.raises(MisesError):
        rt.answer(q.data["question_id"], "again")


async def test_ui_note_publishes(tmp_path):
    rt, fake, _ = make(tmp_path, [
        [ToolCall(id="c1", name="ui_note", input={"text": "halfway"}), Done(stop_reason="tool_use")],
        [TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "go")
    note = await next_event(sub, "note")
    assert note.data["text"] == "halfway" and note.run_id == rid
    await rt.wait(rid)


async def test_provider_failure_marks_run_failed(tmp_path):
    rt, fake, _ = make(tmp_path, [])  # FakeProvider with no turns raises TOOL_FAILED
    rid = await rt.start_run("designer", "go")
    r = await rt.wait(rid)
    assert r.stop_reason == "error" and rt.store.get_run(rid).status == "failed"


async def test_create_external_run_has_policy_and_workdir_but_no_loop(tmp_path):
    rt, fake, _ = make(tmp_path, [])
    sub = rt.events.subscribe()
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x",
                                 edit_scope=("part_a",), read_scope=("part_b",),
                                 allowed_classes={C.READ})
    started = await next_event(sub, "run_started")
    assert started.run_id == rid and started.data["external"] is True
    run = rt.store.get_run(rid)
    assert (run.status, run.agent, run.provider, run.model) == ("running", "codex", "openai", "gpt-x")
    pol = rt.store.current_policy(rid)
    assert pol.edit_scope == ["part_a"] and pol.read_scope == ["part_b"]
    assert pol.allowed_classes == [C.READ]
    assert (tmp_path / "home" / "runs" / rid).is_dir()
    assert fake.requests == []
    # the gate works for it
    out = await rt.gate.call(rid, "mirror_part_list", {}, call_id="x1", workdir=rt.workdir(rid))
    assert out.ok
    with pytest.raises(MisesError) as e:
        await rt.send(rid, "hi")
    assert e.value.code == ErrorCode.INVALID_INPUT


async def test_finish_external_run(tmp_path):
    rt, _, _ = make(tmp_path, [])
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    a = await rt.approvals.request(rid, "danger", {"n": 1})
    sub = rt.events.subscribe(rid)
    rt.finish_external_run(rid, "done")
    fin = await next_event(sub, "run_finished")
    assert fin.data["status"] == "done"
    assert rt.store.get_run(rid).status == "done"
    assert rt.store.get_approval(a.id).status == "rejected"
    with pytest.raises(MisesError) as e:
        rt.finish_external_run(rid, "bogus")
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(MisesError) as e:
        rt.finish_external_run("nope", "done")
    assert e.value.code == ErrorCode.NOT_FOUND


async def test_cancel_external_run(tmp_path):
    rt, _, _ = make(tmp_path, [])
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    rt.cancel(rid)
    assert rt.store.get_run(rid).status == "cancelled"
