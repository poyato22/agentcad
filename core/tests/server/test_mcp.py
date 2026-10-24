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
        by_class = {t.safety: t.name for t in rt.registry.list()}
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
        assert names == {t.name for t in rt.registry.list(classes={C.READ})}
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
        assert names == {t.name for t in rt.registry.list()}
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
