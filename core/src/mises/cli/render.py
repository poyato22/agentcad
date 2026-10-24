"""Shared terminal rendering of run events, used by the chat REPL and `mises acp`.

One EventRenderer turns events (text, collapsed tool lines, approvals, questions) into rich
output and asks the human when a decision is needed. `read_line(prompt, default="")` is an
async callable returning one line (it may raise KeyboardInterrupt or EOFError).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from ..events import Event, Subscription
from ..types import MisesError, StepKind

ReadLine = Callable[..., Awaitable[str]]

APPROVE_PROMPT = "approve? [y/N/e(dit)] "
HINT_MAX = 40
OUTPUT_MAX = 4000


@dataclass
class Call:
    index: int
    call_id: str
    run_id: str | None
    name: str
    input: Any
    ts: float
    ok: bool | None = None
    code: str | None = None


def short_json(obj: Any, limit: int = OUTPUT_MAX) -> str:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


def first_hint(inp: Any) -> str:
    """The first short string argument of a call, for the collapsed line."""
    if isinstance(inp, dict):
        for v in inp.values():
            if isinstance(v, str) and v:
                return v if len(v) <= HINT_MAX else v[:HINT_MAX - 1] + "…"
    return ""


def is_explicit_yes(answer: str) -> bool:
    """Only a single typed `y` approves; anything containing a newline (a paste) never does."""
    if "\n" in answer or "\r" in answer:
        return False
    return answer.strip().lower() == "y"


@contextlib.contextmanager
def sigint_handler(callback: Callable[[], None]):
    """Run `callback` on Ctrl-C while inside the block (best effort outside the main thread)."""
    loop = asyncio.get_running_loop()
    installed = False
    try:
        loop.add_signal_handler(signal.SIGINT, callback)
        installed = True
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    try:
        yield
    finally:
        if installed:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(signal.SIGINT)


class EventRenderer:
    def __init__(self, runtime, console: Console, read_line: ReadLine):
        self.runtime = runtime
        self.console = console
        self.read_line = read_line
        self.calls: list[Call] = []
        self._by_id: dict[tuple[str | None, str], Call] = {}
        self._mid_line = False
        self._last_run: str | None = None
        self.on_interrupt: Callable[[], None] | None = None  # set by the owner during a turn

    # ---- low-level output
    def end_line(self) -> None:
        if self._mid_line:
            self.console.print()
            self._mid_line = False

    def say(self, text: str, style: str | None = None) -> None:
        self.end_line()
        self.console.print(Text(text, style=style or ""), soft_wrap=True)

    def error(self, e: MisesError) -> None:
        msg = f"✗ {e.code}: {e.message}"
        if e.hint:
            msg += f" ({e.hint})"
        self.say(msg, "red")

    # ---- event dispatch
    async def handle(self, ev: Event) -> None:
        d = ev.data
        if ev.run_id != self._last_run and ev.type in ("text_delta", "tool_result", "thought_delta"):
            self._announce_run(ev.run_id)
        if ev.type == "text_delta":
            self.console.print(Text(d.get("text", "")), end="", soft_wrap=True)
            self._mid_line = True
        elif ev.type == "thought_delta":
            self.console.print(Text(d.get("text", ""), style="dim"), end="", soft_wrap=True)
            self._mid_line = True
        elif ev.type == "tool_call":
            self._on_call(ev)
        elif ev.type == "tool_result":
            self._on_result(ev)
        elif ev.type == "approval_requested":
            await self._approval(ev)
        elif ev.type == "question_asked":
            await self._question(ev)
        elif ev.type == "note":
            self.say(f"note: {d.get('text', '')}", "dim")
        elif ev.type == "turn_retry":
            self.say("(provider hiccup, retrying the turn)", "dim")
        elif ev.type == "compaction":
            self.say("(earlier context summarized)", "dim")
        elif ev.type == "run_finished" and d.get("status") in ("cancelled", "failed"):
            reason = f": {d['error']}" if d.get("error") else ""
            self.say(f"run {ev.run_id} {d['status']}{reason}", "yellow")

    def _announce_run(self, run_id: str | None) -> None:
        first = self._last_run is None
        self._last_run = run_id
        if first or run_id is None:
            return
        try:
            agent = self.runtime.store.get_run(run_id).agent
        except KeyError:
            agent = "?"
        self.say(f"[{agent} · run {run_id}]", "dim")

    # ---- tool lines
    def _on_call(self, ev: Event) -> None:
        d = ev.data
        c = Call(len(self.calls) + 1, d.get("call_id", ""), ev.run_id, d.get("name", "?"),
                 d.get("input"), ev.ts)
        self.calls.append(c)
        self._by_id[(ev.run_id, c.call_id)] = c

    def _on_result(self, ev: Event) -> None:
        d = ev.data
        cid = d.get("call_id", "")
        c = self._by_id.get((ev.run_id, cid))
        if c is None:  # rejected before the gate saw it (unknown or unavailable tool)
            c = Call(len(self.calls) + 1, cid, ev.run_id, d.get("name", "?"), None, ev.ts)
            self.calls.append(c)
            self._by_id[(ev.run_id, cid)] = c
        c.ok = bool(d.get("ok"))
        hint = first_hint(c.input)
        head = f"▸ {c.name}" + (f" {hint}" if hint else "")
        if c.ok:
            tail = f"✓ {max(0, round((ev.ts - c.ts) * 1000))} ms"
        else:
            c.code = str(d.get("error") or d.get("status") or "failed")
            tail = f"✗ {c.code}"
        self.end_line()
        self.console.print(Text(f"{head}  {tail}", style="green" if c.ok else "red"),
                           soft_wrap=True)

    def expand(self, n: int) -> None:
        if not 1 <= n <= len(self.calls):
            self.say(f"no call #{n} (1..{len(self.calls)})" if self.calls else "no tool calls yet",
                     "yellow")
            return
        c = self.calls[n - 1]
        body: list[Any] = [Text("input", style="bold"), Text(short_json(c.input))]
        out = None
        if c.run_id:
            for st in self.runtime.store.steps(c.run_id):
                if st.payload.get("call_id") == c.call_id and st.kind != StepKind.TOOL_CALL:
                    out = st.payload
        body += [Text("output", style="bold"),
                 Text(short_json(out.get("output", out) if out else "(no recorded output)"))]
        self.end_line()
        self.console.print(Panel(Group(*body), title=f"#{n} {c.name}", border_style="dim"))

    # ---- approvals
    def _requester(self, run_id: str | None) -> str:
        """`<agent> · run <id> · <task excerpt>` so parallel subagents' cards are told apart."""
        if not run_id:
            return "unknown run"
        try:
            run = self.runtime.store.get_run(run_id)
        except KeyError:
            return f"run {run_id}"
        task = " ".join(run.task.split())
        task = task if len(task) <= 60 else task[:59] + "…"
        return f"{run.agent} · run {run_id}" + (f" · {task}" if task else "")

    def _approval_panel(self, d: dict, payload: dict, edited: bool) -> None:
        parts: list[Any] = [Text(f"requested by: {self._requester(d.get('run_id'))}"),
                            Text(f"action: {d.get('action')}", style="bold"),
                            Text(short_json(payload))]
        if edited:
            parts.append(Text("(edited payload)", style="yellow"))
        if d.get("deltas"):
            parts += [Text("deltas", style="bold"), Text(short_json(d["deltas"]))]
        parts.append(Text(f"hash {str(d.get('payload_hash', ''))[:8]}", style="dim"))
        self.end_line()
        self.console.print(Panel(Group(*parts), title="approval needed", border_style="blue"))

    async def _approval(self, ev: Event) -> None:
        d = ev.data
        payload = dict(d.get("payload") or {})
        edited: dict | None = None
        approve = False
        interrupted = False
        self._approval_panel(d, payload, False)
        while True:
            try:
                ans = await self.read_line(APPROVE_PROMPT)
            except (KeyboardInterrupt, EOFError):
                interrupted = True
                break
            if "\n" in ans or "\r" in ans:
                self.say("Pasted text is never an approval; rejected.", "yellow")
                break
            if is_explicit_yes(ans):
                approve = True
                break
            if ans.strip().lower() == "e":
                if d.get("action") == "acp_permission":
                    self.say("This request cannot be edited.", "yellow")
                    continue
                try:
                    raw = await self.read_line("edit payload JSON> ",
                                               default=json.dumps(edited or payload))
                except (KeyboardInterrupt, EOFError):
                    interrupted = True
                    break
                try:
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("payload must be a JSON object")
                except ValueError as e:
                    self.say(f"Invalid JSON ({e}); payload unchanged.", "red")
                    continue
                edited = obj
                self._approval_panel(d, edited, True)
                continue
            break  # anything else, including an empty line, rejects
        try:
            self.runtime.approvals.decide(d["id"], d["payload_hash"], approve, "cli:local",
                                          edited_payload=edited if approve else None)
        except MisesError as e:
            self.error(e)
            return
        self.say("approval granted" if approve else "approval rejected",
                 "green" if approve else "yellow")
        if interrupted and self.on_interrupt:
            self.on_interrupt()

    # ---- questions
    async def _question(self, ev: Event) -> None:
        d = ev.data
        options = list(d.get("options") or [])
        self.say(f"? {d.get('question', '')}", "bold")
        for i, o in enumerate(options, 1):
            self.say(f"  {i}) {o}")
        try:
            ans = (await self.read_line("answer> ")).strip()
        except (KeyboardInterrupt, EOFError):
            ans = ""
            if self.on_interrupt:
                self.on_interrupt()
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            ans = options[int(ans) - 1]
        try:
            self.runtime.answer(d["question_id"], ans)
        except MisesError as e:
            self.error(e)

    # ---- pump
    async def pump(self, sub: Subscription, task: asyncio.Future) -> None:
        """Render events from `sub` until `task` is done, then drain what is left."""
        while True:
            if task.done():
                while not sub.queue.empty():
                    await self.handle(sub.queue.get_nowait())
                self.end_line()
                return
            getter = asyncio.ensure_future(sub.queue.get())
            await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter.done():
                await self.handle(getter.result())
            else:
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
