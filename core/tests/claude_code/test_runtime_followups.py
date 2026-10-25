"""Task 3 review follow-ups: a short Claude Code status cache for /models and /setup, wait() on a
Claude Code run that has no turn running, and orchestrated children releasing their session."""
import asyncio

import httpx
import pytest

import mises.runtime as runtime_mod
from mises.claude_code import detect
from mises.runtime import Runtime, _ClaudeRun
from mises.server.app import create_app
from mises.types import ErrorCode, MisesError

TOKEN = "t6-token"


class _Counter:
    def __init__(self, signed_in=True):
        self.calls = 0
        self.signed_in = signed_in

    async def __call__(self, info):
        self.calls += 1
        return detect.AuthStatus(signed_in=self.signed_in, auth_method="claude.ai" if self.signed_in
                                 else "none", email=None, config_dir=None, raw_ok=True)


@pytest.fixture
def counted(rt, monkeypatch):
    monkeypatch.setattr(detect, "find_claude", lambda: detect.ClaudeInfo("/opt/claude", "2.1.4"))
    c = _Counter()
    monkeypatch.setattr(detect, "auth_status", c)
    return c


def _client(rt):
    app = create_app(rt, TOKEN, local_probe=_no_local)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core",
                             headers={"Authorization": f"Bearer {TOKEN}"})


async def _no_local(urls):
    return False


# ---- status cache


async def test_models_and_setup_share_a_short_cache(rt, counted):
    async with _client(rt) as c:
        for _ in range(3):
            assert (await c.get("/models")).status_code == 200
            assert (await c.get("/setup")).status_code == 200
    assert counted.calls == 1


async def test_claude_code_status_route_is_always_fresh_and_refreshes_the_cache(rt, counted):
    async with _client(rt) as c:
        await c.get("/models")
        counted.signed_in = False
        st = (await c.get("/claude-code/status")).json()
        assert st["signed_in"] is False and counted.calls == 2
        entry = next(v for v in (await c.get("/models")).json()["vendors"]
                     if v["vendor"] == "claude-code")
        assert entry["key"] == "signed_out" and counted.calls == 2


async def test_cache_expires(rt, counted, monkeypatch):
    monkeypatch.setattr(runtime_mod, "CLAUDE_STATUS_TTL_S", 0.05)
    async with _client(rt) as c:
        await c.get("/models")
        await asyncio.sleep(0.1)
        await c.get("/models")
    assert counted.calls == 2


async def test_logout_drops_the_cache(rt, counted, monkeypatch):
    async def fake_logout(info):
        return True
    monkeypatch.setattr(detect, "logout", fake_logout)
    await rt.claude_status_cached()
    await rt.claude_logout()
    await rt.claude_status_cached()
    assert counted.calls == 2


async def test_concurrent_cached_reads_run_one_status_check(rt, counted):
    await asyncio.gather(*(rt.claude_status_cached() for _ in range(5)))
    assert counted.calls == 1


# ---- wait() on a Claude Code run without a turn task


def _claude_run(rt) -> str:
    return rt.create_external_run("designer", provider="claude-code", model="sonnet", task="hi")


async def test_wait_after_cancel_during_start_is_cancelled(rt):
    rid = _claude_run(rt)
    rt._claude[rid] = _ClaudeRun(cancel_requested=True)
    res = await asyncio.wait_for(rt.wait(rid), 2)
    assert res.stop_reason == "cancelled"


async def test_wait_on_an_idle_resumed_session_is_an_idle_result(rt):
    rid = _claude_run(rt)
    rt.finish_external_run(rid, "done")
    rt._claude[rid] = _ClaudeRun()                # resumed without a message: no turn
    res = await asyncio.wait_for(rt.wait(rid), 2)
    assert res.stop_reason == "end_turn" and res.final_text == ""


async def test_wait_keeps_the_last_turn_result(rt):
    rid = _claude_run(rt)
    st = rt._claude[rid] = _ClaudeRun()
    st.result = runtime_mod.RunResult(final_text="done it", messages=[], stop_reason="end_turn")
    assert (await rt.wait(rid)).final_text == "done it"


@pytest.mark.parametrize("status,stop", [("done", "end_turn"), ("cancelled", "cancelled"),
                                         ("failed", "error")])
async def test_wait_on_a_claude_run_this_process_no_longer_holds(rt, status, stop):
    rid = _claude_run(rt)
    rt.finish_external_run(rid, status)
    res = await rt.wait(rid)
    assert res.stop_reason == stop


async def test_wait_on_unknown_run_is_still_not_found(rt):
    with pytest.raises(MisesError) as ei:
        await rt.wait("nope")
    assert ei.value.code == ErrorCode.NOT_FOUND


# ---- releasing a child's session


class _FakeSession:
    def __init__(self):
        self.closed = 0
        self.alive = True

    async def close(self):
        self.closed += 1
        self.alive = False


async def test_release_closes_a_claude_session_and_forgets_it(rt):
    rid = _claude_run(rt)
    s = _FakeSession()
    rt._claude[rid] = _ClaudeRun(session=s)
    await rt.release_claude_session(rid)
    assert s.closed == 1 and rid not in rt._claude
    await rt.release_claude_session(rid)          # idempotent
    await rt.release_claude_session("not-a-claude-run")


async def test_release_waits_for_nothing_mid_turn(rt):
    # a turn still running is left alone: release only frees an idle session
    rid = _claude_run(rt)
    s = _FakeSession()
    task = asyncio.get_running_loop().create_future()
    st = rt._claude[rid] = _ClaudeRun(session=s, task=task)  # type: ignore[arg-type]
    await rt.release_claude_session(rid)
    assert s.closed == 0 and rt._claude[rid] is st
    task.cancel()


def test_runtime_has_the_ttl_constant():
    assert 0 < runtime_mod.CLAUDE_STATUS_TTL_S <= 10
    assert Runtime.release_claude_session
