import json
import os
import sys
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from mises.server.app import create_app, serve, stop
from mises.types import SafetyClass as C, StepKind

from .conftest import AUTH, TOKEN


def mcp_client(app, run_id=None, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"}
    if run_id is not None:
        headers["X-Mises-Run"] = run_id
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers,
                              base_url="http://127.0.0.1")
    return http, Client(streamable_http_client("http://127.0.0.1/mcp", http_client=http))


@asynccontextmanager
async def running(rt):
    # entered inside the test task: the MCP session manager's task group must be exited
    # by the task that entered it, so this cannot be an async-generator fixture
    a = create_app(rt, TOKEN)
    async with a.router.lifespan_context(a):
        yield a


async def test_mcp_list_tools_has_annotations(rt):
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
        http, client = mcp_client(app, rid)
        async with http, client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"mirror_part_list", "mirror_part_create", "ui_note"} <= set(tools)
        by_class = {t.safety: t.name for t in rt.registry.list() if t.name in tools}
        read = tools[by_class[C.READ]].annotations
        assert read.read_only_hint is True and read.destructive_hint is False
        local = tools[by_class[C.LOCAL_MUTATE]].annotations
        assert local.read_only_hint is False and local.destructive_hint is False
        assert local.idempotent_hint is False
        irr = tools[by_class[C.IRREVERSIBLE]].annotations
        assert irr.destructive_hint is True and irr.read_only_hint is False
        schema = rt.registry.schema(rt.registry.get("mirror_part_create"))
        assert tools["mirror_part_create"].input_schema == schema["input_schema"]


async def test_mcp_list_tools_filtered_by_run_policy(rt):
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x",
                                     allowed_classes={C.READ})
        http, client = mcp_client(app, rid)
        async with http, client:
            names = {t.name for t in (await client.list_tools()).tools}
        from mises.server.mcp_server import INTERNAL_ONLY
        assert names == {t.name for t in rt.registry.list(classes={C.READ})} - INTERNAL_ONLY
        assert "mirror_part_list" in names and "mirror_part_create" not in names


async def test_mcp_call_goes_through_gate(rt):
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
        http, client = mcp_client(app, rid)
        async with http, client:
            res = await client.call_tool("mirror_part_list", {})
            assert res.is_error is False
            body = json.loads(res.content[0].text)
            assert body["ok"] is True and body["data"] == {"parts": []}
            bad = await client.call_tool("mirror_part_create", {"nonsense": 1})
            assert bad.is_error is True
            err = json.loads(bad.content[0].text)
            assert err["error"] == "invalid_input" and set(err) == {"error", "message", "hint",
                                                                    "retryable"}
        calls = rt.store.steps(rid, [StepKind.TOOL_CALL])
        assert [s.payload["name"] for s in calls] == ["mirror_part_list", "mirror_part_create"]
        assert len({s.payload["call_id"] for s in calls}) == 2
        assert rt.store.steps(rid, [StepKind.TOOL_RESULT])[0].payload["name"] == "mirror_part_list"


async def test_mcp_call_denied_by_policy_class(rt):
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x",
                                     allowed_classes={C.READ})
        http, client = mcp_client(app, rid)
        async with http, client:
            res = await client.call_tool("mirror_part_set_vars", {"part_id": "p", "vars": {}})
        assert res.is_error is True and json.loads(res.content[0].text)["error"] == "denied"


async def test_mcp_missing_run_header_rejected(rt):
    async with running(rt) as app:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://127.0.0.1") as http:
            init = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            hdrs = {**AUTH, "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"}
            r = await http.post("/mcp", json=init, headers=hdrs)
            assert r.status_code == 400 and r.json()["error"] == "invalid_input"
            r = await http.post("/mcp", json=init, headers={**hdrs, "X-Mises-Run": "nope"})
            assert r.status_code == 404 and r.json()["error"] == "not_found"
            rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
            rt.finish_external_run(rid, "done")
            r = await http.post("/mcp", json=init, headers={**hdrs, "X-Mises-Run": rid})
            assert r.status_code == 409
        http, client = mcp_client(app, None)
        with pytest.raises(Exception):
            async with http, client:
                await client.list_tools()


async def test_stdio_proxy_forwards(rt, home):
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    port, token = serve(rt)
    try:
        env = {**os.environ, "MISES_HOME": str(home)}
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "mises.server.stdio_proxy", "--run", rid],
                                       env=env)
        async with Client(params) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            res = await client.call_tool("mirror_part_list", {})
        from mises.server.mcp_server import INTERNAL_ONLY
        assert names == {t.name for t in rt.registry.list()} - INTERNAL_ONLY
        assert res.is_error is False and json.loads(res.content[0].text)["ok"] is True
        assert [s.payload["name"] for s in rt.store.steps(rid, [StepKind.TOOL_CALL])] == [
            "mirror_part_list"]
    finally:
        stop(port)


async def test_stdio_proxy_uses_env_url_and_mcp_token(rt, tmp_path):
    # C2a: an agent's proxy gets the MCP-only token via its environment, never server.json
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    port, _token = serve(rt, mcp_token="mcp-only-xyz")
    empty = tmp_path / "nohome"
    empty.mkdir()
    try:
        env = {**os.environ, "MISES_HOME": str(empty),
               "MISES_MCP_URL": f"http://127.0.0.1:{port}/mcp", "MISES_MCP_TOKEN": "mcp-only-xyz"}
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "mises.server.stdio_proxy", "--run", rid],
                                       env=env)
        async with Client(params) as client:
            res = await client.call_tool("mirror_part_list", {})
        assert res.is_error is False
        assert not (empty / "server.json").exists()
    finally:
        stop(port)


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
async def test_stdio_proxy_works_inside_agent_sandbox(rt):
    # C2b: the proxy an ACP agent spawns inherits the agent sandbox and still works
    from mises.platform import agent_sandboxed_command
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    port, _token = serve(rt, mcp_token="mcp-only-xyz")
    try:
        wd = rt.workdir(rid)
        argv = agent_sandboxed_command(
            [sys.executable, "-m", "mises.server.stdio_proxy", "--run", rid], rt.home, wd)
        env = {**os.environ, "MISES_HOME": str(rt.home),
               "MISES_MCP_URL": f"http://127.0.0.1:{port}/mcp", "MISES_MCP_TOKEN": "mcp-only-xyz"}
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=env, cwd=str(wd))
        async with Client(params) as client:
            res = await client.call_tool("mirror_part_list", {})
        assert res.is_error is False
    finally:
        stop(port)


