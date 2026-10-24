"""Interactive chat REPL: a terminal coding-agent feel for CAD runs."""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..runtime import USER
from ..types import MisesError
from .render import EventRenderer, ReadLine, sigint_handler

HELP = ("/model REF  /agent NAME  /plan GOAL  /runs  /cost  /steps  /expand N  /clear  /quit")


def prompt_toolkit_reader(history_path: Path | None = None) -> ReadLine:
    """A read_line backed by prompt_toolkit (history, line editing, bracketed paste)."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory

    history = InMemoryHistory()
    if history_path is not None:
        with contextlib.suppress(OSError):
            history = FileHistory(str(history_path))
    session: PromptSession = PromptSession(history=history)

    async def read_line(prompt: str, default: str = "") -> str:
        return await session.prompt_async(prompt, default=default)
    return read_line


class Repl:
    def __init__(self, runtime, agent: str, model_ref: str | None, *, console: Console | None = None,
                 read_line: ReadLine | None = None, scope: tuple[str, ...] = ()):
        self.runtime = runtime
        self.agent = agent
        self.model_ref = model_ref
        self.scope = tuple(scope)
        self.console = console or Console()
        self.read_line = read_line or prompt_toolkit_reader(runtime.home / "repl_history")
        self.renderer = EventRenderer(runtime, self.console, self.read_line)
        self.run_id: str | None = None
        self._cancel = None  # callable that stops the turn in flight

    # ---- Ctrl-C
    def interrupt(self) -> bool:
        """Stop the turn in flight (cancel its run). Returns False if nothing is running."""
        if self._cancel is None:
            return False
        self._cancel()
        return True

    # ---- loop
    async def run(self) -> None:
        self.console.print(f"mises chat · agent {self.agent} · model "
                           f"{self.model_ref or '(agent default)'} · /help for commands")
        strikes = 0
        while True:
            try:
                line = await self.read_line(f"{self.agent}> ")
            except KeyboardInterrupt:
                strikes += 1
                if strikes >= 2:
                    break
                self.console.print("(press Ctrl-C again to exit)")
                continue
            except EOFError:
                break
            strikes = 0
            if not await self.handle_line(line):
                break

    async def handle_line(self, line: str) -> bool:
        """Process one input line; returns False when the user quit."""
        line = line.strip()
        if not line:
            return True
        try:
            if line.startswith("/"):
                return await self._slash(line)
            await self._turn(line)
        except MisesError as e:
            self.renderer.error(e)
        return True

    # ---- turns
    async def _turn(self, text: str) -> None:
        rt = self.runtime
        sub = rt.events.subscribe()
        try:
            if self.run_id is None:
                self.run_id = await rt.start_run(
                    self.agent, text, model_ref=self.model_ref,
                    edit_scope=self.scope or ("*",))
            else:
                await rt.send(self.run_id, text)
            run_id = self.run_id
            self._cancel = self.renderer.on_interrupt = lambda: rt.cancel(run_id)
            task = asyncio.ensure_future(rt.wait(run_id))
            with sigint_handler(self.interrupt):
                try:
                    await self.renderer.pump(sub, task)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    rt.cancel(run_id)
                    await self.renderer.pump(sub, task)
            res = task.result()
            if res.stop_reason == "max_turns":
                self.renderer.say("(stopped: turn limit reached)", "yellow")
        finally:
            self._cancel = self.renderer.on_interrupt = None
            sub.close()

    async def _plan(self, goal: str) -> None:
        rt = self.runtime
        if not goal:
            self.renderer.say("usage: /plan GOAL", "yellow")
            return
        parent = rt.create_external_run("repl", provider="cli", model="-",
                                        edit_scope=self.scope or ("*",))
        sub = rt.events.subscribe()
        task = asyncio.ensure_future(rt.orchestrator.run(
            goal, parent_run_id=parent, model_ref=self.model_ref))
        self._cancel = self.renderer.on_interrupt = task.cancel
        status = "done"
        try:
            with sigint_handler(self.interrupt):
                await self.renderer.pump(sub, task)
            try:
                result = task.result()
            except asyncio.CancelledError:
                status = "cancelled"
                self.renderer.say("plan cancelled", "yellow")
                return
            except MisesError as e:
                status = "failed"
                self.renderer.error(e)
                return
            table = Table("task", "status", "output")
            for tid, r in result.get("results", {}).items():
                out = (r.get("output") or "").strip().replace("\n", " ")
                table.add_row(tid, r.get("status", "?"), out[:200])
            self.console.print(table)
        finally:
            self._cancel = self.renderer.on_interrupt = None
            sub.close()
            with contextlib.suppress(MisesError):
                rt.finish_external_run(parent, status)

    # ---- slash commands
    async def _slash(self, line: str) -> bool:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        say = self.renderer.say
        rt = self.runtime
        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd == "/help":
            say(HELP)
        elif cmd == "/model":
            if not arg:
                say(f"model: {self.model_ref or '(agent default)'}")
                return True
            try:
                rt.provider_resolver(arg)
            except MisesError as e:
                self.renderer.error(e)
                return True
            self.model_ref, self.run_id = arg, None
            say(f"model set to {arg}; the next message starts a new session")
        elif cmd == "/agent":
            if arg not in rt.definitions:
                say(f"unknown agent: {arg or '(none)'}; agents: {', '.join(sorted(rt.definitions))}",
                    "red")
                return True
            self.agent, self.run_id = arg, None
            say(f"agent set to {arg}; the next message starts a new session")
        elif cmd == "/plan":
            await self._plan(arg)
        elif cmd == "/runs":
            self._runs()
        elif cmd == "/cost":
            self._cost()
        elif cmd == "/steps":
            self._steps()
        elif cmd == "/expand":
            if arg.isdigit():
                self.renderer.expand(int(arg))
            else:
                say("usage: /expand N", "yellow")
        elif cmd == "/clear":
            self.run_id = None
            self.renderer.calls.clear()
            self.renderer._by_id.clear()
            self.console.clear()
            say("session cleared")
        else:
            say(f"unknown command {cmd}; try /help", "red")
        return True

    def _runs(self) -> None:
        table = Table("id", "agent", "model", "status", "task")
        for r in self.runtime.store.list_runs(10):
            table.add_row(r.id, r.agent, r.model, r.status, r.task[:40].replace("\n", " "))
        self.console.print(table)

    def _steps(self) -> None:
        if self.run_id is None:
            self.renderer.say("no run yet", "yellow")
            return
        table = Table("#", "kind", "summary")
        for st in self.runtime.store.steps(self.run_id):
            summary = str(st.payload.get("text") or st.payload.get("name")
                          or st.payload.get("message") or "")
            table.add_row(str(st.id), str(st.kind), summary[:60].replace("\n", " "))
        self.console.print(table)

    def _cost(self) -> None:
        if self.run_id is None:
            self.renderer.say("no run yet", "yellow")
            return
        rt = self.runtime
        run = rt.store.get_run(self.run_id)
        caps = rt.store.current_policy(self.run_id).caps
        year = rt.store.ledger_used(USER, since=time.time() - 365 * 86400)
        self.renderer.say(
            f"tokens: {run.input_tokens} in / {run.output_tokens} out · cost: ${run.usd:.4f}\n"
            f"Onshape calls: {run.onshape_calls}/{caps.onshape_calls_run} this run · "
            f"{year}/{caps.onshape_calls_year} this year")
