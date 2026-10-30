"""Reference-image routes (admin token): `POST /references` stores a reference image (a photo
or a three-view) and returns its id; `GET /references/{id}` returns its summary. Agents learn
the id through `mises_context_get` and match renders against it with `critique_model`."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter

from ..projects import check_project
from ..references import ReferenceUpload, reference_summary, save_reference

if TYPE_CHECKING:
    from ..runtime import Runtime


def build_references_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/references")
    async def post_reference(body: ReferenceUpload):
        if body.project_id is not None:          # a reference belongs to a real build
            check_project(runtime.store, body.project_id)
        # image decoding (and HEIC via sips) is blocking work
        return await asyncio.to_thread(save_reference, runtime.home, body)

    @router.get("/references/{reference_id}")
    async def get_reference(reference_id: str):
        return reference_summary(runtime.home, reference_id)

    return router
