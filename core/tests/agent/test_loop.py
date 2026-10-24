import asyncio
import json
import time

import pytest
from pydantic import BaseModel

from mises.agent.definitions import AgentDefinition
from mises.agent.loop import AgentLoop, LoopConfig
from mises.approvals import ApprovalService
from mises.events import EventBus
from mises.gate import Gate
from mises.providers.base import Done, TextDelta, ToolCall, Usage
from mises.providers.fake import FakeProvider
from mises.store import Store
from mises.tools.registry import ToolRegistry, tool
from mises.types import (Caps, ErrorCode, MisesError, SafetyClass as C, StepKind, TextBlock,
                         ToolResultBlock, ToolUseBlock, ToolOutput)

ALL = [C.READ, C.LOCAL_MUTATE, C.ONSHAPE_MUTATE, C.IRREVERSIBLE]


class In(BaseModel):
    n: int = 0


def call(cid, name, **inp):
    return ToolCall(id=cid, name=name, input=inp)


def tool_turn(*calls):
    return [*calls, Done(stop_reason="tool_use")]


def text_turn(text):
    return [TextDelta(text=text), Done(stop_reason="end_turn")]


class Env:
    def __init__(self, tmp_path, turns=(), *, provider=None, allowed=ALL):
        self.store = Store(":memory:")
        self.bus = EventBus()
        self.approvals = ApprovalService(self.store, self.bus)
        self.reg = ToolRegistry()
        self.gate = Gate(self.reg, self.store, self.approvals, self.bus)
        self.run_id = self.store.create_run("tester", "fake", "m", "task").id
        self.store.create_policy(self.run_id, ["*"], ["*"], Caps(), list(allowed))
        self.provider = provider or FakeProvider(list(turns))
        self.tmp = tmp_path
        self.log: list = []

    def add(self, name, safety=C.READ, *, sleep=0.0, fail: ErrorCode | None = None):
        @tool(name, description=f"{name} tool", safety=safety)
        async def f(ctx, args: In) -> ToolOutput:
            self.log.append(("start", name, args.n))
            if sleep:
                await asyncio.sleep(sleep)
            self.log.append(("end", name, args.n))
            if fail:
                raise MisesError(fail, f"{name} failed")
            return ToolOutput(summary=f"{name} ran {args.n}", data={"n": args.n})
        self.reg.register(f)

    def loop(self, defn=None, **cfg):
        cfg.setdefault("retry_base_delay", 0)
        return AgentLoop(run_id=self.run_id,
                         defn=defn or AgentDefinition(name="tester", description="d", body="Be a tester."),
                         provider=self.provider, model="m", gate=self.gate, registry=self.reg,
                         store=self.store, events=self.bus, workdir=self.tmp,
                         config=LoopConfig(**cfg))


def results_of(msg):
    return [b for b in msg.content if isinstance(b, ToolResultBlock)]


async def test_text_only_turn(tmp_path):
    env = Env(tmp_path, [[TextDelta(text="hel"), TextDelta(text="lo"),
                          Usage(input_tokens=10, output_tokens=5), Done(stop_reason="end_turn")]])
    sub = env.bus.subscribe(env.run_id)
    r = await env.loop().run("hi")
    assert r.stop_reason == "end_turn" and r.final_text == "hello" and r.error is None
    assert [m.role for m in r.messages] == ["user", "assistant"]
    assert r.messages[1].content == [TextBlock(text="hello")]
    run = env.store.get_run(env.run_id)
    assert (run.input_tokens, run.output_tokens) == (10, 5)
    steps = env.store.steps(env.run_id, [StepKind.MESSAGE])
    assert [s.payload["role"] for s in steps] == ["user", "assistant"]
    assert steps[1].payload["text"] == "hello"
    deltas = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "text_delta":
            deltas.append(ev.data["text"])
    assert deltas == ["hel", "lo"]
    req = env.provider.requests[0]
    assert "Be a tester." in req.system and req.messages[0].content == [TextBlock(text="hi")]


async def test_tool_roundtrip(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "echo", n=3)), text_turn("done")])
    env.add("echo")
    r = await env.loop().run("go")
    assert r.stop_reason == "end_turn" and r.final_text == "done"
    assert [t.name for t in env.provider.requests[0].tools] == ["echo"]
    second = env.provider.requests[1].messages
    assert isinstance(second[1].content[0], ToolUseBlock) and second[1].content[0].id == "c1"
    (res,) = results_of(second[2])
    assert res.tool_use_id == "c1" and not res.is_error
    assert json.loads(res.content)["summary"] == "echo ran 3"
    assert [m.role for m in r.messages] == ["user", "assistant", "user", "assistant"]


async def test_unknown_tool_result_is_error_and_run_continues(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "nope")), text_turn("ok")])
    r = await env.loop().run("go")
    assert r.stop_reason == "end_turn" and r.final_text == "ok"
    (res,) = results_of(env.provider.requests[1].messages[2])
    assert res.is_error and json.loads(res.content)["error"] == "not_found"


