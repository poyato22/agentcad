"""Concept routes (admin token), for the app's sketch and pitch cards:

- `GET  /projects/{id}/concept`                 the record, sketch versions (+ feedback), pitches
- `GET  /concept/sketches/{sketch_id}`           one sketch version with its feedback rounds
- `POST /concept/sketches/{sketch_id}/feedback`  {marks[], comment} → the build's lead
- `POST /projects/{id}/concept/approve`          {sketch_id?} → status approved (gate open)
- `POST /projects/{id}/concept/skip`             → status skipped (gate open)
- `POST /concept/pitches/{pitch_id}/choose`      {option | mix[], comment} → the build's lead

Images are render ids served by `GET /renders/{id}.png`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from .. import projects as _projects
from . import ApproveIn, ChooseIn, FeedbackIn
from . import service

if TYPE_CHECKING:
    from ..runtime import Runtime


def build_concept_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/concept")
    async def get_concept(project_id: str):
        _projects.check_project(runtime.store, project_id)
        return service.view(runtime, project_id)

    @router.get("/concept/sketches/{sketch_id}")
    async def get_sketch(sketch_id: str):
        sk = runtime.concepts.get_sketch(sketch_id)
        return {**sk, "feedback": runtime.concepts.feedback(sk["project_id"], sketch_id)}

    @router.post("/concept/sketches/{sketch_id}/feedback")
    async def post_feedback(sketch_id: str, body: FeedbackIn):
        return await service.submit_feedback(runtime, sketch_id, body)

    @router.post("/projects/{project_id}/concept/approve")
    async def post_approve(project_id: str, body: ApproveIn | None = None):
        _projects.check_project(runtime.store, project_id)
        return await service.approve(runtime, project_id, body.sketch_id if body else None)

    @router.post("/projects/{project_id}/concept/skip")
    async def post_skip(project_id: str):
        _projects.check_project(runtime.store, project_id)
        return await service.skip(runtime, project_id)

    @router.post("/concept/pitches/{pitch_id}/choose")
    async def post_choose(pitch_id: str, body: ChooseIn):
        return await service.choose(runtime, pitch_id, body)

    return router
