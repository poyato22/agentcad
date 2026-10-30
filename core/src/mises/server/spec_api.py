"""Spec and design-study routes for the Mac app. Admin token only (like every non-MCP route).

GET/PUT /projects/{id}/spec is the editable engineering spec; a PUT is the user's own edit, so it
is validated the same way the agent's spec_set is and emits `spec_changed`. GET
/projects/{id}/studies lists the design studies for a build. These keep the spec and studies
keyed by project_id (a TEXT key; Task C1 owns the projects table itself).

Two more routes serve the spec panel (Plan 5 A7):
- GET /projects/{id}/spec/check?part_id= measures a part against the spec. It is a plain admin
  read (the same evaluation spec_check uses), so it creates no run and no Step.
- POST /projects/{id}/studies/{study_id}/apply is "Use this variant": study_apply through the
  Gate, on that build's one long-lived "ui" run (Runtime._ui_run_id(project_id)), recorded as a
  Step like every UI-started change. study_apply re-checks the variant against the spec as it
  is now, not the flag stored when the study ran.
PUT /spec takes an optional `base_updated_at` (from GET's `updated_at`): a spec written since is
409 stale_version, so a user's edit never silently replaces an agent's spec_set.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..cad.tools import PART_ID_RE
from ..engineering.spec import Spec
from ..engineering.tools import STUDY_ID_PREFIX, mark_interrupted, spec_report
from ..events import Event
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
_MAX_PROJECT_ID = 128
_MAX_BODY = 256 * 1024  # a spec is small; refuse anything larger before parsing it


_NO_BASE = object()


def _check_project_id(project_id: str) -> None:
    if not project_id or len(project_id) > _MAX_PROJECT_ID:
        raise MisesError(ErrorCode.INVALID_INPUT, "bad project id")


class ApplyBody(BaseModel):
    iteration: int = Field(ge=0)
    base_version: int | None = None
    allow_infeasible: bool = False


async def _apply(runtime: Runtime, project_id: str, args: dict) -> dict:
    """study_apply as the user's own call on the build's ui run; its ToolOutput (as JSON), or
    the MisesError it failed with (the app gets the core's error body)."""
    call = runtime.ui_tool_call("study_apply", args, project_id=project_id)
    try:
        await asyncio.shield(call.task)
    except asyncio.CancelledError:
        pass
    if call.status == "done" and call.output is not None:
        return call.output
    data = (call.output or {}).get("data") or {}
    if call.error is None and data.get("conflict"):
        # the part changed under the user: 409 stale_version (the app offers reload / keep)
        raise MisesError(ErrorCode.STALE_VERSION,
                         f"{data.get('part_id')} changed (now version "
                         f"{data.get('current_version')}); review the variant again",
                         hint="reload the part, then apply again")
    err = call.error or data
    code = err.get("error")
    raise MisesError(ErrorCode(code) if code in set(ErrorCode) else ErrorCode.TOOL_FAILED,
                     err.get("message") or (call.output or {}).get("summary") or "study_apply failed",
                     hint=err.get("hint"))


def build_spec_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/spec")
    async def get_spec(project_id: str):
        _check_project_id(project_id)
        data = await run_in_threadpool(runtime.store.get_spec, project_id)
        spec = Spec.model_validate(data) if data else Spec()
        at = await run_in_threadpool(runtime.store.get_spec_updated_at, project_id)
        return {"project_id": project_id, "spec": spec.model_dump(), "updated_at": at}

    @router.put("/projects/{project_id}/spec")
    async def put_spec(project_id: str, request: Request):
        _check_project_id(project_id)
        raw = await request.body()
        if len(raw) > _MAX_BODY:
            return JSONResponse(MisesError(ErrorCode.INVALID_INPUT, "spec body too large",
                                           hint=f"at most {_MAX_BODY} bytes").to_dict(),
                                status_code=413)
        try:
            body = json.loads(raw)
        except ValueError:
            return JSONResponse(MisesError(ErrorCode.INVALID_INPUT, "body must be JSON").to_dict(),
                                status_code=422)
        base: object = _NO_BASE
        if isinstance(body, dict) and "base_updated_at" in body:
            base = body.pop("base_updated_at")
        if isinstance(body, dict) and "spec" in body and isinstance(body["spec"], dict):
            body = body["spec"]
        try:
            spec = Spec.model_validate(body)
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            return JSONResponse(
                MisesError(ErrorCode.INVALID_INPUT, "invalid spec: " + msgs,
                           hint="fix the listed fields").to_dict(), status_code=422)
        if base is not _NO_BASE:
            cur = await run_in_threadpool(runtime.store.get_spec_updated_at, project_id)
            same = (cur is None and base is None) or (
                isinstance(base, (int, float)) and cur is not None and abs(cur - base) < 1e-6)
            if not same:
                err = MisesError(ErrorCode.STALE_VERSION, "the spec changed since you opened it",
                                 hint="reload it, or save yours anyway (without base_updated_at)")
                return JSONResponse({**err.to_dict(), "current_updated_at": cur}, status_code=409)
        await run_in_threadpool(runtime.store.put_spec, project_id, spec.model_dump())
        at = await run_in_threadpool(runtime.store.get_spec_updated_at, project_id)
        runtime.events.publish(Event(type="spec_changed", data={"project_id": project_id}))
        return {"project_id": project_id, "spec": spec.model_dump(), "updated_at": at}

    @router.get("/projects/{project_id}/studies")
    async def list_studies(project_id: str):
        _check_project_id(project_id)
        rows = await run_in_threadpool(runtime.store.list_studies, project_id)
        return [mark_interrupted(runtime.store, r) for r in rows]

    @router.get("/projects/{project_id}/spec/check")
    async def check_spec(project_id: str, part_id: str):
        """The part measured against this build's spec: per requirement the measured value and
        pass / warn / fail / not_checked (spec_check's data). A plain read: no run, no Step."""
        _check_project_id(project_id)
        if not PART_ID_RE.match(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}")
        data = await run_in_threadpool(runtime.store.get_spec, project_id)
        spec = Spec.model_validate(data) if data else Spec()
        report = await run_in_threadpool(spec_report, Path(runtime.home), spec, part_id)
        return {"project_id": project_id, "part_id": part_id, **report}

    @router.post("/projects/{project_id}/studies/{study_id}/apply")
    async def apply_variant(project_id: str, study_id: str, body: ApplyBody):
        """"Use this variant": study_apply on the study's own part. A stale base_version is
        409 stale_version; an infeasible variant is refused unless allow_infeasible."""
        _check_project_id(project_id)
        if not study_id.startswith(STUDY_ID_PREFIX) or len(study_id) > 64:
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad study id: {study_id!r}")
        rec = await run_in_threadpool(runtime.store.get_study, study_id)
        if rec is None or rec.get("project_id") != project_id:
            raise MisesError(ErrorCode.NOT_FOUND, f"no study {study_id} in {project_id}")
        part_id = str(rec.get("part_id") or "")
        args = {"study_id": study_id, "part_id": part_id, "iteration": body.iteration,
                "allow_infeasible": body.allow_infeasible}
        if not PART_ID_RE.match(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}")
        if body.base_version is not None:
            args["base_version"] = body.base_version
        else:
            # the user's own click with no base: apply on top of the live part as it is now (the
            # app sends the on-screen version to get a 409 on a concurrent change). Agents never
            # come through here: their omitted base_version means their last read (C6).
            from .. import edits
            args["base_version"] = await run_in_threadpool(
                edits.read_version, runtime.home / "parts" / part_id)
        out = await _apply(runtime, project_id, args)
        return {"study_id": study_id, "part_id": part_id, "iteration": body.iteration,
                "summary": out["summary"], "warnings": out.get("warnings", []),
                "data": out["data"]}

    return router
