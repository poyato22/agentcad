import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from mises.approvals import ApprovalService
from mises.events import EventBus
from mises.gate import Gate
from mises.store import Store
from mises.tools.registry import ToolRegistry, tool
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C, StepKind, ToolOutput

ALL = [C.READ, C.LOCAL_MUTATE, C.ONSHAPE_MUTATE, C.IRREVERSIBLE]


class In(BaseModel):
    n: int = 0
    part: str = "p1"


class Env:
    def __init__(self, tmp_path, allowed=ALL, edit=("*",), read=("*",), caps=None):
        self.store = Store(":memory:")
        self.bus = EventBus()
        self.approvals = ApprovalService(self.store, self.bus)
        self.reg = ToolRegistry()
        self.gate = Gate(self.reg, self.store, self.approvals, self.bus)
        self.run = self.store.create_run("designer", "fake", "m", "t")
        self.store.create_policy(self.run.id, list(edit), list(read), caps or Caps(), list(allowed))
        self.calls = 0
        self.tmp = tmp_path

    def add(self, name="t", **kw):
        @tool(name, description="d", **kw)
        async def f(ctx, args: In) -> ToolOutput:
            self.calls += 1
            return ToolOutput(summary=f"ran {args.n}", data={"n": args.n})
        self.reg.register(f)
        return f

    async def call(self, name, inp, cid="c1"):
        return await self.gate.call(self.run.id, name, inp, call_id=cid, workdir=self.tmp)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


async def err(coro) -> MisesError:
    with pytest.raises(MisesError) as e:
        await coro
    return e.value


async def test_unknown_tool_not_found(env):
    assert (await err(env.call("zzz", {}))).code == ErrorCode.NOT_FOUND


async def test_invalid_input(env):
    env.add(safety=C.READ)
    e = await err(env.call("t", {"n": "abc"}))
    assert e.code == ErrorCode.INVALID_INPUT and "n" in e.message
    assert env.calls == 0


async def test_class_not_allowed_denied(tmp_path):
    env = Env(tmp_path, allowed=[C.READ])
    env.add(safety=C.LOCAL_MUTATE)
    assert (await err(env.call("t", {}))).code == ErrorCode.DENIED


async def test_out_of_scope(tmp_path):
    env = Env(tmp_path, edit=["p2"], read=["p3"])
    env.add("m", safety=C.LOCAL_MUTATE, scope_arg="part")
    env.add("r", safety=C.READ, scope_arg="part")
    e = await err(env.call("m", {"part": "p1"}))
    assert e.code == ErrorCode.OUT_OF_SCOPE and e.hint
    await env.call("m", {"part": "p2"})
    await err(env.call("r", {"part": "p1"}))
    await env.call("r", {"part": "p3"})
    await env.call("r", {"part": "p2"})  # edit scope readable too


async def test_star_scope_allows(env):
    env.add(safety=C.LOCAL_MUTATE, scope_arg="part")
    assert (await env.call("t", {"part": "anything"})).ok


async def test_budget_exhausted(tmp_path):
    env = Env(tmp_path, caps=Caps(onshape_calls_run=3))
    env.add(safety=C.ONSHAPE_MUTATE, onshape_cost=4)
    assert (await err(env.call("t", {}))).code == ErrorCode.BUDGET_EXHAUSTED
    assert env.calls == 0


async def test_budget_year(tmp_path):
    env = Env(tmp_path, caps=Caps(onshape_calls_year=3))
    env.add(safety=C.ONSHAPE_MUTATE, onshape_cost=2)
    env.store.ledger_reserve("local", env.run.id, "x", 2, "k")
    assert (await err(env.call("t", {}))).code == ErrorCode.BUDGET_EXHAUSTED


async def test_irreversible_waits_for_human(env):
    env.add(safety=C.IRREVERSIBLE)

    async def human():
        while not env.store.pending_approvals():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        a = env.store.pending_approvals()[0]
        env.approvals.decide(a.id, a.payload_hash, True, "me")

    h = asyncio.create_task(human())
    out = await env.call("t", {"n": 7})
    await h
    assert out.data == {"n": 7} and env.calls == 1


