"""`mises acp NAME`: run an external ACP agent against Mises' own MCP server."""
from __future__ import annotations

import asyncio
import contextlib
import os

from rich.console import Console

from ..acp_host import KNOWN_AGENTS, AcpAgentSpec, AcpSession
from ..platform import keychain_get
from ..server.app import serve_async
from ..types import MisesError
from .render import EventRenderer, ReadLine, sigint_handler

# agent name -> (vendor in the keychain, env var the agent reads)
AGENT_KEYS = {
    "claude": ("anthropic", "ANTHROPIC_API_KEY"),
    "codex": ("openai", "OPENAI_API_KEY"),
    "gemini": ("gemini", "GEMINI_API_KEY"),
}


def agent_env(spec: AcpAgentSpec) -> dict[str, str]:
    """The vendor API key for this agent, from the environment or the Keychain."""
    entry = AGENT_KEYS.get(spec.name)
    if entry is None:
        return {}
    vendor, var = entry
    key = os.environ.get(var) or keychain_get("mises", vendor)
    return {var: key} if key else {}


async def run_acp(runtime, spec: AcpAgentSpec, prompt: str | None, *, console: Console,
                  read_line: ReadLine) -> int:
    """Start the agent, optionally send one prompt (else chat until /quit); returns exit code."""
    spec = spec.model_copy(update={"env": {**spec.env, **agent_env(spec)}})
    run_id = runtime.create_external_run(agent=spec.name, provider="acp", model=spec.name)
    renderer = EventRenderer(runtime, console, read_line)
    sub = runtime.events.subscribe(run_id)
    handle = None
    session = None
    code = 0
    try:
        # embedded server: never touch server.json (a `mises serve` may own it); the agent
        # gets only the MCP-only token, and decisions are made in this process
        _port, _token, handle = await serve_async(runtime, write_server_json=False)
        session = AcpSession(runtime, spec, run_id, mcp_url=handle.mcp_url,
                             mcp_token=handle.mcp_token)
        await session.start()
        console.print(f"mises acp · {spec.name} · run {run_id}")

        async def turn(text: str) -> None:
            task = asyncio.ensure_future(session.prompt(text))
            cancelling = lambda: asyncio.ensure_future(session.cancel())  # noqa: E731
            renderer.on_interrupt = cancelling
            try:
                with sigint_handler(cancelling):
                    await renderer.pump(sub, task)
                with contextlib.suppress(asyncio.CancelledError):
                    task.result()
            finally:
                renderer.on_interrupt = None

        if prompt is not None:
            await turn(prompt)
        else:
            strikes = 0
            while True:
                try:
                    line = (await read_line(f"{spec.name}> ")).strip()
                except KeyboardInterrupt:
                    strikes += 1
                    if strikes >= 2:
                        break
                    console.print("(press Ctrl-C again to exit)")
                    continue
                except EOFError:
                    break
                strikes = 0
                if line in ("/quit", "/exit"):
                    break
                if line:
                    await turn(line)
    except MisesError as e:
        renderer.error(e)
        code = 1
    finally:
        sub.close()
        if session is not None:
            await session.close()
        else:
            with contextlib.suppress(MisesError):
                runtime.finish_external_run(run_id, "failed")
        if handle is not None:
            await handle.stop()
    return code


def known_agent(name: str) -> AcpAgentSpec | None:
    return KNOWN_AGENTS.get(name)
