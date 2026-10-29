"""Spec and design-study routes for the Mac app. Admin token only (like every non-MCP route).

GET/PUT /projects/{id}/spec is the editable engineering spec; a PUT is the user's own edit, so it
is validated the same way the agent's spec_set is and emits `spec_changed`. GET
/projects/{id}/studies lists the design studies for a build. These keep the spec and studies
keyed by project_id (a TEXT key; Task C1 owns the projects table itself).

Two app actions run engineering tools through the Gate, like an agent's call would (Plan 5 A7):
GET /projects/{id}/spec/check?part_id= runs spec_check (read) and POST
/projects/{id}/studies/{study_id}/apply runs study_apply (local_mutate) for "Use this variant".
Each runs on its own short-lived "ui" run of that build, scoped to the one part, so the call is
recorded as a Step, sees only that build's spec and studies, and cannot touch another part.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..cad.tools import PART_ID_RE
from ..engineering.spec import Spec
from ..engineering.tools import STUDY_ID_PREFIX, mark_interrupted
from ..events import Event
from ..types import ErrorCode, MisesError, SafetyClass

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
_MAX_PROJECT_ID = 128
_MAX_BODY = 256 * 1024  # a spec is small; refuse anything larger before parsing it


def _check_project_id(project_id: str) -> None:
    if not project_id or len(project_id) > _MAX_PROJECT_ID:
        raise MisesError(ErrorCode.INVALID_INPUT, "bad project id")


class ApplyBody(BaseModel):
    iteration: int = Field(ge=0)
    base_version: int | None = None
    allow_infeasible: bool = False


UI_AGENT = "spec"


async def _ui_tool(runtime: Runtime, project_id: str, part_id: str, tool: str, args: dict,
                   classes: set[SafetyClass], what: str) -> dict:
    """Run one engineering tool as the user's own call, through the Gate, on a fresh "ui" run
    of this build scoped to `part_id`; its ToolOutput (as JSON), or the MisesError it failed
    with (which the app gets as the core's error body)."""
    if not PART_ID_RE.match(part_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}")
    mutate = SafetyClass.LOCAL_MUTATE in classes
    rid = runtime.create_external_run(
        UI_AGENT, provider="ui", model="spec-panel", edit_scope=(part_id,) if mutate else (),
        read_scope=(part_id,), allowed_classes=classes, project_id=project_id,
        task=f"Spec panel: {what}")
    status = "failed"
    try:
        out = await runtime.gate.call(rid, tool, args, call_id=f"ui-{uuid.uuid4().hex[:12]}",
                                      workdir=runtime.workdir(rid))
        if not out.ok:
            err = out.data if isinstance(out.data, dict) else {}
            code = err.get("error")
            raise MisesError(ErrorCode(code) if code in set(ErrorCode) else ErrorCode.TOOL_FAILED,
                             err.get("message") or out.summary, hint=err.get("hint"))
        status = "done"
        return out.model_dump(mode="json")
    finally:
        try:
            runtime.finish_external_run(rid, status)
        except Exception:  # noqa: BLE001
            log.exception("could not finish spec run %s", rid)


def build_spec_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/spec")
    async def get_spec(project_id: str):
        _check_project_id(project_id)
        data = await run_in_threadpool(runtime.store.get_spec, project_id)
        spec = Spec.model_validate(data) if data else Spec()
        return {"project_id": project_id, "spec": spec.model_dump()}

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
        if isinstance(body, dict) and "spec" in body and isinstance(body["spec"], dict):
            body = body["spec"]
        try:
            spec = Spec.model_validate(body)
        except ValidationError as e:
            msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            return JSONResponse(
                MisesError(ErrorCode.INVALID_INPUT, "invalid spec: " + msgs,
                           hint="fix the listed fields").to_dict(), status_code=422)
        await run_in_threadpool(runtime.store.put_spec, project_id, spec.model_dump())
        runtime.events.publish(Event(type="spec_changed", data={"project_id": project_id}))
        return {"project_id": project_id, "spec": spec.model_dump()}

    @router.get("/projects/{project_id}/studies")
    async def list_studies(project_id: str):
        _check_project_id(project_id)
        rows = await run_in_threadpool(runtime.store.list_studies, project_id)
        return [mark_interrupted(runtime.store, r) for r in rows]

    @router.get("/projects/{project_id}/spec/check")
    async def check_spec(project_id: str, part_id: str):
        """The part measured against this build's spec (spec_check): per requirement the
        measured value and pass / warn / fail / not_checked."""
        _check_project_id(project_id)
        out = await _ui_tool(runtime, project_id, part_id, "spec_check",
                             {"part_id": part_id, "project_id": project_id},
                             {SafetyClass.READ}, f"check {part_id} against the spec")
        return out["data"]

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
        if body.base_version is not None:
            args["base_version"] = body.base_version
        out = await _ui_tool(runtime, project_id, part_id, "study_apply", args,
                             {SafetyClass.READ, SafetyClass.LOCAL_MUTATE},
                             f"use {study_id} #{body.iteration} on {part_id}")
        return {"study_id": study_id, "part_id": part_id, "iteration": body.iteration,
                "summary": out["summary"], "warnings": out.get("warnings", []),
                "data": out["data"]}

    return router
