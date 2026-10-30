"""Agent browser and parts list routes for the app (admin token only; Plan 7 U4c).

Browser: GET /runs/{id}/browser lists the pages a run read (snapshot rows, oldest first;
`?full=1` returns whole snapshots), GET /browser/snapshots/{sid} returns one snapshot (title,
URL, text, tables, images as `proxy_url`s), and GET /browser/snapshots/{sid}/images/{n} is the
image proxy (see mises.browser.images). GET /runs/{id}/part_cards and GET
/browser/cards/{card_id} return the part cards agents showed. GET /browser/sites/defaults is
Mises's own suggested sites (the editor's "reset"); the user's list is the profile's
`suggested_sites` (PUT /profile).

Parts list: GET /projects/{id}/bom ({project_id, material, items, totals}),
POST /projects/{id}/bom (a user row), PATCH / DELETE /projects/{id}/bom/{item_id}, and
GET /projects/{id}/bom.csv. Every change publishes `bom_changed {project_id, item_id}`.

All page-derived text here is untrusted: the app shows it as plain text, never as HTML.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .. import bom as bom_mod
from .. import projects as _projects
from ..browser import images as images_mod
from ..browser.sites import DEFAULT_SITES
from ..browser.snapshot import public, summary
from ..events import Event
from ..profile import default_sites, load_profile, save_profile
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

_IMAGE_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
                  "Content-Security-Policy": "default-src 'none'"}


def build_browser_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    store = runtime.store

    def _run(run_id: str):
        try:
            return store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None

    # ---- browser
    @router.get("/runs/{run_id}/browser")
    async def run_browser(run_id: str, full: int = 0):
        _run(run_id)
        recs = runtime.snapshots.for_run(run_id)
        return [public(r) if full else summary(r) for r in recs]

    @router.get("/runs/{run_id}/part_cards")
    async def run_cards(run_id: str):
        _run(run_id)
        return runtime.part_cards.for_run(run_id)

    @router.get("/browser/snapshots/{sid}")
    async def snapshot(sid: str):
        return public(runtime.snapshots.get(sid))

    @router.get("/browser/snapshots/{sid}/images/{n}")
    async def snapshot_image(sid: str, n: int):
        rec = runtime.snapshots.get(sid)
        http = runtime.gate.services.get("browser_http") or {}
        png = await images_mod.proxied(runtime.home, rec, n, http)
        return Response(png, media_type="image/png", headers=_IMAGE_HEADERS)

    @router.get("/browser/cards/{card_id}")
    async def card(card_id: str):
        return runtime.part_cards.get(card_id)

    @router.get("/browser/sites/defaults")
    async def get_default_sites():
        return DEFAULT_SITES

    @router.post("/browser/sites/restore_defaults")
    async def restore_default_sites():
        """The Profile editor's "Restore defaults": the user's suggested sites become Mises's
        current defaults again (a saved profile otherwise keeps the list it was saved with)."""
        p = load_profile(runtime.home)
        p2 = p.model_copy(update={"suggested_sites": default_sites()})
        save_profile(runtime.home, p2)
        return p2.model_dump(mode="json")["suggested_sites"]

    # ---- parts list
    def _material() -> str:
        return load_profile(runtime.home).default_material

    def _view(project_id: str) -> dict:
        _projects._project_or_404(store, project_id)
        return bom_mod.view(store, runtime.bom, runtime.home, project_id,
                            catalog=runtime.catalog, material=_material(),
                            counts=_counts(project_id))

    def _counts(project_id: str) -> dict:
        return bom_mod.instance_counts(runtime.assembly_store, project_id)

    def _changed(project_id: str, item_id: str) -> None:
        runtime.events.publish(Event(type="bom_changed", data={"project_id": project_id,
                                                               "item_id": item_id}))

    def _row(project_id: str, item_id: str) -> dict:
        for it in _view(project_id)["items"]:
            if it["id"] == item_id:
                return it
        raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")

    async def _json(request: Request):
        try:
            return await request.json()
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be JSON") from None

    def _check_refs(project_id: str, data: dict) -> None:
        pid = data.get("part_id")
        if pid:
            part = store.get_part(pid)
            if part is None or part.project_id != project_id:
                raise MisesError(ErrorCode.NOT_FOUND, f"no part {pid!r} in this build")
        if data.get("catalog_id"):
            runtime.catalog.get(data["catalog_id"])

    @router.get("/projects/{project_id}/bom")
    async def get_bom(project_id: str):
        return _view(project_id)

    @router.get("/projects/{project_id}/bom.csv")
    async def bom_csv(project_id: str):
        v = _view(project_id)
        name = _projects._project_or_404(store, project_id).name
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "build"
        return Response(bom_mod.to_csv(v), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{safe[:60]} parts list.csv"',
            "X-Content-Type-Options": "nosniff"})

    @router.post("/projects/{project_id}/bom")
    async def add_bom(project_id: str, request: Request):
        _projects.check_project(store, project_id)
        data = bom_mod.validate(bom_mod.BomItemIn, await _json(request)).model_dump()
        _check_refs(project_id, data)
        row = bom_mod.add_row(runtime.bom, project_id, data, "user")
        _changed(project_id, row["id"])
        return _row(project_id, row["id"])

    @router.patch("/projects/{project_id}/bom/{item_id}")
    async def edit_bom(project_id: str, item_id: str, request: Request):
        _projects.check_project(store, project_id)
        patch = bom_mod.validate(bom_mod.BomPatch, await _json(request)).model_dump(
            exclude_unset=True)
        for k in ("name", "qty", "currency", "specs", "notes"):   # not nullable
            if k in patch and patch[k] is None:
                patch.pop(k)
        bom_mod.patch_row(store, runtime.bom, runtime.home, project_id, item_id, patch,
                          catalog=runtime.catalog, material=_material(),
                          counts=_counts(project_id))
        _changed(project_id, item_id)
        return _row(project_id, item_id)

    @router.delete("/projects/{project_id}/bom/{item_id}")
    async def delete_bom(project_id: str, item_id: str):
        _projects.check_project(store, project_id)
        bom_mod.delete_row(store, runtime.bom, project_id, item_id)
        _changed(project_id, item_id)
        return {"ok": True}

    return router
