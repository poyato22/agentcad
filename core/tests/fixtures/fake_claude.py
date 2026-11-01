"""A fake `claude` executable for tests: --version, auth status/login, and a -p stream-json session.

Run through a `claude` shim on PATH. Never touches the network or any real Claude config.

- `--version`                -> "2.1.310 (Claude Code)"
- `auth status`              -> JSON per $HOME/fake-claude-state (signed_in | signed_out |
                                malformed; missing = signed_out); logs its env keys to
                                $HOME/fake-claude-status-env.json
- `auth login [--console]`   -> waits a moment, prints a token-looking string on stdout and
                                stderr (Mises must never keep it), writes signed_in to the
                                state, its argv to $HOME/fake-claude-login-args.json
- `auth logout`              -> writes signed_out to the state
  (state lives under $HOME because Mises runs claude with an allowlisted environment)
- `-p ...` session           -> logs argv to <cwd>/fake_claude.jsonl, then reads stream-json
                                user messages from stdin. The prompt text picks the behaviour:
    hello         partial text deltas, an assistant message and a success result
    tool          calls mises' mirror_part_list through the --mcp-config stdio server (the real
                  stdio proxy), reports tool_use/tool_result, then answers
    error         a result with is_error true (e.g. signed out mid-run)
    crash         prints to stderr (with a token-looking string) and exits 3 mid-turn
    hang          ignores SIGTERM and never answers
    peek:<path>   tries to read <path>; answers "peek:denied" or "peek:read"
    rogue         an assistant tool_use of the built-in Bash tool
    agent:<type>  an assistant tool_use of the Agent tool with that subagent_type
    bare:<name>   a tool_use of <name> it does not have (a Mises tool's bare name, an invented
                  one): answered with its own "No such tool available" error, then text
    sneaky:<name> the same, but the tool_result comes back without an error
    recap?        answers with the text it was sent (a recap block shows in it)
    slowtool:<s>  a tool_use, <s> seconds of silence (a long job), its tool_result, an answer
    flaky:<mode>:<n>  fails the first <n> times (counted in <cwd>/fake_flaky_count across
                  processes), then answers "recovered after <n>" with the text it was sent.
                  <mode>: exit (exits 3 mid-turn), hang (alive and silent: G7), during (an
                  error_during_execution result),
                  net (an is_error result "API Error: Connection error (ECONNRESET)"), mcp (the
                  init reports the mises server failed), approve=<tool> (calls the Mises
                  tool <tool>, which waits on an approval card, then exits 3 while it waits)
  `--resume <id>` with <cwd>/fake_gone_sessions listing <id>: prints Claude Code's
  "No conversation found with session ID: <id>" to stderr and exits 1 at once
  stream-json control requests (Plan 11 F8): apply_flag_settings {effortLevel} sets the
  effort (answered with an error when <cwd>/fake_effort_error exists), get_settings answers
  {applied: {effort}} (from --effort until a change); <cwd>/fake_effort_silent: no answer.
  `--help` lists --effort unless $HOME/fake-claude-no-effort exists; with
  $HOME/fake-claude-reject-effort a -p run started with --effort exits 1 ("unknown option"); each is logged as a 'control' line,
  and every turn's log line carries the effort it ran at.
  <cwd>/fake_init.json, if present, overrides system/init: a list (the tools) or an object
  with any of "tools", "mcp_servers", "agents", and "skip": true (emit no init at all).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

LEAK = "sk-ant-oat01-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop0123456789"


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def home_file(name: str) -> Path | None:
    h = os.environ.get("HOME")
    return Path(h) / name if h else None


def state_file() -> Path | None:
    return home_file("fake-claude-state")


def auth_status() -> int:
    envlog = home_file("fake-claude-status-env.json")
    if envlog:
        envlog.write_text(json.dumps(sorted(os.environ)))
    sf = state_file()
    state = sf.read_text().strip() if sf and sf.exists() else "signed_out"
    if state == "malformed":
        print("{not json")
        return 0
    if state == "signed_in":
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                          "email": "user@example.com", "orgName": "Example Org",
                          "configDirectory": "/tmp/fake-claude-config",
                          "subscriptionType": "max"}, indent=2))
        return 0
    print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}))
    return 1


def auth_login(argv: list[str]) -> int:
    time.sleep(0.3)
    print(f"Login successful. token={LEAK}", flush=True)
    print(f"debug: refreshed {LEAK}", file=sys.stderr, flush=True)
    sf = state_file()
    if sf:
        sf.write_text("signed_in")
    args = home_file("fake-claude-login-args.json")
    if args:
        args.write_text(json.dumps(argv))
    return 0


async def call_mises(mcp_config: str, tool: str = "mirror_part_list") -> tuple[bool, str]:
    from mcp import Client, StdioServerParameters

    cfg = json.loads(Path(mcp_config).read_text())["mcpServers"]["mises"]
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"],
                                   env={**os.environ, **cfg.get("env", {})})
    async with Client(params) as client:
        res = await client.call_tool(tool, {})
    return (not res.is_error), res.content[0].text if res.content else ""


class Session:
    def __init__(self, argv: list[str]):
        self.argv = argv
        self.session_id = flag(argv, "--session-id") or flag(argv, "--resume") or "none"
        self.model = flag(argv, "--model") or "default"
        self.effort = flag(argv, "--effort")
        self.inited = False
        self.cwd = Path.cwd()
        with open(self.cwd / "fake_claude.jsonl", "a") as f:
            f.write(json.dumps({"event": "argv", "argv": argv,
                                "env_keys": sorted(os.environ)}) + "\n")

    def init(self) -> None:
        if self.inited:
            return
        self.inited = True
        cfg = {"tools": ["Agent", "mcp__mises__mirror_part_list", "mcp__mises__mirror_measure",
                         "EndConversation"],
               "mcp_servers": [{"name": "mises", "status": "connected"}],
               "agents": ["designer", "verifier", "general-purpose", "Explore", "Plan"]}
        custom = self.cwd / "fake_init.json"
        if custom.exists():
            c = json.loads(custom.read_text())
            cfg.update({"tools": c} if isinstance(c, list) else c)
        if cfg.get("skip"):
            return
        emit({"type": "system", "subtype": "init", "session_id": self.session_id,
              "model": self.model, "tools": cfg["tools"], "cwd": str(self.cwd),
              "mcp_servers": cfg["mcp_servers"], "agents": cfg["agents"],
              "claude_code_version": "2.1.310", "permissionMode": "default",
              "uuid": "u-init"})

    def partial(self, text: str) -> None:
        emit({"type": "stream_event", "session_id": self.session_id,
              "parent_tool_use_id": None,
              "event": {"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": text}}})

    def thinking(self, text: str) -> None:
        emit({"type": "stream_event", "session_id": self.session_id,
              "parent_tool_use_id": None,
              "event": {"type": "content_block_delta", "index": 0,
                        "delta": {"type": "thinking_delta", "thinking": text}}})

    def assistant(self, content: list[dict]) -> None:
        emit({"type": "assistant", "session_id": self.session_id, "parent_tool_use_id": None,
              "message": {"id": "msg_1", "type": "message", "role": "assistant",
                          "model": self.model, "content": content,
                          "usage": {"input_tokens": 1, "output_tokens": 1}}})

    def result(self, text: str, *, is_error: bool = False) -> None:
        emit({"type": "result", "subtype": "success", "is_error": is_error,
              "duration_ms": 5, "num_turns": 1, "result": text,
              "session_id": self.session_id, "total_cost_usd": 0.0123,
              "usage": {"input_tokens": 11, "output_tokens": 7,
                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})

    async def flaky(self, cmd: str, sent: str) -> None:
        _, mode, n = cmd.split(":")
        f = self.cwd / "fake_flaky_count"
        count = int(f.read_text()) if f.exists() else 0
        if count < int(n):
            f.write_text(str(count + 1))
            if mode == "mcp":
                custom = self.cwd / "fake_init.json"
                custom.write_text(json.dumps({"mcp_servers": [{"name": "mises",
                                                               "status": "failed"}]}))
                self.init()
                custom.unlink()
                await asyncio.sleep(3600)          # Mises kills it
            self.init()
            if mode.startswith("approve="):
                tool = mode.split("=", 1)[1]
                self.assistant([{"type": "tool_use", "id": "toolu_ap",
                                 "name": f"mcp__mises__{tool}", "input": {}}])
                asyncio.ensure_future(call_mises(flag(self.argv, "--mcp-config"), tool))
                await asyncio.sleep(1.5)            # the card is up; then the process dies
                os._exit(3)
            if mode == "exit":
                self.partial("working ")
                os._exit(3)
            if mode == "hang":                      # G7: alive, silent, no tool in flight
                self.partial("working ")
                await asyncio.sleep(3600)           # the watchdog ends it
                return
            if mode == "during":
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "session_id": self.session_id, "total_cost_usd": 0.0, "usage": {}})
                return
            self.result("API Error: Connection error (ECONNRESET)", is_error=True)
            return
        self.init()
        self.assistant([{"type": "text", "text": f"recovered after {n}\n{sent}"}])
        self.result(f"recovered after {n}")
    def control(self, msg: dict) -> None:
        req = msg.get("request") or {}
        rid = msg.get("request_id")
        if (self.cwd / "fake_effort_silent").exists():
            return                                   # never answers (a timeout)
        if req.get("subtype") == "apply_flag_settings":
            if (self.cwd / "fake_effort_error").exists():
                emit({"type": "control_response", "response": {
                    "subtype": "error", "request_id": rid, "error": "effort is locked"}})
                return
            self.effort = (req.get("settings") or {}).get("effortLevel", self.effort)
            emit({"type": "control_response", "response": {"subtype": "success",
                                                           "request_id": rid}})
        elif req.get("subtype") == "get_settings":
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": {
                    "effective": {}, "applied": {"model": self.model, "effort": self.effort}}}})
        else:
            emit({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "unsupported"}})

    async def turn(self, text: str) -> None:
        self.init()
        with open(self.cwd / "fake_claude.jsonl", "a") as f:
            f.write(json.dumps({"event": "turn", "effort": self.effort}) + "\n")
        if text.startswith("hello"):
            for chunk in ("Hel", "lo ", "there"):
                self.partial(chunk)
            self.assistant([{"type": "text", "text": "Hello there"}])
            self.result("Hello there")
        elif text.startswith("tool"):
            self.assistant([{"type": "tool_use", "id": "toolu_1",
                             "name": "mcp__mises__mirror_part_list", "input": {}}])
            ok, out = await call_mises(flag(self.argv, "--mcp-config"))
            emit({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": not ok,
                       "content": out}]}})
            self.assistant([{"type": "text", "text": f"listed ok={ok}"}])
            self.result(f"listed ok={ok}")
        elif text.startswith("activity"):
            # thinking -> tool -> writing -> done, without needing a live mises server
            self.thinking("let me ")
            self.thinking("think")
            self.assistant([{"type": "tool_use", "id": "toolu_1",
                             "name": "mcp__mises__mirror_part_list", "input": {}}])
            emit({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": False,
                       "content": "[]"}]}})
            self.partial("writ")
            self.partial("ing")
            self.assistant([{"type": "text", "text": "done"}])
            self.result("done")
        elif text.startswith("slowtool:"):
            # G7: a long tool call (a sim, a render): silent on the stream for <s> seconds
            secs = float(text.split(":", 1)[1])
            self.assistant([{"type": "tool_use", "id": "toolu_slow",
                             "name": "mcp__mises__mirror_part_list", "input": {}}])
            await asyncio.sleep(secs)
            emit({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": "toolu_slow", "is_error": False,
                       "content": "done"}]}})
            self.assistant([{"type": "text", "text": "slow done"}])
            self.result("slow done")
        elif text.startswith("error"):
            self.result("Not logged in · Please run /login", is_error=True)
        elif text.startswith("crash"):
            self.partial("about to ")
            print(f"fatal: something broke {LEAK}", file=sys.stderr, flush=True)
            os._exit(3)
        elif text.startswith("hang"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            await asyncio.sleep(3600)
        elif text.startswith("rogue"):
            self.assistant([{"type": "tool_use", "id": "toolu_9", "name": "Bash",
                             "input": {"command": "cat ~/.ssh/id_rsa"}}])
            self.result("ran bash")
        elif text.startswith(("bare:", "sneaky:")):
            name = text.split(":", 1)[1].strip()
            emit({"type": "stream_event", "session_id": self.session_id,
                  "parent_tool_use_id": None,
                  "event": {"type": "content_block_start", "index": 0,
                            "content_block": {"type": "tool_use", "id": "toolu_b", "name": name,
                                              "input": {}}}})
            self.assistant([{"type": "tool_use", "id": "toolu_b", "name": name, "input": {}}])
            sneaky = text.startswith("sneaky:")
            emit({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None,
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": "toolu_b", "is_error": not sneaky,
                       "content": "ran" if sneaky else
                       f"<tool_use_error>Error: No such tool available: {name}</tool_use_error>"}]}})
            self.assistant([{"type": "text", "text": f"recovered from {name}"}])
            self.result(f"recovered from {name}")
        elif text.startswith("agent:"):
            kind = text.split(":", 1)[1].strip()
            self.assistant([{"type": "tool_use", "id": "toolu_a", "name": "Agent",
                             "input": {"subagent_type": kind, "description": "check",
                                       "prompt": "measure the part"}}])
            self.assistant([{"type": "text", "text": f"delegated to {kind}"}])
            self.result(f"delegated to {kind}")
        elif text.startswith("peek:"):
            outs = []
            for word in text.split():
                path = word.split(":", 1)[1]
                try:
                    Path(path).read_bytes()
                    outs.append("peek:read")
                except OSError:
                    outs.append("peek:denied")
            self.assistant([{"type": "text", "text": " ".join(outs)}])
            self.result(" ".join(outs))
        else:
            self.assistant([{"type": "text", "text": f"echo: {text}"}])
            self.result(f"echo: {text}")


async def session(argv: list[str]) -> int:
    s = Session(argv)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=10 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    print("this line is not JSON", flush=True)
    while True:
        line = await reader.readline()
        if not line:
            return 0
        msg = json.loads(line)
        kind = "control" if msg.get("type") == "control_request" else "stdin"
        with open(s.cwd / "fake_claude.jsonl", "a") as f:
            f.write(json.dumps({"event": kind, "msg": msg}) + "\n")
        if kind == "control":
            s.control(msg)
            continue
        content = msg["message"]["content"]
        text = content if isinstance(content, str) else "".join(
            b.get("text", "") for b in content if b.get("type") == "text")
        # the first prompt may carry a context block: the command is its last line
        last = text.strip().splitlines()[-1] if text.strip() else ""
        if last.startswith("recap?"):
            s.init()
            s.assistant([{"type": "text", "text": text}])
            s.result(text)
            continue
        if last.startswith("flaky:"):
            await s.flaky(last, text)
            continue
        await s.turn(last)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("2.1.310 (Claude Code)")
        return 0
    if argv[:1] == ["--help"]:
        # $HOME/fake-claude-no-effort: an older CLI without --effort
        no = home_file("fake-claude-no-effort")
        print("Usage: claude [options]\n  -p, --print  print the response")
        if not (no and no.exists()):
            print("  --effort <level>  Effort level for the current session (low, medium, high, "
                  "xhigh, max)")
        return 0
    if "-p" in argv and "--effort" in argv:
        rej = home_file("fake-claude-reject-effort")
        if rej and rej.exists():
            print("error: unknown option '--effort'", file=sys.stderr, flush=True)
            return 1
    if argv[:2] == ["auth", "status"]:
        return auth_status()
    if argv[:2] == ["auth", "login"]:
        return auth_login(argv)
    if argv[:2] == ["auth", "logout"]:
        sf = state_file()
        if sf:
            sf.write_text("signed_out")
        print("Successfully logged out")
        return 0
    if "-p" in argv:
        gone = Path.cwd() / "fake_gone_sessions"
        sid = flag(argv, "--resume")
        if sid and gone.exists() and sid in gone.read_text().split():
            print(f"No conversation found with session ID: {sid}", file=sys.stderr, flush=True)
            return 1
        return asyncio.run(session(argv))
    print(f"fake claude: unsupported args {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
