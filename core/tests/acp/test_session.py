import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

import mises.runtime as runtime_mod
from mises.acp_host import KNOWN_AGENTS, AcpAgentSpec, AcpSession
from mises.runtime import Runtime
from mises.types import StepKind

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_agent.py")


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))


def _spec(*extra: str, env: dict | None = None) -> AcpAgentSpec:
    return AcpAgentSpec(name="fake", command=sys.executable, args=[FAKE, *extra],
                        env=env or {}, install_hint="test fixture")


def _new(rt: Runtime, *extra: str, env: dict | None = None, **kw) -> AcpSession:
    spec = _spec(*extra, env=env)
    run_id = rt.create_external_run(spec.name, provider="acp", model=spec.name)
    return AcpSession(rt, spec, run_id, mcp_url="http://127.0.0.1:1/mcp", mcp_token="t", **kw)


async def _session(rt: Runtime, *extra: str, env: dict | None = None, **kw) -> AcpSession:
    s = _new(rt, *extra, env=env, **kw)
    await s.start()
    return s


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _log(rt: Runtime, run_id: str) -> list[dict]:
    p = rt.workdir(run_id) / "fake_agent.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()]


async def _decide_next_approval(rt: Runtime, run_id: str, approve: bool):
    sub = rt.events.subscribe(run_id)
    try:
        async for ev in sub:
            if ev.type == "approval_requested":
                a = rt.approvals.decide(ev.data["id"], ev.data["payload_hash"], approve, "tester")
                return a
    finally:
        sub.close()


async def test_updates_become_steps(rt):
    s = await _session(rt)
    try:
        text = await s.prompt("chat tool")
    finally:
        await s.close()
    assert "hello from fake" in text
    steps = rt.store.steps(s.run_id)
    kinds = [st.kind for st in steps]
    assert StepKind.MESSAGE in kinds
    msgs = [st.payload for st in steps if st.kind == StepKind.MESSAGE]
    assert any(m["role"] == "assistant" and "hello from fake" in m["text"] for m in msgs)
    calls = [st.payload for st in steps if st.kind == StepKind.TOOL_CALL]
    assert calls and calls[0]["call_id"] == "t1" and calls[0]["name"] == "Edit bracket.py"
    results = [st.payload for st in steps if st.kind == StepKind.TOOL_RESULT]
    assert results and results[0]["call_id"] == "t1"
    assert results[0]["status"] == "failed" and results[0]["ok"] is False
    diffs = [st.payload for st in steps if st.kind == StepKind.DIFF]
    assert diffs and diffs[0]["path"] == "/w/bracket.py" and diffs[0]["new_text"] == "x = 2\n"
    plans = [st.payload for st in steps if st.kind == StepKind.PLAN]
    assert plans and [e["content"] for e in plans[0]["entries"]] == ["Sketch the base",
                                                                      "Add a gusset"]
    assert rt.store.verify_chain(s.run_id)
    assert rt.store.get_run(s.run_id).status == "done"


async def test_failed_tool_call_event_is_not_ok(rt):
    s = await _session(rt)
    sub = rt.events.subscribe(s.run_id)
    try:
        await s.prompt("tool")
    finally:
        await s.close()
    seen = []
    while not sub.queue.empty():
        seen.append(sub.queue.get_nowait())
    sub.close()
    results = [e for e in seen if e.type == "tool_result"]
    assert results and results[0].data["ok"] is False and results[0].data["status"] == "failed"
    assert any(e.type == "plan" for e in seen) and any(e.type == "diff" for e in seen)


async def test_permission_becomes_approval(rt):
    s = await _session(rt)
    try:
        decider = asyncio.create_task(_decide_next_approval(rt, s.run_id, True))
        text = await asyncio.wait_for(s.prompt("perm"), 10)
        a = await decider
    finally:
        await s.close()
    assert a.action == "acp_permission"
    assert a.payload["tool_call"]["toolCallId"] == "t2"
    assert {o["optionId"] for o in a.payload["options"]} >= {"opt-once", "opt-always"}
    assert "permission:opt-once" in text
    perm = [r for r in _log(rt, s.run_id) if r["event"] == "permission"]
    assert perm[0]["outcome"] == {"outcome": "selected", "optionId": "opt-once"}
    assert rt.store.get_approval(a.id).status == "executed"


