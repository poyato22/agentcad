"""Runtime: wires store, events, approvals, registry and gate, and drives agent runs."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import hardware, local_models, shape_complexity as shape_complexity_mod
from .agent.definitions import load_definitions
from .agent.loop import AgentLoop, LoopConfig, RunResult
from .approvals import ApprovalService
from .assembly.model import AssemblyStore
from .assembly.internals import register_layout_tools
from .assembly.tools import register_assembly_tools
from .cad.recipes import register_recipe_tools
from .cad.tools import register_mirror_tools
from .cad.views import register_view_tools
from .sim.studies_agent import register_studies_agent_tools
from .catalog import Catalog
from .catalog.tools import register_catalog_tools
from .bom import BomStore
from .bom.tools import register_bom_tools
from .concept import ConceptStore
from .concept.gate import check_call as concept_check_call
from .concept.tools import register_concept_tools
from .browser import images as browser_images
from .browser.snapshot import SnapshotStore
from .browser.tools import CardStore, register_browser_tools
from .imports.drawing_import import register_import_tools
from .appearance import register_appearance_tools
from .mass import register_mass_tools
from .part_tools import register_part_tools
from .engineering import register_engineering_tools
from .sim.tools import register_sim_tools
from .topopt.tools import register_topopt_tools
from .claude_code import detect as claude_detect
from .claude_code.session import MCP_FILE as CLAUDE_MCP_FILE, SOURCE as CLAUDE_CODE
from .claude_code.prompts import context_block
from .claude_code.session import ClaudeCodeSession, model_alias
from .context_tools import build_context, ui_context
from .drawing_pregen import DrawingPregenerator
from .events import ActivityTracker, Event, EventBus
from .gate import Gate
from .onshape import browse as onshape_browse
from .onshape.adapter import OnshapeAdapter
from .onshape.client import OnshapeClient
from .onshape.tools import register_onshape_tools
from .orchestrator import Orchestrator, register_orchestrator_tools
from .orchestrator.orchestrator import DELEGATION_TOOLS, MAX_DEPTH, run_depth
from .parallel import Limits, ParallelLimiter
from .platform import data_home, keychain_get, register_sandbox_home
from .print.tools import register_print_tools
from .profile import load_profile
from .providers.registry import DEFAULT_MODEL, VENDORS, resolve
from .store import Store
from .tools.registry import ToolRegistry
from . import budget as budget_mod
from . import projects as projects_mod
from . import sketch as sketch_mod
from .types import (Block, Caps, ErrorCode, ImageBlock, Message, MisesError, SafetyClass,
                    StepKind, TextBlock)
from .ui_tools import register_ui_tools

log = logging.getLogger(__name__)

#: Tools that plan and coordinate other agents: they need a strong model. Small / local / free
#: models given them wander off into planning instead of building the part, so an agent that is
#: offered every tool (the designer) does not get these on such a model. An agent that lists
#: them by name (the planner) keeps them.
STRONG_MODEL_TOOLS = frozenset({"orchestrator_run", "agent_spawn", "agent_wait", "agent_result",
                                "orchestrator_submit_plan"})


#: `log` key naming the words the design-class classifier may read for this message (the
#: user's own; None or "" for none). Never stored: send / start_run strip it.
CLASSIFY = "_classify"
#: stored-step keys that mark a message whose `text` someone other than the user wrote (a
#: concept pick's label is the agent's option name): classified only via CLASSIFY
_NOT_USER_TEXT = ("concept_choice", "concept_feedback", "system_note")


def _user_words(text: str, user_log: dict | None) -> str:
    """The text the design-class classifier may read: the user's own words, never an agent's
    (Plan 9 G-B I3).
    - `log[CLASSIFY]` set: exactly that (the caller knows which words are the user's; None
      for an agent-to-lead message such as a studies agent's report);
    - a log marked as not the user's text (a concept choice / feedback summary): nothing;
    - a feedback pin's log `text` (the user's note; the message wraps it in agent-written
      part labels): that note;
    - no log: the message itself (the user typed it)."""
    lg = user_log or {}
    if CLASSIFY in lg:
        own = lg[CLASSIFY]
        return own if isinstance(own, str) else ""
    if any(k in lg for k in _NOT_USER_TEXT):
        return ""
    own = lg.get("text")
    return own if isinstance(own, str) and own.strip() else text


def _public_log(user_log: dict | None) -> dict | None:
    """The stored-step overrides without the classifier's private key."""
    if not user_log or CLASSIFY not in user_log:
        return user_log
    rest = {k: v for k, v in user_log.items() if k != CLASSIFY}
    return rest or None


def tools_withheld_for(model_ref: str) -> frozenset[str]:
    """Tools not offered to a run on this model (local Ollama / LM Studio, OpenRouter free)."""
    return STRONG_MODEL_TOOLS if hardware.model_kind(model_ref) in ("local", "free") else frozenset()


#: Vendors whose models can see images, by provider name / the vendor part of a model ref. Used
#: for the critique loop (which model to call) and the G-R6 model warning. Conservative: an
#: OpenRouter or local model is treated as non-vision unless we know otherwise.
VISION_VENDORS = frozenset({"anthropic", "openai", "gemini", CLAUDE_CODE})


#: Text-only models of otherwise vision-capable vendors: sent an image, they reject the request
#: (and every later turn that still carries it), so they must never be treated as seeing.
_TEXT_ONLY = re.compile(r"^(o1-mini|o1-preview|o3-mini|gpt-3\.5|text-|davinci|babbage|tts-|"
                        r"whisper|omni-moderation|gemini-1\.0-pro|gemini-pro$)|embedding|"
                        r"-instruct\b")


def is_vision_model(model_ref: str) -> bool:
    vendor, _, model = model_ref.partition("/")
    if model_ref.startswith("claude-") or vendor == CLAUDE_CODE:
        return True
    if vendor not in VISION_VENDORS:
        return False
    return _TEXT_ONLY.search(model.lower()) is None


USER = "local"

LOGIN_TIMEOUT_S = 600.0            # a `claude auth login` left open in the browser is stopped
# GET /models and GET /setup reuse a `claude auth status` answer this young (the app asks both on
# every screen load); /claude-code/status and run starts always ask afresh
CLAUDE_STATUS_TTL_S = 5.0
# sizes of downloaded Ollama models (for auto parallel agents) are reused this long
LOCAL_SIZES_TTL_S = 60.0
FREE_GROUP = "openrouter:free"

_STATUS = {"end_turn": "done", "max_tokens": "done", "cancelled": "cancelled",
           "error": "failed", "circuit_open": "failed", "max_turns": "failed",
           "budget": "failed"}


@dataclass
class _RunState:
    loop: AgentLoop
    task: asyncio.Task | None = None
    result: RunResult | None = None
    history: list[Message] = field(default_factory=list)
    cancel_requested: bool = False
    ref: str = ""


@dataclass
class UiCall:
    """A tool call the app's own UI started (see Runtime.ui_tool_call)."""
    call_id: str
    run_id: str
    tool: str
    status: str = "running"  # running | done | failed | cancelled
    output: dict | None = None
    error: dict | None = None
    task: asyncio.Task | None = None
    args: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "run_id": self.run_id, "tool": self.tool,
                "status": self.status, "output": self.output, "error": self.error}


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
    first_context: dict | None = None   # start_run(first_context=...): data for the first prompt


