"""Parts directory routes for the app (admin token only).

POST /catalog/import fetches the page in the core (https only, 2 MB, 10 s, no cookies,
redirects re-checked; see catalog.importer), saves the extracted item and returns it, page text
included as `raw_text` for the review screen to show as plain text. PATCH /catalog/items/{id}
saves the user's review edits; DELETE discards an imported item. Built-in items are read-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..catalog import CATEGORIES
from ..catalog.builtin import categories as builtin_categories
from ..catalog.geometry import insert_item
from ..catalog.importer import import_url
from ..events import Event
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime


class ImportBody(BaseModel):
    url: str = Field(max_length=2048)
    project_id: str | None = Field(None, max_length=64)


class InsertBody(BaseModel):
    project_id: str | None = Field(None, max_length=64)
    part_id: str | None = None


def build_catalog_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    cat = runtime.catalog

    @router.get("/catalog/search")
    async def search(q: str = "", category: str | None = None, project_id: str | None = None,
                     limit: int = 50):
        return cat.search(q, category or None, project_id=project_id, limit=limit)

    @router.get("/catalog/categories")
    async def categories():
        present = set(builtin_categories())
        return [c for c in CATEGORIES if c in present or c == "other"]

    @router.get("/catalog/items/{item_id}")
    async def get_item(item_id: str):
        return cat.get(item_id)

    @router.post("/catalog/import")
    async def import_page(body: ImportBody):
        http = runtime.gate.services.get("catalog_http") or {}
        item = await import_url(body.url, **http)
        return cat.save(item, body.project_id)

    @router.patch("/catalog/items/{item_id}")
    async def edit_item(item_id: str, request: Request):
        try:
            patch = await request.json()
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be JSON") from None
        if not isinstance(patch, dict):
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be an object")
        return cat.update(item_id, patch)

    @router.delete("/catalog/items/{item_id}")
    async def delete_item(item_id: str):
        cat.delete(item_id)
        return {"ok": True}

    @router.post("/catalog/items/{item_id}/insert")
    async def insert(item_id: str, body: InsertBody):
        item = cat.get(item_id)
        out = await insert_item(Path(runtime.home) / "parts", item, body.project_id,
                                part_id=body.part_id)
        if not out["existing"]:
            runtime.events.publish(Event(type="part_changed", data={
                "part_id": out["part_id"], "version": 1, "by": "user", "run_id": None,
                "summary": f"inserted catalog item {item_id}"}))
        return out

    return router
