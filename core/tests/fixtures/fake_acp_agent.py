"""A scripted ACP agent for tests, built on the agent side of `agent-client-protocol`.

Run as:  python fake_acp_agent.py [--hang]

The prompt text selects behaviour (words may be combined):
  chat         stream one agent text chunk
  tool         report a tool_call (with a diff) and then a failed tool_call_update, plus a plan
  perm         request permission and echo the chosen option id
  term         ask the client for a terminal, log ok/error
  write:<p>    ask the client to write "hello" to absolute path <p>, echo ok/error
In --hang mode every prompt blocks forever, session/cancel and SIGTERM are ignored.
In --no-init mode `initialize` never answers (an agent waiting for a login, say).
Everything the agent observes is appended as JSON lines to <cwd>/fake_agent.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from acp import (
    PROTOCOL_VERSION,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    RequestError,
    run_agent,
    start_tool_call,
    text_block,
    tool_diff_content,
    update_agent_message,
    update_plan,
    update_tool_call,
    plan_entry,
)
from acp.schema import PermissionOption, ToolCallUpdate

HANG = "--hang" in sys.argv
NO_INIT = "--no-init" in sys.argv
ENV_KEYS = ("LANG", "TMPDIR", "HTTPS_PROXY", "no_proxy", "SSL_CERT_FILE", "ANTHROPIC_API_KEY",
            "MISES_TEST_SECRET", "FROM_SPEC")


def _log(where: str, **rec: Any) -> None:
    with open(Path(where) / "fake_agent.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


class FakeAgent:
    def __init__(self) -> None:
        self.conn = None
        self.cwd = "."

    def on_connect(self, conn) -> None:
        self.conn = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None,
                         **kw: Any) -> InitializeResponse:
        if NO_INIT:
            await asyncio.Event().wait()
        self._caps = client_capabilities.model_dump(mode="json", by_alias=True, exclude_none=True) if client_capabilities else None
        return InitializeResponse(protocol_version=PROTOCOL_VERSION)

    async def new_session(self, cwd: str, mcp_servers=None, **kw: Any) -> NewSessionResponse:
        self.cwd = cwd
        _log(cwd, event="session_new", cwd=cwd, client_capabilities=self._caps,
             env={k: os.environ[k] for k in ENV_KEYS if k in os.environ},
             mcp_servers=[m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in (mcp_servers or [])])
        return NewSessionResponse(session_id="sess-1")

    async def cancel(self, session_id: str, **kw: Any) -> None:
        _log(self.cwd, event="cancel")
        if not HANG:
            self._cancelled = True

    async def prompt(self, session_id: str, prompt, **kw: Any) -> PromptResponse:
        text = " ".join(getattr(b, "text", "") for b in prompt)
        _log(self.cwd, event="prompt", text=text)
        if HANG:
            await asyncio.Event().wait()
        c = self.conn
        if "chat" in text.split():
            await c.session_update(session_id, update_agent_message(text_block("hello from fake")))
        if "tool" in text.split():
            await c.session_update(session_id, start_tool_call(
                "t1", "Edit bracket.py", kind="edit", status="in_progress",
                content=[tool_diff_content("/w/bracket.py", "x = 2\n", "x = 1\n")],
                raw_input={"path": "bracket.py"}))
            await c.session_update(session_id, update_tool_call(
                "t1", status="failed", raw_output={"error": "boom"}))
            await c.session_update(session_id, update_plan([
                plan_entry("Sketch the base", status="completed"),
                plan_entry("Add a gusset", status="pending")]))
        if "perm" in text.split():
            options = [
                PermissionOption(option_id="opt-always", name="Always allow", kind="allow_always"),
                PermissionOption(option_id="opt-once", name="Allow once", kind="allow_once"),
                PermissionOption(option_id="opt-reject", name="Reject", kind="reject_once"),
                PermissionOption(option_id="opt-reject-always", name="Never", kind="reject_always"),
            ]
            resp = await c.request_permission(
                session_id=session_id, options=options,
                tool_call=ToolCallUpdate(tool_call_id="t2", title="Run build", kind="execute"))
            outcome = resp.outcome.model_dump(mode="json", by_alias=True, exclude_none=True)
            _log(self.cwd, event="permission", outcome=outcome)
            await c.session_update(session_id, update_agent_message(
                text_block(f"permission:{outcome.get('optionId', outcome['outcome'])}")))
        for word in text.split():
            if word.startswith("write:"):
                path = word[len("write:"):]
                try:
                    await c.write_text_file(session_id=session_id, path=path, content="hello")
                    result = "ok"
                except RequestError as e:
                    result = f"error:{e.code}"
                _log(self.cwd, event="write", path=path, result=result)
                await c.session_update(session_id, update_agent_message(text_block(f"write:{result}")))
        if "term" in text.split():
            try:
                await c.create_terminal(session_id=session_id, command="ls")
                result = "ok"
            except RequestError as e:
                result = f"error:{e.code}"
            _log(self.cwd, event="terminal", result=result)
        return PromptResponse(stop_reason="end_turn")


async def main() -> None:
    if HANG or NO_INIT:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    await run_agent(FakeAgent())


if __name__ == "__main__":
    asyncio.run(main())
