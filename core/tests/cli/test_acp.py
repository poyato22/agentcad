import sys
from pathlib import Path

import mises.cli.acp as acp_mod
from mises.acp_host import KNOWN_AGENTS, AcpAgentSpec
from mises.cli.acp import agent_env, run_acp
from mises.runtime import Runtime

from .conftest import Lines

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "fake_acp_agent.py")


def test_agent_env_passes_vendor_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(acp_mod, "keychain_get", lambda s, a: "from-kc" if a == "gemini" else None)
    assert agent_env(KNOWN_AGENTS["claude"]) == {"ANTHROPIC_API_KEY": "from-env"}
    assert agent_env(KNOWN_AGENTS["codex"]) == {}
    assert agent_env(KNOWN_AGENTS["gemini"]) == {"GEMINI_API_KEY": "from-kc"}
    assert agent_env(KNOWN_AGENTS["goose"]) == {}


def _spec():
    return AcpAgentSpec(name="fake", command=sys.executable, args=[FAKE], install_hint="x")


async def test_acp_prompt_streams_and_closes(console, out):
    rt = Runtime()
    status = await run_acp(rt, _spec(), "chat tool", console=console, read_line=Lines())
    text = out.getvalue()
    assert status == 0
    assert "▸" in text
    run = rt.store.list_runs(1)[0]
    assert run.agent == "fake" and run.provider == "acp" and run.status == "done"


async def test_acp_permission_uses_approval_prompt(console, out):
    rt = Runtime()
    lines = Lines("y")
    await run_acp(rt, _spec(), "perm", console=console, read_line=lines)
    assert "approve? [y/N/e(dit)]" in lines.prompts[0]
    assert "acp_permission" in out.getvalue()


async def test_acp_leaves_server_json_and_hands_agent_mcp_token(console, monkeypatch):
    # M7 + C2a: `mises acp` keeps a running `mises serve`'s server.json and gives the agent's
    # proxy the MCP-only token (not the admin token) via env
    import json
    rt = Runtime()
    info = rt.home / "server.json"
    info.write_text(json.dumps({"port": 1, "token": "serve-token", "pid": 1}))
    seen = {}
    real = acp_mod.serve_async

    async def spy(runtime, **kw):
        port, token, handle = await real(runtime, **kw)
        seen.update(kw=kw, token=token, mcp_token=handle.mcp_token, port=port)
        return port, token, handle
    monkeypatch.setattr(acp_mod, "serve_async", spy)
    assert await run_acp(rt, _spec(), "chat", console=console, read_line=Lines()) == 0
    assert seen["kw"].get("write_server_json") is False
    assert json.loads(info.read_text())["token"] == "serve-token"
    run = rt.store.list_runs(1)[0]
    rec = [json.loads(l) for l in (rt.workdir(run.id) / "fake_agent.jsonl").read_text().splitlines()
           if json.loads(l)["event"] == "session_new"][0]
    env = {e["name"]: e["value"] for e in rec["mcp_servers"][0]["env"]}
    assert env["MISES_MCP_TOKEN"] == seen["mcp_token"] != seen["token"]
    assert env["MISES_MCP_URL"] == f"http://127.0.0.1:{seen['port']}/mcp"