async def test_bad_json_args_invalid_input(tmp_path):
    env = Env(tmp_path, [tool_turn(ToolCall(id="c1", name="echo", input={}, raw="{bad")),
                         text_turn("ok")])
    env.add("echo")
    r = await env.loop().run("go")
    assert r.stop_reason == "end_turn"
    (res,) = results_of(env.provider.requests[1].messages[2])
    assert res.is_error and json.loads(res.content)["error"] == "invalid_input"
    assert env.log == []  # handler never ran
    assert env.store.steps(env.run_id, [StepKind.TOOL_CALL]) == []  # gate never called


async def test_tool_not_offered_to_definition_is_refused(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "secret")), text_turn("ok")])
    env.add("echo")
    env.add("secret")
    defn = AgentDefinition(name="tester", description="d", body="b", tools=["echo", "ghost_tool"])
    r = await env.loop(defn).run("go")
    assert r.stop_reason == "end_turn" and env.log == []
    assert [t.name for t in env.provider.requests[0].tools] == ["echo"]
    (res,) = results_of(env.provider.requests[1].messages[2])
    assert res.is_error and json.loads(res.content)["error"] == "not_found"
    (dec,) = env.store.steps(env.run_id, [StepKind.DECISION])
    assert dec.payload["skipped_tools"] == ["ghost_tool"]


async def test_tools_filtered_by_policy_classes(tmp_path):
    env = Env(tmp_path, [text_turn("ok")], allowed=[C.READ])
    env.add("echo")
    env.add("writer", C.LOCAL_MUTATE)
    await env.loop().run("go")
    assert [t.name for t in env.provider.requests[0].tools] == ["echo"]


async def test_reads_run_concurrently(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "slow", n=1), call("c2", "slow", n=2)),
                         text_turn("ok")])
    env.add("slow", sleep=0.2)
    t0 = time.monotonic()
    r = await env.loop().run("go")
    assert time.monotonic() - t0 < 0.35
    assert r.stop_reason == "end_turn"
    res = results_of(env.provider.requests[1].messages[2])
    assert [b.tool_use_id for b in res] == ["c1", "c2"] and not any(b.is_error for b in res)


async def test_mutations_sequential(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "mut", n=1), call("c2", "mut", n=2)),
                         text_turn("ok")])
    env.add("mut", C.LOCAL_MUTATE, sleep=0.05)
    await env.loop().run("go")
    assert env.log == [("start", "mut", 1), ("end", "mut", 1),
                       ("start", "mut", 2), ("end", "mut", 2)]


async def test_max_turns(tmp_path):
    env = Env(tmp_path, [tool_turn(call(f"c{i}", "echo", n=i)) for i in range(3)])
    env.add("echo")
    r = await env.loop(max_turns=2).run("go")
    assert r.stop_reason == "max_turns" and len(env.provider.requests) == 2
    # history stays well-formed: last message carries the tool results
    assert results_of(r.messages[-1])


async def test_circuit_breaker(tmp_path):
    env = Env(tmp_path, [tool_turn(call(f"c{i}", "boom", n=i)) for i in range(5)])
    env.add("boom", fail=ErrorCode.TOOL_FAILED)
    r = await env.loop(max_repeat_errors=3).run("go")
    assert r.stop_reason == "circuit_open" and len(env.provider.requests) == 3
    assert "boom" in (r.error or "")


async def test_circuit_breaker_resets_on_success(tmp_path):
    turns = [tool_turn(call("a", "boom", n=1)), tool_turn(call("b", "boom", n=2)),
             tool_turn(call("c", "echo")), tool_turn(call("d", "boom", n=3)), text_turn("ok")]
    env = Env(tmp_path, turns)
    env.add("boom", fail=ErrorCode.TOOL_FAILED)
    env.add("echo")
    r = await env.loop(max_repeat_errors=3).run("go")
    assert r.stop_reason == "end_turn"


class SummarisingProvider(FakeProvider):
    """Answers summary requests (no tools offered) itself; agent turns come from the script."""

    def __init__(self, turns):
        super().__init__(turns)
        self.summary_requests = []

    async def stream(self, req):
        if not req.tools:
            self.summary_requests.append(req)
            yield TextDelta(text="did several echoes")
            yield Done(stop_reason="end_turn")
            return
        async for ev in super().stream(req):
            yield ev


async def test_compaction_keeps_tool_pairs(tmp_path):
    turns = [tool_turn(call(f"c{i}", "echo", n=i)) for i in range(6)] + [text_turn("fin")]
    env = Env(tmp_path, provider=SummarisingProvider(turns))
    env.add("echo")
    r = await env.loop(context_budget_tokens=300, compact_at=0.5, keep_recent=3).run("go " * 20)
    assert r.stop_reason == "end_turn" and r.final_text == "fin"
    assert env.store.steps(env.run_id, [StepKind.COMPACTION])
    assert env.provider.summary_requests
    agent_reqs = [q for q in env.provider.requests if q.tools]
    assert any(m.content and isinstance(m.content[0], TextBlock)
               and m.content[0].text.startswith("[Summary of earlier work]\n")
               for q in agent_reqs for m in q.messages[:1])
    for q in agent_reqs:
        msgs = q.messages
        assert msgs[0].role == "user"
        for a, b in zip(msgs, msgs[1:]):
            assert a.role != b.role  # alternation kept
        for i, m in enumerate(msgs):
            for res in results_of(m):
                prev = msgs[i - 1] if i > 0 else None
                assert prev is not None and any(
                    isinstance(x, ToolUseBlock) and x.id == res.tool_use_id for x in prev.content)