async def test_mcp_refuses_interrupted_run(rt, home):
    # I1: after a restart, a run orphaned by the dead process can no longer call tools
    import subprocess
    from mises.runtime import Runtime
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    p = subprocess.Popen(["/usr/bin/true"])
    p.wait()
    rt.store._db.execute("UPDATE runs SET owner_pid=? WHERE id=?", (p.pid, rid))
    rt2 = Runtime(home, provider_resolver=rt.provider_resolver)
    assert rid in rt2.interrupted
    async with running(rt2) as app:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://127.0.0.1") as http:
            r = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                              "params": {}},
                                headers={**AUTH, "X-Mises-Run": rid,
                                         "Content-Type": "application/json",
                                         "Accept": "application/json, text/event-stream"})
            assert r.status_code == 409


async def test_mcp_lets_a_held_sessions_proxy_connect_but_not_call_while_queued(rt):
    """A Claude Code session this process holds connects its proxy at launch, maybe while its
    run is queued for a slot: the handshake and tools/list are served, a tool call is not."""
    from types import SimpleNamespace
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
        rt.store.update_run(rid, status="queued")
        rt._claude[rid] = SimpleNamespace(session=SimpleNamespace(alive=True))
        try:
            http, client = mcp_client(app, rid)
            async with http, client:
                names = {t.name for t in (await client.list_tools()).tools}
                assert "mirror_part_list" in names
                res = await client.call_tool("mirror_part_list", {})
            assert res.is_error is True
            err = json.loads(res.content[0].text)
            assert err["error"] == "invalid_input" and "queued" in err["message"]
            assert rt.store.steps(rid, [StepKind.TOOL_CALL]) == []
            # its session gone: refused like any run that is not running
            rt._claude[rid].session.alive = False
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                          base_url="http://127.0.0.1") as h:
                r = await h.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                               "params": {}},
                                 headers={**AUTH, "X-Mises-Run": rid,
                                          "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
                assert r.status_code == 409
        finally:
            rt._claude.pop(rid, None)


async def test_mcp_keeps_only_plan_submission_internal(rt):
    # C4: agent_spawn / agent_wait / agent_result / orchestrator_run are exposed so a Claude
    # Code run can delegate to Mises workers; only the planner's plan handshake stays internal.
    from mises.server.mcp_server import INTERNAL_ONLY
    assert INTERNAL_ONLY == {"orchestrator_submit_plan"}
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
        http, client = mcp_client(app, rid)
        async with http, client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"agent_spawn", "agent_wait", "agent_result", "orchestrator_run"} <= names
            assert "orchestrator_submit_plan" not in names
            res = await client.call_tool("orchestrator_submit_plan", {"plan": {}})
            assert res.is_error and json.loads(res.content[0].text)["error"] == "not_found"


async def test_mcp_list_equals_registry_minus_internal(rt):
    # C4: a guard that no newly registered tool is ever silently dropped from (or leaked into)
    # the MCP surface. The served list is exactly the registry minus the documented internal set.
    from mises.server.mcp_server import INTERNAL_ONLY
    async with running(rt) as app:
        rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
        http, client = mcp_client(app, rid)
        async with http, client:
            names = {t.name for t in (await client.list_tools()).tools}
    assert names == {t.name for t in rt.registry.list()} - INTERNAL_ONLY


async def test_mcp_exposes_the_assembly_control_tools(rt):
    # Plan 7 R11 / R19: a Claude Code lead brings parts in, moves and animates them over MCP
    async with running(rt) as app:
        rid = rt.create_external_run("engineer", provider="claude-code", model="sonnet")
        http, client = mcp_client(app, rid)
        async with http, client:
            names = {t.name for t in (await client.list_tools()).tools}
    assert {"assembly_add_part", "assembly_move", "assembly_animate", "assembly_check"} <= names


@pytest.mark.slow
@pytest.mark.timeout(240)
async def test_a_claude_code_runs_new_part_lands_in_the_assembly(rt):
    """Plan 9 D10 on the Claude Code path: its mirror_part_create comes over MCP into the same
    Gate, so the part is placed and both cards are announced."""
    async with running(rt) as app:
        pid = rt.active_project_id()
        rid = rt.create_external_run("designer", provider="claude-code", model="sonnet", project_id=pid)
        sub = rt.events.subscribe()
        http, client = mcp_client(app, rid)
        async with http, client:
            res = await client.call_tool("mirror_part_create", {
                "part_id": "cube_cc", "script": "VARS = {}\n\ndef build(v):\n    return Box(10, 10, 10)\n"})
            body = json.loads(res.content[0].text)
            assert body["ok"] is True, body
            assert body["data"]["placed"]["project_id"] == pid
        assert [i.part_id for i in rt.assembly_store.get(pid).instances] == ["cube_cc"]
        kinds = []
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev.type == "agent_announce":
                kinds.append(ev.data["kind"])
        assert kinds[:2] == ["part_created", "assembly_added"]