async def test_irreversible_executes_edited_payload(env):
    env.add(safety=C.IRREVERSIBLE)

    async def human():
        while not env.store.pending_approvals():
            await asyncio.sleep(0.01)
        a = env.store.pending_approvals()[0]
        env.approvals.decide(a.id, a.payload_hash, True, "me", edited_payload={"n": 9, "part": "p1"})

    h = asyncio.create_task(human())
    out = await env.call("t", {"n": 7})
    await h
    assert out.data == {"n": 9}


async def test_irreversible_rejected_denied(env):
    env.add(safety=C.IRREVERSIBLE)

    async def human():
        while not env.store.pending_approvals():
            await asyncio.sleep(0.01)
        a = env.store.pending_approvals()[0]
        env.approvals.decide(a.id, a.payload_hash, False, "me")

    h = asyncio.create_task(human())
    e = await err(env.call("t", {}))
    await h
    assert e.code == ErrorCode.DENIED and env.calls == 0


async def test_idempotent_replay(env):
    env.add(safety=C.LOCAL_MUTATE)
    a = await env.call("t", {"n": 1}, "c1")
    b = await env.call("t", {"n": 1}, "c1")      # retry of the same call: replayed
    assert env.calls == 1 and a == b
    await env.call("t", {"n": 1}, "c2")          # new call, same input: runs again
    assert env.calls == 2
    await env.call("t", {"n": 2}, "c3")
    assert env.calls == 3


async def test_local_mutate_toggle_rebuilds_every_time(env):
    # C1: set 20 -> set 10 -> set 20 must really apply the last set, not replay the first
    state = {"width": 10}

    @tool("setw", description="d", safety=C.LOCAL_MUTATE)
    async def f(ctx, args: In) -> ToolOutput:
        state["width"] = args.n
        return ToolOutput(summary=f"width {args.n}")
    env.reg.register(f)
    for i, w in enumerate([20, 10, 20]):
        await env.call("setw", {"n": w}, f"c{i}")
    assert state["width"] == 20
    await env.call("setw", {"n": 10}, "c9")
    assert state["width"] == 10


async def test_failed_output_not_cached(env):
    results = iter([False, True])

    @tool("flaky", description="d", safety=C.LOCAL_MUTATE)
    async def f(ctx, args: In) -> ToolOutput:
        env.calls += 1
        return ToolOutput(ok=next(results), summary="x")
    env.reg.register(f)
    a = await env.call("flaky", {}, "c1")
    b = await env.call("flaky", {}, "c1")        # same call retried after ok=False: runs again
    assert not a.ok and b.ok and env.calls == 2


async def test_read_not_cached(env):
    env.add(safety=C.READ)
    await env.call("t", {})
    await env.call("t", {})
    assert env.calls == 2


async def test_timeout_is_transient(env):
    @tool("slow", description="d", safety=C.READ, timeout_s=0.05)
    async def f(ctx, args: In) -> ToolOutput:
        await asyncio.sleep(1)
        return ToolOutput(summary="")
    env.reg.register(f)
    assert (await err(env.call("slow", {}))).code == ErrorCode.TRANSIENT


async def test_exception_is_tool_failed(env):
    @tool("boom", description="d", safety=C.READ)
    async def f(ctx, args: In) -> ToolOutput:
        raise RuntimeError("kaput")
    env.reg.register(f)
    e = await err(env.call("boom", {}))
    assert e.code == ErrorCode.TOOL_FAILED and e.message == "RuntimeError: kaput"


async def test_mises_error_passes_through(env):
    @tool("st", description="d", safety=C.READ)
    async def f(ctx, args: In) -> ToolOutput:
        raise MisesError(ErrorCode.STALE_VERSION, "old")
    env.reg.register(f)
    assert (await err(env.call("st", {}))).code == ErrorCode.STALE_VERSION


