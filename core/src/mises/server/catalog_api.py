"""Parts directory routes for the app (admin token only).

Changes go through the Gate like an agent's would, as UI calls on the shared "ui" run
(Runtime.ui_tool_call), so they get the same Steps, events and checks; the trusted call meta
marks them as the user's own (no approval card for a link the user pasted, and only the app
may `refresh` an item already in the directory).

POST /catalog/import fetches the page in the core (https only, 2 MB, 10 s, no cookies, pinned
to the vetted address, redirects re-checked; see catalog.importer), saves the extracted item
and returns it, page text included as `raw_text` for the review screen to show as plain text.
A URL already in the directory comes back unchanged (`existing: true`) unless `refresh: true`.
PATCH /catalog/items/{id} saves the user's review edits; DELETE discards an imported item.
Built-in items are read-only. POST/DELETE /catalog/allowed_domains is the card's "Always allow
<domain>": agent imports from those domains ask no card.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..catalog import CATEGORIES
from ..catalog.builtin import categories as builtin_categories
from ..catalog.importer import check_url, item_id_for
from ..profile import Profile, load_profile, save_profile
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime


class ImportBody(BaseModel):
    url: str = Field(max_length=2048)
    project_id: str | None = Field(None, max_length=64)
    refresh: bool = False


class InsertBody(BaseModel):
    project_id: str | None = Field(None, max_length=64)
    part_id: str | None = None


class DomainBody(BaseModel):
    domain: str = Field(max_length=253)


async def ui_call(runtime: Runtime, tool: str, args: dict, meta: dict | None = None,
                  project_id: str | None = None) -> dict:
    """Run a tool as the app's own call through the Gate and wait for it: its ToolOutput data,
    or the MisesError it failed with. `project_id`: on that build's ui run."""
    call = runtime.ui_tool_call(tool, args, meta=meta, project_id=project_id)
    try:
        await asyncio.shield(call.task)
    except asyncio.CancelledError:
        pass
    if call.status == "done" and call.output is not None:
        return call.output.get("data") or {}
    err = call.error or (call.output or {}).get("data") or {}
    code = err.get("error") if err.get("error") in set(ErrorCode) else ErrorCode.TOOL_FAILED
    raise MisesError(ErrorCode(code), err.get("message") or (call.output or {}).get("summary")
                     or f"{tool} failed", hint=err.get("hint"))


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
        url = check_url(body.url)
        data = await ui_call(runtime, "catalog_import_url", {"url": url}, meta={
            "url": url, "refresh": body.refresh, "project_id": body.project_id})
        # the full item (page text included) for the review screen
        return {**cat.get(item_id_for(url)), "existing": bool(data.get("existing"))}

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
        cat.get(item_id)                                 # 404 before any work
        args = {"item_id": item_id, "project_id": body.project_id}
        if body.part_id is not None:
            args["part_id"] = body.part_id
        data = await ui_call(runtime, "catalog_insert", args)
        return _part_result(runtime, data)

    @router.get("/catalog/allowed_domains")
    async def allowed():
        return load_profile(runtime.home).catalog_allowed_domains

    @router.post("/catalog/allowed_domains")
    async def allow(body: DomainBody):
        """The approval card's "Always allow <domain>" (a user action; agents cannot reach it)."""
        p = load_profile(runtime.home)
        try:
            p2 = Profile.model_validate({**p.model_dump(), "catalog_allowed_domains":
                                         [*p.catalog_allowed_domains, body.domain]})
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, f"{body.domain!r} is not a domain name",
                             hint="e.g. shop.example.com") from None
        save_profile(runtime.home, p2)
        return p2.catalog_allowed_domains

    @router.delete("/catalog/allowed_domains/{domain}")
    async def disallow(domain: str):
        p = load_profile(runtime.home)
        keep = [d for d in p.catalog_allowed_domains if d != domain.lower()]
        save_profile(runtime.home, p.model_copy(update={"catalog_allowed_domains": keep}))
        return keep

    return router


def _part_result(runtime: Runtime, data: dict) -> dict:
    """The part a UI call created or found, with its stored metrics (read from disk: the tool
    result stays small)."""
    from ..cad import safe_io

    pid = data["part_id"]
    d = runtime.home / "parts" / pid
    try:  # safe_io: never follows a link a build left in the part dir
        metrics = safe_io.read_json(d, "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        metrics = None
    meta = data.get("meta")
    if meta is None:
        meta = (data.get("untrusted_user_data") or {}).get("meta")
    return {"part_id": pid, "existing": bool(data.get("existing")), "metrics": metrics,
            "meta": meta}
