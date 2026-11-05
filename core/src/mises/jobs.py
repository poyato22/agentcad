"""Background jobs an agent starts and keeps working past (Plan 9 D4): studies (sim_run,
sim_static_run), design reviews and design studies.

A job's tool call returns {id, status: running} at once. When the job ends the core publishes
`sim_finished` and, unless the result was already read (job_wait) or the owning run was
stopped, notes the agent that started it (orchestrator.lead.deliver_note). Stopping a run
cancels the background jobs of its whole family (Runtime.cancel -> cancel_run).

Jobs live in this process only (like studies.active_ids); a result is also written to
<home>/jobs/<id>.json so design_review_get still finds it after the turn has ended.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .events import Event
from .types import ErrorCode, MisesError, ToolOutput

log = logging.getLogger(__name__)

#: background jobs one run family may have outstanding beyond the studies that can solve at
#: once (studies.study_limit): more are refused, with a hint to wait for the notes
EXTRA_QUEUED = 2
JOB_WAIT_MAX_S = 600.0
JOBS_DIR = "jobs"


@dataclass
class Job:
    id: str
    kind: str                      # "sim" | "design_review" | "design_study"
    run_id: str | None
    label: str = ""                # "static study of beam", shown in the note
    scope: list = field(default_factory=list)   # scope keys whose readers may read it
    build_id: str | None = None
    part_id: str | None = None
    task: asyncio.Future | None = None
    cancel: Callable[[], None] | None = None
    status: str = "running"
    summary: str = ""
    output: ToolOutput | None = None
    error: str | None = None
    waiters: int = 0               # job_wait / sim_wait calls waiting on it now
    consumed: bool = False         # an agent read its final result: no note
    stopped: bool = False          # cancelled because its run was stopped: no note
    defer: bool = False            # not worth a turn of its own (a "busy" failure)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


_jobs: dict[str, Job] = {}
_tasks: set[asyncio.Future] = set()

#: a finished job is dropped from memory this long after it ended (its result is still on disk
#: as <home>/jobs/<id>.json, which design_review_get / sim_result_get fall back to), so a long
#: session does not pile up ToolOutputs for the life of the process
FINISHED_KEEP_S = 600.0


def _prune(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for jid, j in list(_jobs.items()):
        if j.status != "running" and j.finished_at is not None and \
                now - j.finished_at > FINISHED_KEEP_S:
            del _jobs[jid]


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def active(job_id: str) -> bool:
    j = _jobs.get(job_id)
    return j is not None and j.status == "running"


def register(job: Job) -> Job:
    _prune()                           # drop long-finished jobs when a new one starts
    _jobs[job.id] = job
    if job.task is not None:
        _tasks.add(job.task)
        job.task.add_done_callback(_tasks.discard)
    return job


def family(store, run_id: str | None) -> set[str]:
    """The run, its root and every run below that root (a lead and its sub-agents)."""
    if not run_id or store is None:
        return {run_id} if run_id else set()
    root = run_id
    try:
        seen = {root}
        r = store.get_run(root)
        while r.parent_id and r.parent_id not in seen:
            seen.add(r.parent_id)
            r = store.get_run(r.parent_id)
        root = r.id
        return {root, *store.descendants(root)}
    except Exception:  # noqa: BLE001
        return {run_id}


def root_of(store, run_id: str | None) -> str | None:
    """The root of run_id's family (the agent its notes go to)."""
    if not run_id or store is None:
        return run_id
    try:
        seen = {run_id}
        r = store.get_run(run_id)
        while r.parent_id and r.parent_id not in seen:
            seen.add(r.parent_id)
            r = store.get_run(r.parent_id)
        return r.id
    except Exception:  # noqa: BLE001
        return run_id


def running() -> list[Job]:
    _prune()
    return [j for j in _jobs.values() if j.status == "running"]


def view(job: Job) -> dict:
    """A job as GET /jobs lists it (the app's Simulations stream and its Stop buttons)."""
    return {"job_id": job.id, "kind": job.kind, "run_id": job.run_id, "build_id": job.build_id,
            "part_id": job.part_id, "label": job.label, "status": job.status, "started_at": job.started_at,
            "cancel_url": cancel_url(job.id)}


def cancel_url(job_id: str) -> str:
    return f"/jobs/{job_id}/cancel"


def user_cancel(job_id: str) -> bool:
    """The user stopped this one job (POST /jobs/{id}/cancel): it ends cancelled and its note
    starts no turn of its own (it rides along with the next one). False if not running."""
    j = _jobs.get(job_id)
    if j is None or j.status != "running":
        return False
    j.defer = True
    if j.cancel is not None:
        j.cancel()
    elif j.task is not None:
        j.task.cancel()
    return True


def outstanding(run_ids: set[str]) -> list[Job]:
    return [j for j in _jobs.values() if j.status == "running" and j.run_id in run_ids]


def check_room(ctx) -> None:
    """Refuse a new background job when the run's family already has too many waiting."""
    from .sim.studies import study_limit
    fam = family(getattr(ctx, "store", None), getattr(ctx, "run_id", None))
    n = len(outstanding(fam))
    cap = study_limit() + EXTRA_QUEUED
    if n >= cap:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{n} background studies or reviews of this "
                         f"agent are still running or queued (at most {cap})",
                         hint="wait for their notes (or job_wait one) before starting more")


def cancel_run(run_ids: set[str] | list[str]) -> list[str]:
    """Stop: cancel every background job started by these runs. They end `cancelled` and
    send no note (the user stopped the agent that would read it)."""
    ids = set(run_ids)
    out = []
    for j in list(_jobs.values()):
        if j.status != "running" or j.run_id not in ids:
            continue
        j.stopped = True
        try:
            if j.cancel is not None:
                j.cancel()
            elif j.task is not None:
                j.task.cancel()
        except Exception:  # noqa: BLE001
            log.exception("could not cancel job %s", j.id)
        out.append(j.id)
    return out