async def test_cancel_mid_tool(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "slow"), call("c2", "after"))])
    started = asyncio.Event()

    @tool("slow", description="slow", safety=C.LOCAL_MUTATE)
    async def slow(ctx, args: In) -> ToolOutput:
        ctx.store.ledger_reserve(ctx.user, ctx.run_id, "/x", 1, "k1")
        started.set()
        await asyncio.sleep(30)
        return ToolOutput(summary="never")
    env.reg.register(slow)
    env.add("after", C.LOCAL_MUTATE)
    loop = env.loop()
    task = asyncio.create_task(loop.run("go"))
    await asyncio.wait_for(started.wait(), 5)
    loop.cancel()
    r = await asyncio.wait_for(task, 5)
    assert r.stop_reason == "cancelled"
    assert env.log == []  # pending call never executed
    assert env.store.ledger_used("local", since=0) == 0  # reservation released
    res = results_of(r.messages[-1])
    assert [b.tool_use_id for b in res] == ["c1", "c2"]
    assert all(b.is_error and json.loads(b.content)["error"] == "cancelled" for b in res)


async def test_cancel_rejects_pending_approval(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "merge"))])
    env.add("merge", C.IRREVERSIBLE)
    sub = env.bus.subscribe(env.run_id)
    loop = env.loop()
    task = asyncio.create_task(loop.run("go"))
    while (await asyncio.wait_for(sub.queue.get(), 5)).type != "approval_requested":
        pass
    assert len(env.store.pending_approvals(env.run_id)) == 1
    loop.cancel()
    r = await asyncio.wait_for(task, 5)
    assert r.stop_reason == "cancelled" and env.log == []
    assert env.store.pending_approvals(env.run_id) == []


async def test_cancel_mid_stream(tmp_path):
    class Hanging(FakeProvider):
        async def stream(self, req):
            self.requests.append(req)
            yield TextDelta(text="part")
            await asyncio.sleep(30)

    env = Env(tmp_path, provider=Hanging([]))
    loop = env.loop()
    task = asyncio.create_task(loop.run("go"))
    while not env.provider.requests:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)
    loop.cancel()
    r = await asyncio.wait_for(task, 5)
    assert r.stop_reason == "cancelled" and [m.role for m in r.messages] == ["user"]


class Flaky(FakeProvider):
    """Raises TRANSIENT (after a partial delta) for the first `fails` streams."""

    def __init__(self, turns, fails, code=ErrorCode.TRANSIENT):
        super().__init__(turns)
        self.fails, self.code, self.attempts = fails, code, 0

    async def stream(self, req):
        self.attempts += 1
        if self.attempts <= self.fails:
            self.requests.append(req)
            yield TextDelta(text="partial ")
            raise MisesError(self.code, "provider unavailable (HTTP 529)")
        async for ev in super().stream(req):
            yield ev


async def test_transient_provider_error_retried_then_fails(tmp_path):
    env = Env(tmp_path, provider=Flaky([], fails=10))
    r = await env.loop().run("go")
    assert r.stop_reason == "error" and "529" in (r.error or "")
    assert env.provider.attempts == 3
    assert [m.role for m in r.messages] == ["user"]
    assert [s.payload["role"] for s in env.store.steps(env.run_id, [StepKind.MESSAGE])] == ["user"]
    assert env.store.steps(env.run_id, [StepKind.ERROR])


async def test_transient_then_success_discards_partial_turn(tmp_path):
    env = Env(tmp_path, provider=Flaky([text_turn("whole answer")], fails=2))
    r = await env.loop().run("go")
    assert r.stop_reason == "end_turn" and r.final_text == "whole answer"
    steps = env.store.steps(env.run_id, [StepKind.MESSAGE])
    assert [s.payload.get("text") for s in steps if s.payload["role"] == "assistant"] == ["whole answer"]


async def test_non_transient_provider_error_stops(tmp_path):
    env = Env(tmp_path, provider=Flaky([], fails=10, code=ErrorCode.DENIED))
    r = await env.loop().run("go")
    assert r.stop_reason == "error" and env.provider.attempts == 1


async def test_max_tokens_stop(tmp_path):
    env = Env(tmp_path, [[TextDelta(text="trunc"), Done(stop_reason="max_tokens")]])
    r = await env.loop().run("go")
    assert r.stop_reason == "max_tokens" and r.final_text == "trunc"


async def test_history_is_continued(tmp_path):
    env = Env(tmp_path, [text_turn("one"), text_turn("two")])
    loop = env.loop()
    r1 = await loop.run("first")
    r2 = await loop.run("second", history=r1.messages)
    assert [m.role for m in r2.messages] == ["user", "assistant", "user", "assistant"]
    assert env.provider.requests[1].messages[0].content == [TextBlock(text="first")]
