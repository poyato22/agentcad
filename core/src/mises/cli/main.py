"""`mises` command line: thin argparse dispatch. REPL logic lives in repl.py."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from ..platform import keychain_get, keychain_set
from ..providers.registry import DEFAULT_MODEL, VENDORS


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mises", description="AgentCAD: an AI CAD agent harness")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("chat", help="interactive chat with a CAD agent")
    p.add_argument("--model", metavar="REF",
                   help="vendor/model, e.g. anthropic/claude-haiku-5-5, or claude-code/sonnet "
                        "(runs your Claude Code)")
    p.add_argument("--agent", default="designer", help="agent definition (default: designer)")
    p.add_argument("--scope", nargs="+", default=[], metavar="PART",
                   help="parts the agent may edit (default: all)")

    p = sub.add_parser("serve", help="run the local HTTP/WebSocket/MCP server")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--exit-on-stdin-eof", action="store_true",
                   help="stop cleanly when stdin closes (parent app died)")

    p = sub.add_parser("mcp-stdio", help="stdio MCP proxy for agents that only speak stdio")
    p.add_argument("--run", required=True, metavar="ID")

    p = sub.add_parser("acp", help="drive an external ACP agent (claude, codex, gemini, ...)")
    p.add_argument("name")
    p.add_argument("--prompt", help="send one prompt and exit")

    sub.add_parser("models", help="vendors, key status and the default model")

    p = sub.add_parser("keys", help="manage API keys in the macOS Keychain")
    ksub = p.add_subparsers(dest="keys_cmd", required=True)
    k = ksub.add_parser("set", help="store a key (hidden prompt)")
    k.add_argument("vendor")

    sub.add_parser("agents", help="list agent definitions")

    p = sub.add_parser("claude", help="your Claude Code: status, sign in, sign out")
    p.add_argument("claude_cmd", choices=["status", "login", "logout"])
    p.add_argument("--console", action="store_true",
                   help="login: sign in with an Anthropic Console account")

    p = sub.add_parser("runs", help="recent runs, or one run's steps")
    p.add_argument("--steps", metavar="ID", help="show the steps of this run")
    return ap


def _err(msg: str) -> int:
    print(f"mises: {msg}", file=sys.stderr)
    return 1


def _chat(args) -> int:
    from ..runtime import Runtime
    from .repl import Repl

    async def go() -> None:
        from ..server.app import serve_async
        rt = Runtime()
        handle = None

        async def start_server() -> None:
            # claude-code/<alias> runs reach Mises through the MCP endpoint: an embedded
            # server (no server.json, so a running `mises serve` is left alone)
            nonlocal handle
            if handle is None:
                _port, _token, handle = await serve_async(rt, write_server_json=False)
        rt.set_mcp_autostart(start_server)
        try:
            if args.agent not in rt.definitions:
                print(f"mises: unknown agent {args.agent!r}; agents: "
                      f"{', '.join(sorted(rt.definitions))}", file=sys.stderr)
                return
            await Repl(rt, args.agent, args.model, scope=tuple(args.scope)).run()
        finally:
            await rt.aclose()
            if handle is not None:
                await handle.stop()
    asyncio.run(go())
    return 0


def _serve(args) -> int:
    from ..runtime import Runtime
    from ..server import app as server_app

    import signal
    import threading

    rt = Runtime()
    if args.exit_on_stdin_eof:
        def _watch_stdin() -> None:
            try:
                while sys.stdin.buffer.read(4096):
                    pass
            except (OSError, ValueError):
                pass
            os.kill(os.getpid(), signal.SIGINT)  # main thread: KeyboardInterrupt -> clean stop

        threading.Thread(target=_watch_stdin, daemon=True).start()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal.default_int_handler)  # clean stop removes server.json
    port, _token = server_app.serve(rt, port=args.port)
    print(f"mises server listening on http://127.0.0.1:{port}")
    print(f"token and port are in {rt.home / 'server.json'}; Ctrl-C to stop")
    try:
        server_app.wait(port)
    except KeyboardInterrupt:
        pass
    finally:
        server_app.stop(port)
    return 0


def _mcp_stdio(args) -> int:
    from ..server import stdio_proxy
    return stdio_proxy.main(["--run", args.run])


def _acp(args) -> int:
    from ..runtime import Runtime
    from . import acp as acp_cmd
    from .repl import prompt_toolkit_reader
    from rich.console import Console

    spec = acp_cmd.known_agent(args.name)
    if spec is None:
        from ..acp_host import KNOWN_AGENTS
        return _err(f"unknown ACP agent {args.name!r}; known: {', '.join(sorted(KNOWN_AGENTS))}")

    async def go() -> int:
        rt = Runtime()
        try:
            return await acp_cmd.run_acp(rt, spec, args.prompt, console=Console(),
                                         read_line=prompt_toolkit_reader(rt.home / "acp_history"))
        finally:
            await rt.aclose()
    return asyncio.run(go())


def _models(_args) -> int:
    print(f"{'vendor':<12} {'key':<16} default model")
    for name, spec in VENDORS.items():
        if spec.env_key is None:
            source = "no key needed"
        elif os.environ.get(spec.env_key):
            source = "env"
        elif keychain_get("mises", name):
            source = "keychain"
        else:
            source = "none"
        print(f"{name:<12} {source:<16} {DEFAULT_MODEL if name == 'anthropic' else ''}".rstrip())
    from ..claude_code import MODEL_ALIASES, auth_status, detect
    info = detect.find_claude()
    cc = ("not installed" if not info.path
          else "signed in" if asyncio.run(auth_status(info)).signed_in else "signed out")
    print(f"{'claude-code':<12} {cc:<16} {', '.join(MODEL_ALIASES)}")
    onshape = "keychain" if keychain_get("mises", "onshape") else "none"
    print(f"{'onshape':<12} {onshape:<16}")
    print(f"\ndefault model: {DEFAULT_MODEL}")
    return 0


def _keys_set(args) -> int:
    vendor = args.vendor
    if vendor == "onshape":
        access = getpass.getpass("Onshape access key: ").strip()
        secret = getpass.getpass("Onshape secret key: ").strip()
        if not access or not secret:
            return _err("both keys are required")
        value = f"{access}:{secret}"
    elif vendor in VENDORS and VENDORS[vendor].env_key:
        value = getpass.getpass(f"{vendor} API key: ").strip()
        if not value:
            return _err("empty key, nothing stored")
    else:
        keyed = [v for v, s in VENDORS.items() if s.env_key] + ["onshape"]
        return _err(f"unknown vendor {vendor!r}; keys can be set for: {', '.join(keyed)}")
    try:
        keychain_set("mises", vendor, value)
    except (RuntimeError, ValueError) as e:
        return _err(str(e))
    print(f"stored {vendor} key in the Keychain")
    return 0


def _claude(args) -> int:
    """`mises claude status|login|logout`: the user's own Claude Code. Sign-in is Anthropic's
    `claude auth login` flow; Mises only shows the non-secret status."""
    from ..claude_code import auth_status, detect, logout, start_login

    async def go() -> int:
        info = detect.find_claude()
        if not info.path:
            print("Claude Code is not installed (see code.claude.com)")
            return 1
        if args.claude_cmd == "login":
            print("opening the Claude sign-in page in your browser...")
            h = await start_login(info, console=args.console)
            st = await h.wait()
            print("signed in" if st.signed_in else "not signed in")
            return 0 if st.signed_in else 1
        if args.claude_cmd == "logout":
            ok = await logout(info)
            print("signed out" if ok else "sign-out failed")
            return 0 if ok else 1
        st = await auth_status(info)
        print(f"Claude Code {info.version} at {info.path}")
        print(f"signed in ({st.auth_method})" if st.signed_in else "not signed in")
        return 0 if st.signed_in else 1
    return asyncio.run(go())


def _agents(_args) -> int:
    from ..runtime import Runtime
    rt = Runtime()
    for name in sorted(rt.definitions):
        d = rt.definitions[name]
        print(f"{name:<12} {d.model:<10} {d.description}")
    return 0


def _runs(args) -> int:
    from ..runtime import Runtime
    rt = Runtime()
    if args.steps:
        try:
            rt.store.get_run(args.steps)
        except KeyError:
            return _err(f"unknown run: {args.steps}")
        for st in rt.store.steps(args.steps):
            p = st.payload
            summary = str(p.get("text") or p.get("name") or p.get("message") or "")
            print(f"{st.id:>5} {str(st.kind):<12} {summary[:70].replace(chr(10), ' ')}")
        return 0
    for r in rt.store.list_runs(20):
        print(f"{r.id:<14} {r.status:<10} {r.agent:<10} {r.model:<24} {r.task[:40]!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help(sys.stderr)
        raise SystemExit(2)
    handlers = {"chat": _chat, "serve": _serve, "mcp-stdio": _mcp_stdio, "acp": _acp,
                "models": _models, "keys": _keys_set, "agents": _agents, "runs": _runs,
                "claude": _claude}
    try:
        return handlers[args.cmd](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