async def test_large_output_becomes_handle(env):
    @tool("big", description="d", safety=C.READ)
    async def f(ctx, args: In) -> ToolOutput:
        return ToolOutput(summary="big", data={"blob": "x" * 20000})
    env.reg.register(f)
    out = await env.call("big", {}, "cbig")
    assert out.data == {} and out.summary == "big"
    p = Path(out.handles[0])
    assert p.exists() and p == env.tmp / "outputs" / "cbig.json"
    assert len(json.loads(p.read_text())["data"]["blob"]) == 20000


async def test_steps_recorded(env):
    env.add(safety=C.READ)
    await env.call("t", {"n": 3})
    await err(env.call("zzz", {}))
    kinds = [s.kind for s in env.store.steps(env.run.id)]
    assert kinds == [StepKind.TOOL_CALL, StepKind.TOOL_RESULT, StepKind.TOOL_CALL, StepKind.ERROR]
    assert env.store.verify_chain(env.run.id)


async def test_events_published(env):
    env.add(safety=C.READ)
    sub = env.bus.subscribe(env.run.id)
    await env.call("t", {})
    types = [(await sub.__anext__()).type for _ in range(2)]
    assert types == ["tool_call", "tool_result"]


async def test_preview_result_in_approval_deltas(env):
    seen = []

    async def preview(ctx, args: In) -> dict:
        seen.append(ctx.run_id)
        return {"would_change": args.n}

    @tool("pv", description="d", safety=C.IRREVERSIBLE, preview=preview)
    async def f(ctx, args: In) -> ToolOutput:
        env.calls += 1
        return ToolOutput(summary="done")
    env.reg.register(f)

    async def human():
        while not env.store.pending_approvals():
            await asyncio.sleep(0.01)
        a = env.store.pending_approvals()[0]
        assert a.deltas == {"would_change": 4}
        env.approvals.decide(a.id, a.payload_hash, False, "me")

    h = asyncio.create_task(human())
    e = await err(env.call("pv", {"n": 4}))
    await h
    assert e.code == ErrorCode.DENIED and env.calls == 0 and seen == [env.run.id]


async def test_preview_error_propagates_without_approval(env):
    async def preview(ctx, args: In) -> dict:
        raise MisesError(ErrorCode.STALE_VERSION, "moved")

    @tool("pv", description="d", safety=C.IRREVERSIBLE, preview=preview)
    async def f(ctx, args: In) -> ToolOutput:
        env.calls += 1
        return ToolOutput(summary="done")
    env.reg.register(f)
    e = await err(env.call("pv", {}))
    assert e.code == ErrorCode.STALE_VERSION and env.calls == 0
    assert env.store.pending_approvals() == []
    assert env.store.steps(env.run.id)[-1].kind == StepKind.ERROR


async def _approve_all(env, n):
    done = 0
    while done < n:
        for a in env.store.pending_approvals():
            env.approvals.decide(a.id, a.payload_hash, True, "me")
            done += 1
        await asyncio.sleep(0.01)


async def test_irreversible_identical_input_new_call_runs_again(env):
    env.add(safety=C.IRREVERSIBLE)
    h = asyncio.create_task(_approve_all(env, 2))
    await env.call("t", {"n": 1}, "c1")
    await env.call("t", {"n": 1}, "c2")
    await h
    assert env.calls == 2
    n_appr = env.store._one("SELECT COUNT(*) FROM approvals")[0]
    assert n_appr == 2


async def test_irreversible_same_call_id_deduplicated(env):
    env.add(safety=C.IRREVERSIBLE)
    h = asyncio.create_task(_approve_all(env, 2))
    await env.call("t", {"n": 1}, "c1")
    await env.call("t", {"n": 1}, "c1")
    await h
    assert env.calls == 1


async def test_onshape_mutate_keyed_by_call_id(env):
    env.add(safety=C.ONSHAPE_MUTATE)
    await env.call("t", {"n": 1}, "c1")
    await env.call("t", {"n": 1}, "c2")
    await env.call("t", {"n": 1}, "c2")
    assert env.calls == 2
