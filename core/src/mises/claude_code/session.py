"""ClaudeCodeSession: the user's own `claude` binary, run headless as a Mises agent.

Mises spawns `claude -p` with stream-json in and out; the only built-in tool is Agent
(`--tools "Agent"`, for the Mises designer/verifier subagents; Claude Code's own subagent
types are denied), the only MCP server is `mises` (`--strict-mcp-config --mcp-config`, a stdio
proxy bound to this run), no user/project/local settings load (`--setting-sources ""`: no
user hooks, plugins, CLAUDE.md or permission rules), and `--allowedTools
"mcp__mises__*,Agent"`. So every action goes through the Gate (which records the tool_call
Steps and raises approval cards for irreversible actions). The process runs under
sandbox-exec (`platform.claude_sandbox_profile`): the Mises home is unreadable except the run's
workdir; Claude Code's own config dir and the network stay available. Mises never touches
Claude's credentials: sign-in is `claude auth login`'s business (see detect.py).

Stream-json mapping (code.claude.com/docs/en/headless):
- `system/init`            -> decision Step; a tool, agent or MCP server outside the allowed
                              set, or `mises` not connected, fails the run
- anything before a valid init, or a tool_use outside the allowed set, fails the run too
  (the confinement tripwire fails closed if Claude Code's output changes)
- `stream_event` deltas    -> text_delta / thought_delta events (live UI)
- `assistant`              -> message Step (text); tool_use -> lightweight tool_call event
- `user` tool_result       -> lightweight tool_result event (the Gate already has the Steps)
- `result`                 -> usage tokens, then run_finished done (failed if is_error)
- non-JSON stdout lines are ignored with a warning; stderr is kept as a bounded, redacted tail

The caller owns the run: `runtime.create_external_run(agent, provider="claude-code",
model=alias)`, then start() / prompt()... / close(). cancel() (also via runtime.cancel) stops
writing to stdin, SIGTERMs the process group, waits, then SIGKILLs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

from ..events import Event, tool_label
from ..platform import claude_sandboxed_command
from ..types import ErrorCode, MisesError, StepKind
from ..server.mcp_server import INTERNAL_ONLY
from ..orchestrator.orchestrator import DELEGATION_TOOLS
from .detect import ClaudeInfo, claude_environment, redact
from .prompts import agents_json, context_block, mises_rules_md, mises_subagent_names

log = logging.getLogger(__name__)

SOURCE = "claude-code"            # the provider name of its runs, and the model-ref prefix
# the aliases Mises offers (`--model <alias>`); `claude-code/<other alias>` is accepted too
MODEL_ALIASES = ("sonnet", "opus", "haiku", "fable")
_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,79}")
MISES_TOOL_PREFIX = "mcp__mises__"
# Built-ins a run may have: Agent (Task is its pre-2.1.63 name) for the Mises subagents, and
# EndConversation, which per the tools reference no --tools list can remove while any other
# tool remains (it only ends the session).
ALLOWED_BUILTINS = frozenset({"Agent", "Task", "EndConversation"})
AGENT_TOOLS = frozenset({"Agent", "Task"})
# Claude Code's own subagent types (sub-agents docs: Explore, Plan, general-purpose, plus
# claude, statusline-setup, claude-code-guide), denied with Agent(<type>) rules.
BUILTIN_AGENT_TYPES = ("general-purpose", "Explore", "Plan", "claude", "statusline-setup",
                       "claude-code-guide")
UNVERIFIED = "Claude Code output not recognised — confinement unverified"
STDIO_LIMIT = 50 * 1024 * 1024
STDERR_KEEP_BYTES = 8192
STDERR_TAIL_CHARS = 2048
TERM_GRACE_S = 5.0                     # SIGTERM -> SIGKILL
CLOSE_GRACE_S = 2.0                    # stdin EOF -> SIGTERM on a normal close
MCP_FILE, AGENTS_FILE, RULES_FILE = "claude-mcp.json", "claude-agents.json", "mises-rules.md"


def model_alias(model_ref: str) -> str | None:
    """`claude-code/<alias>` -> the alias; None for any other model ref. An alias that could not
    be a plain `--model` value (empty, a flag, spaces) is refused."""
    vendor, sep, alias = model_ref.partition("/")
    if vendor != SOURCE or not sep:
        return None
    if not _ALIAS_RE.fullmatch(alias):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad Claude Code model {alias!r}",
                         hint=f"use claude-code/<alias>, e.g. {', '.join(MODEL_ALIASES)}")
    return alias


def default_config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


class ClaudeCodeSession:
    def __init__(self, runtime, run_id: str, *, model_alias: str, info: ClaudeInfo,
                 mcp_url: str, mcp_token: str, resume_session_id: str | None = None,
                 config_dir: str | os.PathLike | None = None, run_label: str | None = None,
                 term_grace_s: float = TERM_GRACE_S):
        self.runtime = runtime
        self.run_id = run_id
        self.model_alias = model_alias
        self.info = info
        # the MCP-only token (never the admin token); it reaches the proxy via mcp.json's env
        self.mcp_url = mcp_url
        self.mcp_token = mcp_token
        self.resumed = resume_session_id is not None
        self.session_id: str = resume_session_id or str(uuid.uuid4())
        self.config_dir = Path(config_dir).expanduser() if config_dir else default_config_dir()
        self.run_label = run_label or f"Mises {run_id}"
        self.term_grace_s = term_grace_s
        self.workdir: Path = runtime.workdir(run_id)
        self.init_info: dict | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task] = []
        self._stderr = bytearray()
        self._turn: asyncio.Future[str] | None = None
        self._turn_text: list[str] = []
        self._first_sent = False
        self._aborted = False          # confinement check failed: ignore anything else it says
        self._init_ok = False          # a valid system/init has been seen
        self._stopping = False         # we are stopping it: its exit is not a crash
        self._cancelling = False
        self._failed = False
        self._closed = False
        self.activity = runtime.activity_tracker(run_id)
        runtime.register_cancel_hook(run_id, self.cancel)

    def _mises_subagents(self) -> frozenset[str]:
        """Mises agents this run may delegate to with the Agent tool (the ones in agents.json)."""
        return frozenset(mises_subagent_names(self.runtime.definitions))

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def alive(self) -> bool:
        """The claude process is running and this session can take another prompt."""
        p = self._proc
        return (p is not None and p.returncode is None
                and not (self._closed or self._aborted or self._cancelling or self._stopping))

    @property
    def stderr_tail(self) -> str:
        return redact(self._stderr.decode("utf-8", "replace"))[-STDERR_TAIL_CHARS:]

    # ---- files + argv
    def mcp_config(self) -> dict:
        return {"mcpServers": {"mises": {
            "type": "stdio", "command": sys.executable,
            "args": ["-m", "mises.server.stdio_proxy", "--run", self.run_id],
            "env": {"MISES_MCP_URL": self.mcp_url, "MISES_MCP_TOKEN": self.mcp_token,
                    "MISES_HOME": str(self.runtime.home)}}}}

    def _write_files(self) -> None:
        role = None
        with contextlib.suppress(Exception):
            defn = self.runtime.definitions.get(self.runtime.store.get_run(self.run_id).agent)
            role = defn.body if defn is not None else None
        (self.workdir / RULES_FILE).write_text(mises_rules_md(role))
        # subagents never get the delegating tools (they would orchestrate past the depth intent)
        deny = INTERNAL_ONLY | DELEGATION_TOOLS
        tools = [t.name for t in self.runtime.registry.list() if t.name not in deny]
        (self.workdir / AGENTS_FILE).write_text(
            json.dumps(agents_json(tools, self.runtime.definitions), indent=1))
        path = self.workdir / MCP_FILE
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.mcp_config(), f)

    def claude_args(self) -> list[str]:
        """The global-constraints invocation with the controller's rulings A (Agent tool for the
        Mises subagents only) and B (no user/project/local settings); resume swaps
        --session-id for --resume."""
        wd = self.workdir
        return [
            "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--tools", "Agent", "--strict-mcp-config",
            "--mcp-config", str(wd / MCP_FILE), "--allowedTools", "mcp__mises__*,Agent",
            "--disallowedTools", ",".join(f"Agent({t})" for t in BUILTIN_AGENT_TYPES),
            "--setting-sources=",           # the Agent SDK's form for setting_sources=[]
            "--append-system-prompt-file", str(wd / RULES_FILE),
            "--agents", str(wd / AGENTS_FILE),
            *(["--resume", self.session_id] if self.resumed else ["--session-id", self.session_id]),
            "--model", self.model_alias, "--name", self.run_label]

    # ---- lifecycle
    async def start(self) -> None:
        if self._proc is not None:
            raise MisesError(ErrorCode.INVALID_INPUT, "Claude Code session already started")
        try:
            await self._start()
        except BaseException as e:
            cancelled = isinstance(e, asyncio.CancelledError)
            if not cancelled:
                self._error(f"Claude Code failed to start: {e}")
            await asyncio.shield(self._shutdown(kill=True))
            self._finish("cancelled" if cancelled else "failed")
            if isinstance(e, (MisesError, asyncio.CancelledError)):
                raise
            if isinstance(e, OSError):
                raise MisesError(ErrorCode.NOT_FOUND, f"could not start Claude Code: {e}",
                                 hint="reinstall Claude Code, then retry") from None
            raise MisesError(ErrorCode.TOOL_FAILED, f"could not start Claude Code: {e}") from None

    async def _start(self) -> None:
        if not self.info.path:
            raise MisesError(ErrorCode.NOT_FOUND, "Claude Code is not installed",
                             hint="install it from code.claude.com, then sign in with "
                                  "`claude auth login`")
        binary_dir = Path(os.path.realpath(self.info.path)).parent
        try:
            argv = claude_sandboxed_command([self.info.path, *self.claude_args()],
                                            self.runtime.home, self.workdir, self.config_dir,
                                            binary_dir=binary_dir)
        except RuntimeError as e:
            raise MisesError(ErrorCode.DENIED, f"refusing to run Claude Code unsandboxed: {e}",
                             hint="sandbox-exec ships with macOS; check /usr/bin/sandbox-exec"
                             ) from None
        except ValueError as e:
            raise MisesError(ErrorCode.DENIED, f"cannot sandbox Claude Code: {e}") from None
        self._write_files()
        self._proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(self.workdir), env=claude_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=STDIO_LIMIT)
        self._readers = [asyncio.create_task(self._read_stdout()),
                         asyncio.create_task(self._read_stderr())]
        self.runtime.store.append_step(self.run_id, StepKind.DECISION, {
            "claude_code": "session_started", "session_id": self.session_id,
            "resumed": self.resumed, "model": self.model_alias, "version": self.info.version})

    async def prompt(self, text: str, *, context: dict | None = None,
                     images: list[tuple[str, str]] | None = None) -> str:
        """Send one user message; returns the turn's assistant text when `result` arrives.
        `context` (the Mises UI context) is prepended as a data block to the first prompt only.
        `images` ((media_type, base64) pairs, e.g. a sketch photo) follow the text as image
        content blocks."""
        proc = self._proc
        if proc is None or proc.returncode is not None or self._closed or self._aborted:
            raise MisesError(ErrorCode.INVALID_INPUT, "Claude Code session is not running",
                             hint="start a new session")
        if self._turn is not None and not self._turn.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "Claude Code is still working",
                             hint="wait for it to finish or cancel it")
        self._reopen()
        content = text
        body: dict = {"role": "user", "text": text}
        if not self._first_sent and context is not None:
            content = context_block(context) + "\n" + text
            body["context"] = context
        self._first_sent = True
        message_content: str | list[dict] = content
        if images:
            body["images"] = len(images)
            message_content = [{"type": "text", "text": content}] + [
                {"type": "image", "source": {"type": "base64", "media_type": m, "data": d}}
                for m, d in images]
        self.runtime.store.append_step(self.run_id, StepKind.MESSAGE, body)
        self._turn = asyncio.get_running_loop().create_future()
        self._turn_text = []
        line = json.dumps({"type": "user", "message": {"role": "user", "content": message_content},
                           "parent_tool_use_id": None}) + "\n"
        try:
            proc.stdin.write(line.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass        # it died; the stdout reader reports the exit and fails the turn
        try:
            return await asyncio.shield(self._turn)
        except asyncio.CancelledError:
            if not self._turn.done():       # our caller was cancelled: stop Claude too
                await asyncio.shield(self.cancel())
            raise

    async def cancel(self) -> None:
        if self._cancelling or self._closed:
            return
        self._cancelling = True
        self.runtime.approvals.cancel_run(self.run_id)
        await self._shutdown(kill=True)
        self._fail_turn(MisesError(ErrorCode.CANCELLED, "run cancelled"))
        self._finish("cancelled")

    async def close(self) -> None:
        if self._closed:
            return
        mid_turn = self._turn is not None and not self._turn.done()
        await self._shutdown(kill=self._aborted or self._cancelling or mid_turn)
        self._fail_turn(MisesError(ErrorCode.CANCELLED, "session closed"))
        self._finish("failed" if self._failed else "cancelled" if mid_turn else "done")
        self._closed = True
        self.runtime.unregister_cancel_hook(self.run_id)

    # ---- run status
    def _status(self) -> str | None:
        try:
            return self.runtime.store.get_run(self.run_id).status
        except KeyError:
            return None

    def _finish(self, status: str) -> None:
        if self._status() != "running":
            return          # finished already (a turn's result, or runtime.cancel)
        with contextlib.suppress(MisesError):
            self.runtime.finish_external_run(self.run_id, status)

    def _reopen(self) -> None:
        """A new turn on a run whose last turn finished: mark it running again so the MCP
        endpoint serves its tool calls and runtime.cancel reaches this session."""
        if self._status() == "running":
            return
        self._failed = False
        self.runtime.reopen_external_run(self.run_id, cancel_hook=self.cancel, provider=SOURCE,
                                         model=self.model_alias)

    def _error(self, message: str, **extra: Any) -> None:
        self._failed = True
        self.runtime.store.append_step(self.run_id, StepKind.ERROR, {
            "code": str(ErrorCode.TOOL_FAILED), "message": message, "source": SOURCE, **extra})

    def _fail_turn(self, err: MisesError) -> None:
        if self._turn is not None and not self._turn.done():
            self._turn.set_exception(err)
            self._turn.exception()      # retrieved: no "never retrieved" warning if unawaited

    # ---- teardown
    def _kill_group(self, sig: int) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(sig)

    async def _shutdown(self, *, kill: bool) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if not kill:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), CLOSE_GRACE_S)
            if proc.returncode is None:
                self._kill_group(signal.SIGTERM)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), self.term_grace_s)
            if proc.returncode is None:
                self._kill_group(signal.SIGKILL)
                await proc.wait()
        if proc is not None:
            # its children (the MCP proxy) share the process group
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        for t in self._readers:
            if not t.done():
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(t, 2)
        (self.workdir / MCP_FILE).unlink(missing_ok=True)

    # ---- stream-json -> Steps + events
    def _publish(self, type_: str, **data: Any) -> None:
        self.runtime.events.publish(Event(type=type_, run_id=self.run_id, data=data))

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while chunk := await self._proc.stderr.read(4096):
            self._stderr += chunk
            del self._stderr[:-STDERR_KEEP_BYTES]

    async def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except ValueError:           # a line over STDIO_LIMIT
                    log.warning("claude-code %s: dropped an oversized stdout line", self.run_id)
                    continue
                if not line:
                    break
                if self._aborted:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.warning("claude-code %s: ignoring a non-JSON stdout line (%d bytes)",
                                self.run_id, len(line))
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    self._on_message(msg)
                except Exception:
                    log.exception("claude-code %s: could not handle a %r message", self.run_id,
                                  msg.get("type"))
            await proc.wait()
            if self._readers[1:]:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(self._readers[1]), 2)
        finally:
            self._on_exit()

    def _on_exit(self) -> None:
        proc = self._proc
        rc = proc.returncode if proc else None
        with contextlib.suppress(ProcessLookupError, PermissionError, TypeError):
            os.killpg(proc.pid, signal.SIGKILL)
        (self.workdir / MCP_FILE).unlink(missing_ok=True)
        if self._stopping or self._aborted:
            return
        turn_open = self._turn is not None and not self._turn.done()
        msg = (f"Claude Code exited (code {rc}) before finishing the turn" if turn_open
               else f"Claude Code exited (code {rc})")
        self._error(msg, stderr_tail=self.stderr_tail)
        self._fail_turn(MisesError(ErrorCode.TOOL_FAILED, msg,
                                   hint="see the run's error step for Claude Code's stderr"))
        self._finish("failed")

    def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if not self._init_ok and kind in ("assistant", "stream_event", "user"):
            self._abort(f"{UNVERIFIED}: a {kind!r} message arrived before system/init")
            return
        if kind == "system":
            self._on_system(msg)
        elif kind == "stream_event":
            self._on_partial(msg)
        elif kind == "assistant":
            self._on_assistant(msg)
        elif kind == "user":
            self._on_user(msg)
        elif kind == "result":
            self._on_result(msg)

    def _on_system(self, msg: dict) -> None:
        sub = msg.get("subtype")
        if sub == "init":
            tools = [t for t in msg.get("tools") or [] if isinstance(t, str)]
            servers = [s.get("name") for s in msg.get("mcp_servers") or [] if isinstance(s, dict)]
            if isinstance(msg.get("session_id"), str):
                self.session_id = msg["session_id"]
            self.init_info = {"model": msg.get("model"), "tools": tools, "mcp_servers": servers,
                              "session_id": self.session_id,
                              "version": msg.get("claude_code_version")}
            self.runtime.store.append_step(self.run_id, StepKind.DECISION,
                                           {"claude_code": "init", **self.init_info})
            bad = [t for t in tools
                   if not t.startswith(MISES_TOOL_PREFIX) and t not in ALLOWED_BUILTINS]
            bad += [f"mcp server {s}" for s in servers if s != "mises"]
            agents = msg.get("agents")
            if isinstance(agents, list):
                allowed_agents = self._mises_subagents() | frozenset(BUILTIN_AGENT_TYPES)
                bad += [f"agent {a}" for a in agents if a not in allowed_agents]
            if bad:
                self._abort(f"unexpected tools in Claude Code session: {', '.join(map(str, bad))}"
                            "; only mcp__mises__* tools and the Mises agents are allowed")
                return
            mises = [s for s in msg.get("mcp_servers") or []
                     if isinstance(s, dict) and s.get("name") == "mises"]
            status = mises[0].get("status") if mises else "missing"
            if status != "connected":
                self._abort(f"the Mises tools did not connect to Claude Code (mises server: "
                            f"{status}); the run cannot act")
                return
            self._init_ok = True
        elif sub == "api_retry":
            self._publish("retry", source=SOURCE, attempt=msg.get("attempt"),
                          error=msg.get("error"), error_status=msg.get("error_status"))

    def _tool_allowed(self, name: Any, tool_input: Any) -> str | None:
        """None if a tool_use is within the confinement, else what it tried to use."""
        if not isinstance(name, str):
            return "a tool without a name"
        if name.startswith(MISES_TOOL_PREFIX) or name == "EndConversation":
            return None
        if name in AGENT_TOOLS:
            kind = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
            if kind in self._mises_subagents():
                return None
            return f"the Agent tool with subagent type {kind!r}"
        return f"the tool {name!r}"

    def _abort(self, message: str) -> None:
        self._aborted = True
        self._error(message)
        self._kill_group(signal.SIGKILL)
        (self.workdir / MCP_FILE).unlink(missing_ok=True)
        self._fail_turn(MisesError(ErrorCode.DENIED, message,
                                   hint="check the user's Claude Code settings and plugins"))
        self._finish("failed")

    def _on_partial(self, msg: dict) -> None:
        ev = msg.get("event")
        if not isinstance(ev, dict):
            return
        if ev.get("type") == "content_block_start":
            block = ev.get("content_block") or {}
            if isinstance(block, dict) and block.get("type") == "tool_use":
                # its input streams later; only the name can be checked here
                name = block.get("name")
                if not (isinstance(name, str) and (name.startswith(MISES_TOOL_PREFIX)
                                                   or name in ALLOWED_BUILTINS)):
                    self._abort(f"Claude Code tried to use {self._tool_allowed(name, None)}, "
                                "outside the Mises tools")
            return
        if ev.get("type") != "content_block_delta":
            return
        delta = ev.get("delta") or {}
        parent = msg.get("parent_tool_use_id")
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            self._publish("text_delta", text=delta["text"], source=SOURCE,
                          **({"parent_tool_use_id": parent} if parent else {}))
            if parent is None:
                self.activity.set("writing")
        elif delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
            self._publish("thought_delta", text=delta["thinking"], source=SOURCE)
            if parent is None:
                self.activity.thinking(delta["thinking"])

    def _on_assistant(self, msg: dict) -> None:
        message = msg.get("message") or {}
        parent = msg.get("parent_tool_use_id")
        texts = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") == "tool_use":
                why = self._tool_allowed(block.get("name"), block.get("input"))
                if why is not None:
                    self._abort(f"Claude Code tried to use {why}, outside the Mises tools")
                    return
                # the Gate records mises tool calls as Steps; this is only for the live UI
                self._publish("tool_call", call_id=block.get("id"), name=block.get("name"),
                              source=SOURCE)
                if parent is None:
                    self.activity.set("tool", tool_label(str(block.get("name") or "")))
        if texts:
            text = "".join(texts)
            if parent is None:
                self.activity.set("writing")
                self._turn_text.append(text)
            self.runtime.store.append_step(self.run_id, StepKind.MESSAGE, {
                "role": "assistant", "text": text, "source": SOURCE,
                "parent_tool_use_id": parent})

    def _on_user(self, msg: dict) -> None:
        content = (msg.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self._publish("tool_result", call_id=block.get("tool_use_id"),
                              ok=not block.get("is_error", False), source=SOURCE)

    def _on_result(self, msg: dict) -> None:
        if not self._init_ok and not msg.get("is_error"):
            self._abort(f"{UNVERIFIED}: a result arrived before system/init")
            return
        usage = msg.get("usage") or {}
        tokens_in = (_int(usage.get("input_tokens")) + _int(usage.get("cache_read_input_tokens"))
                     + _int(usage.get("cache_creation_input_tokens")))
        self.runtime.store.add_usage(self.run_id, input_tokens=tokens_in,
                                     output_tokens=_int(usage.get("output_tokens")))
        if isinstance(msg.get("session_id"), str):
            self.session_id = msg["session_id"]
        if msg.get("is_error"):
            detail = msg.get("result") if isinstance(msg.get("result"), str) else None
            message = f"Claude Code: {detail or msg.get('subtype') or 'error'}"
            self._error(message, subtype=msg.get("subtype"))
            self._finish("failed")
            self._fail_turn(MisesError(ErrorCode.TOOL_FAILED, message))
            return
        self.activity.set("done", tokens_out=_int(usage.get("output_tokens")))
        self._finish("done")
        if self._turn is not None and not self._turn.done():
            self._turn.set_result("".join(self._turn_text))
