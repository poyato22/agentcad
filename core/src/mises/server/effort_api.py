"""Plan 11 F8: the composer's Think control.

- `GET /projects/{id}/effort`  -> {default, choices, levels}: the build's default Think choice
- `PUT /projects/{id}/effort`  {default: auto|normal|hard|max} -> the same, after saving it

A message's own choice rides on `POST /projects/{id}/chat` as `effort` (ChatBody).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .. import effort as effort_mod
from .. import projects as _projects

if TYPE_CHECKING:
    from ..runtime import Runtime


class EffortDefault(BaseModel):
    default: Literal["auto", "normal", "hard", "max"]


def _view(runtime: Runtime, project_id: str) -> dict:
    return {"default": effort_mod.build_default(runtime.store, project_id) or "auto",
            "choices": list(effort_mod.CHOICES),
            "levels": {c: effort_mod.CHOICE_LEVEL.get(c) for c in effort_mod.CHOICES}}


def build_effort_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/effort")
    async def get_effort(project_id: str):
        pid = _projects.check_project(runtime.store, project_id)
        return _view(runtime, pid)

    @router.put("/projects/{project_id}/effort")
    async def put_effort(project_id: str, body: EffortDefault):
        pid = _projects.check_project(runtime.store, project_id)
        effort_mod.set_build_default(runtime.store, pid, body.default)
        return _view(runtime, pid)

    return router
