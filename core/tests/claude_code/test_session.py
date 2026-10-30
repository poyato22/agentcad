import asyncio
import builtins
import json
import os
import sys
from pathlib import Path

import pytest

import mises.platform as platform_mod
from mises.claude_code import ClaudeCodeSession, find_claude
from mises.server.app import serve_async
from mises.types import MisesError, StepKind

from .conftest import LEAK

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")


def _new(rt, *, resume=None, url="http://127.0.0.1:1/mcp", token="mcp-only-t", **kw):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    return ClaudeCodeSession(rt, rid, model_alias="sonnet", info=find_claude(), mcp_url=url,
                             mcp_token=token, resume_session_id=resume, **kw)


def _log(rt, run_id) -> list[dict]:
    p = rt.workdir(run_id) / "fake_claude.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _dead(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _drain(sub) -> list:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    sub.close()
    return out


def _errors(rt, rid) -> list[dict]:
    return [s.payload for s in rt.store.steps(rid, [StepKind.ERROR])]


async def _wait_for(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


pytestmark = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")


async def test_start_passes_exactly_the_documented_flags(rt, claude_bin):
    s = _new(rt)
    await s.start()
    try:
        wd = rt.workdir(s.run_id)
        mcp = json.loads((wd / "claude-mcp.json").read_text())
        assert oct((wd / "claude-mcp.json").stat().st_mode & 0o777) == "0o600"
        await s.prompt("hello")
    finally:
        await s.close()
    argv = _log(rt, s.run_id)[0]["argv"]
    assert argv == [
        "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages", "--tools", "Agent", "--strict-mcp-config",
        "--mcp-config", str(wd / "claude-mcp.json"), "--allowedTools", "mcp__mises__*,Agent",
        "--disallowedTools", "Agent(general-purpose),Agent(Explore),Agent(Plan),Agent(claude),"
        "Agent(statusline-setup),Agent(claude-code-guide)",
        "--setting-sources=",
        "--append-system-prompt-file", str(wd / "mises-rules.md"),
        "--agents", str(wd / "claude-agents.json"), "--session-id", s.session_id,
        "--model", "sonnet", "--name", f"Mises {s.run_id}"]
    srv = mcp["mcpServers"]
    assert list(srv) == ["mises"]
    assert srv["mises"]["command"] == sys.executable
    assert srv["mises"]["args"] == ["-m", "mises.server.stdio_proxy", "--run", s.run_id]
    assert srv["mises"]["env"] == {"MISES_MCP_URL": "http://127.0.0.1:1/mcp",
                                   "MISES_MCP_TOKEN": "mcp-only-t", "MISES_HOME": str(rt.home)}
    agents = json.loads((wd / "claude-agents.json").read_text())
    from mises.agent.definitions import load_definitions
    assert set(agents) == set(load_definitions()) - {"planner"}
    assert {"designer", "verifier"} <= set(agents) and "planner" not in agents
    # explicit mises tool names, no wildcard; orchestration tools are not served over MCP
    tools = agents["designer"]["tools"]
    assert "mcp__mises__mirror_part_list" in tools and "mcp__mises__orchestrator_run" not in tools
    assert all(t.startswith("mcp__mises__") and "*" not in t for t in tools)
    assert "Mises harness rules" in (wd / "mises-rules.md").read_text()
    # the token file is gone once the session is closed
    assert not (wd / "claude-mcp.json").exists()
    env_keys = set(_log(rt, s.run_id)[0]["env_keys"])
    assert env_keys <= {"HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "LANG", "LC_ALL",
                        "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
                        "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "CLAUDE_CONFIG_DIR",
                        "MCP_TOOL_TIMEOUT",   # M-6: long studies must not time out in claude
                        "PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "OLDPWD"}


async def test_text_streams_as_message_step_and_deltas(rt, claude_bin, caplog):
    s = _new(rt)
    sub = rt.events.subscribe(s.run_id)
    await s.start()
    try:
        text = await asyncio.wait_for(s.prompt("hello"), 10)
    finally:
        await s.close()
    assert text == "Hello there"
    events = _drain(sub)
    assert [e.data["text"] for e in events if e.type == "text_delta"] == ["Hel", "lo ", "there"]
    steps = rt.store.steps(s.run_id)
    msgs = [st.payload for st in steps if st.kind == StepKind.MESSAGE]
    assert msgs[0]["role"] == "user" and msgs[0]["text"] == "hello"
    assert msgs[-1] == {"role": "assistant", "text": "Hello there", "source": "claude-code",
                        "parent_tool_use_id": None}
    init = [st.payload for st in steps if st.kind == StepKind.DECISION
            and st.payload.get("claude_code") == "init"]
    assert init and init[0]["model"] == "sonnet" and "mcp__mises__mirror_part_list" in init[0]["tools"]
    run = rt.store.get_run(s.run_id)
    assert run.status == "done" and run.input_tokens == 11 and run.output_tokens == 7
    assert run.usd == pytest.approx(0.0123)                # stream-json total_cost_usd
    finished = [e for e in events if e.type == "run_finished"]
    assert finished and finished[-1].data["status"] == "done"
    assert any("non-JSON" in r.getMessage() for r in caplog.records)
    assert rt.store.verify_chain(s.run_id)


async def test_activity_transitions_thinking_tool_writing_done(rt, claude_bin):
    # C4: the Claude Code stream drives agent_activity through thinking -> tool -> writing -> done,
    # and the model thinking arrives as agent_thinking and is stored truncated.
    s = _new(rt)
    sub = rt.events.subscribe(s.run_id)
    await s.start()
    try:
        await asyncio.wait_for(s.prompt("activity"), 10)
    finally:
        await s.close()
    events = _drain(sub)
    states = [e.data["state"] for e in events if e.type == "agent_activity"]
    # the first few transitions are the contract sequence (slot "starting" may precede them)
    assert [st for st in states if st in ("thinking", "tool", "writing", "done")] == [
        "thinking", "tool", "writing", "done"]
    tool_act = next(e for e in events if e.type == "agent_activity" and e.data["state"] == "tool")
    assert tool_act.data["detail"] == "listing parts"
    thinking = [e.data["text_delta"] for e in events if e.type == "agent_thinking"]
    assert "".join(thinking) == "let me think"
    stored = [st.payload for st in rt.store.steps(s.run_id, [StepKind.DECISION])
              if st.payload.get("agent_thinking")]
    assert stored and stored[0]["agent_thinking"] == "let me think"
    assert rt.store.get_run(s.run_id).status == "done"


async def test_unexpected_tool_in_init_fails_the_run(rt, claude_bin):
    s = _new(rt)
    (rt.workdir(s.run_id) / "fake_init.json").write_text(json.dumps(
        ["mcp__mises__mirror_part_list", "Bash", "mcp__github__create_issue"]))
    await s.start()
    pid = s.pid
    with pytest.raises(MisesError):
        await asyncio.wait_for(s.prompt("hello"), 10)
    await s.close()
    errs = _errors(rt, s.run_id)
    assert errs and "unexpected tools" in errs[0]["message"]
    assert "Bash" in errs[0]["message"] and "mcp__github__create_issue" in errs[0]["message"]
    assert rt.store.get_run(s.run_id).status == "failed"
    await _wait_for(lambda: _dead(pid))
    # the model's reply never became a message step
    assert not [st for st in rt.store.steps(s.run_id, [StepKind.MESSAGE])
                if st.payload["role"] == "assistant"]


async def test_mises_tool_call_goes_through_stdio_proxy_and_gate(rt, claude_bin):
    port, _token, handle = await serve_async(rt, write_server_json=False)
    try:
        s = _new(rt, url=handle.mcp_url, token=handle.mcp_token)
        sub = rt.events.subscribe(s.run_id)
        await s.start()
        try:
            text = await asyncio.wait_for(s.prompt("tool"), 30)
        finally:
            await s.close()
    finally:
        await handle.stop()
    assert text == "listed ok=True"
    calls = [st.payload for st in rt.store.steps(s.run_id, [StepKind.TOOL_CALL])]
    # exactly the Gate's step: the session does not duplicate tool calls as Steps
    assert [c["name"] for c in calls] == ["mirror_part_list"]
    assert calls[0]["call_id"].startswith("mcp_")
    events = _drain(sub)
    light = [e.data for e in events if e.type == "tool_call" and e.data.get("source") == "claude-code"]
    assert light == [{"call_id": "toolu_1", "name": "mcp__mises__mirror_part_list",
                      "source": "claude-code"}]
    res = [e.data for e in events if e.type == "tool_result" and e.data.get("source") == "claude-code"]
    assert res == [{"call_id": "toolu_1", "ok": True, "source": "claude-code"}]
    assert rt.store.get_run(s.run_id).status == "done"


async def test_error_result_fails_the_run(rt, claude_bin):
    s = _new(rt)
    await s.start()
    try:
        with pytest.raises(MisesError) as ei:
            await asyncio.wait_for(s.prompt("error"), 10)
    finally:
        await s.close()
    assert "Not logged in" in str(ei.value)
    assert rt.store.get_run(s.run_id).status == "failed"
    assert any("Not logged in" in e["message"] for e in _errors(rt, s.run_id))


async def test_crash_mid_run_fails_with_stderr_tail(rt, claude_bin):
    s = _new(rt)
    await s.start()
    pid = s.pid
    try:
        with pytest.raises(MisesError):
            await asyncio.wait_for(s.prompt("crash"), 10)
    finally:
        await s.close()
    run = rt.store.get_run(s.run_id)
    assert run.status == "failed"
    errs = _errors(rt, s.run_id)
    assert errs and "exited" in errs[-1]["message"]
    assert "fatal: something broke" in errs[-1]["stderr_tail"]
    assert LEAK not in json.dumps(errs) and "[redacted]" in errs[-1]["stderr_tail"]
    assert _dead(pid)


async def test_cancel_kills_hung_claude(rt, claude_bin):
    s = _new(rt, term_grace_s=0.5)
    await s.start()
    pid = s.pid
    task = asyncio.create_task(s.prompt("hang"))
    await _wait_for(lambda: any(r["event"] == "stdin" for r in _log(rt, s.run_id)))
    await asyncio.sleep(0.2)           # let it install its SIGTERM-ignoring handler
    await asyncio.wait_for(s.cancel(), 10)
    with pytest.raises(MisesError):
        await asyncio.wait_for(task, 5)
    assert rt.store.get_run(s.run_id).status == "cancelled"
    assert _dead(pid)
    await s.close()


async def test_runtime_cancel_kills_claude(rt, claude_bin):
    s = _new(rt, term_grace_s=0.5)
    await s.start()
    pid = s.pid
    task = asyncio.create_task(s.prompt("hang"))
    await _wait_for(lambda: any(r["event"] == "stdin" for r in _log(rt, s.run_id)))
    rt.cancel(s.run_id)
    await _wait_for(lambda: _dead(pid))
    with pytest.raises(MisesError):
        await asyncio.wait_for(task, 5)
    assert rt.store.get_run(s.run_id).status == "cancelled"
    await s.close()


async def test_resume_uses_resume_flag(rt, claude_bin):
    sid = "550e8400-e29b-41d4-a716-446655440000"
    s = _new(rt, resume=sid)
    await s.start()
    try:
        await s.prompt("hello")
    finally:
        await s.close()
    argv = _log(rt, s.run_id)[0]["argv"]
    assert argv[argv.index("--resume") + 1] == sid and "--session-id" not in argv
    assert s.session_id == sid


async def test_context_block_only_in_first_prompt_and_turns_continue(rt, claude_bin):
    s = _new(rt)
    await s.start()
    try:
        await s.prompt("hello", context={"selected_part_id": "mount_plate"})
        assert rt.store.get_run(s.run_id).status == "done"
        second = await s.prompt("hello again", context={"selected_part_id": "other_part"})
    finally:
        await s.close()
    assert second == "Hello there"
    sent = [r["msg"] for r in _log(rt, s.run_id) if r["event"] == "stdin"]
    assert len(sent) == 2
    assert sent[0]["type"] == "user" and sent[0]["message"]["role"] == "user"
    first, again = sent[0]["message"]["content"], sent[1]["message"]["content"]
    assert first.startswith("<mises-context>") and "mount_plate" in first
    assert first.rstrip().endswith("hello")
    assert again == "hello again" and "mises-context" not in again
    run = rt.store.get_run(s.run_id)
    assert run.status == "done" and run.input_tokens == 22


async def test_no_claude_credential_paths_touched(rt, claude_bin, auth_state, monkeypatch):
    from mises.claude_code import auth_status
    auth_state.write_text("signed_in")
    home = Path.home()
    forbidden = (str(home / ".claude"), "/tmp/fake-claude-config", ".credentials")

    def check(p):
        s = os.fspath(p) if isinstance(p, (str, os.PathLike)) else ""
        if any(f in s for f in forbidden):
            raise AssertionError(f"touched a Claude credential path: {s}")

    real_open, real_rt, real_rb = builtins.open, Path.read_text, Path.read_bytes
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: (check(p), real_open(p, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (check(self), real_rt(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda self, *a, **k: (check(self), real_rb(self, *a, **k))[1])
    st = await auth_status(find_claude())
    s = _new(rt, config_dir=st.config_dir)
    await s.start()
    try:
        await s.prompt("hello")
    finally:
        await s.close()
    assert rt.store.get_run(s.run_id).status == "done"


async def test_claude_is_sandboxed_from_mises_home(rt, claude_bin):
    (rt.home / "server.json").write_text('{"port": 1, "token": "admin"}')
    s = _new(rt)
    await s.start()
    try:
        text = await s.prompt(f"peek:{rt.home / 'server.json'} peek:{rt.home / 'mises.db'}")
    finally:
        await s.close()
    assert text.split() == ["peek:denied", "peek:denied"]


async def test_refuses_to_start_without_sandbox_exec(rt, claude_bin, monkeypatch):
    monkeypatch.setattr(platform_mod, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    s = _new(rt)
    with pytest.raises(MisesError) as ei:
        await s.start()
    assert "sandbox" in str(ei.value)
    assert s.pid is None
    assert rt.store.get_run(s.run_id).status == "failed"


async def test_not_installed_refuses(rt):
    from mises.claude_code import ClaudeInfo
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    s = ClaudeCodeSession(rt, rid, model_alias="sonnet", info=ClaudeInfo(None, None),
                          mcp_url="http://127.0.0.1:1/mcp", mcp_token="t")
    with pytest.raises(MisesError) as ei:
        await s.start()
    assert ei.value.code == "not_found"
    assert rt.store.get_run(rid).status == "failed"


async def _aborted_run(rt, init: dict | list | None, prompt: str):
    s = _new(rt)
    if init is not None:
        (rt.workdir(s.run_id) / "fake_init.json").write_text(json.dumps(init))
    await s.start()
    pid = s.pid
    with pytest.raises(MisesError):
        await asyncio.wait_for(s.prompt(prompt), 10)
    mcp_gone = not (rt.workdir(s.run_id) / "claude-mcp.json").exists()
    await s.close()
    await _wait_for(lambda: _dead(pid))
    assert rt.store.get_run(s.run_id).status == "failed"
    return s, mcp_gone


async def test_output_before_init_fails_closed(rt, claude_bin):
    s, mcp_gone = await _aborted_run(rt, {"skip": True}, "hello")
    errs = _errors(rt, s.run_id)
    assert errs and "confinement unverified" in errs[0]["message"]
    assert mcp_gone                       # the token file goes with the aborted process
    assert not [st for st in rt.store.steps(s.run_id, [StepKind.MESSAGE])
                if st.payload["role"] == "assistant"]


async def test_tool_use_before_init_fails_closed(rt, claude_bin):
    s, _ = await _aborted_run(rt, {"skip": True}, "rogue")
    assert "confinement unverified" in _errors(rt, s.run_id)[0]["message"]


async def test_mises_server_not_connected_fails(rt, claude_bin):
    s, _ = await _aborted_run(rt, {"mcp_servers": [{"name": "mises", "status": "failed"}]},
                              "hello")
    msg = _errors(rt, s.run_id)[0]["message"]
    assert "did not connect" in msg and "failed" in msg


async def test_mises_server_missing_fails(rt, claude_bin):
    s, _ = await _aborted_run(rt, {"mcp_servers": []}, "hello")
    assert "missing" in _errors(rt, s.run_id)[0]["message"]


async def test_builtin_tool_use_fails_the_run(rt, claude_bin):
    s, mcp_gone = await _aborted_run(rt, None, "rogue")
    msg = _errors(rt, s.run_id)[0]["message"]
    assert "'Bash'" in msg and "outside the Mises tools" in msg
    assert mcp_gone


async def test_builtin_subagent_type_fails_the_run(rt, claude_bin):
    s, _ = await _aborted_run(rt, None, "agent:general-purpose")
    assert "'general-purpose'" in _errors(rt, s.run_id)[0]["message"]


async def test_unknown_agent_in_init_fails_the_run(rt, claude_bin):
    s, _ = await _aborted_run(rt, {"agents": ["designer", "verifier", "my-user-agent"]}, "hello")
    assert "agent my-user-agent" in _errors(rt, s.run_id)[0]["message"]


async def test_mises_subagent_is_allowed(rt, claude_bin):
    s = _new(rt)
    await s.start()
    try:
        text = await asyncio.wait_for(s.prompt("agent:verifier"), 10)
    finally:
        await s.close()
    assert text == "delegated to verifier"
    assert rt.store.get_run(s.run_id).status == "done" and not _errors(rt, s.run_id)


async def test_mcp_file_removed_when_claude_exits_on_its_own(rt, claude_bin):
    s = _new(rt)
    await s.start()
    with pytest.raises(MisesError):
        await asyncio.wait_for(s.prompt("crash"), 10)
    assert not (rt.workdir(s.run_id) / "claude-mcp.json").exists()
    await s.close()
