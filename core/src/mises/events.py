"""In-process event bus with per-subscriber bounded queues, plus the agent-activity tracker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel, Field, computed_field

from .types import StepKind

if TYPE_CHECKING:
    from .store import Store


log = logging.getLogger(__name__)


# ---- streams (Plan 9 D4): agent output comes out in three parallel streams the app shows at
# once: "sim" (studies), "view" (renders, immersive views, critiques) and "text" (the rest).
STREAMS = ("text", "sim", "view")
SIM_TOOLS = frozenset({"design_review", "design_review_get", "study_run", "study_get",
                       "job_wait",
                       # Plan 11 F6: the rocket cards (rocket_motor is a library lookup: text)
                       "rocket_analyze", "rocket_flight"})
VIEW_TOOLS = frozenset({"render_views", "present_view", "critique_model", "part_show",
                        "section_check", "assembly_animate",
                        # Plan 9 G-A: the concept stage's cards live in the View stream
                        "concept_sketch", "pitch_options"})
SIM_EVENTS = frozenset({"sim_progress", "sim_finished", "study_progress", "topopt_progress",
                        "topopt_finished"})
VIEW_EVENTS = frozenset({"part_shown", "assembly_animate"})
#: announcement cards that are pictures of the model (the rest are conversation cards). G-A's
#: concept sketch and pitch cards are pictures of the design, so they ride the View stream too.
VIEW_ANNOUNCE = frozenset({"view_presented", "critique_round", "part_shown",
                           "concept_sketch", "concept_pitch"})


def tool_stream(name: str | None) -> str:
    bare = (name or "").split("mcp__mises__", 1)[-1]
    if bare.startswith(("sim_", "topopt_", "dyn_", "assembly_study")) or bare in SIM_TOOLS:
        return "sim"
    return "view" if bare in VIEW_TOOLS else "text"


def stream_of(event_type: str, data: dict | None = None) -> str:
    """The stream an event (or a step, by its kind and payload) belongs to."""
    # an explicit stream hint on the payload wins (topopt_accepted, studies_proposed mark "sim")
    if isinstance(data, dict) and data.get("stream") in STREAMS:
        return data["stream"]
    if event_type in SIM_EVENTS or event_type == "sim_run":
        return "sim"
    if event_type in VIEW_EVENTS or event_type.startswith("view_"):
        return "view"
    data = data or {}
    card = data if event_type == "agent_announce" else data.get("agent_announce")
    if isinstance(card, dict) and card.get("kind") in VIEW_ANNOUNCE:
        return "view"
    name = data.get("name")
    return tool_stream(name) if isinstance(name, str) else "text"


class Event(BaseModel):
    type: str
    run_id: str | None = None
    data: dict = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)

    @computed_field
    @property
    def stream(self) -> str:
        return stream_of(self.type, self.data)


class EventBus:
    QUEUE_MAX = 1000

    def __init__(self) -> None:
        self._subs: list[tuple[str | None, asyncio.Queue[Event]]] = []

    def publish(self, event: Event) -> None:
        for run_id, q in list(self._subs):
            if run_id is not None and event.run_id != run_id:
                continue
            while True:
                try:
                    q.put_nowait(event)
                    break
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

    def subscribe(self, run_id: str | None = None) -> "Subscription":
        """Register eagerly; events published after this call are never lost."""
        sub = Subscription(self, run_id)
        self._subs.append((run_id, sub.queue))
        return sub


class Subscription:
    def __init__(self, bus: EventBus, run_id: str | None):
        self._bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=EventBus.QUEUE_MAX)
        self._entry = (run_id, self.queue)
        self._closed = False

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Event:
        if self._closed:
            raise StopAsyncIteration
        return await self.queue.get()

    async def aclose(self) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._bus._subs.remove(self._entry)
            except ValueError:
                pass

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


# ---- agent activity (Plan 5 "Activity" contract)
#: Coarse states an agent run moves through; emitted on every transition as `agent_activity`.
ACTIVITY_STATES = ("queued", "starting", "thinking", "writing", "tool", "waiting_approval",
                   "waiting_user", "idle", "done")

#: tool name -> a short human label for the `detail` field (the argument-free part of it).
TOOL_LABELS: dict[str, str] = {
    "mirror_part_create": "building part", "mirror_part_update": "editing part",
    "mirror_part_list": "listing parts", "mirror_measure": "measuring",
    "mirror_regenerate": "regenerating", "sim_static_run": "running FEM study",
    "onshape_docs_search": "searching Onshape", "onshape_doc_elements": "reading Onshape",
    "onshape_doc_open": "opening Onshape", "catalog_search": "searching the catalog",
    "catalog_import_url": "reading a part page",
    "reference_import": "importing a reference model", "reference_sheet_set": "writing a dimension sheet",
    "reference_sheet_get": "reading a dimension sheet",
    "reference_photo_check": "comparing with a photo", "reference_ask_measurement": "asking you to measure", "catalog_insert": "inserting a catalog part",
    "study_run": "running design study", "spec_set": "writing the spec",
    "spec_check": "checking requirements", "assembly_drive": "driving the assembly",
    "assembly_check": "checking the assembly", "ui_ask_user": "asking you",
    "agent_spawn": "delegating", "orchestrator_run": "delegating",
    "agent_wait": "waiting on subagents", "agent_result": "collecting a subagent",
    "sim_run": "running a study", "design_review": "reviewing the design",
    "sim_wait": "waiting on a study", "job_wait": "waiting on a background job",
    "design_review_get": "reading a design review",
    "study_apply": "applying a variant", "study_get": "reading a design study",
    "interview_plan": "planning the interview", "spec_get": "reading the spec",
    "loads_impact": "estimating impact loads",
    "loads_propulsion": "estimating thrust", "catalog_get": "reading a catalog part",
    "onshape_import_element": "importing from Onshape",
    "assembly_add_part": "adding to the assembly", "assembly_move": "moving a part",
    "assembly_animate": "animating the assembly",
    "web_search": "searching parts sites", "web_open": "reading a web page",
    "part_show": "showing a part", "bom_add": "adding to the parts list",
    "bom_list": "reading the parts list",
}


def tool_label(name: str) -> str:
    """A human `detail` label for a tool call. `mcp__mises__` prefixes are stripped first."""
    bare = name.split("mcp__mises__", 1)[-1]
    return TOOL_LABELS.get(bare, bare.replace("_", " "))


def calls_label(names: list[str]) -> str:
    """The `detail` for one turn's tool calls: the first call's label, "+N" for the rest."""
    if not names:
        return ""
    more = len(names) - 1
    return tool_label(names[0]) + (f" +{more}" if more else "")


class ActivityTracker:
    """Tracks one run's coarse activity state and publishes `agent_activity` on each transition.

    The `writing` state is re-emitted at most ~4/s (the model streams text far faster). Model
    thinking is published as `agent_thinking` deltas (display-only, coalesced to ~4/s) and, when
    the thinking phase ends, stored once as a truncated DECISION step.
    """
    WRITING_INTERVAL = 0.25          # >= 4/s while writing
    THINKING_INTERVAL = 0.25         # >= 4/s coalesced thinking deltas
    THINKING_STORE_CHARS = 4000      # thinking is stored truncated to this many characters
    ACTIVITY_DB_INTERVAL = 5.0       # runs.last_activity_at is written at most once per 5 s

    def __init__(self, bus: EventBus, run_id: str, store: "Store | None" = None, *,
                 clock: Callable[[], float] = time.time):
        self._bus = bus
        self._run_id = run_id
        self._store = store
        self._clock = clock
        self.state: str | None = None
        self.detail: str = ""
        self.since: float | None = None
        self._tokens_out: int = 0
        self._last_write = 0.0
        self._think_full: list[str] = []     # accumulated for storage (capped)
        self._think_len = 0
        self._think_pending: list[str] = []  # not yet published (coalesced)
        self._last_think = float("-inf")     # the first thinking delta always publishes
        self._last_db = float("-inf")        # last runs.last_activity_at write (throttled)
        # G7: when the run last reported anything (a tool's progress too): the turn watchdog
        self.last_touch: float | None = None

    def snapshot(self) -> dict | None:
        """The current state, for GET /runs/{id}'s `activity` field (None before the first)."""
        if self.state is None:
            return None
        data = {"state": self.state, "detail": self.detail, "since": self.since}
        if self._tokens_out:
            data["tokens_out"] = self._tokens_out
        return data

    def _emit(self) -> None:
        self._bus.publish(Event(type="agent_activity", run_id=self._run_id, data=self.snapshot()))
        self._touch_db()

    def _touch_db(self) -> None:
        """Keep runs.last_activity_at (C1's "last active" sort and cleanup age) current from
        the activity stream, writing at most once per ACTIVITY_DB_INTERVAL per run."""
        touch = getattr(self._store, "touch_run_activity", None)
        if touch is None:
            return
        now = self._clock()
        if now - self._last_db < self.ACTIVITY_DB_INTERVAL:
            return
        self._last_db = now
        try:
            touch(self._run_id, now)
        except Exception:                      # never let bookkeeping break a run
            log.debug("last_activity_at update failed for %s", self._run_id, exc_info=True)

    def set(self, state: str, detail: str = "", *, tokens_out: int | None = None) -> None:
        now = self._clock()
        self.last_touch = now
        if tokens_out is not None:
            self._tokens_out = tokens_out
        if state != self.state or detail != self.detail:
            if self.state == "thinking" and state != "thinking":
                self.flush_thinking()
            self.state, self.detail, self.since = state, detail, now
            self._last_write = now
            self._emit()
        elif state == "writing" and now - self._last_write >= self.WRITING_INTERVAL:
            self._last_write = now
            self._emit()

    def thinking(self, text_delta: str) -> None:
        self.set("thinking")
        self._think_pending.append(text_delta)
        if self._think_len < self.THINKING_STORE_CHARS:
            self._think_full.append(text_delta)
            self._think_len += len(text_delta)
        now = self._clock()
        if now - self._last_think >= self.THINKING_INTERVAL:
            self._publish_thinking(now)

    def _publish_thinking(self, now: float) -> None:
        if not self._think_pending:
            return
        chunk = "".join(self._think_pending)
        self._think_pending = []
        self._last_think = now
        self._bus.publish(Event(type="agent_thinking", run_id=self._run_id,
                                data={"text_delta": chunk}))

    def flush_thinking(self) -> None:
        """Publish any pending thinking delta and store the whole phase, truncated."""
        self._publish_thinking(self._clock())
        if self._store is not None and self._think_full:
            text = "".join(self._think_full)[:self.THINKING_STORE_CHARS]
            self._store.append_step(self._run_id, StepKind.DECISION, {
                "agent_thinking": text, "truncated": self._think_len >= self.THINKING_STORE_CHARS})
        self._think_full = []
        self._think_len = 0