async def wait(job_id: str, timeout_s: float) -> Job | None:
    """Wait up to timeout_s; a job that ends while someone waits counts as read (no note)."""
    j = _jobs.get(job_id)
    if j is None or j.task is None or j.task.done():
        if j is not None and j.status != "running":
            j.consumed = True
        return j
    j.waiters += 1
    try:
        await asyncio.wait({j.task}, timeout=min(timeout_s, JOB_WAIT_MAX_S))
    finally:
        j.waiters -= 1
    if j.status != "running":
        j.consumed = True
    return j


def _root(ctx) -> Path | None:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    home = getattr(rt, "home", None)
    return Path(home) / JOBS_DIR if home else None


def persist(ctx, job: Job) -> None:
    root = _root(ctx)
    if root is None:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{job.id}.json").write_text(json.dumps({
            "id": job.id, "kind": job.kind, "run_id": job.run_id, "label": job.label,
            "scope": list(job.scope),
            "status": job.status, "summary": job.summary, "error": job.error,
            "started_at": job.started_at, "finished_at": job.finished_at,
            "output": job.output.model_dump(mode="json", exclude={"images"})
            if job.output is not None else None}, default=float))
    except OSError:
        log.exception("could not write job %s", job.id)


def load(ctx, job_id: str) -> dict | None:
    """A finished job's stored record (this process or an earlier one)."""
    root = _root(ctx)
    if root is None or not job_id.replace("_", "").isalnum():
        return None
    try:
        return json.loads((root / f"{job_id}.json").read_text())
    except (OSError, ValueError):
        return None


def build_of(ctx, run_id: str | None) -> str | None:
    store = getattr(ctx, "store", None)
    if store is None or not run_id:
        return None
    try:
        return getattr(store.get_run(run_id), "project_id", None)
    except Exception:  # noqa: BLE001
        return None


def note_text(job: Job) -> str:
    """One line for the agent: what ended and how, the summary marked as data."""
    verb = {"done": "finished", "cancelled": "was cancelled"}.get(job.status, "failed")
    summary = " ".join((job.summary or "").split())[:400].replace('"', "'")
    read = "sim_result_get" if job.kind == "sim" else (
        "design_review_get" if job.kind == "design_review" else
        "job_wait" if job.kind == "dyn_fem" else
        "assembly_study_get" if job.kind == "assembly_study" else
        "job_wait" if job.kind == "assembly_study_fem" else "study_get")
    return (f"[AgentCAD system note, not from the user] {job.label or job.kind} ({job.id}) "
            f"{verb}. Result summary (data): \"{summary}\". Read it with {read} {job.id}.")


def finished(ctx, job: Job, *, build_id: str | None = None, data: dict | None = None,
             note: bool = True) -> None:
    """The job ended: sim_finished on the bus, and the note unless it is not wanted."""
    job.finished_at = job.finished_at or time.time()
    if job.status == "cancelled" and job.defer and not job.stopped:
        job.summary = f"{job.label or job.kind} was cancelled by the user"
    events = getattr(ctx, "events", None)
    if events is not None:
        events.publish(Event(type="sim_finished", run_id=job.run_id, data={
            "kind": job.kind, "job_id": job.id, "run_id": job.run_id,
            "build_id": build_id if build_id is not None else build_of(ctx, job.run_id),
            "status": job.status, "summary": job.summary, "cancel_url": cancel_url(job.id),
            **(data or {})}))
    if not note or job.stopped or job.consumed or job.waiters:
        if job.waiters:
            job.consumed = True
        return
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    if rt is None or not job.run_id:
        return
    from .orchestrator.lead import deliver_note
    meta = {"kind": "sim_finished", "job": job.kind, "id": job.id, "status": job.status}
    if job.kind == "sim":
        meta["sim_id"] = job.id
    t = asyncio.ensure_future(deliver_note(rt, job.run_id, note_text(job), meta,
                                           defer=job.defer))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def start(ctx, kind: str, job_id: str, label: str,
          make: Callable[[], Awaitable[ToolOutput]], *, timeout_s: float,
          data: Callable[[ToolOutput | None], dict] | None = None) -> Job:
    """Run `make()` as a background job of ctx's run, capped at timeout_s (the Gate's
    timeout for the same tool when it blocks). Its ToolOutput is the job's result."""
    job = Job(id=job_id, kind=kind, run_id=getattr(ctx, "run_id", None), label=label)
    job.build_id = build_of(ctx, job.run_id)

    async def runner() -> None:
        try:
            out = await asyncio.wait_for(make(), timeout_s)
            job.output, job.summary = out, out.summary
            job.status = "done" if out.ok else "failed"
        except asyncio.TimeoutError:
            job.status, job.error = "failed", f"timed out after {timeout_s:g}s"
            job.summary = f"{label} {job.error}"
        except asyncio.CancelledError:
            job.status, job.summary = "cancelled", f"{label} cancelled"
        except MisesError as e:
            job.status, job.error, job.summary = "failed", e.message, f"{label}: {e.message}"
        except Exception as e:  # noqa: BLE001
            log.exception("background job %s failed", job_id)
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            job.summary = f"{label}: {job.error}"
        finally:
            job.finished_at = time.time()
            persist(ctx, job)
            finished(ctx, job, data=data(job.output) if data else None)

    job.task = asyncio.ensure_future(runner())
    return register(job)


def reset_for_tests() -> None:
    _jobs.clear()
