"""Studies page routes: list/read static stress studies, their heatmap GLB, solver status, and
start a study from the UI. Admin token only (like every non-MCP route).

A UI-started study is not a side door: POST /sims creates a "studies" run (scope: that part,
classes read + local_mutate) and calls sim_static_run through the Gate, so it gets the same
Steps, events, scope checks and timeouts as an agent's call. It runs in the background; the
page follows `sim_progress` / `run_finished` events or polls GET /sims?run_id=. Cancel it with
POST /runs/{run_id}/cancel (kills the mesher/solver).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..cad.tools import PART_ID_RE
from ..sim.ccx import solver_status
from ..sim.tools import SIM_ID_RE, StaticArgs, find_sim, list_sims, read_record, register_sim_tools
from ..types import ErrorCode, MisesError, SafetyClass
from .ui_api import _contained, _etag_matches

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

UI_AGENT = "studies"


def _check_part(part_id: str) -> None:
    if not PART_ID_RE.fullmatch(part_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                         hint=f"part ids match {PART_ID_RE.pattern}")


def _check_sim(sim_id: str) -> None:
    if not SIM_ID_RE.fullmatch(sim_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad sim id: {sim_id!r}",
                         hint=f"sim ids match {SIM_ID_RE.pattern}")


def build_sim_router(runtime: Runtime) -> APIRouter:
    register_sim_tools(runtime.registry)
    router = APIRouter()
    tasks: set[asyncio.Task] = set()

    def parts_root() -> Path:
        return Path(runtime.home) / "parts"

    def _interrupted(rec: dict) -> dict:
        # "running" on disk but its run is no longer running (Mises restarted mid-study)
        if rec.get("status") == "running" and rec.get("run_id"):
            try:
                if runtime.store.get_run(rec["run_id"]).status != "running":
                    rec = {**rec, "status": "interrupted"}
            except KeyError:
                rec = {**rec, "status": "interrupted"}
        return rec

    @router.get("/sims/solvers")
    async def solvers():
        return await run_in_threadpool(solver_status)

    @router.get("/sims")
    async def sims(part_id: str | None = None, run_id: str | None = None):
        if part_id is not None:
            _check_part(part_id)
        return [_interrupted(r) for r in list_sims(parts_root(), part_id, run_id)]

    def _dir(sim_id: str) -> Path:
        _check_sim(sim_id)
        d = find_sim(parts_root(), sim_id)
        if d is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no study {sim_id}")
        return d

    @router.get("/sims/{sim_id}")
    async def sim(sim_id: str):
        return _interrupted(read_record(_dir(sim_id), parts_root()))

    @router.get("/sims/{sim_id}/result.glb")
    async def sim_glb(sim_id: str, request: Request):
        d = _dir(sim_id)
        real = _contained(parts_root(), d / "result.glb")
        if real is None or not real.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, f"study {sim_id} has no result model",
                             hint="only finished studies have one")
        st = real.stat()
        etag = f'"{st.st_mtime_ns}-{st.st_size}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(real.read_bytes(), media_type="model/gltf-binary", headers=headers)

    @router.post("/sims")
    async def start(body: StaticArgs):
        _check_part(body.part_id)
        part = parts_root() / body.part_id
        if not (part / "metrics.json").is_file() or not (part / "model.step").is_file():
            raise MisesError(ErrorCode.NOT_FOUND, f"no built part {body.part_id!r}")
        rid = runtime.create_external_run(
            UI_AGENT, provider="ui", model="studies-page",
            edit_scope=(body.part_id,), read_scope=(body.part_id,),
            allowed_classes={SafetyClass.READ, SafetyClass.LOCAL_MUTATE},
            task=f"Studies page: static study of {body.part_id}")
        payload = body.model_dump()

        async def drive() -> None:
            status = "failed"
            try:
                out = await runtime.gate.call(rid, "sim_static_run", payload,
                                              call_id=f"ui-{uuid.uuid4().hex[:12]}",
                                              workdir=runtime.workdir(rid))
                status = "done" if out.ok else "failed"
            except asyncio.CancelledError:
                status = "cancelled"
            except MisesError as e:  # already recorded as an error Step by the Gate
                log.info("studies run %s: %s", rid, e.message)
            except Exception:  # noqa: BLE001
                log.exception("studies run %s failed", rid)
            finally:
                runtime.unregister_cancel_hook(rid)
                try:
                    if runtime.store.get_run(rid).status == "running":
                        runtime.finish_external_run(rid, status)
                except Exception:  # noqa: BLE001
                    log.exception("could not finish studies run %s", rid)

        task = asyncio.create_task(drive(), name=f"studies-{rid}")
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        async def cancel() -> None:
            task.cancel()
        runtime.register_cancel_hook(rid, cancel)
        return JSONResponse({"run_id": rid, "status": "running"}, status_code=202)

    return router