async def test_permission_reject(rt):
    s = await _session(rt)
    try:
        decider = asyncio.create_task(_decide_next_approval(rt, s.run_id, False))
        text = await asyncio.wait_for(s.prompt("perm"), 10)
        await decider
    finally:
        await s.close()
    assert "permission:opt-reject" in text
    perm = [r for r in _log(rt, s.run_id) if r["event"] == "permission"]
    assert perm[0]["outcome"] == {"outcome": "selected", "optionId": "opt-reject"}


def test_choose_option_never_allow_always():
    from acp.schema import PermissionOption

    from mises.acp_host.session import choose_option

    def opts(*kinds):
        return [PermissionOption(option_id=k, name=k, kind=k) for k in kinds]

    assert choose_option(opts("allow_always", "allow_once", "reject_once"), True) == "allow_once"
    assert choose_option(opts("allow_always", "reject_once"), True) == "reject_once"
    assert choose_option(opts("allow_always"), True) is None
    assert choose_option(opts("allow_once", "reject_always"), False) == "reject_always"
    assert choose_option(opts("allow_once", "allow_always"), False) is None


async def test_fs_write_outside_workdir_refused(rt, tmp_path):
    outside = tmp_path / "outside.txt"
    s = await _session(rt)
    inside = rt.workdir(s.run_id) / "sub" / "inside.txt"
    try:
        text = await s.prompt(f"write:{outside} write:{inside}")
    finally:
        await s.close()
    assert not outside.exists()
    writes = {r["path"]: r["result"] for r in _log(rt, s.run_id) if r["event"] == "write"}
    assert writes[str(outside)].startswith("error:")
    assert writes[str(inside)] == "ok" and inside.read_text() == "hello"
    assert "write:error" in text


async def test_fs_symlink_escape_refused(rt, tmp_path):
    s = await _session(rt)
    wd = rt.workdir(s.run_id)
    (wd / "esc").symlink_to(tmp_path, target_is_directory=True)
    try:
        await s.prompt(f"write:{wd / 'esc' / 'x.txt'} write:{wd / '..' / 'y.txt'}")
    finally:
        await s.close()
    assert not (tmp_path / "x.txt").exists()
    assert not (wd.parent / "y.txt").exists()


async def test_terminal_not_supported(rt):
    s = await _session(rt)
    try:
        await s.prompt("term")
    finally:
        await s.close()
    rec = [r for r in _log(rt, s.run_id) if r["event"] == "terminal"][0]
    assert rec["result"] == "error:-32601"


async def test_cancel_kills_hung_agent(rt):
    s = await _session(rt, "--hang", cancel_timeout_s=1)
    pending = asyncio.create_task(s.prompt("chat"))
    await asyncio.sleep(0.3)
    pid = s.pid
    t0 = time.monotonic()
    await s.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 3
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    await asyncio.wait_for(pending, 2)
    assert rt.store.get_run(s.run_id).status == "cancelled"
    assert rt.store.pending_approvals(s.run_id) == []
    await s.close()
    assert rt.store.get_run(s.run_id).status == "cancelled"


async def test_close_keeps_status_set_elsewhere(rt):
    s = await _session(rt)
    rt.cancel(s.run_id)
    await s.close()
    assert rt.store.get_run(s.run_id).status == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(s.pid, 0)


async def test_session_new_passes_mises_mcp_server(rt):
    s = await _session(rt)
    await s.close()
    rec = [r for r in _log(rt, s.run_id) if r["event"] == "session_new"][0]
    assert rec["cwd"] == str(rt.workdir(s.run_id))
    assert rec["client_capabilities"]["terminal"] is False
    (srv,) = rec["mcp_servers"]
    assert srv["name"] == "mises"
    assert srv["command"] == sys.executable
    assert srv["args"] == ["-m", "mises.server.stdio_proxy", "--run", s.run_id]
    env = {e["name"]: e["value"] for e in srv["env"]}
    assert env["MISES_MCP_URL"] == "http://127.0.0.1:1/mcp" and env["MISES_MCP_TOKEN"] == "t"


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
async def test_agent_is_sandboxed_from_mises_home(rt):
    # C2b: the agent process cannot read the admin token or the database, but uses its workdir
    (rt.home / "server.json").write_text('{"port": 1, "token": "admin"}')
    s = await _session(rt)
    try:
        text = await s.prompt(f"peek:{rt.home / 'server.json'} peek:{rt.home / 'mises.db'}")
    finally:
        await s.close()
    assert text.split() == ["peek:denied", "peek:denied"]
    assert _log(rt, s.run_id)[0]["event"] == "session_new"   # wrote its workdir log fine


