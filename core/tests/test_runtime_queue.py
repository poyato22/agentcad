"""Runtime enforces the parallel-agents limit: runs beyond it are queued and start when a slot
frees. The autouse conftest fixture makes every test's Mac a 24 GB M5 Pro with 6 performance
cores (auto: 3 cloud runs at once)."""
import asyncio

import pytest
from pydantic import BaseModel

import mises.runtime as runtime_mod
from mises.profile import Profile, save_profile
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.tools.registry import tool
from mises.types import SafetyClass as C, ToolOutput


class In(BaseModel):
    n: int = 0


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


def text(t="ok"):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


def block_turn():
    return [ToolCall(id="b1", name="block", input={}), Done(stop_reason="tool_use")]


class Env:
    """Each run gets its own FakeProvider: a run whose prompt starts with "block" calls the
    `block` tool (it waits for `gate`), then answers; any other run answers at once."""

    def __init__(self, tmp_path, *, max_parallel="auto"):
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        save_profile(home, Profile(max_parallel_agents=max_parallel))
        self.gate = asyncio.Event()
        self.started: list[str] = []
        self.refs: list[str] = []
        self.rt = Runtime(home, provider_resolver=self.resolve)
        gate, started = self.gate, self.started

        @tool("block", description="waits", safety=C.READ)
        async def block(ctx, args: In) -> ToolOutput:
            started.append(ctx.run_id)
            await gate.wait()
            return ToolOutput(summary="unblocked")
        self.rt.registry.register(block)
        self._next_prompt: list[str] = []

    def resolve(self, ref):
        self.refs.append(ref)
        return _PromptFake(), "fake-model"


class _PromptFake(FakeProvider):
    def __init__(self):
        super().__init__([])

    async def stream(self, req):
        self.requests.append(req)
        first = req.messages[0].content[0].text
        done_tool = any(m.role == "user" and any(getattr(b, "type", "") == "tool_result"
                                                 for b in m.content) for m in req.messages)
        turn = block_turn() if first.startswith("block") and not done_tool else text(first)
        for ev in turn:
            yield ev


async def until(cond, timeout=5.0):
    async def wait():
        while not cond():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout)


def events_of(sub, type_):
    out = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == type_:
            out.append(ev)
    return out


