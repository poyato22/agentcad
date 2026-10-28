"""Runtime: wires store, events, approvals, registry and gate, and drives agent runs."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import hardware, local_models
from .agent.definitions import load_definitions
from .agent.loop import AgentLoop, LoopConfig, RunResult
from .approvals import ApprovalService
from .cad.tools import register_mirror_tools
from .claude_code import detect as claude_detect
from .claude_code.session import MCP_FILE as CLAUDE_MCP_FILE, SOURCE as CLAUDE_CODE
from .claude_code.session import ClaudeCodeSession, model_alias
from .context_tools import build_context, ui_context
from .events import Event, EventBus
from .gate import Gate
from .onshape.adapter import OnshapeAdapter
from .onshape.client import OnshapeClient
from .onshape.tools import register_onshape_tools
from .orchestrator import Orchestrator, register_orchestrator_tools
from .parallel import Limits, ParallelLimiter
from .platform import data_home, keychain_get
from .profile import load_profile
from .providers.registry import DEFAULT_MODEL, VENDORS, resolve
from .store import Store
from .tools.registry import ToolRegistry
from .types import Caps, ErrorCode, Message, MisesError, SafetyClass, StepKind
from .ui_tools import register_ui_tools

log = logging.getLogger(__name__)

USER = "local"

LOGIN_TIMEOUT_S = 600.0            # a `claude auth login` left open in the browser is stopped
# GET /models and GET /setup reuse a `claude auth status` answer this young (the app asks both on
# every screen load); /claude-code/status and run starts always ask afresh
CLAUDE_STATUS_TTL_S = 5.0
# sizes of downloaded Ollama models (for auto parallel agents) are reused this long
LOCAL_SIZES_TTL_S = 60.0
FREE_GROUP = "openrouter:free"

_STATUS = {"end_turn": "done", "max_tokens": "done", "cancelled": "cancelled",
           "error": "failed", "circuit_open": "failed", "max_turns": "failed"}


@dataclass
class _RunState:
    loop: AgentLoop
    task: asyncio.Task | None = None
    result: RunResult | None = None
    history: list[Message] = field(default_factory=list)
    cancel_requested: bool = False
    ref: str = ""


@dataclass
class _ClaudeRun:
    """A run driven by the user's Claude Code: its live session (None or dead between a cancel
    or a restart and the next message, which resumes it) and the current turn's task."""
    session: ClaudeCodeSession | None = None
    task: asyncio.Task | None = None
    result: RunResult | None = None
    cancel_requested: bool = False
    ref: str = ""
    slot: asyncio.Future[bool] | None = None   # set while the next turn waits for a slot
    started_data: dict = field(default_factory=dict)
    restore: str | None = None    # follow-up turns: the status to go back to once a slot frees


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
        register_orchestrator_tools(self.registry)
        self.orchestrator = Orchestrator(self)
        services: dict = {"approvals": self.approvals, "runtime": self,
                          "orchestrator": self.orchestrator}
        adapter = self._onshape_adapter(keychain_get("mises", "onshape"))
        if adapter is not None:
            services["onshape"] = adapter
        self.gate = Gate(self.registry, self.store, self.approvals, self.events,
                         user=USER, services=services)
        self.definitions = load_definitions([self.home / "agents"])
        for w in self.definitions.warnings:
            log.warning("agent definition skipped: %s", w)
        self._runs: dict[str, _RunState] = {}
        self._external: set[str] = set()
        self._questions: dict[str, tuple[str, asyncio.Future[str]]] = {}
        # external run id -> owner's async cancel (e.g. AcpSession.cancel kills the agent)
        self._cancel_hooks: dict[str, Callable[[], Awaitable[None]]] = {}
        self._hook_tasks: set[asyncio.Task] = set()
        # Claude Code runs this process drives; the MCP endpoint (url, MCP-only token) of the
        # server serving this runtime, set by serve()/serve_async(); an optional starter for it
        self._claude: dict[str, _ClaudeRun] = {}
        self._external_slots: dict[str, tuple[asyncio.Future[bool] | None, dict]] = {}
        self.mcp_endpoint: tuple[str, str] | None = None
        self._mcp_autostart: Callable[[], Awaitable[None]] | None = None
        self._login: claude_detect.LoginHandle | None = None
        self._claude_status_cache: tuple[float, tuple[claude_detect.ClaudeInfo,
                                                      claude_detect.AuthStatus]] | None = None
        self._claude_status_lock = asyncio.Lock()
        # how many runs talk to their model at once (profile max_parallel_agents)
        self.parallel = ParallelLimiter(self._limits_for)
        hardware.machine()          # sysctl once now, not later on the event loop
        self._local_sizes: tuple[float, dict[str, float]] | None = None
        self.interrupted = self._fail_orphaned_runs()
        self._remove_stale_mcp_files()

    # ---- crash recovery
    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _fail_orphaned_runs(self) -> list[str]:
        """Fail runs left "running" by a process that is gone (crash, kill -9, power loss):
        stop_reason "interrupted", reservations released, pending approvals rejected. Runs
        owned by another live process (e.g. a `mises serve` next to this CLI) are left alone.
        Once failed, the MCP endpoint refuses them (it only serves running runs)."""
        out = []
        for run in self.store.running_runs(include_queued=True):
            pid = run.owner_pid
            if pid is not None and (pid == os.getpid() or self._pid_alive(pid)):
                continue
            self.approvals.cancel_run(run.id)
            self.store.release_run_reservations(run.id)
            self.store.append_step(run.id, StepKind.ERROR, {
                "stop_reason": "interrupted", "error": str(ErrorCode.CANCELLED),
                "message": "the process driving this run exited before it finished"})
            self.store.update_run(run.id, status="failed")
            self.events.publish(Event(type="run_finished", run_id=run.id, data={
                "status": "failed", "stop_reason": "interrupted",
                "error": "interrupted: the process driving this run exited", "final_text": ""}))
            out.append(run.id)
        if out:
            log.warning("marked %d interrupted run(s) failed: %s", len(out), ", ".join(out))
        return out

    def _remove_stale_mcp_files(self) -> None:
        """A Claude Code session's MCP config (it holds an MCP-only token) is deleted when the
        session ends; if the process that ran it died, delete it here."""
        for path in (self.home / "runs").glob(f"*/{CLAUDE_MCP_FILE}"):
            try:
                pid = self.store.get_run(path.parent.name).owner_pid
            except KeyError:
                pid = None
            if pid is not None and pid != os.getpid() and self._pid_alive(pid):
                continue
            path.unlink(missing_ok=True)

    def _onshape_adapter(self, creds: str | None) -> OnshapeAdapter | None:
        if creds and ":" in creds:
            access, secret = creds.split(":", 1)
            return OnshapeAdapter(OnshapeClient(access, secret, store=self.store, user=USER),
                                  self.store)
        return None

    async def set_onshape_credentials(self, creds: str | None) -> None:
        """Swap the live Onshape adapter after the keys changed ("access:secret" or None)."""
        old = self.gate.services.pop("onshape", None)
        new = self._onshape_adapter(creds)
        if new is not None:
            self.gate.services["onshape"] = new
        if old is not None:
            try:
                await old.client.aclose()
            except Exception:  # noqa: BLE001 - the old client is dropped either way
                log.warning("closing the previous Onshape client failed", exc_info=True)

    def profile(self):
        return load_profile(self.home)

    async def aclose(self) -> None:
        """Shut down: every live Claude Code session is closed (no orphan `claude` process or
        MCP token file), a sign-in in progress is stopped, the Onshape client is closed."""
        await self.close_claude_sessions()
        ad = self.gate.services.get("onshape")
        if ad is not None:
            await ad.client.aclose()

    # ---- the MCP endpoint Claude Code runs reach Mises through
    def attach_mcp(self, url: str, mcp_token: str) -> None:
        self.mcp_endpoint = (url, mcp_token)

    def detach_mcp(self, url: str | None = None) -> None:
        if url is None or (self.mcp_endpoint is not None and self.mcp_endpoint[0] == url):
            self.mcp_endpoint = None

    def set_mcp_autostart(self, start: Callable[[], Awaitable[None]] | None) -> None:
        """For embedded use (`mises chat`): how to start a server when a Claude Code run needs
        one. `start` must end with attach_mcp (serve_async does)."""
        self._mcp_autostart = start

    async def _mcp(self) -> tuple[str, str]:
        if self.mcp_endpoint is None and self._mcp_autostart is not None:
            await self._mcp_autostart()
        if self.mcp_endpoint is None:
            raise MisesError(ErrorCode.DENIED,
                             "Claude Code runs need the Mises server: it is how Claude Code "
                             "reaches the Mises tools",
                             hint="open the Mises app or run `mises serve`, then retry")
        return self.mcp_endpoint

    # ---- the user's Claude Code: status, sign-in
    async def claude_status(self, *, max_age: float = 0.0
                            ) -> tuple[claude_detect.ClaudeInfo, claude_detect.AuthStatus]:
        """Where `claude` is and whether it is signed in. With `max_age` > 0 an answer at most
        that many seconds old is reused (concurrent callers share one check); 0 always asks
        `claude auth status` again and refreshes what later cached reads see."""
        def cached():
            c = self._claude_status_cache
            if max_age > 0 and c is not None and time.monotonic() - c[0] <= max_age:
                return c[1]
            return None
        hit = cached()
        if hit is not None:
            return hit
        async with self._claude_status_lock:
            hit = cached()
            if hit is not None:
                return hit
            info = await asyncio.to_thread(claude_detect.find_claude)
            auth = ((await claude_detect.auth_status(info)) if info.path
                    else claude_detect.NOT_SIGNED_IN)
            self._claude_status_cache = (time.monotonic(), (info, auth))
            return info, auth

    async def claude_status_cached(self) -> tuple[claude_detect.ClaudeInfo,
                                                  claude_detect.AuthStatus]:
        """claude_status() reusing an answer up to CLAUDE_STATUS_TTL_S old (/models, /setup)."""
        return await self.claude_status(max_age=CLAUDE_STATUS_TTL_S)

    def forget_claude_status(self) -> None:
        self._claude_status_cache = None

    async def _claude_ready(self) -> tuple[claude_detect.ClaudeInfo, claude_detect.AuthStatus]:
        info, auth = await self.claude_status()
        if not info.path:
            raise MisesError(ErrorCode.NOT_FOUND, "Claude Code is not installed",
                             hint="install it from code.claude.com, then sign in from Setup")
        if not auth.signed_in:
            raise MisesError(ErrorCode.DENIED, "Claude Code is not signed in",
                             hint="sign in from Setup or Profile (runs `claude auth login`)")
        return info, auth

    async def start_claude_login(self, *, console: bool = False) -> bool:
        """Start Anthropic's `claude auth login` (opens the browser) and return at once; False
        if one is already in progress. Poll claude_status() for the outcome."""
        if self._login is not None and not self._login.done:
            return False
        info = await asyncio.to_thread(claude_detect.find_claude)
        if not info.path:
            raise MisesError(ErrorCode.NOT_FOUND, "Claude Code is not installed",
                             hint="install it from code.claude.com, then retry")
        self.forget_claude_status()
        h = self._login = await claude_detect.start_login(info, console=console)

        async def finish() -> None:
            try:
                await h.wait(LOGIN_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                log.warning("claude auth login did not finish cleanly", exc_info=True)
            finally:
                if self._login is h:
                    self._login = None
        self._track(asyncio.get_running_loop().create_task(finish()))
        return True

    async def claude_logout(self) -> bool:
        h = self._login
        if h is not None and not h.done:
            await h.cancel()
        info = await asyncio.to_thread(claude_detect.find_claude)
        if not info.path:
            raise MisesError(ErrorCode.NOT_FOUND, "Claude Code is not installed")
        try:
            return await claude_detect.logout(info)
        finally:
            self.forget_claude_status()

    def _track(self, task: asyncio.Task) -> None:
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)

    # ---- parallel agents (profile max_parallel_agents: "auto" or a number)
    def _local_size(self, ref: str) -> float | None:
        vendor, _, model = ref.partition("/")
        if vendor != "ollama" or self._local_sizes is None:
            return None
        return self._local_sizes[1].get(model)

    async def _ollama_sizes(self, base_url: str) -> dict[str, float]:
        st = await local_models.ollama_status(base_url)
        return {m["name"]: m["size_gb"] for m in st["models"] if m.get("size_gb")}

    async def refresh_local_sizes(self, *, max_age: float = LOCAL_SIZES_TTL_S) -> None:
        """Learn the downloaded Ollama models' sizes (a local model runs twice at once only
        when memory holds two copies). Failures leave sizes unknown: one run at a time."""
        c = self._local_sizes
        if c is not None and time.monotonic() - c[0] < max_age:
            return
        base = self.profile().local_endpoints.get("ollama") or VENDORS["ollama"].base_url or ""
        try:
            sizes = await self._ollama_sizes(base)
        except Exception:  # noqa: BLE001 - unknown size means the safe choice (1)
            log.warning("could not read Ollama model sizes", exc_info=True)
            sizes = {}
        self._local_sizes = (time.monotonic(), sizes)

    def forget_local_sizes(self) -> None:
        self._local_sizes = None

    def note_local_sizes(self, models: list[dict]) -> None:
        """Sizes seen by GET /local/status (same shape as ollama_status()["models"])."""
        self._local_sizes = (time.monotonic(), {m["name"]: m["size_gb"] for m in models
                                                if m.get("size_gb")})

    def _total_limit(self, prof=None) -> int:
        prof = prof if prof is not None else self.profile()
        if prof.max_parallel_agents == "auto":
            return hardware.auto_parallel(DEFAULT_MODEL)       # the cloud budget of this Mac
        return int(prof.max_parallel_agents)

    def _limits_for(self, ref: str) -> Limits:
        """A run on `ref` may start when fewer than `total` runs work and its group (one local
        model; all OpenRouter free models) is below its cap. The group caps apply in manual
        mode too: a number in the profile never oversubscribes a local model."""
        total = self._total_limit()
        kind = hardware.model_kind(ref)
        if kind == "local":
            return Limits(total, group=ref, group_cap=hardware.auto_parallel(
                ref, model_size_gb=self._local_size(ref)))
        if kind == "free":
            return Limits(total, group=FREE_GROUP, group_cap=1)
        return Limits(total)

    def parallel_limit(self, ref: str | None = None) -> int:
        """How many runs on `ref` (default: the profile's default model) can work at once."""
        prof = self.profile()
        ref = ref or prof.default_model or DEFAULT_MODEL
        lim = self._limits_for(ref)
        return min(lim.total, lim.group_cap) if lim.group is not None else lim.total

    def parallel_status(self) -> dict:
        """GET /status `parallel`: {limit, auto, reason} for the profile's default model."""
        prof = self.profile()
        ref = prof.default_model or DEFAULT_MODEL
        limit = self.parallel_limit(ref)
        auto = prof.max_parallel_agents == "auto"
        if auto:
            reason = hardware.parallel_reason(ref, model_size_gb=self._local_size(ref),
                                              limit=limit)
        elif limit < int(prof.max_parallel_agents):
            kind = hardware.model_kind(ref)
            what = "local model" if kind == "local" else "OpenRouter free model"
            reason = (f"set in Profile: {prof.max_parallel_agents}; the default {what} runs "
                      f"{limit} at a time → {limit}")
        else:
            reason = f"set in Profile → {limit}"
        return {"limit": limit, "auto": auto, "reason": reason}

    def _admit(self, run_id: str, ref: str, started: dict) -> asyncio.Future[bool] | None:
        """Take a slot for the run's next turn now (status running, run_started), or queue it
        (status queued, run_queued) and return the future that resolves when its slot is free
        (False: withdrawn by cancel)."""
        if self.parallel.try_acquire(run_id, ref):
            self.store.update_run(run_id, status="running")
            self.events.publish(Event(type="run_started", run_id=run_id, data=started))
            return None
        return self._queue(run_id, ref, started)

    def _queue(self, run_id: str, ref: str, started: dict) -> asyncio.Future[bool]:
        fut = self.parallel.enqueue(run_id, ref)
        self.store.update_run(run_id, status="queued")
        lim = self._limits_for(ref)
        st = self.parallel_status()
        self.events.publish(Event(type="run_queued", run_id=run_id, data={
            **started, "position": self.parallel.position(run_id), "limit": lim.total,
            "reason": st["reason"]}))
        log.info("run %s queued (limit %d)", run_id, lim.total)
        return fut

    async def _await_slot(self, run_id: str, fut: asyncio.Future[bool] | None, started: dict,
                          *, restore: str | None = None) -> bool:
        """Wait for a queued turn's slot; then status running + run_started (or, with
        `restore`, back to that status: the Claude Code session reopens the run itself)."""
        if fut is None:
            return True
        try:
            granted = await fut
        except asyncio.CancelledError:
            if not self.parallel.withdraw(run_id):
                self.parallel.release(run_id)
            raise
        if granted:
            if restore is not None:
                self.store.update_run(run_id, status=restore)
            else:
                self.store.update_run(run_id, status="running")
                self.events.publish(Event(type="run_started", run_id=run_id, data=started))
        return granted

    @contextlib.asynccontextmanager
    async def waiting_on_subagents(self, run_id: str):
        """A run that waits on its subagents gives its slot up meanwhile (no deadlock with a
        limit of 1) and takes it back ahead of queued runs."""
        async with self.parallel.yielding(run_id):
            yield

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
                        edit_scope=("*",), read_scope=("*",), caps: Caps | None = None,
                        parent_id: str | None = None,
                        allowed_classes: set[SafetyClass] | None = None) -> str:
        defn = self.definitions.get(agent)
        if defn is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown agent: {agent}",
                             hint=f"agents: {', '.join(sorted(self.definitions))}")
        prof = self.profile()
        if caps is None:
            caps = Caps(onshape_calls_run=prof.onshape_calls_run_cap,
                        onshape_calls_year=prof.onshape_calls_year_cap)
        ref = self.model_ref_for(defn, model_ref, prof)
        alias = model_alias(ref)
        if alias is not None:
            return await self._start_claude_run(agent, prompt, alias, edit_scope=edit_scope,
                                                read_scope=read_scope, caps=caps,
                                                parent_id=parent_id,
                                                allowed_classes=allowed_classes)
        provider, model = self.provider_resolver(ref)
        if hardware.model_kind(ref) == "local":
            await self.refresh_local_sizes()
        run = self.store.create_run(agent, provider.name, model, prompt, parent_id=parent_id)
        classes = set(SafetyClass) if allowed_classes is None else set(allowed_classes)
        self.store.create_policy(run.id, list(edit_scope), list(read_scope), caps,
                                 [c for c in SafetyClass if c in classes])
        loop = AgentLoop(run_id=run.id, defn=defn, provider=provider, model=model, gate=self.gate,
                         registry=self.registry, store=self.store, events=self.events,
                         workdir=self.workdir(run.id), config=LoopConfig())
        st = self._runs[run.id] = _RunState(loop=loop, ref=ref)
        started = {"agent": agent, "provider": provider.name, "model": model,
                   "parent_id": parent_id}
        slot = self._admit(run.id, ref, started)
        st.task = asyncio.create_task(self._drive_slotted(run.id, prompt, slot, started))
        return run.id

    async def _drive_slotted(self, run_id: str, prompt: str, slot: asyncio.Future[bool] | None,
                             started: dict) -> RunResult:
        """Wait for a slot (queued runs), drive the turn, give the slot back."""
        try:
            granted = await self._await_slot(run_id, slot, started)
        except asyncio.CancelledError:
            self._finish_cancelled(run_id, "run task cancelled")
            raise
        if not granted:
            self._runs[run_id].cancel_requested = True
        try:
            return await self._drive(run_id, prompt)
        finally:
            self.parallel.release(run_id)

    def _finish_cancelled(self, run_id: str, error: str) -> None:
        self.approvals.cancel_run(run_id)
        self.store.release_run_reservations(run_id)
        self.store.update_run(run_id, status="cancelled")
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": "cancelled", "stop_reason": "cancelled", "error": error,
            "final_text": ""}))

    def model_ref_for(self, defn, model_ref: str | None = None, prof=None) -> str:
        """model_ref, else the definition's model (unless "inherit"), else the profile's
        default model, else DEFAULT_MODEL."""
        if model_ref:
            return model_ref
        if defn is not None and defn.model and defn.model != "inherit":
            return defn.model
        prof = prof if prof is not None else self.profile()
        return prof.default_model or DEFAULT_MODEL

    def check_model_ref(self, ref: str) -> None:
        """Raise MisesError if `ref` cannot name a model (claude-code/<alias> or vendor/model)."""
        if model_alias(ref) is None:
            self.provider_resolver(ref)

    # ---- Claude Code runs (the user's own `claude`, through the MCP endpoint and the Gate)
    async def _start_claude_run(self, agent: str, prompt: str, alias: str, *, edit_scope,
                                read_scope, caps: Caps, parent_id: str | None,
                                allowed_classes: set[SafetyClass] | None) -> str:
        url, token = await self._mcp()
        info, auth = await self._claude_ready()
        ref = f"{CLAUDE_CODE}/{alias}"
        rid = self.create_external_run(agent, provider=CLAUDE_CODE, model=alias,
                                       edit_scope=edit_scope, read_scope=read_scope, caps=caps,
                                       allowed_classes=allowed_classes, parent_id=parent_id,
                                       task=prompt, slot_ref=ref)
        st = self._claude[rid] = _ClaudeRun(ref=ref)
        st.slot, st.started_data = self._external_slots.pop(rid, (None, {}))
        try:
            await self._spawn_claude(rid, st, alias, info, auth, url, token, resume=None)
        except BaseException:
            self.parallel.withdraw(rid)
            self.parallel.release(rid)
            if self.store.get_run(rid).status == "queued":
                self.finish_external_run(rid, "failed")
            raise
        if st.cancel_requested:          # cancelled while it started
            await st.session.close()
            return rid
        st.task = asyncio.create_task(self._drive_claude(rid, prompt, first=True))
        return rid

    async def _spawn_claude(self, rid: str, st: _ClaudeRun, alias: str, info, auth, url: str,
                            token: str, *, resume: str | None) -> None:
        s = st.session = ClaudeCodeSession(self, rid, model_alias=alias, info=info, mcp_url=url,
                                           mcp_token=token, resume_session_id=resume,
                                           config_dir=auth.config_dir)
        try:
            await s.start()          # a failure is recorded on the run (error step) and raised
        except BaseException:
            if self._claude.get(rid) is st:
                del self._claude[rid]
            raise
        self.store.update_run(rid, session_ref=s.session_id, owner_pid=os.getpid())

    def _claude_context(self, rid: str) -> dict | None:
        """The app's UI context for the first prompt, when the user has selected something."""
        try:
            ui = ui_context(self)
            if not (ui.selected_part_id or ui.selected_run_id or ui.picks):
                return None
            return build_context(self, rid)
        except Exception:  # noqa: BLE001 - context is a convenience, never a reason to fail
            log.warning("could not build the UI context for run %s", rid, exc_info=True)
            return None

    async def _drive_claude(self, rid: str, text: str, *, first: bool) -> RunResult:
        st = self._claude[rid]
        assert st.session is not None
        slot, st.slot = st.slot, None
        granted = await self._await_slot(rid, slot, st.started_data, restore=st.restore)
        if not granted or st.cancel_requested:
            self.parallel.release(rid)
            if (slot is not None and st.restore is not None
                    and self.store.get_run(rid).status == "queued"):
                self.store.update_run(rid, status=st.restore)   # a cancelled queued follow-up
            result = RunResult(final_text="", messages=[], stop_reason="cancelled",
                               error="run cancelled")
            st.result = result
            return result
        try:
            final = await st.session.prompt(text, context=self._claude_context(rid) if first
                                            else None)
            result = RunResult(final_text=final, messages=[], stop_reason="end_turn")
        except MisesError as e:
            stop = "cancelled" if e.code == ErrorCode.CANCELLED else "error"
            result = RunResult(final_text="", messages=[], stop_reason=stop, error=e.message)
        finally:
            self.parallel.release(rid)
        st.result = result
        return result

    def _is_claude_run(self, run_id: str) -> bool:
        if run_id in self._claude:
            return True
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return False
        return run.external and run.provider == CLAUDE_CODE

    async def resume_run(self, run_id: str) -> None:
        """Re-spawn the user's Claude Code for a Claude Code run with `--resume <session>`
        (after a cancel, a crash or a Mises restart). The run's status changes with its next
        message. No-op while its session is alive."""
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None
        if not (run.external and run.provider == CLAUDE_CODE):
            raise MisesError(ErrorCode.INVALID_INPUT, "only Claude Code runs can be resumed",
                             hint="send a message to continue other runs")
        st = self._claude.get(run_id)
        if st is not None and st.session is not None and st.session.alive:
            return
        if st is not None and st.task is not None and not st.task.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        if run.status == "running" and run_id not in self._external:
            raise MisesError(ErrorCode.INVALID_INPUT, "run is driven by another Mises process",
                             hint="continue it there, or cancel it first")
        if not run.session_ref:
            raise MisesError(ErrorCode.INVALID_INPUT,
                             "this run has no Claude Code session to resume",
                             hint="start a new run")
        url, token = await self._mcp()
        info, auth = await self._claude_ready()
        if st is not None and st.session is not None:
            await st.session.close()     # the dead one: drop its hooks before the new one
        st = self._claude[run_id] = _ClaudeRun()
        await self._spawn_claude(run_id, st, run.model, info, auth, url, token,
                                 resume=run.session_ref)

    async def _send_claude(self, run_id: str, text: str) -> None:
        st = self._claude.get(run_id)
        if st is not None and st.task is not None and not st.task.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        if st is None or st.session is None or not st.session.alive:
            await self.resume_run(run_id)
            st = self._claude[run_id]
        st.cancel_requested = False
        if not st.ref:
            st.ref = f"{CLAUDE_CODE}/{self.store.get_run(run_id).model}"
        # the session marks the run running when its turn begins (reopen_external_run), so
        # a queued follow-up goes back to its previous status once its slot is free
        st.restore = self.store.get_run(run_id).status
        st.started_data = {"resumed": True, "external": True}
        st.slot = (None if self.parallel.try_acquire(run_id, st.ref)
                   else self._queue(run_id, st.ref, st.started_data))
        st.task = asyncio.create_task(self._drive_claude(run_id, text, first=False))

    async def release_claude_session(self, run_id: str) -> None:
        """Close an idle Claude Code session once its result is collected (orchestrated
        children): no `claude` process waits on stdin for a message that never comes. A later
        message resumes it (--resume). No-op for other runs and while a turn is running."""
        st = self._claude.get(run_id)
        if st is None or (st.task is not None and not st.task.done()):
            return
        del self._claude[run_id]
        if st.session is not None:
            try:
                await st.session.close()
            except Exception:  # noqa: BLE001 - the session is dropped either way
                log.warning("closing the Claude Code session of %s failed", run_id,
                            exc_info=True)

    async def close_claude_sessions(self) -> None:
        """Close every live Claude Code session (and stop a sign-in in progress)."""
        h, self._login = self._login, None
        if h is not None and not h.done:
            with contextlib.suppress(Exception):
                await h.cancel()
        states = list(self._claude.values())
        self._claude.clear()
        sessions = [st.session for st in states if st.session is not None]
        for r in await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True):
            if isinstance(r, BaseException):
                log.warning("closing a Claude Code session failed: %r", r)
        tasks = [st.task for st in states if st.task is not None and not st.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    # ---- external runs (an outside agent drives the run through the MCP endpoint)
    def create_external_run(self, agent: str, *, provider: str, model: str, edit_scope=("*",),
                            read_scope=("*",), caps: Caps = Caps(),
                            allowed_classes: set[SafetyClass] | None = None,
                            parent_id: str | None = None, task: str | None = None,
                            slot_ref: str | None = None) -> str:
        """`slot_ref`: the run's first turn takes a parallel-agents slot for that model (the
        user's Claude Code); queued if none is free. ACP runs pass none: their agent is
        already running."""
        if not agent:
            raise MisesError(ErrorCode.INVALID_INPUT, "external run needs an agent name")
        run = self.store.create_run(agent, provider, model, task or f"external agent: {agent}",
                                    parent_id=parent_id, external=True)
        classes = set(SafetyClass) if allowed_classes is None else set(allowed_classes)
        self.store.create_policy(run.id, list(edit_scope), list(read_scope), caps,
                                 [c for c in SafetyClass if c in classes])
        self.workdir(run.id)
        self._external.add(run.id)
        started = {"agent": agent, "provider": provider, "model": model,
                   "parent_id": parent_id, "external": True}
        if slot_ref is None:
            self.events.publish(Event(type="run_started", run_id=run.id, data=started))
        else:
            self._external_slots[run.id] = (self._admit(run.id, slot_ref, started), started)
        return run.id

    def reopen_external_run(self, run_id: str, *,
                            cancel_hook: Callable[[], Awaitable[None]] | None = None,
                            **data) -> bool:
        """A new turn on an external run whose last turn finished (or was cancelled): mark it
        running in this process again, so the MCP endpoint serves its calls and cancel() and
        cancel_all reach its owner. Returns False if it was already running here."""
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None
        if not run.external:
            raise MisesError(ErrorCode.INVALID_INPUT, "not an external run",
                             hint="use send() for runs Mises drives itself")
        if run.status == "running" and run_id in self._external:
            return False
        self.store.update_run(run_id, status="running", owner_pid=os.getpid())
        self._external.add(run_id)
        if cancel_hook is not None:
            self.register_cancel_hook(run_id, cancel_hook)
        self.events.publish(Event(type="run_started", run_id=run_id, data={
            "resumed": True, "external": True, **data}))
        return True

    def register_cancel_hook(self, run_id: str, hook: Callable[[], Awaitable[None]]) -> None:
        """The owner of an external run (e.g. AcpSession) registers how to stop its agent;
        cancel(run_id) and POST /runs/cancel_all then run it."""
        self._cancel_hooks[run_id] = hook

    def unregister_cancel_hook(self, run_id: str) -> None:
        self._cancel_hooks.pop(run_id, None)

    def _run_cancel_hook(self, run_id: str) -> None:
        hook = self._cancel_hooks.pop(run_id, None)
        if hook is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("cannot run the cancel hook of %s: no event loop", run_id)
            return

        async def run() -> None:
            try:
                await hook()
            except Exception:
                log.exception("cancel hook of run %s failed", run_id)
        self._track(loop.create_task(run()))

    def finish_external_run(self, run_id: str, status: str) -> None:
        if status not in ("done", "failed", "cancelled"):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad final status: {status!r}",
                             hint="use done, failed or cancelled")
        try:
            self.store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None
        self.approvals.cancel_run(run_id)
        self.store.release_run_reservations(run_id)
        self.store.update_run(run_id, status=status)
        self._external.discard(run_id)
        self.parallel.withdraw(run_id)
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": status, "stop_reason": status, "error": None, "final_text": "",
            "external": True}))

    async def _drive(self, run_id: str, prompt: str) -> RunResult:
        st = self._runs[run_id]
        try:
            if st.cancel_requested:
                result = RunResult(final_text="", messages=st.history, stop_reason="cancelled",
                                   error="run cancelled")
            else:
                result = await st.loop.run(prompt, st.history)
        except asyncio.CancelledError:
            self._finish_cancelled(run_id, "run task cancelled")
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
        if self._is_claude_run(run_id):
            await self._send_claude(run_id, text)
            return
        if run_id in self._external:
            raise MisesError(ErrorCode.INVALID_INPUT, "run is driven by an external agent",
                             hint="send the message to that agent instead")
        st = self._state(run_id)
        if st.task is not None and not st.task.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        st.cancel_requested = False
        if hardware.model_kind(st.ref) == "local":
            await self.refresh_local_sizes()
        started = {"resumed": True}
        slot = self._admit(run_id, st.ref, started)
        st.task = asyncio.create_task(self._drive_slotted(run_id, text, slot, started))

    async def wait(self, run_id: str) -> RunResult:
        cst = self._claude.get(run_id)
        if cst is not None:
            if cst.task is not None:
                return await asyncio.shield(cst.task)
            # no turn running: cancelled while it started, or resumed without a message
            if cst.result is not None:
                return cst.result
            stop = "cancelled" if cst.cancel_requested else "end_turn"
            return RunResult(final_text="", messages=[], stop_reason=stop,
                             error="run cancelled" if stop == "cancelled" else None)
        if run_id not in self._runs and self._is_claude_run(run_id):
            # a Claude Code run this process no longer holds (released, closed, or another
            # process's): its last turn's outcome is the run's stored status
            status = self.store.get_run(run_id).status
            stop = {"done": "end_turn", "cancelled": "cancelled", "failed": "error"}.get(status)
            if stop is None:
                raise MisesError(ErrorCode.NOT_FOUND, f"run {run_id} is not driven here",
                                 hint="it is running in another Mises process")
            return RunResult(final_text="", messages=[], stop_reason=stop,
                             error=None if stop == "end_turn" else f"run {status}")
        st = self._state(run_id)
        assert st.task is not None
        return await asyncio.shield(st.task)

    def is_live(self, run_id: str) -> bool:
        """True if this process drives the run (internal loop or external)."""
        return run_id in self._runs or run_id in self._external or run_id in self._claude

    def is_cancelling(self, run_id: str) -> bool:
        """True if cancel was requested on an internal run and it has not been resumed since."""
        st = self._runs.get(run_id)
        return st is not None and st.cancel_requested

    def cancel(self, run_id: str) -> None:
        self.parallel.withdraw(run_id)      # a queued run never starts
        if run_id in self._external:
            # mark it cancelled first (the MCP endpoint then refuses its calls), then let the
            # owner stop the agent process
            if run_id in self._claude:
                self._claude[run_id].cancel_requested = True
            self.finish_external_run(run_id, "cancelled")
            self._run_cancel_hook(run_id)
            return
        cst = self._claude.get(run_id)
        if cst is not None:
            # a Claude Code run idle between turns: stop its process; the run keeps the status
            # of its last turn and the next message resumes the session (--resume)
            cst.cancel_requested = True
            self._cancel_hooks.pop(run_id, None)
            if cst.session is not None and cst.session.alive:
                self._track(asyncio.get_running_loop().create_task(cst.session.cancel()))
            return
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
