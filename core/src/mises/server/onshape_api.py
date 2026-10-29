"""Onshape browser routes for the app (admin token only): documents, elements, thumbnails,
import into a build, and the live budget.

Every route that reaches Onshape makes real, ledgered requests with the user's keys (one per
documents page, one per elements list, one per uncached thumbnail, two per import) and each is
followed by an `onshape_budget` event. Thumbnails are fetched only for Onshape thumbnail URLs
(https, *.onshape.com, /api/.../thumbnails/...), capped at 512 KB and cached under
<home>/onshape/thumbnails, so the grid costs each image once. Nothing here writes to Onshape.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from ..onshape import browse
from ..onshape.adapter import OnshapeAdapter
from ..types import ErrorCode, MisesError
from .catalog_api import _part_result, ui_call

if TYPE_CHECKING:
    from ..runtime import Runtime

_ID = r"^[0-9a-fA-F]{24}$"


class ImportBody(BaseModel):
    did: str = Field(pattern=_ID)
    wid: str = Field(pattern=_ID)
    eid: str = Field(pattern=_ID)
    project_id: str | None = Field(None, max_length=64)


def build_onshape_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    def adapter() -> OnshapeAdapter:
        ad = runtime.gate.services.get("onshape")
        if ad is None:
            raise MisesError(ErrorCode.DENIED, "Onshape is not connected",
                             hint="add the Onshape API keys in Settings")
        return ad

    def thumbs_dir() -> Path:
        return Path(runtime.home) / "onshape" / "thumbnails"

    @router.get("/onshape/documents")
    async def documents(q: str = "", owner: str = "me", offset: int = 0,
                        limit: int = browse.PAGE_MAX):
        """One page, newest change first; page on with offset (a short page is the last)."""
        return await browse.list_documents(adapter().client, q=q,
                                           owner="me" if owner == "me" else None,
                                           offset=offset, limit=limit)

    @router.get("/onshape/documents/{did}/elements")
    async def elements(did: str, wid: str):
        return await browse.list_elements(adapter().client, did, wid)

    @router.get("/onshape/thumbnail")
    async def thumbnail(href: str):
        data, mime = await browse.thumbnail(adapter().client, thumbs_dir(), href)
        return Response(data, media_type=mime,
                        headers={"Cache-Control": "private, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})

    @router.post("/onshape/import")
    async def import_element(body: ImportBody):
        """Through the Gate as the app's own call (onshape_import_element on the ui run)."""
        adapter()                                       # 403 before a call when not connected
        data = await ui_call(runtime, "onshape_import_element", body.model_dump())
        return _part_result(runtime, data)

    @router.get("/onshape/budget")
    async def budget(run_id: str | None = None):
        if run_id is not None:
            try:
                runtime.store.get_run(run_id)
            except KeyError:
                raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None
        return runtime.onshape_budget(run_id)

    return router
