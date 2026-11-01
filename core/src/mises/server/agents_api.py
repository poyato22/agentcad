"""Plan 7 U3c: the build's agents (lead + sub-agents) and feedback pins.

- GET  /projects/{id}/agents    the lead with its sub-tree (status, live activity), plus the
                                build's other top-level runs
- POST /projects/{id}/feedback  {part_id, point:[x,y,z], face_id?, text, instance_id?,
                                world_point?} -> a message to the build's lead with the
                                location; returns {run_id, step_id, routed}. A pin from the
                                assembly view names its instance and the world point (E35).

Admin token only, like every app route (app.py's bearer check).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import projects as _projects
from ..orchestrator import lead as _lead
from ..profile import load_profile

if TYPE_CHECKING:
    from ..runtime import Runtime


Finite = Annotated[float, Field(allow_inf_nan=False)]


class Feedback(BaseModel):
    part_id: str = Field(min_length=1, max_length=80)
    point: list[Finite] = Field(min_length=3, max_length=3)
    face_id: str | None = Field(default=None, pattern=r"^f\d{1,6}$")
    text: str = Field(min_length=1, max_length=4000)
    model_ref: str | None = None
    # Plan 10 E35: a pin dropped in the assembly view names the instance and the world point
    instance_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    world_point: list[Finite] | None = Field(default=None, min_length=3, max_length=3)


def build_agents_router(runtime: Runtime) -> APIRouter:
    r = APIRouter()

    @r.get("/projects/{project_id}/agents")
    async def project_agents(project_id: str):
        _projects.check_project(runtime.store, project_id)
        return _lead.agents_view(runtime, project_id)

    @r.post("/projects/{project_id}/feedback")
    async def feedback(project_id: str, body: Feedback):
        _projects.check_project(runtime.store, project_id)
        prof = load_profile(runtime.home)
        return await _lead.submit_feedback(runtime, project_id, part_id=body.part_id,
                                           point=body.point, face_id=body.face_id,
                                           text=body.text, model_ref=body.model_ref,
                                           default_agent=prof.default_agent,
                                           instance_id=body.instance_id,
                                           world_point=body.world_point)

    return r
