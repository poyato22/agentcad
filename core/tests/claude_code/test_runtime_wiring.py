"""claude-code/<alias> as a normal model choice: Runtime routing, routes and the CLI (fake claude)."""
import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from mises.claude_code import detect
from mises.runtime import Runtime
from mises.server.app import serve_async
from mises.types import ErrorCode, MisesError, StepKind

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


def _log(rt, run_id) -> list[dict]:
    p = rt.workdir(run_id) / "fake_claude.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _argvs(rt, run_id) -> list[list[str]]:
    return [r["argv"] for r in _log(rt, run_id) if r["event"] == "argv"]


def _sent(rt, run_id) -> list[str]:
    return [r["msg"]["message"]["content"] for r in _log(rt, run_id) if r["event"] == "stdin"]


def _dead(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


async def _wait_for(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def served(rt):
    port, token, handle = await serve_async(rt, write_server_json=False)
    try:
        yield port, token, handle
    finally:
        await handle.stop()


@pytest.fixture
def signed_in(auth_state):
    auth_state.write_text("signed_in")
    return auth_state


def _pid(rt, rid):
    return rt._claude[rid].session.pid


# ---- runtime


async def test_claude_code_run_needs_the_server(rt, claude_bin, signed_in):
    with pytest.raises(MisesError) as ei:
        await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
    assert ei.value.code == ErrorCode.DENIED and ei.value.hint
    assert rt.store.list_runs() == []


@needs_sandbox
async def test_start_run_creates_external_run_and_finishes_done(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        run = rt.store.get_run(rid)
        assert res.final_text == "Hello there" and res.stop_reason == "end_turn"
        assert (run.provider, run.model, run.external, run.status) == (
            "claude-code", "sonnet", True, "done")
        assert run.task == "hello" and run.session_ref
        argv = _argvs(rt, rid)[0]
        assert argv[argv.index("--session-id") + 1] == run.session_ref
        assert argv[argv.index("--model") + 1] == "sonnet"


@needs_sandbox
async def test_send_continues_the_same_session(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/opus")
        await asyncio.wait_for(rt.wait(rid), 15)
        await rt.send(rid, "second message")
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.final_text == "echo: second message"
        assert len(_argvs(rt, rid)) == 1                      # one process, two turns
        assert _sent(rt, rid)[-1] == "second message"
        run = rt.store.get_run(rid)
        assert run.status == "done" and run.input_tokens == 22


@needs_sandbox
async def test_activity_goes_idle_between_turns_and_back_on_send(rt, claude_bin, signed_in):
    """A Claude Code session outlives its turn: after the turn the run reads 'idle' (QA C,
    fix-core 47b7137), over the event stream and GET /runs/{id}, and leaves it on the next
    message."""
    async with served(rt) as (port, token, _):
        sub = rt.events.subscribe()
        rid = await rt.start_run("designer", "activity", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 20)
        await _wait_for(lambda: (rt.activity_of(rid) or {}).get("state") == "idle")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            act = (await c.get(f"/runs/{rid}")).json()["activity"]
        assert act["state"] == "idle" and act["detail"] == "waiting for your next message"
        states = []
        while True:
            try:
                ev = sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if ev.type == "agent_activity" and ev.run_id == rid:
                states.append(ev.data["state"])
        assert "tool" in states and states[-1] == "idle"
        await rt.send(rid, "hello again")
        await asyncio.wait_for(rt.wait(rid), 15)
        await _wait_for(lambda: (rt.activity_of(rid) or {}).get("state") == "idle")
        assert rt.store.get_run(rid).status == "done"


@needs_sandbox
async def test_cancel_mid_turn_kills_claude(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hang", model_ref="claude-code/sonnet")
        pid = _pid(rt, rid)
        rt._claude[rid].session.term_grace_s = 0.5
        await _wait_for(lambda: _sent(rt, rid))
        await asyncio.sleep(0.2)
        rt.cancel(rid)
        res = await asyncio.wait_for(rt.wait(rid), 10)
        assert res.stop_reason == "cancelled"
        assert rt.store.get_run(rid).status == "cancelled"
        await _wait_for(lambda: _dead(pid))


@needs_sandbox
async def test_cancel_reaches_an_idle_session_and_send_resumes(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        pid = _pid(rt, rid)
        assert rt.store.get_run(rid).status == "done"
        rt.cancel(rid)                                   # idle between turns: still reachable
        await _wait_for(lambda: _dead(pid))
        assert not (rt.workdir(rid) / "claude-mcp.json").exists()
        await rt.send(rid, "hello again")                # re-spawns with --resume
        res = await asyncio.wait_for(rt.wait(rid), 15)
        assert res.final_text == "Hello there"
        session_ref = rt.store.get_run(rid).session_ref
        argvs = _argvs(rt, rid)
        assert len(argvs) == 2
        assert argvs[1][argvs[1].index("--resume") + 1] == session_ref
        assert "--session-id" not in argvs[1]


@needs_sandbox
async def test_reopen_is_a_public_runtime_call(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        sub = rt.events.subscribe(rid)
        await rt.send(rid, "hello")
        await asyncio.wait_for(rt.wait(rid), 15)
        evs = []
        while not sub.queue.empty():
            evs.append(sub.queue.get_nowait())
        sub.close()
        started = [e.data for e in evs if e.type == "run_started"]
        assert started and started[0]["resumed"] is True and started[0]["provider"] == "claude-code"
        assert rt.store.get_run(rid).owner_pid == os.getpid()


@needs_sandbox
async def test_resume_after_a_new_runtime(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        session_ref = rt.store.get_run(rid).session_ref
    await rt.aclose()
    rt2 = Runtime(rt.home, provider_resolver=rt.provider_resolver)
    async with served(rt2) as (port, token, _h):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            r = await c.post(f"/runs/{rid}/resume", json={"text": "more from rt2"})
            assert r.status_code == 200, r.text
        res = await asyncio.wait_for(rt2.wait(rid), 15)
        assert res.final_text == "echo: more from rt2"
        argv = _argvs(rt2, rid)[-1]
        assert argv[argv.index("--resume") + 1] == session_ref
        assert rt2.store.get_run(rid).status == "done"
    await rt2.aclose()


async def test_resume_refuses_non_claude_runs(rt):
    rid = rt.create_external_run("designer", provider="acp", model="x")
    rt.finish_external_run(rid, "done")
    with pytest.raises(MisesError) as ei:
        await rt.resume_run(rid)
    assert ei.value.code == ErrorCode.INVALID_INPUT


@needs_sandbox
async def test_aclose_closes_every_live_session(rt, claude_bin, signed_in):
    async with served(rt):
        a = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(a), 15)
        b = await rt.start_run("designer", "hang", model_ref="claude-code/haiku")
        await _wait_for(lambda: _sent(rt, b))
        for rid in (a, b):
            rt._claude[rid].session.term_grace_s = 0.5
        pids = [_pid(rt, a), _pid(rt, b)]
        await asyncio.wait_for(rt.aclose(), 20)
        for pid in pids:
            await _wait_for(lambda: _dead(pid))
        for rid in (a, b):
            assert not (rt.workdir(rid) / "claude-mcp.json").exists()
        assert rt.store.get_run(a).status == "done"
        assert rt.store.get_run(b).status == "cancelled"


@needs_sandbox
async def test_server_shutdown_closes_sessions(rt, claude_bin, signed_in):
    async with served(rt):
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        await asyncio.wait_for(rt.wait(rid), 15)
        pid = _pid(rt, rid)
    await _wait_for(lambda: _dead(pid))
    assert not (rt.workdir(rid) / "claude-mcp.json").exists()
    assert rt.mcp_endpoint is None


def test_stale_token_files_removed_on_start(rt):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    rt.store._db.execute("UPDATE runs SET owner_pid=? WHERE id=?", (2 ** 22 + 12345, rid))
    stale = rt.workdir(rid) / "claude-mcp.json"
    stale.write_text("{}")
    Runtime(rt.home, provider_resolver=rt.provider_resolver)
    assert not stale.exists()


async def test_not_installed_and_signed_out_refuse(rt, claude_bin, auth_state, monkeypatch):
    async with served(rt):
        with pytest.raises(MisesError) as ei:            # signed out (fake default)
            await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        assert ei.value.code == ErrorCode.DENIED and "signed in" in ei.value.message
        monkeypatch.setattr(detect, "find_claude", lambda: detect.ClaudeInfo(None, None))
        with pytest.raises(MisesError) as ei:
            await rt.start_run("designer", "hello", model_ref="claude-code/sonnet")
        assert ei.value.code == ErrorCode.NOT_FOUND
    assert rt.store.list_runs() == []


@pytest.mark.parametrize("ref", ["claude-code/", "claude-code/--resume", "claude-code/a b"])
async def test_bad_alias_rejected(rt, ref):
    with pytest.raises(MisesError) as ei:
        await rt.start_run("designer", "hello", model_ref=ref)
    assert ei.value.code == ErrorCode.INVALID_INPUT


@needs_sandbox
async def test_claude_code_lead_spawns_a_claude_code_child(rt, claude_bin, signed_in):
    """D3: a claude-code/<alias> lead's sub-agent inherits claude-code — it must not fall back
    to the keyless Anthropic default (which 401'd). The child is a real (fake-binary) claude run
    on the same alias."""
    async with served(rt):
        lead = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet", role="lead")
        await asyncio.wait_for(rt.wait(lead), 15)
        out = await rt.gate.call(lead, "agent_spawn",
                                 {"agent": "verifier", "prompt": "hello", "wait": False},
                                 call_id="c1", workdir=rt.workdir(lead))
        child = out.data["run_id"]
        await asyncio.wait_for(rt.wait(child), 15)
        run = rt.store.get_run(child)
        assert (run.provider, run.model, run.external) == ("claude-code", "sonnet", True)
        assert run.parent_id == lead


@needs_sandbox
async def test_profile_default_model_can_be_claude_code(rt, claude_bin, signed_in):
    from mises.profile import Profile, save_profile
    save_profile(rt.home, Profile(default_model="claude-code/haiku"))
    async with served(rt):
        rid = await rt.start_run("designer", "hello")
        await asyncio.wait_for(rt.wait(rid), 15)
    assert rt.store.get_run(rid).model == "haiku"


# ---- routes


def _client(port, token):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)


@needs_sandbox
async def test_post_runs_with_claude_code_model(rt, claude_bin, signed_in):
    async with served(rt) as (port, token, _h):
        async with _client(port, token) as c:
            r = await c.post("/runs", json={"prompt": "hello", "model_ref": "claude-code/sonnet"})
            assert r.status_code == 200, r.text
            rid = r.json()["run_id"]
            await asyncio.wait_for(rt.wait(rid), 15)
            r = await c.post(f"/runs/{rid}/message", json={"text": "more"})
            assert r.status_code == 200
            await asyncio.wait_for(rt.wait(rid), 15)
            run = (await c.get(f"/runs/{rid}")).json()
            assert run["provider"] == "claude-code" and run["status"] == "done"
            msgs = [s for s in (await c.get(f"/runs/{rid}/steps")).json()
                    if s["kind"] == StepKind.MESSAGE]
            assert [m["payload"]["text"] for m in msgs] == [
                "hello", "Hello there", "more", "echo: more"]


async def test_models_lists_claude_code(rt, claude_bin, auth_state):
    async with served(rt) as (port, token, _h):
        async with _client(port, token) as c:
            def cc(body):
                return next(v for v in body["vendors"] if v["vendor"] == "claude-code")
            entry = cc((await c.get("/models")).json())
            assert {k: entry[k] for k in ("vendor", "key", "models")} == {
                "vendor": "claude-code", "key": "signed_out",
                "models": ["sonnet", "opus", "haiku", "fable"]}
            assert [e["model"] for e in entry["entries"]] == [
                "claude-code/sonnet", "claude-code/opus", "claude-code/haiku", "claude-code/fable"]
            auth_state.write_text("signed_in")
            # /models reuses a few-seconds-old answer; /claude-code/status (what the app polls
            # while signing in) asks afresh and refreshes it
            assert cc((await c.get("/models")).json())["key"] == "signed_out"
            assert (await c.get("/claude-code/status")).json()["signed_in"] is True
            assert cc((await c.get("/models")).json())["key"] == "signed_in"


async def test_models_claude_code_not_installed(rt):
    async with served(rt) as (port, token, _h):
        async with _client(port, token) as c:
            body = (await c.get("/models")).json()
    entry = next(v for v in body["vendors"] if v["vendor"] == "claude-code")
    assert entry["key"] == "not_installed"


async def test_claude_code_status_login_logout(rt, claude_bin, auth_state, user_home):
    async with served(rt) as (port, token, _h):
        async with _client(port, token) as c:
            st = (await c.get("/claude-code/status")).json()
            assert st == {"installed": True, "version": "2.1.310", "signed_in": False,
                          "auth_method": "none"}
            r = await c.post("/claude-code/login")
            assert r.status_code == 200 and r.json()["started"] is True
            await _wait_for(lambda: auth_state.exists() and auth_state.read_text() == "signed_in")
            await _wait_for(lambda: rt._login is None or rt._login.done)
            st = (await c.get("/claude-code/status")).json()
            assert st["signed_in"] is True and st["auth_method"] == "claude.ai"
            assert "sk-ant" not in json.dumps(st)
            r = await c.post("/claude-code/logout")
            assert r.status_code == 200 and r.json()["signed_in"] is False
            assert auth_state.read_text() == "signed_out"


async def test_claude_code_status_not_installed(rt):
    async with served(rt) as (port, token, _h):
        async with _client(port, token) as c:
            assert (await c.get("/claude-code/status")).json() == {
                "installed": False, "version": None, "signed_in": False, "auth_method": None}
            r = await c.post("/claude-code/login")
            assert r.status_code == 404


@pytest.mark.parametrize("method,path", [("GET", "/claude-code/status"),
                                         ("POST", "/claude-code/login"),
                                         ("POST", "/claude-code/logout"),
                                         ("POST", "/runs/abc/resume")])
async def test_claude_routes_admin_only(rt, claude_bin, method, path, user_home):
    async with served(rt) as (port, _token, handle):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            r = await c.request(method, path, headers={"Authorization": f"Bearer {handle.mcp_token}"})
            assert r.status_code == 403
            assert (await c.request(method, path)).status_code == 401
    # nothing was started by the refused calls
    assert not (user_home / "fake-claude-login-args.json").exists()


# ---- CLI


@needs_sandbox
def test_cli_chat_with_claude_code_model(tmp_path, claude_bin, signed_in, monkeypatch, capsys):
    import mises.cli.repl as repl_mod
    import mises.runtime as runtime_mod
    from mises.cli.main import main

    monkeypatch.setenv("MISES_HOME", str(tmp_path / "clihome"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    lines = iter(["hello", "/quit"])

    def reader(_history=None):
        async def read_line(prompt: str, default: str = "") -> str:
            return next(lines)
        return read_line
    monkeypatch.setattr(repl_mod, "prompt_toolkit_reader", reader)
    assert main(["chat", "--model", "claude-code/sonnet"]) == 0
    out = capsys.readouterr().out
    assert "Hello there" in out
    rt = Runtime(tmp_path / "clihome", provider_resolver=lambda r: None)
    runs = rt.store.list_runs()
    assert len(runs) == 1 and runs[0].provider == "claude-code" and runs[0].status == "done"
    assert not (rt.home / "server.json").exists()          # embedded server, no server.json


def test_cli_claude_status_and_login(claude_bin, auth_state, user_home, capsys):
    from mises.cli.main import main
    assert main(["claude", "status"]) == 1
    out = capsys.readouterr().out
    assert "2.1.310" in out and "not signed in" in out
    assert main(["claude", "login"]) == 0
    assert auth_state.read_text() == "signed_in"
    out = capsys.readouterr().out
    assert "sk-ant" not in out
    assert main(["claude", "status"]) == 0
    assert "signed in" in capsys.readouterr().out


def test_cli_claude_status_not_installed(capsys):
    from mises.cli.main import main
    assert main(["claude", "status"]) == 1
    assert "not installed" in capsys.readouterr().out
