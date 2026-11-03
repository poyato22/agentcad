"""Whole-assembly studies for the Studies page (Plan 13 H4): which kinds fit the build, start
one, list and read the build's records, and hand a finished one's peak loads to per-part FEM.
Admin token only (like every non-MCP route).

Like POST /sims, a start is not a side door: it creates a "studies" run (scope: the build, read
only; the FEM hand-off: the build and its parts, read + local_mutate) that calls
assembly_study / assembly_study_fem through the Gate, so it gets the same Steps, scope checks,
events and timeouts as an agent's call. Both run in the background (B3 jobs: GET /jobs lists
them, POST /jobs/{id}/cancel stops them); the page follows `assembly_study_progress` events
and GET /assembly_studies?project_id=.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..sim import system_study as ss
from ..types import ErrorCode, MisesError, SafetyClass

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UI_AGENT = "studies"


class StartBody(BaseModel):
    project_id: str = Field(pattern=PROJECT_ID_RE.pattern)
    kind: ss.Kind
    settings: dict[str, Any] = Field(default_factory=dict)


class FemBody(BaseModel):
    n_links: int = Field(2, ge=1, le=6)
    min_safety_factor: float = Field(2.0, gt=0, le=20)


def build_system_study_router(runtime: Runtime) -> APIRouter:
    ss.register_system_study_tools(runtime.registry)   # no-op: Runtime registers them
    router = APIRouter()
    tasks: set[asyncio.Task] = set()

    def _bare():
        from types import SimpleNamespace
        return SimpleNamespace(workdir=runtime.home, services={"assembly": runtime.assembly_store},
                               store=runtime.store, run_id=None, events=None)

    def _build(pid: str):
        if not PROJECT_ID_RE.fullmatch(pid or ""):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad project id: {pid!r}")
        try:
            proj = runtime.store.get_project(pid)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"no build {pid!r}") from None
        if getattr(proj, "trashed_at", None) is not None:
            raise MisesError(ErrorCode.INVALID_INPUT, f"build {pid!r} is in the trash",
                             hint="restore it first")

    def _record(study_id: str, project_id: str | None = None) -> dict:
        rec = ss.load(runtime.home, study_id, project_id)
        if rec is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no assembly study {study_id!r}")
        return ss.interrupted(rec)

    def _launch(rid: str, tool: str, payload: dict) -> None:
        async def drive() -> None:
            status = "failed"
            try:
                out = await runtime.gate.call(rid, tool, payload,
                                              call_id=f"ui-{uuid.uuid4().hex[:12]}",
                                              workdir=runtime.workdir(rid))
                status = ("done" if out.ok else
                          "cancelled" if out.data.get("error") == "cancelled" else "failed")
            except asyncio.CancelledError:
                status = "cancelled"
            except MisesError as e:     # already an error Step (the Gate recorded it)
                log.info("assembly study run %s: %s", rid, e.message)
            except Exception:  # noqa: BLE001
                log.exception("assembly study run %s failed", rid)
            finally:
                runtime.unregister_cancel_hook(rid)
                try:
                    if runtime.store.get_run(rid).status == "running":
                        runtime.finish_external_run(rid, status)
                except Exception:  # noqa: BLE001
                    log.exception("could not finish assembly study run %s", rid)

        task = asyncio.create_task(drive(), name=f"assembly-study-{rid}")
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        async def cancel_run() -> None:
            task.cancel()
        runtime.register_cancel_hook(rid, cancel_run)

    @router.get("/assembly_studies/kinds")
    async def kinds(project_id: str):
        _build(project_id)
        rows = await run_in_threadpool(ss.propose, _bare(), project_id)
        info = {k["id"]: k for k in ss.kinds_info()}
        return {"project_id": project_id,
                "kinds": [{**r, "doc": info[r["kind"]]["doc"],
                           "schema": info[r["kind"]]["schema"]} for r in rows]}

    @router.get("/assembly_studies")
    async def list_studies(project_id: str):
        _build(project_id)
        return [ss.interrupted(r) for r in ss.list_records(runtime.home, project_id)]

    @router.get("/assembly_studies/{study_id}")
    async def get_study(study_id: str):
        return _record(study_id)

    @router.post("/assembly_studies")
    async def start(body: StartBody):
        _build(body.project_id)
        ss.parse_settings(body.kind, body.settings)        # refused before a run exists
        asm = runtime.assembly_store.get(body.project_id)
        if not asm.instances:
            raise MisesError(ErrorCode.NOT_FOUND, "this build has no assembly yet",
                             hint="place its parts in the assembly first")
        pid = body.project_id
        rid = runtime.create_external_run(
            UI_AGENT, provider="ui", model="studies-page", project_id=pid,
            edit_scope=(pid, f"project:{pid}"), read_scope=(pid, f"project:{pid}"),
            allowed_classes={SafetyClass.READ},
            task=f"Studies page: {ss.NAMES[body.kind].lower()} study of build {pid}")
        _launch(rid, "assembly_study", {"project_id": pid, "kind": body.kind,
                                        "settings": body.settings, "wait": True})
        return JSONResponse({"run_id": rid, "status": "running"}, status_code=202)

    @router.post("/assembly_studies/{study_id}/fem")
    async def fem(study_id: str, body: FemBody | None = None):
        body = body or FemBody()
        rec = _record(study_id)
        pid = rec["project_id"]
        _build(pid)
        if rec.get("status") != "done":
            raise MisesError(ErrorCode.INVALID_INPUT, f"{study_id} is {rec.get('status')}",
                             hint="check part stresses on a finished study")
        if not (rec.get("fem") or {}).get("available"):
            raise MisesError(ErrorCode.INVALID_INPUT, f"a {rec.get('kind')} study has no "
                             "per-part stress hand-off", hint=(rec.get("fem") or {}).get("note"))
        asm = runtime.assembly_store.get(pid)
        part_ids = sorted({i.part_id for i in asm.instances})
        rid = runtime.create_external_run(
            UI_AGENT, provider="ui", model="studies-page", project_id=pid,
            edit_scope=(pid, f"project:{pid}", *part_ids),
            read_scope=(pid, f"project:{pid}", *part_ids),
            allowed_classes={SafetyClass.READ, SafetyClass.LOCAL_MUTATE},
            task=f"Studies page: part stresses of assembly study {study_id}")
        _launch(rid, "assembly_study_fem", {"project_id": pid, "study_id": study_id,
                                            "n_links": body.n_links,
                                            "min_safety_factor": body.min_safety_factor,
                                            "wait": True})
        return JSONResponse({"run_id": rid, "status": "running"}, status_code=202)

    return router
