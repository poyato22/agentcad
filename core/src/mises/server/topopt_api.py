"""Generative design routes (D14): start / list / get / cancel / accept a topology-optimization
run, and its variants' files (live preview, result and stress GLBs, render, STEP / STL).

Start and accept are not a side door: they call topopt_run / topopt_generate / topopt_accept
through the Gate on the build's ui run (Runtime.ui_tool_call), like every UI edit, so the same
scope checks, Steps and events apply. A run continues in the background (202); the app follows
`topopt_progress` / `topopt_finished` events or polls GET /topopt/{id}. Admin token only.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..cad.tools import PART_ID_RE
from ..topopt import jobs
from ..topopt.spec import TopoptGenerateArgs
from ..topopt.tools import register_topopt_tools
from ..types import ErrorCode, MisesError
from .ui_api import _etag_matches

if TYPE_CHECKING:
    from ..runtime import Runtime

_STATUS = {"not_found": 404, "out_of_scope": 403, "invalid_input": 400, "cancelled": 409}


def build_topopt_router(runtime: Runtime) -> APIRouter:
    register_topopt_tools(runtime.registry)   # no-op: Runtime registers them
    router = APIRouter()

    async def _call(tool: str, args: dict) -> tuple[dict | None, JSONResponse | None]:
        call = runtime.ui_tool_call(tool, args)
        if call.task is not None:
            await call.task
        if call.error is not None:
            code = call.error.get("error", "tool_failed")
            return None, JSONResponse(call.error, status_code=_STATUS.get(code, 400))
        out = call.output or {}
        data = out.get("data") or {}
        if not out.get("ok", False):
            if data.get("conflict"):
                return None, JSONResponse(
                    {"error": "stale_version", "message": out.get("summary"),
                     "current_version": data.get("current_version")}, status_code=409)
            return None, JSONResponse({"error": "failed", "message": out.get("summary")},
                                      status_code=422)
        return {**data, "summary": out.get("summary")}, None

    @router.post("/topopt")
    async def start(body: dict[str, Any]):
        body = {k: v for k, v in body.items() if k not in ("wait", "tool")}
        try:
            args = TopoptGenerateArgs.model_validate(body)
        except ValidationError as e:
            return JSONResponse({"error": "invalid_input", "message": str(e).splitlines()[0],
                                 "errors": e.errors(include_url=False, include_context=False)},
                                status_code=400)
        tool = "topopt_generate" if (args.variants or args.sweep) else "topopt_run"
        payload = args.model_dump(mode="json", exclude_none=True)
        if tool == "topopt_run":
            for k in ("variants", "sweep", "max_variants"):
                payload.pop(k, None)
        data, err = await _call(tool, payload)
        if err is not None:
            return err
        return JSONResponse(jobs.public(jobs.read_record(runtime.home, data["topopt_id"])),
                            status_code=202)

    @router.get("/topopt")
    async def listing(part_id: str | None = None, project_id: str | None = None,
                      run_id: str | None = None):
        if part_id is not None and not PART_ID_RE.fullmatch(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id {part_id!r}")
        return jobs.list_records(runtime.home, part_id=part_id, project_id=project_id,
                                 run_id=run_id)

    @router.get("/topopt/{topopt_id}")
    async def get(topopt_id: str):
        rec = jobs.read_record(runtime.home, topopt_id)
        return {**jobs.public(rec), "summary": rec.get("summary") or jobs.summary(rec)}

    @router.post("/topopt/{topopt_id}/cancel")
    async def cancel(topopt_id: str):
        rec = jobs.read_record(runtime.home, topopt_id)
        if not jobs.cancel(topopt_id):
            return JSONResponse({"error": "not_running", "status": rec.get("status"),
                                 "message": f"{topopt_id} is not running"}, status_code=409)
        return {"topopt_id": topopt_id, "status": "cancelling"}

    @router.post("/topopt/{topopt_id}/accept")
    async def accept(topopt_id: str, body: dict[str, Any]):
        rec = jobs.read_record(runtime.home, topopt_id)
        args = {"topopt_id": topopt_id, "part_id": rec["part_id"],
                **{k: body[k] for k in ("variant", "mode", "new_part_id", "base_version")
                   if body.get(k) is not None}}
        data, err = await _call("topopt_accept", args)
        return err if err is not None else data

    @router.get("/topopt/{topopt_id}/variants/{vid}/{name}")
    async def variant_file(topopt_id: str, vid: str, name: str, request: Request):
        data, media = jobs.variant_file(runtime.home, topopt_id, vid, name)
        etag = f'"{hashlib.sha256(data).hexdigest()[:24]}-{len(data)}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        if name in ("part.step", "part.stl"):
            headers["Content-Disposition"] = (f'attachment; filename="{rec_name(topopt_id, vid)}'
                                              f'.{name.split(".")[1]}"')
        return Response(data, media_type=media, headers=headers)

    def rec_name(topopt_id: str, vid: str) -> str:
        try:
            rec = jobs.read_record(runtime.home, topopt_id)
            return f"{rec['part_id']}_{vid}"
        except MisesError:
            return f"{topopt_id}_{vid}"

    return router