class Runtime:
    def __init__(self, home: Path | None = None, *, provider_resolver=resolve):
        self.home = Path(home) if home is not None else data_home()
        # every sandboxed worker denies reads of this home (server.json, mises.db, runs/)
        register_sandbox_home(self.home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.provider_resolver = provider_resolver
        self.store = Store(self.home / "mises.db")
        self.assembly_store = AssemblyStore(self.home / "assembly.db")
        self.events = EventBus()
        self.approvals = ApprovalService(self.store, self.events)
        self.registry = ToolRegistry()
        register_mirror_tools(self.registry)
        register_recipe_tools(self.registry)
        register_sim_tools(self.registry)
        register_onshape_tools(self.registry)
        register_ui_tools(self.registry)
        register_print_tools(self.registry)
        register_engineering_tools(self.registry)
        register_orchestrator_tools(self.registry)
        register_assembly_tools(self.registry)
        register_layout_tools(self.registry)  # Plan 8 G3: skeleton + internals
        register_catalog_tools(self.registry)
        register_browser_tools(self.registry)
        register_bom_tools(self.registry)
        register_import_tools(self.registry)
        register_appearance_tools(self.registry)
        register_mass_tools(self.registry)         # Plan 9 B2
        register_part_tools(self.registry)  # Plan 9 D29
        register_view_tools(self.registry)
        register_topopt_tools(self.registry)  # Plan 9 D14: generative design
        register_studies_agent_tools(self.registry)  # Plan 9 U-D: the studies agent
        register_concept_tools(self.registry)   # Plan 9 G-A: concept sketch + pitch
        from .design_tools import register_design_tools     # Plan 9 G-B
        register_design_tools(self.registry)
        # Plan 10 C3: integrated dynamics and the practical engineering checks
        from .dyn.tools import register_dyn_tools
        from .engineering.eng_tools import register_eng_check_tools
        register_dyn_tools(self.registry)
        register_eng_check_tools(self.registry)
        self.orchestrator = Orchestrator(self)
        self.started_at = time.time()
        self.catalog = Catalog(self.store)
        # the agent browser's snapshots and part cards, and the builds' parts lists (U4c)
        self.snapshots = SnapshotStore(self.store)
        self.part_cards = CardStore(self.store)
        self.bom = BomStore(self.store)
        self.concepts = ConceptStore(self.store)
        services: dict = {"approvals": self.approvals, "runtime": self,
                          "orchestrator": self.orchestrator, "assembly": self.assembly_store,
                          "catalog": self.catalog, "secrets": set(),
                          "browser": self.snapshots, "part_cards": self.part_cards,
                          "bom": self.bom, "concept": self.concepts,
                          # checks every call passes before approval and its handler (G-A's
                          # concept gate); each raises MisesError to refuse the call
                          "call_checks": [concept_check_call]}
        adapter = self._onshape_adapter(keychain_get("mises", "onshape"))
        if adapter is not None:
            services["onshape"] = adapter
        self.gate = Gate(self.registry, self.store, self.approvals, self.events,
                         user=USER, services=services)
        self.definitions = load_definitions([self.home / "agents"])
        for w in self.definitions.warnings:
            log.warning("agent definition skipped: %s", w)
        self._runs: dict[str, _RunState] = {}
        self._sending: set[str] = set()      # send() in progress (the turn has not begun yet)
        self._deleting: set[str] = set()     # delete_run() in progress
        self._external: set[str] = set()
        # the live activity tracker of each run this process drives (GET /runs/{id} `activity`)
        self._activity: dict[str, ActivityTracker] = {}
        self.ui_calls: dict[str, UiCall] = {}
        self._ui_run: str | None = None
        # build id -> that build's long-lived ui run (spec panel apply; Plan 5 A7)
        self._ui_runs: dict[str, str] = {}
        self._questions: dict[str, tuple[str, asyncio.Future[str]]] = {}
        # Plan 7 U3c: messages waiting for a busy lead (orchestrator/lead.py), per lead run, and
        # the per-build locks around find-or-start-lead
        self.lead_queues: dict[str, list] = {}
        self.lead_locks: dict[str, asyncio.Lock] = {}
        self.lead_drainers: dict[str, asyncio.Task] = {}
        # Plan 9 D4: system notes (a background study finished): turns they started since the
        # user's last message, runs whose user pressed Stop (notes wait), and notes that ride
        # along with the next one (orchestrator/lead.py deliver_note)
        self.note_turns: dict[str, int] = {}
        self.note_hold: set[str] = set()
        self.deferred_notes: dict[str, list] = {}
        # question id -> its question_asked payload (+ run_id, asked_at), so a reloaded app can
        # recover the open question cards (GET /runs/{id}/pending; QA C-2)
        self._question_data: dict[str, dict] = {}
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
        # warms the 2D-drawing cache in the background when a part changes (U6); started and
        # stopped by the server lifespan. Half the parallel budget (min 1) bounds the heavy
        # sandboxed drawing workers so pre-gen never crowds out the agents' own work. The cap is
        # read once here: a later max_parallel_agents change applies after a restart.
        self.drawing_pregen = DrawingPregenerator(
            self.home, self.events, material=lambda: self.profile().default_material,
            max_concurrent=max(1, self._total_limit() // 2))
        hardware.machine()          # sysctl once now, not later on the event loop
        self._local_sizes: tuple[float, dict[str, float]] | None = None
        self.interrupted = self._fail_orphaned_runs()
        projects_mod.register_legacy_parts(self.store, self.home)   # once per database
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
            client = OnshapeClient(access, secret, store=self.store, user=USER)
            client.on_request = self.publish_onshape_budget
            return OnshapeAdapter(client, self.store)
        return None

    def onshape_budget(self, run_id: str | None = None) -> dict:
        """The live budget meter (GET /onshape/budget, the `onshape_budget` event)."""
        cap = load_profile(self.home).onshape_calls_year_cap
        return onshape_browse.budget(self.store, USER, cap, run_id=run_id,
                                     session_since=self.started_at)

    def publish_onshape_budget(self, run_id: str | None) -> None:
        """After every real Onshape request (OnshapeClient.on_request)."""
        self.events.publish(Event(type="onshape_budget", run_id=run_id,
                                  data=self.onshape_budget(run_id)))

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

    # ---- builds (Plan 5 C1)
    def active_project_id(self) -> str:
        return projects_mod.active_project_id(self.store, self.profile().active_project_id)

    def project_for(self, project_id: str | None, parent_id: str | None) -> str:
        """Every run gets a build at creation: the one asked for, else its parent run's (an
        orchestrator's workers stay in its build), else the active build."""
        if project_id:
            return project_id
        if parent_id:
            try:
                inherited = self.store.get_run(parent_id).project_id
            except KeyError:
                inherited = None
            if inherited:
                return inherited
        return self.active_project_id()

    def run_busy(self, run_id: str) -> bool:
        """Live work the status column may not show yet: a message still being sent (the turn
        has not begun), or a turn task still running."""
        if run_id in self._sending:
            return True
        cst = self._claude.get(run_id)
        if cst is not None and cst.task is not None and not cst.task.done():
            return True
        st = self._runs.get(run_id)
        return st is not None and st.task is not None and not st.task.done()

    async def delete_run(self, run_id: str) -> tuple[str, list[str]] | None:
        """Delete a finished run: its rows, then its workdir. Refused (returns (reason,
        children)) while it or any descendant is running, busy or awaiting approval. The
        check and the deleting mark happen with no await between them, and send() refuses a
        run being deleted, so a follow-up message cannot slip in. A Claude Code run's idle
        session is closed first. Parts (shared) and Onshape are never touched."""
        run = self.store.get_run(run_id)
        if run_id in self._deleting:
            return "running", []
        blocked = projects_mod.delete_reason(self.store, run, self.run_busy)
        if blocked is not None:
            return blocked
        self._deleting.add(run_id)
        try:
            await self.release_claude_session(run_id)
            if run_id in self._claude:        # a turn started after all: leave it alone
                return "running", []
            self._runs.pop(run_id, None)
            self._external.discard(run_id)
            self.lead_queues.pop(run_id, None)
            browser_images.forget(self.home, self.snapshots.ids_for_run(run_id))
            projects_mod.delete_run_renders(self.home, run_id)
            self.store.delete_run(run_id)
            self.forget_activity(run_id)      # C4's tracker, dropped without a "done" event
            wd = self.home / "runs" / run_id
            try:
                if wd.resolve().parent == (self.home / "runs").resolve() and wd.is_dir():
                    shutil.rmtree(wd, ignore_errors=True)
            except (OSError, RuntimeError):
                log.warning("could not remove workdir for run %s", run_id)
            # QA C-6: other windows (History, the run switcher) drop the run on this
            self.events.publish(Event(type="run_deleted", run_id=run_id,
                                      data={"run_id": run_id,
                                            "project_id": run.project_id}))
        finally:
            self._deleting.discard(run_id)
        return None

    def _busy_for_trash(self, run_id: str) -> bool:
        """run_busy, plus a UI call still in flight on that (ui) run."""
        if self.run_busy(run_id):
            return True
        return any(c.run_id == run_id and c.status == "running" for c in self.ui_calls.values())

    def empty_trash(self, only: str | None = None) -> dict:
        """Purge every trashed build (projects.empty_trash; TrashBusy while one has live work).
        The check and the purge run with no await between them. A purged build's ui run is
        finished and forgotten, so no later UI call lands in a build that no longer exists."""
        out = projects_mod.empty_trash(self.store, self.home, assembly=self.assembly_store,
                                       busy=self._busy_for_trash, only=only,
                                       concepts=self.concepts)
        for pid in out["purged"]:
            rid = self._ui_runs.pop(pid, None)
            if rid is not None and rid in self._external:
                self.finish_external_run(rid, "done")
        return out

    def reload_agents(self) -> None:
        """Re-read the agent definitions after a custom agent was created, edited or deleted."""
        self.definitions = load_definitions([self.home / "agents"])
        for w in self.definitions.warnings:
            log.warning("agent definition skipped: %s", w)

    async def aclose(self) -> None:
        """Shut down: every live Claude Code session is closed (no orphan `claude` process or
        MCP token file), a sign-in in progress is stopped, the Onshape client is closed."""
        await self.close_claude_sessions()
        for c in list(self.ui_calls.values()):
            if c.task is not None and not c.task.done():
                c.task.cancel()
        for t in list(self.lead_drainers.values()):
            t.cancel()
        for rid in [self._ui_run, *self._ui_runs.values()]:
            if rid is not None and rid in self._external:
                self.finish_external_run(rid, "done")
        ad = self.gate.services.get("onshape")
        if ad is not None:
            await ad.client.aclose()

    def register_secrets(self, *tokens: str) -> None:
        """Defense in depth: the gate scrubs these strings (the admin and MCP tokens) from every
        tool result and error, so a build or worker that somehow read one cannot carry it back."""
        secrets_ = self.gate.services.setdefault("secrets", set())
        secrets_.update(t for t in tokens if t)

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
            size = self._local_size(ref)
            budget = hardware.usable_memory_gb()
            # admission budgets what the model needs to run (weights + KV + headroom), not its
            # raw weight size; an unknown size claims the whole budget (runs with no other local)
            need = hardware.model_need_gb(size) if size else budget
            return Limits(total, group=ref,
                          group_cap=hardware.auto_parallel(ref, model_size_gb=size),
                          local_gb=need, budget_gb=budget)
        if kind == "free":
            return Limits(total, group=FREE_GROUP, group_cap=1)
        return Limits(total)

    def parallel_limit(self, ref: str | None = None) -> int:
        """How many runs on `ref` (default: the profile's default model) can work at once."""
        prof = self.profile()
        ref = ref or prof.default_model or DEFAULT_MODEL
        lim = self._limits_for(ref)
        return min(lim.total, lim.group_cap) if lim.group is not None else lim.total

    def parallel_status(self, ref: str | None = None) -> dict:
        """GET /status `parallel`: {limit, auto, reason} for `ref` (default: the profile's
        default model)."""
        prof = self.profile()
        ref = ref or prof.default_model or DEFAULT_MODEL
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
            self.activity_tracker(run_id).set("starting")
            return None
        return self._queue(run_id, ref, started)

    def _queue(self, run_id: str, ref: str, started: dict) -> asyncio.Future[bool]:
        fut = self.parallel.enqueue(run_id, ref)
        self.store.update_run(run_id, status="queued")
        lim = self._limits_for(ref)
        st = self.parallel_status(ref)
        self.events.publish(Event(type="run_queued", run_id=run_id, data={
            **started, "position": self.parallel.position(run_id), "limit": lim.total,
            "reason": st["reason"]}))
        log.info("run %s queued (limit %d)", run_id, lim.total)
        self.activity_tracker(run_id).set("queued")
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
            self.activity_tracker(run_id).set("starting")
        return granted

    @contextlib.asynccontextmanager
    async def waiting_on_subagents(self, run_id: str):
        """A run that waits on its subagents gives its slot up meanwhile (no deadlock with a
        limit of 1) and takes it back ahead of queued runs."""
        async with self.parallel.yielding(run_id,
                                          is_cancelling=lambda: self.is_cancelling(run_id)):
            yield

    # ---- activity
    def activity_tracker(self, run_id: str) -> ActivityTracker:
        """The run's activity tracker (created on first use); it publishes agent_activity /
        agent_thinking and keeps the latest state for GET /runs/{id}."""
        t = self._activity.get(run_id)
        if t is None:
            t = self._activity[run_id] = ActivityTracker(self.events, run_id, self.store)
        return t

    def activity_of(self, run_id: str) -> dict | None:
        """The live state while this process drives the run; "done" (since its last update)
        once it has finished, the tracker having been dropped; None otherwise."""
        t = self._activity.get(run_id)
        if t is not None:
            return t.snapshot()
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return None
        if run.status in ("done", "failed", "cancelled"):
            return {"state": "done", "detail": "", "since": run.updated_at}
        return None

    def _end_activity(self, run_id: str) -> None:
        """A run (or a Claude Code turn) finished: emit "done", then drop its tracker so the
        dict does not grow with every run this process ever drove."""
        self.activity_tracker(run_id).set("done")
        self._activity.pop(run_id, None)

    def forget_activity(self, run_id: str) -> None:
        """Drop a run's tracker without emitting (a deleted run; C1's delete calls this)."""
        self._activity.pop(run_id, None)

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
                        allowed_classes: set[SafetyClass] | None = None,
                        sketch_id: str | None = None, project_id: str | None = None,
                        role: str | None = None, title: str | None = None,
                        routed: dict | None = None, log: dict | None = None,
                        first_context: dict | None = None,
                        images: list[tuple[str, str]] | None = None) -> str:
        """`sketch_id` (from POST /sketches): copy that sketch into the run's workdir and give
        it to the agent as data (plus its images for vision-capable providers).
        Plan 7 U3c: `role` "lead" (the build's main agent: its definition gets the lead prompt
        and tools) or "sub" (a spawned sub-agent, `title` its task), `routed` the router's
        choice; all three are stored on the run.
        `first_context` / `images` (e.g. a drawing import): a data block and (media_type,
        base64) images for the first prompt; images reach vision-capable providers only."""
        defn = self.definitions.get(agent)
        if defn is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown agent: {agent}",
                             hint=f"agents: {', '.join(sorted(self.definitions))}")
        words, log = _user_words(prompt, log), _public_log(log)
        meta = {k: v for k, v in (("role", role), ("title", title), ("routed", routed))
                if v is not None}
        project_id = self.project_for(project_id, parent_id)
        if sketch_id is not None:
            sketch_mod.load_summary(self.home, sketch_id)    # unknown → 404 before any run exists
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
                                                allowed_classes=allowed_classes,
                                                sketch_id=sketch_id, project_id=project_id,
                                                meta=meta, log=log, design_words=words,
                                                first_context=first_context, images=images)
        provider, model = self._resolve_provider(ref, prof)
        if hardware.model_kind(ref) == "local":
            await self.refresh_local_sizes()
        run = self.store.create_run(agent, provider.name, model, prompt, parent_id=parent_id,
                                    project_id=project_id)
        if meta:
            self.store.update_run(run.id, **meta)
        if role == "lead":
            from .orchestrator.lead import lead_definition
            defn = lead_definition(defn)
        shape = self._note_shape_complexity(run.id, prompt)
        design = self._note_design_class(run.id, words, parent_id)
        if parent_id is None and words.strip():   # a sub-agent inherits; only the user's
            self._design_model_rubric(run.id, words, prof)  # own request is rated
        defn = self._design_rules_for(defn, role, design)
        classes = set(SafetyClass) if allowed_classes is None else set(allowed_classes)
        self.store.create_policy(run.id, list(edit_scope), list(read_scope), caps,
                                 [c for c in SafetyClass if c in classes])
        loop = AgentLoop(run_id=run.id, defn=defn, provider=provider, model=model, gate=self.gate,
                         registry=self.registry, store=self.store, events=self.events,
                         workdir=self.workdir(run.id),
                         config=LoopConfig(withheld_tools=self._withheld_tools(ref, parent_id)),
                         activity=self.activity_tracker(run.id))
        st = self._runs[run.id] = _RunState(loop=loop, ref=ref)
        started = {"agent": agent, "provider": provider.name, "model": model,
                   "parent_id": parent_id, "shape_complexity": shape,
                   "design_class": design, **meta}
        slot = self._admit(run.id, ref, started)
        first: str | list[Block] = prompt
        user_log = log      # e.g. a feedback pin's note instead of the wrapped message
        if sketch_id is not None:
            try:
                first, user_log = self._sketch_first_prompt(run.id, sketch_id, prompt, provider.name)
            except BaseException:
                self.parallel.withdraw(run.id)       # the failed run gives its slot / place back
                self.parallel.release(run.id)
                raise
        elif first_context is not None or images:
            first, user_log = self._attached_first_prompt(prompt, first_context, images or [],
                                                          provider.name)
        st.task = asyncio.create_task(self._drive_slotted(run.id, first, slot, started,
                                                          user_log=user_log))
        st.task.add_done_callback(functools.partial(self._slotted_done, run.id))
        return run.id

    def _withheld_tools(self, ref: str, parent_id: str | None) -> frozenset[str]:
        """Tools not offered to a new native run: the model-policy set, plus the delegation
        tools for a worker at the depth limit (check_depth would refuse every call to them)."""
        out = tools_withheld_for(ref)
        if parent_id is not None and run_depth(self, parent_id) + 1 >= MAX_DEPTH:
            out = out | DELEGATION_TOOLS
        return out

    def _slotted_done(self, run_id: str, task: asyncio.Task) -> None:
        """Whatever ended the turn's task (even a cancel before its first step, when no code
        in it ran): its slot or queue place is given back, and a cancelled run is finished."""
        st = self._runs.get(run_id)
        if st is not None and st.task is not task:
            return                       # a newer turn owns the slot now
        self.parallel.withdraw(run_id)
        self.parallel.release(run_id)
        if task.cancelled():
            try:
                status = self.store.get_run(run_id).status
            except KeyError:
                return
            if status in ("queued", "running"):
                self._finish_cancelled(run_id, "run task cancelled")

    async def _drive_slotted(self, run_id: str, prompt: str | list[Block],
                             slot: asyncio.Future[bool] | None, started: dict, *,
                             user_log: dict | None = None) -> RunResult:
        """Wait for a slot (queued runs), drive the turn, give the slot back."""
        try:
            granted = await self._await_slot(run_id, slot, started)
        except asyncio.CancelledError:
            self._finish_cancelled(run_id, "run task cancelled")
            raise
        if not granted:
            self._runs[run_id].cancel_requested = True
        try:
            return await self._drive(run_id, prompt, user_log=user_log)
        finally:
            self.parallel.release(run_id)

    def _finish_cancelled(self, run_id: str, error: str) -> None:
        self.approvals.cancel_run(run_id)
        self.store.release_run_reservations(run_id)
        self.store.update_run(run_id, status="cancelled")
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": "cancelled", "stop_reason": "cancelled", "error": error,
            "final_text": ""}))
        self._end_activity(run_id)

    def _attach_sketch(self, rid: str, sketch_id: str, wd: Path) -> None:
        """Copy the sketch into the new run's workdir; on failure the run is marked failed
        (with an error step and run_finished), never left "running" without its sketch."""
        try:
            sketch_mod.attach_to_run(self.home, sketch_id, wd)
            return
        except MisesError as e:
            err = e
        except Exception as e:  # noqa: BLE001 - e.g. disk full; reported on the run
            err = MisesError(ErrorCode.TOOL_FAILED, f"could not attach the sketch: {e}")
        self._runs.pop(rid, None)
        self._external.discard(rid)
        self.store.append_step(rid, StepKind.ERROR, {
            "stop_reason": "error", "error": err.code.value, "message": err.message,
            "hint": err.hint, "retryable": False})
        self.store.update_run(rid, status="failed")
        self.events.publish(Event(type="run_finished", run_id=rid, data={
            "status": "failed", "stop_reason": "error", "error": err.message, "final_text": ""}))
        raise err

    def _sketch_first_prompt(self, rid: str, sketch_id: str, prompt: str,
                             provider: str) -> tuple[list[Block], dict]:
        """A native run's first message: the sketch as a <mises-context> data block, the
        user's prompt with the staged sketch procedure, then the images (vision providers).
        Also the user step's payload: like a Claude Code run's, the words sent plus the context
        as its own field (no data block in the text, no image bytes)."""
        wd = self.workdir(rid)
        self._attach_sketch(rid, sketch_id, wd)
        vision = provider in sketch_mod.VISION_PROVIDERS
        ctx = {"sketch": sketch_mod.sketch_context(wd, vision=vision)}
        words = f"{prompt}\n\n{sketch_mod.SKETCH_PROCEDURE}"
        blocks: list[Block] = [TextBlock(text=f"{context_block(ctx)}\n{words}")]
        if vision:
            blocks += [ImageBlock(media_type=m, data_b64=d)
                       for m, d in sketch_mod.sketch_images(wd)]
        log = {"text": words, "context": ctx, "images": len(blocks) - 1}
        return blocks, log

    @staticmethod
    def _attached_first_prompt(prompt: str, ctx: dict | None, images: list[tuple[str, str]],
                               provider: str) -> tuple[list[Block], dict]:
        """A native run's first message with a data block and images (start_run first_context /
        images), and the user step's payload (the words, the context as its own field)."""
        text = f"{context_block(ctx)}\n{prompt}" if ctx is not None else prompt
        blocks: list[Block] = [TextBlock(text=text)]
        if provider in sketch_mod.VISION_PROVIDERS:
            blocks += [ImageBlock(media_type=m, data_b64=d) for m, d in images]
        return blocks, {"text": prompt, "context": ctx, "images": len(blocks) - 1}

    def model_ref_for(self, defn, model_ref: str | None = None, prof=None) -> str:
        """model_ref, else the definition's model (unless "inherit"), else the profile's
        default model, else DEFAULT_MODEL."""
        if model_ref:
            return model_ref
        if defn is not None and defn.model and defn.model != "inherit":
            return defn.model
        prof = prof if prof is not None else self.profile()
        return prof.default_model or DEFAULT_MODEL

    def _note_shape_complexity(self, run_id: str, prompt: str) -> dict:
        """Estimate how shape-heavy the request is (G-R6), store it on the run and announce it,
        so routing can prefer a vision-capable strong model and the UI can warn. Never fatal."""
        est = shape_complexity_mod.estimate(prompt)
        try:
            self.store.set_shape_complexity(run_id, est)
        except Exception:  # noqa: BLE001 - a routing hint must never break a run
            log.debug("could not store shape_complexity for %s", run_id, exc_info=True)
        return est

    def _user_design_class(self, run_id: str) -> dict | None:
        """The class the user set on the run's build (PATCH /projects/{id}), if any."""
        from . import design_intensity as design_mod
        try:
            pid = self.store.get_run(run_id).project_id
            cls = self.store.project_design_class(pid) if pid else None
        except Exception:  # noqa: BLE001
            return None
        return design_mod.user_override(cls) if cls else None

    def _note_design_class(self, run_id: str, prompt: str, parent_id: str | None = None) -> dict:
        """Classify how design-heavy the request is (D27) and store it on the run as
        `design_class`. The user's own class on the build always wins; a sub-agent takes its
        lead's class as is (its prompt is agent-written, so it never moves the class); only a
        top-level run classifies its (user-written) prompt. Never fatal."""
        from . import design_intensity as design_mod
        try:
            parent = self.store.get_run(parent_id).design_class if parent_id else None
        except Exception:  # noqa: BLE001
            parent = None
        dc = (self._user_design_class(run_id) or parent
              or design_mod.design_intensity("" if parent_id else prompt))
        try:
            self.store.set_design_class(run_id, dc)
        except Exception:  # noqa: BLE001 - a process hint must never break a run
            log.debug("could not store design_class for %s", run_id, exc_info=True)
        return dc

    def _design_model_rubric(self, run_id: str, prompt: str, prof) -> None:
        """The optional cheap-model rubric (profile `design_classifier_model`, e.g. a Haiku),
        off the critical path: a background task bounded to 2 s whose usage is billed to the
        run. It may raise the stored class for later turns and the critique, never lower it,
        and never past a user-set class or the user's "keep it plain"."""
        ref = getattr(prof, "design_classifier_model", None)
        if not ref or model_alias(ref) is not None:
            return
        from . import design_intensity as design_mod

        async def rate() -> None:
            try:
                provider, model = self._resolve_provider(ref, prof)
                score = await asyncio.wait_for(design_mod.model_score(
                    provider, model, prompt,
                    on_usage=lambda u: self.store.add_usage(
                        run_id, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                        usd=max(0.0, u.usd or 0.0))), 2.0)
                cur = self.store.get_run(run_id).design_class
                if cur and cur.get("source") != "user":
                    new = design_mod.combine(cur, score)
                    if new is not cur:
                        self.store.set_design_class(run_id, new)
            except Exception:  # noqa: BLE001 - never fatal
                log.debug("design classifier model unavailable", exc_info=True)
        task = asyncio.get_running_loop().create_task(rate())
        self._bg_tasks = getattr(self, "_bg_tasks", set())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def set_build_design_class(self, project_id: str, cls: str | None) -> dict | None:
        """PATCH /projects/{id} {design_class}: the user's class for the build (None hands it
        back to the classifier). Every run of the build takes it now, and a live native loop's
        prompt is refreshed; G-A's concept gate reads it from the runs.

        Setting it keeps each run's classifier result (meta `design_class_auto:<run>`); "auto"
        restores exactly that (with the user's follow-up raises and lowers), never
        reclassifying agent-written text (a run's `task` may be a feedback pin's wrapped
        message or a sub-agent's brief). A run started while the override was on: a top-level
        run is classified from its first user step's own words, a sub-agent inherits its
        parent's restored class. Returns the lead's class now (the user's, or the restored
        classifier result for "auto")."""
        import json as _json

        from . import design_intensity as design_mod
        self.store.set_project_design_class(project_id, cls)
        runs = self.store.list_runs(10_000, project_id=project_id)
        ids = {r.id for r in runs}
        ordered, placed = [], set()
        while len(ordered) < len(runs):          # parents before their sub-agents
            before = len(ordered)
            for r in runs:
                if r.id not in placed and (r.parent_id not in ids or r.parent_id in placed):
                    ordered.append(r)
                    placed.add(r.id)
            if len(ordered) == before:           # a cycle (never expected): keep the rest
                ordered += [r for r in runs if r.id not in placed]
                break
        out, restored = None, {}
        for run in ordered:
            key = f"design_class_auto:{run.id}"
            cur = run.design_class
            if cls:
                if cur and cur.get("source") != "user":
                    self.store.meta_set(key, _json.dumps(cur))
                dc = design_mod.user_override(cls, (cur or {}).get("archetype"))
            else:
                saved = self.store.meta_get(key)
                if saved:
                    dc = _json.loads(saved)
                elif run.parent_id:
                    dc = restored.get(run.parent_id) or design_mod.design_intensity("")
                else:
                    dc = design_mod.design_intensity(self._first_user_words(run.id))
                self.store.meta_delete(key)
            restored[run.id] = dc
            self.store.set_design_class(run.id, dc)
            self._refresh_design_rules(run.id, dc)
            if run.role == "lead" or out is None:
                out = dc
        return out

    def _first_user_words(self, run_id: str) -> str:
        """The user's own words in a run's first user step ("" when that step is not the
        user's: a concept pick, an agent's message)."""
        try:
            steps = self.store.steps(run_id, [StepKind.MESSAGE])
        except Exception:  # noqa: BLE001
            return ""
        for s in steps:
            if s.payload.get("role") == "user":
                return _user_words(str(s.payload.get("text") or ""), s.payload)
        return ""

    def _refresh_design_rules(self, run_id: str, dc: dict) -> None:
        st = self._runs.get(run_id)
        if st is None:
            return
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return
        from .orchestrator.lead import lead_definition
        base = self.definitions.get(run.agent)
        if base is not None:
            base = lead_definition(base) if run.role == "lead" else base
            st.loop.defn = self._design_rules_for(base, run.role, dc)

    @staticmethod
    def _design_rules_for(defn, role: str | None, dc: dict | None):
        from .design_intensity import rules_for
        return rules_for(defn, role, dc)

    def run_sees_images(self, run_id: str | None) -> bool:
        """Whether the model driving `run_id` can look at images itself: a Claude Code run, an
        external MCP agent on a vision vendor, or a native run on a vision-capable model. Tools
        then hand renders straight back to it (MCP image content / provider image blocks)."""
        if not run_id:
            return False
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return False
        if run.provider == "ui":
            return False
        st = self._runs.get(run_id)
        return is_vision_model(st.ref if st is not None else f"{run.provider}/{run.model}")

    #: how long a design critique waits for its judge sub-agent before reporting "no judge"
    JUDGE_TIMEOUT_S = 90.0

    async def judge_subagent(self, run_id: str, system: str, text: str,
                             images: list[tuple[str, str]]) -> str | None:
        """Plan 9 G-B (I6): a separate design judge for a run whose own model is not reachable
        as a plain API call (a Claude Code run). The judge is a fresh sub-agent of the run on
        the run's OWN runtime and model, with no tools (no action class allowed, empty scopes),
        given only the rubric, the request and the renders (images). Returns its final text,
        or None when it could not run (no session, an error, the timeout)."""
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return None
        st = self._runs.get(run_id)
        ref = st.ref if st is not None else f"{run.provider}/{run.model}"
        prompt = (system + "\n\n---\n\n" + text +
                  "\n\nYou have no tools in this run: look at the attached renders and reply "
                  "with the one JSON object only.")
        rid = None
        try:
            # the parent gives its parallel slot up while its judge runs (as for any sub-agent
            # it waits on): with a limit of 1 the judge would otherwise queue behind it forever
            async with self.waiting_on_subagents(run_id):
                rid = await self.start_run("verifier", prompt, model_ref=ref, parent_id=run_id,
                                           role="sub", title="Design judge", edit_scope=(),
                                           read_scope=(), allowed_classes=set(),
                                           project_id=run.project_id, images=images)
                res = await asyncio.wait_for(self.wait(rid), self.JUDGE_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - no judge: the critique says so
            log.warning("design judge sub-agent for %s failed", run_id, exc_info=True)
            if rid is not None:
                with contextlib.suppress(Exception):
                    self.cancel(rid)
            return None
        return res.final_text if res.stop_reason == "end_turn" and res.final_text else None

    def vision_model_for(self, run_id: str):
        """(provider, model, reason) for a critique of `run_id`: the run's own model when it can
        see images, else a configured vision-capable model, else (None, None, reason)."""
        st = self._runs.get(run_id)
        if st is not None and is_vision_model(st.ref):
            return st.loop.provider, st.loop.model, None
        prof = self.profile()
        fallback = prof.default_model or DEFAULT_MODEL
        if is_vision_model(fallback) and model_alias(fallback) is None:
            try:
                provider, model = self._resolve_provider(fallback, prof)
                return provider, model, None
            except MisesError as e:
                return None, None, f"no vision model available: {e.message}"
        own = st.ref if st is not None else "this run's model"
        return (None, None, f"{own} cannot see images and no vision-capable model is configured "
                "(set a vision model such as an Anthropic or OpenAI model in Profile → Model)")

    def _resolve_provider(self, ref: str, prof):
        """Resolve a provider, letting the real registry honour the profile's local endpoints
        (Ollama/LM Studio base URLs). Test doubles keep the simple `(ref)` signature."""
        if self.provider_resolver is resolve:
            return resolve(ref, local_endpoints=prof.local_endpoints)
        return self.provider_resolver(ref)

    def check_model_ref(self, ref: str) -> None:
        """Raise MisesError if `ref` cannot name a model (claude-code/<alias> or vendor/model)."""
        if model_alias(ref) is None:
            self.provider_resolver(ref)

    # ---- Claude Code runs (the user's own `claude`, through the MCP endpoint and the Gate)
    async def _start_claude_run(self, agent: str, prompt: str, alias: str, *, edit_scope,
                                read_scope, caps: Caps, parent_id: str | None,
                                allowed_classes: set[SafetyClass] | None,
                                sketch_id: str | None = None,
                                project_id: str | None = None,
                                meta: dict | None = None, log: dict | None = None,
                                design_words: str | None = None,
                                first_context: dict | None = None,
                                images: list[tuple[str, str]] | None = None) -> str:
        url, token = await self._mcp()
        info, auth = await self._claude_ready()
        ref = f"{CLAUDE_CODE}/{alias}"
        rid = self.create_external_run(agent, provider=CLAUDE_CODE, model=alias,
                                       edit_scope=edit_scope, read_scope=read_scope, caps=caps,
                                       allowed_classes=allowed_classes, parent_id=parent_id,
                                       task=prompt, slot_ref=ref, project_id=project_id)
        if meta:     # before the session starts: its rules file reads the run's role
            self.store.update_run(rid, **meta)
        self._note_shape_complexity(rid, prompt)
        # before the session starts too: its rules file carries the design-class block
        words = _user_words(prompt, log) if design_words is None else design_words
        self._note_design_class(rid, words, parent_id)
        if parent_id is None and words.strip():
            self._design_model_rubric(rid, words, self.profile())
        st = self._claude[rid] = _ClaudeRun(ref=ref, first_context=first_context)
        st.slot, st.started_data = self._external_slots.pop(rid, (None, {}))
        text = prompt
        try:
            if sketch_id is not None:
                wd = self.workdir(rid)
                self._attach_sketch(rid, sketch_id, wd)
                text = f"{prompt}\n\n{sketch_mod.SKETCH_PROCEDURE}"
                images = sketch_mod.sketch_images(wd)      # Claude Code sees images
            await self._spawn_claude(rid, st, alias, info, auth, url, token, resume=None)
        except BaseException:
            self.parallel.withdraw(rid)
            self.parallel.release(rid)
            if self.store.get_run(rid).status == "queued":
                self.finish_external_run(rid, "failed")
            raise
        if st.cancel_requested:          # cancelled while it started
            self.parallel.withdraw(rid)
            self.parallel.release(rid)
            await st.session.close()
            return rid
        st.task = asyncio.create_task(self._drive_claude(rid, text, first=True, images=images,
                                                         log=log))
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
            if not (ui.selected_part_id or ui.selected_run_id or ui.picks
                    or sketch_mod.has_sketch(self.home / "runs" / rid)):
                return None
            return build_context(self, rid)
        except Exception:  # noqa: BLE001 - context is a convenience, never a reason to fail
            log.warning("could not build the UI context for run %s", rid, exc_info=True)
            return None

    async def _drive_claude(self, rid: str, text: str, *, first: bool,
                            images: list[tuple[str, str]] | None = None,
                            log: dict | None = None) -> RunResult:
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
            context = self._claude_context(rid) if first else None
            if first and st.first_context is not None:
                context = {**(context or {}), **st.first_context}
            final = await st.session.prompt(text, context=context, images=images, log=log)
            result = RunResult(final_text=final, messages=[], stop_reason="end_turn")
        except MisesError as e:
            stop = "cancelled" if e.code == ErrorCode.CANCELLED else "error"
            result = RunResult(final_text="", messages=[], stop_reason=stop, error=e.message)
        finally:
            self.parallel.release(rid)
        st.result = result
        if result.stop_reason == "end_turn" and not st.cancel_requested:
            self._finish_checks(rid)         # Plan 10 C3: the engineering "done" gate
            # a Claude Code turn ended but the session stays open for the next message
            self.activity_tracker(rid).set("idle", "waiting for your next message")
        return result

    def _finish_checks(self, run_id: str) -> None:
        """A lead's turn ended: warn (a VERIFY step) when its build has moving joints and its
        engineering checks are missing, failing or stale (engineering/pipeline.py)."""
        from .engineering.pipeline import finish_warning
        text = finish_warning(self, run_id)
        if text:
            self.events.publish(Event(type="engineering_warning", run_id=run_id,
                                      data={"text": text}))

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

    async def _send_claude(self, run_id: str, text: str, log: dict | None = None,
                           images: list[tuple[str, str]] | None = None) -> None:
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
        st.task = asyncio.create_task(self._drive_claude(run_id, text, first=False, log=log,
                                                         images=images or None))

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
                            slot_ref: str | None = None, project_id: str | None = None) -> str:
        """`slot_ref`: the run's first turn takes a parallel-agents slot for that model (the
        user's Claude Code); queued if none is free. ACP runs pass none: their agent is
        already running."""
        if not agent:
            raise MisesError(ErrorCode.INVALID_INPUT, "external run needs an agent name")
        run = self.store.create_run(agent, provider, model, task or f"external agent: {agent}",
                                    parent_id=parent_id, external=True,
                                    project_id=self.project_for(project_id, parent_id))
        classes = set(SafetyClass) if allowed_classes is None else set(allowed_classes)
        self.store.create_policy(run.id, list(edit_scope), list(read_scope), caps,
                                 [c for c in SafetyClass if c in classes])
        self.workdir(run.id)
        self._external.add(run.id)
        if provider == "ui":    # I5: the gate scrubs these ids from every agent's results
            self.gate.services.setdefault("ui_run_ids", set()).add(run.id)
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
        self.parallel.release(run_id)       # idempotent; a turn's own finally releases too
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": status, "stop_reason": status, "error": None, "final_text": "",
            "external": True}))
        self._end_activity(run_id)

    def _ui_run_id(self, project_id: str | None = None) -> str:
        """The one long-lived external run every UI-started tool call goes through (created on
        first use, recreated if it was finished or failed). Finished by aclose(). With
        `project_id`: that build's own ui run (tools that check the run's build, e.g. spec and
        study tools, see the right one)."""
        rid = self._ui_runs.get(project_id) if project_id else self._ui_run
        if rid is not None:
            try:
                if self.store.get_run(rid).status == "running" and rid in self._external:
                    return rid
            except KeyError:
                pass
        rid = self.create_external_run("you", provider="ui", model="ui",
                                       task="actions started from the app", project_id=project_id)
        if project_id:
            self._ui_runs[project_id] = rid
        else:
            self._ui_run = rid

        async def stop_all() -> None:  # run-level cancel (cancel_all): stop every UI call
            for c in list(self.ui_calls.values()):
                if c.task is not None and not c.task.done():
                    c.task.cancel()
        self.register_cancel_hook(rid, stop_all)
        return rid

    def ui_project_for(self, args: dict, meta: dict | None = None) -> str | None:
        """The build a UI-started tool call belongs to, so every UI call (C6 edits, print, A6
        imports and inserts, A7 spec apply) goes through the same per-build ui run."""
        for pid in (args.get("project_id"), (meta or {}).get("project_id")):
            if isinstance(pid, str) and pid:
                try:
                    return self.store.get_project(pid).id
                except KeyError:
                    pass
        part_id = args.get("part_id")
        if isinstance(part_id, str):
            row = self.store.get_part(part_id)
            if row is not None:
                return row.project_id
        try:
            return self.active_project_id()
        except Exception:  # noqa: BLE001 - no build resolvable: the legacy shared ui run
            return None

    def ui_tool_call(self, tool: str, args: dict, *, meta: dict | None = None,
                     project_id: str | None = None) -> UiCall:
        """Run a tool for the app's UI through the Gate, under the shared "ui" run, so the
        same checks apply as for an agent: an irreversible tool raises the approval card and
        nothing happens until the user approves it. Returns at once; poll `ui_calls`.
        `meta` is trusted app-side data the tool may read (agents cannot set it).
        Every UI call rides its BUILD's ui run (A7's per-build helper): `project_id` when given,
        else the one the call names, else the part's build, else the active build."""
        rid = self._ui_run_id(project_id or self.ui_project_for(args, meta))
        call = UiCall(call_id="ui_" + uuid.uuid4().hex[:12], run_id=rid, tool=tool,
                      args=dict(args), meta=dict(meta or {}))
        self.ui_calls[call.call_id] = call

        async def go() -> None:
            try:
                out = await self.gate.call(rid, tool, args, call_id=call.call_id,
                                           workdir=self.workdir(rid))
                call.output = out.model_dump(mode="json")
                call.status = "done" if out.ok else "failed"
            except asyncio.CancelledError:
                call.status = "cancelled"
                call.error = MisesError(ErrorCode.CANCELLED, "cancelled").to_dict()
            except MisesError as e:
                call.status = "failed"
                call.error = e.to_dict()
            except Exception as e:  # noqa: BLE001
                call.status = "failed"
                call.error = MisesError(ErrorCode.TOOL_FAILED, f"{type(e).__name__}: {e}").to_dict()

        call.task = asyncio.get_running_loop().create_task(go())
        self._track(call.task)
        return call

    def cancel_ui_call(self, call_id: str) -> None:
        """Cancel one UI-started call: its task, and its own pending approval card. Other calls
        (and their cards) under the same run are untouched."""
        call = self.ui_calls.get(call_id)
        if call is None or call.status != "running" or call.task is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no running UI call {call_id!r}")
        call.task.cancel()
        for a in self.store.pending_approvals(call.run_id):
            if a.action == call.tool and all(a.payload.get(k) == v for k, v in call.args.items()):
                self.approvals.decide(a.id, a.payload_hash, False, decided_by="system:cancelled")

    async def _drive(self, run_id: str, prompt: str | list[Block], *,
                     user_log: dict | None = None) -> RunResult:
        st = self._runs[run_id]
        try:
            if st.cancel_requested:
                result = RunResult(final_text="", messages=st.history, stop_reason="cancelled",
                                   error="run cancelled")
            elif user_log:
                result = await st.loop.run(prompt, st.history, log=user_log)
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
        if result.stop_reason == "end_turn":
            self._finish_checks(run_id)      # Plan 10 C3: the engineering "done" gate
        status = _STATUS[result.stop_reason]
        self.store.update_run(run_id, status=status)
        self.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": status, "stop_reason": result.stop_reason, "error": result.error,
            "final_text": result.final_text}))
        self._end_activity(run_id)
        return result

    def _state(self, run_id: str) -> _RunState:
        st = self._runs.get(run_id)
        if st is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}")
        return st

    async def send(self, run_id: str, text: str, *, log: dict | None = None,
                   images: list[tuple[str, str]] | None = None,
                   user_turn: bool = True) -> None:
        """`log` overrides the stored user step's fields (e.g. a feedback pin's note instead of
        the wrapped message the model reads). `images` ((media_type, base64) pairs, e.g. a
        concept sketch with the user's marks drawn on it) go with the message to a model that
        can see images. `user_turn=False` (a system note): the turn it starts is not a new
        user turn, so the user message's budget window goes on."""
        if run_id in self._deleting:
            raise MisesError(ErrorCode.NOT_FOUND, f"run {run_id} is being deleted")
        if run_id in self._sending:
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        busy = self.run_busy(run_id)
        words, log = _user_words(text, log), _public_log(log)
        self._sending.add(run_id)
        try:
            if not busy and user_turn:
                self.new_user_turn(run_id)
                if words.strip():      # only the user's own words move the class (G-B I3)
                    self._raise_design_class(run_id, words)
            elif user_turn:     # joins a running turn: still the user, so notes may wake it
                self.note_turns.pop(run_id, None)
                self.note_hold.discard(run_id)
            # the plain two-argument call when there is no log (tests patch _send that way)
            if images:
                await self._send(run_id, text, log, images)
            else:
                await (self._send(run_id, text) if log is None else self._send(run_id, text, log))
        finally:
            self._sending.discard(run_id)

    def new_user_turn(self, run_id: str) -> None:
        """The one hook for "a new user turn begins on this run" (a message to an idle run, or
        a queued message the lead's drainer delivers): everything scoped to a user turn starts
        afresh here.
        - the render-critique loop's budgets (clock, rounds, image tokens), so a long-lived
          lead's follow-up request gets its own loop (fix-bench, final review I2);
        - a build lead's caps (dollars, Onshape calls, web fetches, spawns) open a new window,
          counting per message, not over the build (fix-final-core C1, budget.start_turn).
        A message that joins a turn still running is not a new turn: nothing resets."""
        from .cad.views import reset_loop
        reset_loop(run_id)
        self.note_turns.pop(run_id, None)        # the user is back: notes may start turns again
        self.note_hold.discard(run_id)
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return
        if run.role == "lead":
            budget_mod.start_turn(self.store, run_id)

    def _raise_design_class(self, run_id: str, text: str) -> None:
        """A follow-up message may change the class (design_intensity.follow_up): it rises on
        an archetype or styling signal ("now make it sleek"), falls when the user asks for
        plain geometry ("keep it simple, just functional"), and never moves a class the user
        set on the build. A native lead's prompt picks up the new block on its next turn (a
        Claude Code session's rules file is written when the session starts)."""
        from . import design_intensity as design_mod
        try:
            run = self.store.get_run(run_id)
        except KeyError:
            return
        before = self._user_design_class(run_id) or run.design_class
        dc = design_mod.follow_up(before, text)
        if dc is None or dc == run.design_class:
            return
        try:
            self.store.set_design_class(run_id, dc)
        except Exception:  # noqa: BLE001
            return
        if (before or {}).get("cls") != dc.get("cls"):
            self._refresh_design_rules(run_id, dc)

    async def _send(self, run_id: str, text: str, log: dict | None = None,
                    images: list[tuple[str, str]] | None = None) -> None:
        if self._is_claude_run(run_id):
            await (self._send_claude(run_id, text, log, images) if images
                   else self._send_claude(run_id, text, log))
            return
        if run_id in self._external:
            raise MisesError(ErrorCode.INVALID_INPUT, "run is driven by an external agent",
                             hint="send the message to that agent instead")
        st = self._state(run_id)
        if st.task is not None and not st.task.done():
            raise MisesError(ErrorCode.INVALID_INPUT, "run is still working",
                             hint="wait for it to finish or cancel it")
        st.cancel_requested = False
        st.loop.activity = self.activity_tracker(run_id)   # the last turn's tracker was dropped
        if hardware.model_kind(st.ref) == "local":
            await self.refresh_local_sizes()
        started = {"resumed": True}
        slot = self._admit(run_id, st.ref, started)
        prompt: str | list[Block] = text
        if images and self.run_sees_images(run_id):
            # images before the text that asks about them (Claude vision guidance)
            prompt = [*(ImageBlock(media_type=m, data_b64=d) for m, d in images),
                      TextBlock(text=text)]
            log = {"text": text, **(log or {}), "images": len(images)}
        st.task = asyncio.create_task(self._drive_slotted(run_id, prompt, slot, started,
                                                          user_log=log))
        st.task.add_done_callback(functools.partial(self._slotted_done, run_id))

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
        self.stop_background(run_id)
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

    def stop_background(self, run_id: str) -> None:
        """Stop reaches the background jobs (studies, reviews, design studies) of the run and
        of every run below it, and no note wakes them until the user writes again."""
        from . import jobs
        try:
            fam = {run_id, *self.store.descendants(run_id)}
        except Exception:  # noqa: BLE001
            fam = {run_id}
        self.note_hold.update(fam)
        jobs.cancel_run(fam)

    # ---- questions (ui_ask_user)
    async def ask(self, run_id: str, question: str, options: list[str], *,
                  questions: list[dict] | None = None) -> str:
        """`questions` (optional) is a batch of up to 3 structured questions; a UI that knows
        them answers with a JSON object {id: value}, an older one with plain text."""
        qid = uuid.uuid4().hex[:12]
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._questions[qid] = (run_id, fut)
        data = {"question_id": qid, "question": question, "options": list(options)}
        if questions:
            data["questions"] = list(questions)
        self._question_data[qid] = {**data, "run_id": run_id, "asked_at": time.time()}
        self.events.publish(Event(type="question_asked", run_id=run_id, data=data))
        tracker = self.activity_tracker(run_id)
        prev_state, prev_detail = tracker.state, tracker.detail
        tracker.set("waiting_user", "waiting for your answer")
        try:
            return await fut
        finally:
            self._questions.pop(qid, None)
            self._question_data.pop(qid, None)
            if prev_state is not None:          # restore the state the run was in before asking
                tracker.set(prev_state, prev_detail)

    def pending_questions(self, run_id: str | None = None) -> list[dict]:
        """The open ui_ask_user questions (of one run), oldest first, each the question_asked
        payload plus run_id and asked_at."""
        return sorted((dict(d) for qid, d in self._question_data.items()
                       if (run_id is None or d["run_id"] == run_id)
                       and qid in self._questions and not self._questions[qid][1].done()),
                      key=lambda d: d["asked_at"])

    def answer(self, question_id: str, text: str) -> None:
        entry = self._questions.get(question_id)
        if entry is None or entry[1].done():
            raise MisesError(ErrorCode.NOT_FOUND, f"no open question {question_id}")
        run_id, fut = entry
        fut.set_result(text)
        self.events.publish(Event(type="question_answered", run_id=run_id,
                                  data={"question_id": question_id}))