async def test_missing_agent_command_is_not_found(rt):
    spec = AcpAgentSpec(name="nope", command="definitely-not-a-command-xyz", install_hint="hint")
    run_id = rt.create_external_run(spec.name, provider="acp", model=spec.name)
    s = AcpSession(rt, spec, run_id, mcp_url="http://127.0.0.1:1/mcp", mcp_token="t")
    from mises.types import ErrorCode, MisesError
    with pytest.raises(MisesError) as e:
        await s.start()
    assert e.value.code == ErrorCode.NOT_FOUND
    assert rt.store.get_run(run_id).status == "failed"


def test_known_agents():
    assert {"claude", "codex", "gemini", "opencode", "goose"} <= set(KNOWN_AGENTS)
    for name, spec in KNOWN_AGENTS.items():
        assert spec.name == name and spec.command and spec.install_hint
    assert "--acp" in KNOWN_AGENTS["gemini"].args
    assert all("@zed-industries" not in a for s in KNOWN_AGENTS.values() for a in s.args)


async def test_start_times_out_when_agent_never_answers(rt):
    from mises.types import ErrorCode, MisesError

    s = _new(rt, "--no-init", handshake_timeout_s=1)
    t0 = time.monotonic()
    with pytest.raises(MisesError) as ei:
        await s.start()
    assert time.monotonic() - t0 < 3
    assert ei.value.code == ErrorCode.TRANSIENT
    assert s.pid is not None and _dead(s.pid)
    assert rt.store.get_run(s.run_id).status == "failed"


async def test_caller_cancelling_start_kills_agent(rt):
    s = _new(rt, "--no-init", handshake_timeout_s=30)
    task = asyncio.create_task(s.start())
    for _ in range(100):
        if s.pid is not None:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert s.pid is not None and _dead(s.pid)
    assert rt.store.get_run(s.run_id).status == "cancelled"


async def test_cancel_while_permission_pending(rt):
    s = await _session(rt)
    rt.store.ledger_reserve("local", s.run_id, "GET /features", 1, "idem-1")
    sub = rt.events.subscribe(s.run_id)
    pending = asyncio.create_task(s.prompt("perm"))
    async for ev in sub:
        if ev.type == "approval_requested":
            approval_id = ev.data["id"]
            break
    sub.close()
    await s.cancel()
    text = await asyncio.wait_for(pending, 5)
    perm = [r for r in _log(rt, s.run_id) if r["event"] == "permission"]
    assert perm[0]["outcome"] == {"outcome": "cancelled"}
    assert "permission:cancelled" in text
    assert rt.store.get_approval(approval_id).status != "pending"
    assert rt.store.pending_approvals(s.run_id) == []
    assert rt.store.get_run(s.run_id).status == "cancelled"
    rows = rt.store._all("SELECT state FROM ledger WHERE run_id=?", (s.run_id,))
    assert [r["state"] for r in rows] == ["released"]
    await s.close()


async def test_permission_expiry_rejects(rt):
    s = await _session(rt, approval_ttl_s=0.3)
    try:
        text = await asyncio.wait_for(s.prompt("perm"), 10)
    finally:
        await s.close()
    perm = [r for r in _log(rt, s.run_id) if r["event"] == "permission"]
    assert perm[0]["outcome"] == {"outcome": "selected", "optionId": "opt-reject"}
    assert "permission:opt-reject" in text


async def test_agent_env_allowlist(rt, monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("TMPDIR", "/tmp/mises-test")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("no_proxy", "localhost")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("MISES_TEST_SECRET", "nope")
    s = await _session(rt, env={"FROM_SPEC": "yes"})
    await s.close()
    env = [r for r in _log(rt, s.run_id) if r["event"] == "session_new"][0]["env"]
    assert env == {"LANG": "en_US.UTF-8", "TMPDIR": "/tmp/mises-test",
                   "HTTPS_PROXY": "http://proxy:3128", "no_proxy": "localhost",
                   "SSL_CERT_FILE": "/etc/ssl/cert.pem", "FROM_SPEC": "yes"}
