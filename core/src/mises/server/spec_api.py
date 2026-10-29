"""Spec and design-study routes for the Mac app. Admin token only (like every non-MCP route).

GET/PUT /projects/{id}/spec is the editable engineering spec; a PUT is the user's own edit, so it
is validated the same way the agent's spec_set is and emits `spec_changed`. GET
/projects/{id}/studies lists the design studies for a build. These keep the spec and studies
keyed by project_id (a TEXT key; Task C1 owns the projects table itself).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..engineering.spec import Spec
from ..events import Event
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)
_MAX_PROJECT_ID = 128


def _check_project_id(project_id: str) -> None:
    if not project_id or len(project_id) > _MAX_PROJECT_ID:
        raise MisesError(ErrorCode.INVALID_INPUT, "bad project id")


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
        try:
            body = await request.json()
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
        return await run_in_threadpool(runtime.store.list_studies, project_id)

    return router