async def test_run_beyond_the_limit_is_queued_then_starts(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    sub = rt.events.subscribe()
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b")
    assert rt.store.get_run(b).status == "queued"
    queued = [e for e in events_of(sub, "run_queued")]
    assert [e.run_id for e in queued] == [b]
    assert queued[0].data["position"] == 1 and queued[0].data["limit"] == 1
    assert queued[0].data["agent"] == "designer"
    await asyncio.sleep(0.05)
    assert rt.store.get_run(b).status == "queued"       # still waiting for a's slot
    env.gate.set()
    rb = await rt.wait(b)
    assert rb.stop_reason == "end_turn" and rb.final_text == "b"
    assert rt.store.get_run(a).status == "done" and rt.store.get_run(b).status == "done"
    assert rt.parallel.active() == set()


async def test_auto_limit_is_three_cloud_runs_on_this_mac(tmp_path):
    env = Env(tmp_path)
    rt = env.rt
    ids = [await rt.start_run("designer", f"block {i}") for i in range(3)]
    await until(lambda: len(env.started) == 3)
    d = await rt.start_run("designer", "d")
    assert rt.store.get_run(d).status == "queued"
    env.gate.set()
    assert (await rt.wait(d)).final_text == "d"
    for i in ids:
        await rt.wait(i)


async def test_cancel_a_queued_run(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    sub = rt.events.subscribe()
    b = await rt.start_run("designer", "b")
    rt.cancel(b)
    r = await rt.wait(b)
    assert r.stop_reason == "cancelled"
    assert rt.store.get_run(b).status == "cancelled"
    assert [e.run_id for e in events_of(sub, "run_finished")] == [b]
    assert rt.parallel.queued() == []
    env.gate.set()
    await rt.wait(a)
    assert rt.parallel.active() == set()


async def test_one_run_per_local_model_even_with_a_higher_limit(tmp_path):
    env = Env(tmp_path, max_parallel=4)
    rt = env.rt
    a = await rt.start_run("designer", "block a", model_ref="ollama/qwen3:8b")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b", model_ref="ollama/qwen3:8b")
    c = await rt.start_run("designer", "c", model_ref="anthropic/claude-x")
    assert rt.store.get_run(b).status == "queued"
    assert (await rt.wait(c)).final_text == "c"        # a cloud run is not held up
    env.gate.set()
    assert (await rt.wait(b)).final_text == "b"


async def test_small_local_model_runs_two_at_once_when_memory_allows(tmp_path, monkeypatch):
    env = Env(tmp_path)
    rt = env.rt

    async def sizes(base_url):
        return {"qwen3.5:9b": 6.6}
    monkeypatch.setattr(rt, "_ollama_sizes", sizes)
    ids = [await rt.start_run("designer", f"block {i}", model_ref="ollama/qwen3.5:9b")
           for i in range(2)]
    await until(lambda: len(env.started) == 2)
    third = await rt.start_run("designer", "x", model_ref="ollama/qwen3.5:9b")
    assert rt.store.get_run(third).status == "queued"
    env.gate.set()
    for i in [*ids, third]:
        await rt.wait(i)


async def test_openrouter_free_models_share_one_slot(tmp_path):
    env = Env(tmp_path)
    rt = env.rt
    a = await rt.start_run("designer", "block a", model_ref="openrouter/x/a:free")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b", model_ref="openrouter/y/b:free")
    assert rt.store.get_run(b).status == "queued"
    env.gate.set()
    assert (await rt.wait(b)).final_text == "b"


async def test_send_beyond_the_limit_queues_the_next_turn(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "a")
    await rt.wait(a)
    b = await rt.start_run("designer", "block b")
    await until(lambda: env.started == [b])
    await rt.send(a, "again")
    assert rt.store.get_run(a).status == "queued"
    env.gate.set()
    await rt.wait(b)
    r = await rt.wait(a)
    assert r.stop_reason == "end_turn" and rt.store.get_run(a).status == "done"


async def test_subagents_do_not_deadlock_with_a_limit_of_one(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt

    class Spawning(_PromptFake):
        async def stream(self, req):
            self.requests.append(req)
            first = req.messages[0].content[0].text
            has_result = any(getattr(b, "type", "") == "tool_result"
                             for m in req.messages for b in m.content)
            if first == "parent" and not has_result:
                yield ToolCall(id="s1", name="agent_spawn",
                               input={"agent": "designer", "prompt": "child"})
                yield Done(stop_reason="tool_use")
                return
            yield TextDelta(text=f"{first} done")
            yield Done(stop_reason="end_turn")

    rt.provider_resolver = lambda ref: (Spawning(), "fake-model")
    p = await rt.start_run("designer", "parent")
    r = await asyncio.wait_for(rt.wait(p), 5)
    assert r.final_text == "parent done"
    assert rt.parallel.active() == set() and rt.parallel.queued() == []


async def test_orchestrator_runs_at_most_the_limit(tmp_path, monkeypatch):
    env = Env(tmp_path, max_parallel=2)
    seen = {}

    async def fake_run_plan(plan, spawn, *, max_parallel=3):
        seen["max_parallel"] = max_parallel
        return {}
    import mises.orchestrator.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "run_plan", fake_run_plan)

    async def fake_plan(goal, parent_run_id, model_ref):
        from mises.orchestrator.plan import Plan
        return Plan(goal="g", tasks=[])
    monkeypatch.setattr(env.rt.orchestrator, "_plan", fake_plan)
    parent = env.rt.create_external_run("tester", provider="x", model="y")
    await env.rt.orchestrator.run("g", parent_run_id=parent)
    assert seen["max_parallel"] == 2


async def test_parallel_status_auto_and_manual(tmp_path):
    env = Env(tmp_path)
    assert env.rt.parallel_status() == {"limit": 3, "auto": True,
                                        "reason": "M5 Pro · 24 GB · cloud model → 3"}
    save_profile(env.rt.home, Profile(max_parallel_agents=5))
    st = env.rt.parallel_status()
    assert st["limit"] == 5 and st["auto"] is False and "Profile" in st["reason"]
    save_profile(env.rt.home, Profile(default_model="ollama/qwen3:8b"))
    st = env.rt.parallel_status()
    assert st["limit"] == 1 and st["auto"] is True and "local model" in st["reason"]


async def test_restart_fails_queued_runs_of_a_dead_process(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b")
    rt.store.update_run(b, owner_pid=999_999_999)        # its process is gone
    rt.store.update_run(a, owner_pid=999_999_999)
    rt2 = Runtime(rt.home, provider_resolver=env.resolve)
    assert set(rt2.interrupted) == {a, b}
    assert rt2.store.get_run(b).status == "failed"
    env.gate.set()


async def test_cancel_all_also_cancels_queued_runs(tmp_path):
    import httpx
    from mises.server.app import create_app
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b")
    assert rt.store.get_run(b).status == "queued"
    transport = httpx.ASGITransport(app=create_app(rt, "tok"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/runs/cancel_all", headers={"Authorization": "Bearer tok"})
    assert sorted(r.json()["cancelled"]) == sorted([a, b])
    await asyncio.wait_for(rt.wait(b), 5)
    await asyncio.wait_for(rt.wait(a), 5)
    assert rt.store.get_run(b).status == "cancelled"
    assert rt.store.get_run(a).status == "cancelled"
    assert rt.parallel.active() == set() and rt.parallel.queued() == []


async def test_raising_the_limit_in_the_profile_starts_queued_runs(tmp_path):
    import httpx
    from mises.server.app import create_app
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "block b")
    assert rt.store.get_run(b).status == "queued"
    transport = httpx.ASGITransport(app=create_app(rt, "tok"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.put("/profile", headers={"Authorization": "Bearer tok"},
                        json={"max_parallel_agents": 2})
    assert r.status_code == 200
    await until(lambda: env.started == [a, b])
    env.gate.set()
    await rt.wait(a)
    await rt.wait(b)
