"""AcpSession: Mises as the ACP *client*, hosting one external agent subprocess.

The agent gets the `mises` MCP server (stdio proxy bound to this run) in `session/new`, so
its CAD tool calls go through Mises's Gate. Its `session/update` stream becomes Steps and
events; `session/request_permission` becomes a Mises approval card; `fs/*` and `terminal/*` are refused
(the host is unsandboxed, so a check-then-open file API would be raceable).

The agent runs under sandbox-exec (`platform.agent_sandbox_profile`): it cannot read or write
the Mises home (server.json, mises.db, other runs, parts) except its own workdir. Its stdio MCP
proxy inherits that sandbox, so it gets the server url and the MCP-only token through its
environment (MISES_MCP_URL / MISES_MCP_TOKEN) instead of reading server.json.

The caller owns the run: it calls `runtime.create_external_run(agent=spec.name,
provider="acp", model=spec.name)` and passes the id in. `close()` / `cancel()` finish it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError, connect_to_agent, text_block
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    EnvVariable,
    FileEditToolCallContent,
    FileSystemCapabilities,
    Implementation,
    McpServerStdio,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    WriteTextFileResponse,
)
from acp.transports import default_environment

from ..events import Event
from ..platform import agent_sandboxed_command
from ..types import ErrorCode, MisesError, StepKind

log = logging.getLogger(__name__)

STDIO_LIMIT = 50 * 1024 * 1024          # same default the acp package uses for its own stdio
TERM_GRACE_S = 0.5                      # SIGTERM -> SIGKILL grace when killing the agent
CLOSE_GRACE_S = 2.0                     # stdin EOF -> SIGTERM on a normal close
_FINAL = ("completed", "failed")
# Non-secret variables agents commonly need, on top of acp's default (HOME, PATH, ...).
# Provider API keys are deliberately not inherited: pass them in AcpAgentSpec.env.
EXTRA_ENV_VARS = (
    "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
)


def agent_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = default_environment()
    for key in EXTRA_ENV_VARS:
        value = os.environ.get(key)
        if value is not None and not value.startswith("()"):
            env[key] = value
    env.update(extra or {})
    return env


def choose_option(options: list[PermissionOption], approve: bool) -> str | None:
    """Map the human's decision to one of the agent's option ids.

    Never picks `allow_always` (that would let the agent skip future cards). If the agent
    offers no `allow_once`, an approval degrades to a one-time reject; rejects prefer
    `reject_once`, then `reject_always`. None means "answer with the cancelled outcome".
    """
    by_kind: dict[str, str] = {}
    for o in options:
        by_kind.setdefault(o.kind, o.option_id)
    order = ("allow_once", "reject_once", "reject_always") if approve else (
        "reject_once", "reject_always")
    for kind in order:
        if kind in by_kind:
            return by_kind[kind]
    return None


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _content_text(content: list | None) -> str:
    out = []
    for c in content or []:
        block = getattr(c, "content", None)
        if isinstance(block, TextContentBlock):
            out.append(block.text)
    return "".join(out)


class _Client:
    """The ACP Client interface; every method delegates to the session."""

    def __init__(self, session: "AcpSession"):
        self._s = session

    async def request_permission(self, session_id: str, tool_call: ToolCallUpdate,
                                 options: list[PermissionOption], **kw: Any):
        return await self._s._on_permission(tool_call, options)

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self._s._on_update(update)

    async def read_text_file(self, session_id: str, path: str, line: int | None = None,
                             limit: int | None = None, **kw: Any) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, session_id: str, path: str, content: str,
                              **kw: Any) -> WriteTextFileResponse:
        raise RequestError.method_not_found("fs/write_text_file")

    # fs/* and terminal/* are not offered (clientCapabilities.terminal = false); refuse if called anyway
    async def create_terminal(self, *a: Any, **kw: Any):
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, *a: Any, **kw: Any):
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, *a: Any, **kw: Any):
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, *a: Any, **kw: Any):
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, *a: Any, **kw: Any):
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict) -> dict:
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict) -> None:
        return None


class AcpSession:
    def __init__(self, runtime, spec, run_id: str, *, mcp_url: str, mcp_token: str,
                 cancel_timeout_s: float = 10, handshake_timeout_s: float = 60,
                 approval_ttl_s: float = 1800):
        self.runtime = runtime
        self.spec = spec
        self.run_id = run_id
        # The MCP-only token (never the admin token): it reaches the stdio proxy through its
        # environment, not the command line.
        self.mcp_url = mcp_url
        self.mcp_token = mcp_token
        self.cancel_timeout_s = cancel_timeout_s
        self.handshake_timeout_s = handshake_timeout_s
        self.approval_ttl_s = approval_ttl_s
        self.workdir: Path = runtime.workdir(run_id)
        self.session_id: str | None = None
        self.agent_info: dict | None = None
        self.stop_reason: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._conn = None
        self._stderr = None
        self._inflight: asyncio.Task | None = None
        self._text: list[str] = []          # agent text since the last flush
        self._turn_text: list[str] = []     # all agent text of the current prompt
        self._calls: dict[str, dict] = {}   # toolCallId -> {"title", "kind", "done"}
        self._cancelling = False
        self._failed = False
        self._finished = False
        # runtime.cancel(run_id) / cancel_all must kill this agent, not just mark the run
        runtime.register_cancel_hook(run_id, self.cancel)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # ---- lifecycle
    def mcp_servers(self) -> list[McpServerStdio]:
        return [McpServerStdio(
            name="mises", command=sys.executable,
            args=["-m", "mises.server.stdio_proxy", "--run", self.run_id],
            env=[EnvVariable(name="MISES_MCP_URL", value=self.mcp_url),
                 EnvVariable(name="MISES_MCP_TOKEN", value=self.mcp_token)])]

    async def start(self) -> None:
        """Spawn the agent and run initialize + session/new within handshake_timeout_s.

        On any failure (timeout, protocol error, bad response, caller cancellation) the
        agent's process group is killed and the run is finished failed (cancelled if the
        caller cancelled), then the error is raised.
        """
        try:
            await asyncio.wait_for(self._start(), self.handshake_timeout_s)
        except BaseException as e:
            cancelled = isinstance(e, asyncio.CancelledError)
            if not cancelled:
                self._fail(f"ACP start failed: {type(e).__name__}: {e}")
            await asyncio.shield(self._shutdown(kill=True))
            self._finish("cancelled" if cancelled else "failed")
            if isinstance(e, TimeoutError):
                raise MisesError(
                    ErrorCode.TRANSIENT,
                    f"{self.spec.name} did not finish the ACP handshake in "
                    f"{self.handshake_timeout_s:g}s",
                    hint="the agent may be waiting for a login, still downloading, or not "
                         f"speaking ACP on stdout; see agent-stderr.log. {self.spec.install_hint}",
                ) from None
            if isinstance(e, (RequestError, ConnectionError)):
                raise MisesError(ErrorCode.TOOL_FAILED,
                                 f"{self.spec.name} failed to start a session: {e}",
                                 hint=self.spec.install_hint) from None
            if isinstance(e, OSError):  # spawn failed (ConnectionError handled above)
                raise MisesError(ErrorCode.NOT_FOUND, f"could not start agent {self.spec.name}: {e}",
                                 hint=self.spec.install_hint) from None
            raise

    async def _start(self) -> None:
        self._stderr = open(self.workdir / "agent-stderr.log", "ab")
        env = agent_environment(self.spec.env)
        # resolve here: under sandbox-exec a missing command would not raise at spawn time
        exe = shutil.which(self.spec.command, path=env.get("PATH"))
        if exe is None:
            raise FileNotFoundError(f"{self.spec.command!r} not found on PATH")
        argv = agent_sandboxed_command([exe, *self.spec.args], self.runtime.home, self.workdir)
        self._proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(self.workdir), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr, start_new_session=True, limit=STDIO_LIMIT)
        self._conn = connect_to_agent(_Client(self), self._proc.stdin, self._proc.stdout)
        init = await self._conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=False, write_text_file=False),
                terminal=False),
            client_info=Implementation(name="mises", title="Mises", version="0.1.0"))
        self.agent_info = _dump(init.agent_info) if init.agent_info else None
        new = await self._conn.new_session(cwd=str(self.workdir),
                                           mcp_servers=self.mcp_servers())
        self.session_id = new.session_id
        self.runtime.store.append_step(self.run_id, StepKind.DECISION, {
            "acp": "session_started", "agent": self.spec.name, "session_id": self.session_id,
            "protocol_version": init.protocol_version, "agent_info": self.agent_info})

    async def prompt(self, text: str) -> str:
        if self._conn is None or self.session_id is None or self._finished:
            raise MisesError(ErrorCode.INVALID_INPUT, "ACP session is not running",
                             hint="start() it first")
        if self._inflight is not None and not self._inflight.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "agent is still working",
                             hint="wait for it to finish or cancel it")
        self._turn_text = []
        self.runtime.store.append_step(self.run_id, StepKind.MESSAGE, {"role": "user", "text": text})
        self._inflight = asyncio.ensure_future(
            self._conn.prompt(session_id=self.session_id, prompt=[text_block(text)]))
        try:
            resp = await asyncio.shield(self._inflight)
            self.stop_reason = resp.stop_reason
            if resp.usage is not None:
                self.runtime.store.add_usage(self.run_id, input_tokens=resp.usage.input_tokens,
                                             output_tokens=resp.usage.output_tokens)
        except (RequestError, ConnectionError) as e:
            if self._cancelling:
                self.stop_reason = "cancelled"
            else:
                self._flush_text()
                self._fail(f"agent error: {e}")
                raise MisesError(ErrorCode.TOOL_FAILED, f"{self.spec.name} failed: {e}") from None
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if not (self._cancelling and self._inflight.cancelled()
                    and not (me and me.cancelling())):
                # our caller was cancelled: stop the agent's turn too
                await asyncio.shield(self.cancel())
                raise
            self.stop_reason = "cancelled"
        self._flush_text(stop_reason=self.stop_reason)
        return "".join(self._turn_text)

    async def cancel(self) -> None:
        if self._finished or self._cancelling:
            return
        self._cancelling = True
        # ACP: the client MUST answer pending permission requests with `cancelled`
        self.runtime.approvals.cancel_run(self.run_id)
        if self._conn is not None and self.session_id is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._conn.cancel(session_id=self.session_id),
                                       self.cancel_timeout_s)
        hung = False
        if self._inflight is not None and not self._inflight.done():
            done, _ = await asyncio.wait({self._inflight}, timeout=self.cancel_timeout_s)
            hung = not done
        if hung:
            self.runtime.store.append_step(self.run_id, StepKind.ERROR, {
                "code": "cancelled", "message": f"agent ignored session/cancel for "
                f"{self.cancel_timeout_s}s; killed"})
        for cid, call in self._calls.items():
            if not call["done"]:
                call["done"] = True
                self._record_result(cid, "cancelled", None, None)
        await self._shutdown(kill=hung)
        self._finish("cancelled")

    async def close(self) -> None:
        if self._finished:
            await self._shutdown(kill=True)
            return
        await self._shutdown(kill=False)
        self._finish("failed" if self._failed else "done")

    # ---- teardown
    def _kill_group(self, sig: int) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(self._proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                self._proc.send_signal(sig)

    async def _shutdown(self, *, kill: bool) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if kill:
                self._kill_group(signal.SIGTERM)
                grace = TERM_GRACE_S
            else:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
                grace = CLOSE_GRACE_S
            try:
                await asyncio.wait_for(proc.wait(), grace)
            except asyncio.TimeoutError:
                if not kill:
                    self._kill_group(signal.SIGTERM)
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
            if proc.returncode is None:
                self._kill_group(signal.SIGKILL)
                await proc.wait()
        # the agent's children (e.g. its MCP proxy) share the process group
        if proc is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
            with contextlib.suppress(BaseException):
                await self._inflight
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _finish(self, status: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.runtime.unregister_cancel_hook(self.run_id)
        try:
            if self.runtime.store.get_run(self.run_id).status != "running":
                return      # already finished elsewhere (e.g. runtime.cancel); keep that status
        except KeyError:
            return
        with contextlib.suppress(MisesError):
            self.runtime.finish_external_run(self.run_id, status)

    def _fail(self, message: str) -> None:
        self._failed = True
        self.runtime.store.append_step(self.run_id, StepKind.ERROR,
                                       {"code": str(ErrorCode.TOOL_FAILED), "message": message})

    # ---- session/update -> Steps + events
    def _publish(self, type_: str, **data: Any) -> None:
        self.runtime.events.publish(Event(type=type_, run_id=self.run_id, data=data))

    def _flush_text(self, stop_reason: str | None = None) -> None:
        if not self._text and stop_reason is None:
            return
        body: dict = {"role": "assistant", "text": "".join(self._text), "source": "acp"}
        if stop_reason is not None:
            body["stop_reason"] = stop_reason
        self._text = []
        self.runtime.store.append_step(self.run_id, StepKind.MESSAGE, body)

    def _on_update(self, u: Any) -> None:
        # synchronous on purpose: the acp package runs one task per notification, and
        # doing all work before any await keeps Steps in arrival order
        if isinstance(u, AgentMessageChunk):
            if isinstance(u.content, TextContentBlock):
                self._text.append(u.content.text)
                self._turn_text.append(u.content.text)
                self._publish("text_delta", text=u.content.text)
        elif isinstance(u, AgentThoughtChunk):
            if isinstance(u.content, TextContentBlock):
                self._publish("thought_delta", text=u.content.text)
        elif isinstance(u, ToolCallStart):
            self._flush_text()
            self._calls[u.tool_call_id] = {"title": u.title, "kind": u.kind, "done": False}
            body = {"call_id": u.tool_call_id, "name": u.title, "kind": u.kind,
                    "status": u.status or "pending", "input": u.raw_input, "source": "acp"}
            self.runtime.store.append_step(self.run_id, StepKind.TOOL_CALL, body)
            self._publish("tool_call", **body)
            self._tool_content(u.tool_call_id, u.content)
            if u.status in _FINAL:
                self._calls[u.tool_call_id]["done"] = True
                self._record_result(u.tool_call_id, u.status, u.raw_output, u.content)
        elif isinstance(u, ToolCallProgress):
            call = self._calls.setdefault(u.tool_call_id,
                                          {"title": u.title, "kind": u.kind, "done": False})
            if u.title:
                call["title"] = u.title
            self._tool_content(u.tool_call_id, u.content)
            if u.status in _FINAL and not call["done"]:
                call["done"] = True
                self._record_result(u.tool_call_id, u.status, u.raw_output, u.content)
        elif isinstance(u, AgentPlanUpdate):
            self._flush_text()
            entries = [_dump(e) for e in u.entries]
            self.runtime.store.append_step(self.run_id, StepKind.PLAN,
                                           {"entries": entries, "source": "acp"})
            self._publish("plan", entries=entries)
        elif isinstance(u, UsageUpdate):
            self._publish("usage", used=u.used, size=u.size,
                          cost=_dump(u.cost) if u.cost else None)
        # other updates (mode, commands, config, session info, user echo) are not logged

    def _tool_content(self, call_id: str, content: list | None) -> None:
        for c in content or []:
            if isinstance(c, FileEditToolCallContent):
                body = {"call_id": call_id, "path": c.path, "old_text": c.old_text,
                        "new_text": c.new_text, "source": "acp"}
                self.runtime.store.append_step(self.run_id, StepKind.DIFF, body)
                self._publish("diff", call_id=call_id, path=c.path)

    def _record_result(self, call_id: str, status: str, raw_output: Any,
                       content: list | None) -> None:
        call = self._calls.get(call_id, {})
        ok = status == "completed"
        body = {"call_id": call_id, "name": call.get("title"), "status": status, "ok": ok,
                "output": raw_output, "text": _content_text(content), "source": "acp"}
        self.runtime.store.append_step(self.run_id, StepKind.TOOL_RESULT, body)
        self._publish("tool_result", call_id=call_id, name=call.get("title"), ok=ok,
                      status=status)

    # ---- session/request_permission -> approval card
    async def _on_permission(self, tool_call: ToolCallUpdate,
                             options: list[PermissionOption]) -> RequestPermissionResponse:
        cancelled = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        if self._cancelling or self._finished:
            return cancelled
        approvals = self.runtime.approvals
        a = await approvals.request(self.run_id, "acp_permission", {
            "tool_call": _dump(tool_call), "options": [_dump(o) for o in options]},
            ttl_s=self.approval_ttl_s)
        a = await approvals.wait(a.id)
        if self._cancelling or self._finished:
            return cancelled
        approve = a.status == "approved"
        if approve:
            try:
                approvals.consume(a.id, a.payload_hash)
            except MisesError:
                approve = False
        option_id = choose_option(options, approve)
        self.runtime.store.append_step(self.run_id, StepKind.DECISION, {
            "acp": "permission", "approval_id": a.id, "status": a.status,
            "tool_call_id": tool_call.tool_call_id, "option_id": option_id})
        if option_id is None:
            return cancelled
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected",
                                                                option_id=option_id))
