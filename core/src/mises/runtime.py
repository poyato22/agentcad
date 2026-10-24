"""Runtime: wires store, events, approvals, registry and gate, and drives agent runs."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .agent.definitions import load_definitions
from .agent.loop import AgentLoop, LoopConfig, RunResult
from .approvals import ApprovalService
from .cad.tools import register_mirror_tools
from .events import Event, EventBus
from .gate import Gate
from .onshape.adapter import OnshapeAdapter
from .onshape.client import OnshapeClient
from .onshape.tools import register_onshape_tools
from .platform import data_home, keychain_get
from .providers.registry import DEFAULT_MODEL, resolve
from .store import Store
from .tools.registry import ToolRegistry
from .types import Caps, ErrorCode, Message, MisesError, SafetyClass
from .ui_tools import register_ui_tools

log = logging.getLogger(__name__)

USER = "local"

_STATUS = {"end_turn": "done", "max_tokens": "done", "cancelled": "cancelled",
           "error": "failed", "circuit_open": "failed", "max_turns": "failed"}


@dataclass
class _RunState:
    loop: AgentLoop
    task: asyncio.Task | None = None
    result: RunResult | None = None
    history: list[Message] = field(default_factory=list)
    cancel_requested: bool = False


class Runtime:
    def __init__(self, home: Path | None = None, *, provider_resolver=resolve):
        self.home = Path(home) if home is not None else data_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.provider_resolver = provider_resolver
        self.store = Store(self.home / "mises.db")
        self.events = EventBus()
        self.approvals = ApprovalService(self.store, self.events)
        self.registry = ToolRegistry()
        register_mirror_tools(self.registry)
        register_onshape_tools(self.registry)
        register_ui_tools(self.registry)
        services: dict = {"approvals": self.approvals, "runtime": self}
        creds = keychain_get("mises", "onshape")
        if creds and ":" in creds:
            access, secret = creds.split(":", 1)
            client = OnshapeClient(access, secret, store=self.store, user=USER)
            services["onshape"] = OnshapeAdapter(client, self.store)
        self.gate = Gate(self.registry, self.store, self.approvals, self.events,
                         user=USER, services=services)
        self.definitions = load_definitions([self.home / "agents"])
        for w in self.definitions.warnings:
            log.warning("agent definition skipped: %s", w)
        self._runs: dict[str, _RunState] = {}
        self._questions: dict[str, tuple[str, asyncio.Future[str]]] = {}

    async def aclose(self) -> None:
        ad = self.gate.services.get("onshape")
        if ad is not None:
            await ad.client.aclose()

    # ---- runs
    def workdir(self, run_id: str) -> Path:
        wd = self.home / "runs" / run_id
        wd.mkdir(parents=True, exist_ok=True)
        parts = self.home / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        link = wd / "parts"
        if not link.is_symlink() and not link.exists():
            link.symlink_to(parts, target_is_directory=True)
        return wd

    async def start_run(self, agent: str, prompt: str, *, model_ref: str | None = None,
                        edit_scope=("*",), read_scope=("*",), caps: Caps = Caps(),
                        parent_id: str | None = None,
                        allowed_classes: set[SafetyClass] | None = None) -> str:
        defn = self.definitions.get(agent)
        if defn is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown agent: {agent}",
                             hint=f"agents: {', '.join(sorted(self.definitions))}")
        ref = model_ref or (defn.model if defn.model != "inherit" else DEFAULT_MODEL)
        provider, model = self.provider_resolver(ref)
        run = self.store.create_run(agent, provider.name, model, prompt, parent_id=parent_id)
        classes = set(SafetyClass) if allowed_classes is None else set(allowed_classes)
        self.store.create_policy(run.id, list(edit_scope), list(read_scope), caps,
                                 [c for c in SafetyClass if c in classes])
        loop = AgentLoop(run_id=run.id, defn=defn, provider=provider, model=model, gate=self.gate,
                         registry=self.registry, store=self.store, events=self.events,
                         workdir=self.workdir(run.id), config=LoopConfig())
        st = self._runs[run.id] = _RunState(loop=loop)
        self.events.publish(Event(type="run_started", run_id=run.id, data={
            "agent": agent, "provider": provider.name, "model": model, "parent_id": parent_id}))
        st.task = asyncio.create_task(self._drive(run.id, prompt))
        return run.id

    async def _drive(self, run_id: str, prompt: str) -> RunResult:
        st = self._runs[run_id]
        try:
            if st.cancel_requested:
                result = RunResult(final_text="", messages=st.history, stop_reason="cancelled",
                                   error="run cancelled")
            else:
                result = await st.loop.run(prompt, st.history)
        except asyncio.CancelledError:
            self.approvals.cancel_run(run_id)
            self.store.release_run_reservations(run_id)
            self.store.update_run(run_id, status="cancelled")
            raise
        except Exception as e:
            log.exception("run %s crashed", run_id)
            result = RunResult(final_text="", messages=st.history, stop_reason="error",
                               error=f"{type(e).__name__}: {e}")
        st.result, st.history = result, result.messages
        status = _STATUS[result.stop_reason]
        self.store.update_run(run_id, status=status)
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": status, "stop_reason": result.stop_reason, "error": result.error,
            "final_text": result.final_text}))
        return result

    def _state(self, run_id: str) -> _RunState:
        st = self._runs.get(run_id)
        if st is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}")
        return st

    async def send(self, run_id: str, text: str) -> None:
        st = self._state(run_id)
        if st.task is not None and not st.task.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        st.cancel_requested = False
        self.store.update_run(run_id, status="running")
        self.events.publish(Event(type="run_started", run_id=run_id, data={"resumed": True}))
        st.task = asyncio.create_task(self._drive(run_id, text))

    async def wait(self, run_id: str) -> RunResult:
        st = self._state(run_id)
        assert st.task is not None
        return await asyncio.shield(st.task)

    def cancel(self, run_id: str) -> None:
        st = self._state(run_id)
        st.cancel_requested = True
        st.loop.cancel()
        self.approvals.cancel_run(run_id)
        self.store.release_run_reservations(run_id)

    # ---- questions (ui_ask_user)
    async def ask(self, run_id: str, question: str, options: list[str]) -> str:
        qid = uuid.uuid4().hex[:12]
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._questions[qid] = (run_id, fut)
        self.events.publish(Event(type="question_asked", run_id=run_id, data={
            "question_id": qid, "question": question, "options": list(options)}))
        try:
            return await fut
        finally:
            self._questions.pop(qid, None)

    def answer(self, question_id: str, text: str) -> None:
        entry = self._questions.get(question_id)
        if entry is None or entry[1].done():
            raise MisesError(ErrorCode.NOT_FOUND, f"no open question {question_id}")
        run_id, fut = entry
        fut.set_result(text)
        self.events.publish(Event(type="question_answered", run_id=run_id,
                                  data={"question_id": question_id}))
