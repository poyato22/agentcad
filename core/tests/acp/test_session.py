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


def _spec(*extra: str) -> AcpAgentSpec:
    return AcpAgentSpec(name="fake", command=sys.executable, args=[FAKE, *extra],
                        install_hint="test fixture")


async def _session(rt: Runtime, *extra: str, **kw) -> AcpSession:
    spec = _spec(*extra)
    run_id = rt.create_external_run(spec.name, provider="acp", model=spec.name)
    s = AcpSession(rt, spec, run_id, mcp_url="http://127.0.0.1:1/mcp", mcp_token="t", **kw)
    await s.start()
    return s


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
    assert {"name": "MISES_HOME", "value": str(rt.home)} in srv["env"]


def test_known_agents():
    assert {"claude", "codex", "gemini", "opencode", "goose"} <= set(KNOWN_AGENTS)
    for name, spec in KNOWN_AGENTS.items():
        assert spec.name == name and spec.command and spec.install_hint
    assert "--acp" in KNOWN_AGENTS["gemini"].args
    assert all("@zed-industries" not in a for s in KNOWN_AGENTS.values() for a in s.args)
