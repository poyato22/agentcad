"""Fix round 1: slot leaks on early cancel, and the combined local-model memory budget."""
import asyncio

import pytest

from mises.claude_code import detect
from mises.profile import Profile, save_profile

from test_runtime_queue import Env, isolated, until  # noqa: F401


# ---- slot leaks

class _StubSession:
    alive = False
    session_id = "s"

    async def close(self):
        pass


async def test_claude_run_cancelled_while_starting_gives_its_slot_back(tmp_path, monkeypatch):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    rt.mcp_endpoint = ("http://127.0.0.1:1/mcp", "tok")

    async def ready():
        return detect.ClaudeInfo(path="/x/claude", version="1"), detect.NOT_SIGNED_IN
    monkeypatch.setattr(rt, "_claude_ready", ready)

    async def spawn(rid, st, alias, info, auth, url, token, *, resume):
        st.session = _StubSession()
        assert rt.parallel.holds(rid)
        rt.cancel(rid)                     # the user stops it while `claude` starts
    monkeypatch.setattr(rt, "_spawn_claude", spawn)
    rid = await rt.start_run("designer", "hi", model_ref="claude-code/sonnet")
    assert rt.store.get_run(rid).status == "cancelled"
    assert rt.parallel.active() == set()
    # the freed slot is usable
    other = await rt.start_run("designer", "next")
    assert (await rt.wait(other)).final_text == "next"


async def test_finish_external_run_releases_an_active_slot(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    rid = rt.create_external_run("tester", provider="claude-code", model="sonnet",
                                 slot_ref="claude-code/sonnet")
    assert rt.parallel.holds(rid)
    rt.finish_external_run(rid, "done")
    assert rt.parallel.active() == set()
    rt.finish_external_run(rid, "done")     # idempotent
    assert rt.parallel.active() == set()


async def test_run_task_cancelled_before_its_first_step_releases_the_slot(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    sub = rt.events.subscribe()
    rid = await rt.start_run("designer", "a")
    assert rt.parallel.holds(rid)
    task = rt._runs[rid].task
    task.cancel()                           # before the task ever ran
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rt.parallel.active() == set()
    assert rt.store.get_run(rid).status == "cancelled"
    finished = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "run_finished":
            finished.append(ev.run_id)
    assert finished == [rid]


async def test_queued_run_task_cancelled_before_its_first_step_leaves_the_queue(tmp_path):
    env = Env(tmp_path, max_parallel=1)
    rt = env.rt
    a = await rt.start_run("designer", "block a")
    await until(lambda: env.started == [a])
    b = await rt.start_run("designer", "b")
    task = rt._runs[b].task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rt.parallel.queued() == [] and rt.store.get_run(b).status == "cancelled"
    env.gate.set()
    await rt.wait(a)
    assert rt.parallel.active() == set()


# ---- combined memory budget for local models (24 GB Mac: 19 GB for models)

SIZES = {"qwen3-coder:30b": 19.0, "gpt-oss:20b": 14.0, "qwen3.5:9b": 6.6, "tiny:1b": 1.0,
         "huge:70b": 40.0}


@pytest.fixture
def sized(tmp_path, monkeypatch):
    env = Env(tmp_path, max_parallel=4)

    async def sizes(base_url):
        return dict(SIZES)
    monkeypatch.setattr(env.rt, "_ollama_sizes", sizes)
    return env


async def test_two_big_local_models_do_not_run_together(sized):
    rt = sized.rt
    a = await rt.start_run("designer", "block a", model_ref="ollama/qwen3-coder:30b")
    await until(lambda: sized.started == [a])
    b = await rt.start_run("designer", "b", model_ref="ollama/gpt-oss:20b")
    assert rt.store.get_run(b).status == "queued"
    c = await rt.start_run("designer", "c", model_ref="anthropic/x")   # cloud is unaffected
    assert (await rt.wait(c)).final_text == "c"
    sized.gate.set()
    assert (await rt.wait(b)).final_text == "b"


async def test_two_runs_of_a_small_model_run_together(sized):
    rt = sized.rt
    ids = [await rt.start_run("designer", f"block {i}", model_ref="ollama/qwen3.5:9b")
           for i in range(2)]
    await until(lambda: len(sized.started) == 2)
    assert all(rt.store.get_run(i).status == "running" for i in ids)
    sized.gate.set()
    for i in ids:
        await rt.wait(i)


async def test_distinct_small_models_share_the_budget(sized):
    rt = sized.rt
    a = await rt.start_run("designer", "block a", model_ref="ollama/gpt-oss:20b")
    await until(lambda: sized.started == [a])
    b = await rt.start_run("designer", "block b", model_ref="ollama/tiny:1b")     # 15 <= 19
    await until(lambda: len(sized.started) == 2)
    c = await rt.start_run("designer", "c", model_ref="ollama/qwen3.5:9b")        # 21.6 > 19
    assert rt.store.get_run(b).status == "running"
    assert rt.store.get_run(c).status == "queued"
    sized.gate.set()
    for i in (a, b, c):
        await rt.wait(i)


async def test_a_model_larger_than_the_budget_still_runs_alone(sized):
    rt = sized.rt
    a = await rt.start_run("designer", "huge", model_ref="ollama/huge:70b")
    assert (await rt.wait(a)).final_text == "huge"


async def test_status_reason_explains_the_shared_budget(tmp_path):
    env = Env(tmp_path)
    save_profile(env.rt.home, Profile(default_model="ollama/qwen3:8b"))
    reason = env.rt.parallel_status()["reason"]
    assert "local models share 19 GB" in reason
