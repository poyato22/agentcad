"""Builds (projects), part-tree edits and custom-agent CRUD for the Mac app.

Admin token only (app.py's bearer check refuses the MCP-only token everywhere but /mcp and
/health), so no agent reaches these routes. The part-tree /source, /vars and /ops routes are
owned by Task C6; this router covers the rest of the Builds and Part-tree contracts.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import projects as _projects
from ..agent import definitions as _defs
from ..appearance import render_params
from ..cad import thumbs
from ..events import Event
from ..profile import load_profile, save_profile
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime


class NewProject(BaseModel):
    name: str


class PatchProject(BaseModel):
    name: str | None = None


class Reorder(BaseModel):
    project_id: str
    parent_id: str | None = None
    ids: list[str]


class RestoreBody(BaseModel):
    undo: str  # DELETE /parts/{id}'s `undo`


class ExportBody(BaseModel):
    format: str  # "step" | "stl" (checked by projects.export_part: a 400, not a 422)


class NewAgent(BaseModel):
    name: str
    description: str = ""
    model: str = "inherit"
    tools: list[str] | None = None
    body: str = ""


class EditAgent(BaseModel):
    description: str = ""
    model: str = "inherit"
    tools: list[str] | None = None
    body: str | None = None  # None (left out): keep the agent's current instructions


def build_projects_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    store = runtime.store

    def _home() -> Path:
        return Path(runtime.home)

    # ---- builds
    @router.get("/projects")
    async def list_projects():
        # every request belongs to a build: on a fresh home (or after the last live build was
        # purged) the Default is made here too, not only by the first project-less request.
        # Otherwise the app's first GET /projects raced its GET /parts and saw no build at all
        # (no tabs until the next reconnect).
        runtime.active_project_id()
        return _projects.list_projects(store, _home())

    @router.post("/projects")
    async def create_project(body: NewProject):
        return _projects.create_project(store, body.name)

    @router.patch("/projects/{project_id}")
    async def patch_project(project_id: str, body: PatchProject):
        if body.name is None:
            return _projects.project_view(store, _projects._project_or_404(store, project_id),
                                          _home())
        return _projects.rename_project(store, project_id, body.name)

    @router.delete("/projects/{project_id}")
    async def trash_project(project_id: str):
        out = _projects.trash_project(store, project_id)
        # a trashed build is neither active nor an open tab any more
        prof = load_profile(_home())
        if prof.active_project_id == project_id or project_id in prof.open_project_ids:
            prof.open_project_ids = [p for p in prof.open_project_ids if p != project_id]
            if prof.active_project_id == project_id:
                prof.active_project_id = _projects.active_project_id(store, None)
            save_profile(_home(), prof)
        return out

    @router.get("/projects/{project_id}/skeleton")
    async def project_skeleton(project_id: str):
        """The build's layout skeleton (G3) and the parts that read it: skeleton_get's answer,
        so the app's skeleton panel fills without waiting for an agent's run steps. A build
        with no skeleton answers {skeleton: null, version: 0}."""
        _projects._project_or_404(store, project_id)
        from ..cad.skeleton_tools import skeleton_view
        return await run_in_threadpool(skeleton_view, _home(), store, project_id)

    def _cover_placements(home: Path, project_id: str) -> list[thumbs.Placement]:
        """What the cover draws: every assembly instance at its solved world transform (a solve
        that fails keeps the stored transforms, as the agents' views do), each in its part's
        finish; with no instance, the build's largest built part (by volume) in its own frame.
        Thumbs skips parts without a mesh."""
        from ..assembly.geometry import face_resolver
        from ..assembly.solve import solve
        asm_store = getattr(runtime, "assembly_store", None)
        insts = []
        if asm_store is not None:
            asm = asm_store.get(project_id)
            insts = list(asm.instances)
            tf: dict = {}
            if insts:
                try:
                    tf = solve(asm, resolve_face=face_resolver(home / "parts")).transforms
                except Exception:  # noqa: BLE001 - a broken solve still shows the stored layout
                    tf = {}
        rows: dict = {}

        def part(pid: str):
            if pid not in rows:
                rows[pid] = store.get_part(pid)
            return rows[pid]
        placed = [(i.part_id, _projects.part_version(home, r), tf.get(i.id) or i.transform,
                   render_params(r))
                  for i in insts if (r := part(i.part_id)) is not None]
        if any(thumbs.mesh_stamp(home, p[0]) for p in placed):
            return placed
        built = [p for p in _projects.list_parts(store, home, project_id)
                 if p.get("kind") == "part" and thumbs.mesh_stamp(home, p["part_id"])]
        if not built:
            return []
        big = max(built, key=lambda p: float((p.get("metrics") or {}).get("volume_mm3") or 0))
        r = store.get_part(big["part_id"])
        return [(big["part_id"], big["version"], None, render_params(r) if r else None)]

    @router.get("/projects/{project_id}/thumb.png")
    async def project_thumb(project_id: str, request: Request, size: int = thumbs.COVER_W):
        """The build's Home cover (D24): one sandboxed render of its assembly, every instance at
        its solved pose in its finish, 16:10 (`size` is the width), background transparent;
        the largest built part when nothing is placed. 404 until a part has a built mesh."""
        _projects._project_or_404(store, project_id)
        home = _home()
        placed = await run_in_threadpool(_cover_placements, home, project_id)
        key = thumbs.cover_key(home, placed, size)
        etag = f'"proj-{project_id}-{key}"'                       # = the cache key
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        inm = request.headers.get("if-none-match")
        if key is not None and inm and any(t.strip().removeprefix("W/") == etag
                                           for t in inm.split(",")):
            return Response(status_code=304, headers=headers)
        png = await thumbs.project_cover(home, project_id, placed, size)
        if png is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no preview for build {project_id!r} yet",
                             hint="it has no parts with a built mesh")
        return Response(png, media_type="image/png", headers=headers)

    @router.post("/projects/{project_id}/restore")
    async def restore_project(project_id: str):
        return _projects.restore_project(store, project_id)

    @router.post("/projects/trash/empty")
    async def empty_trash():
        try:
            return runtime.empty_trash()
        except _projects.TrashBusy as e:
            return JSONResponse(e.body(), status_code=409)

    @router.post("/projects/{project_id}/purge")
    async def purge_project(project_id: str):
        """Delete one trashed build for good (Home's "Delete forever"): Empty Trash for that
        build only, with the same live-work refusal (409)."""
        try:
            return runtime.empty_trash(only=project_id)
        except _projects.TrashBusy as e:
            return JSONResponse(e.body(), status_code=409)

    # ---- part tree (metadata only; GET /parts lives in ui_api; /source, /vars, /ops are C6)
    @router.patch("/parts/{part_id}")
    async def patch_part(part_id: str, request: Request):
        # PATCH semantics: a key left out is unchanged; an explicit null clears color/parent
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be a JSON object")
        return _projects.patch_part(
            store, _home(), part_id, label=body.get("label"),
            color=body["color"] if "color" in body else ...,
            parent_id=body["parent_id"] if "parent_id" in body else ...,
            appearance=body["appearance"] if "appearance" in body else ...)

    @router.post("/parts/reorder")
    async def reorder(body: Reorder):
        return _projects.reorder_parts(store, _home(), body.project_id, body.parent_id, body.ids)

    # ---- the parts manager's menu (Plan 7 U8)
    @router.post("/parts/{part_id}/duplicate")
    async def duplicate_part(part_id: str):
        # under the part lock (cad.tools._lock): never a copy mixing an agent's new model.step
        # with the old metrics.json mid-accept
        from ..cad.tools import PART_ID_RE, _lock as part_lock
        if not PART_ID_RE.match(part_id):        # not a buildable id: 404 / 400 from projects
            return await run_in_threadpool(_projects.duplicate_part, store, _home(), part_id)
        async with part_lock(_home() / "parts" / part_id):
            return await run_in_threadpool(_projects.duplicate_part, store, _home(), part_id)

    def _assembly_changed(project_id: str | None) -> None:
        events = getattr(runtime, "events", None)
        if events is not None and project_id:
            events.publish(Event(type="assembly_changed", data={"project_id": project_id}))

    @router.delete("/parts/{part_id}")
    async def delete_part(part_id: str, cascade: bool = False):
        # the same live-work rule as the build's Empty Trash (409 while an agent may edit it);
        # Plan 9 D29: ?cascade=true also removes the part's instances from the assembly
        project = getattr(store.get_part(part_id), "project_id", None)
        try:
            out = _projects.delete_part(store, _home(), part_id,
                                        assembly=getattr(runtime, "assembly_store", None),
                                        busy=getattr(runtime, "_busy_for_trash", None),
                                        cascade=cascade)
        except _projects.PartBusy as e:
            return JSONResponse(e.body(), status_code=409)
        if out["removed_instances"]:
            _assembly_changed(project)
        return out

    @router.post("/parts/{part_id}/restore")
    async def restore_part(part_id: str, body: RestoreBody):
        """Undo of DELETE /parts/{id}: the files, the tree row and the assembly instances."""
        from ..cad.tools import PART_ID_RE, _lock as part_lock
        if not PART_ID_RE.match(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}")
        async with part_lock(_home() / "parts" / part_id):   # as delete: never mid-build
            out = _projects.restore_part(store, _home(), part_id, body.undo,
                                         assembly=getattr(runtime, "assembly_store", None))
        if out["restored_instances"]:
            _assembly_changed(out.get("project_id"))
        return out

    @router.post("/parts/{part_id}/export")
    async def export_part(part_id: str, body: ExportBody):
        return await run_in_threadpool(_projects.export_part, store, _home(), part_id, body.format)

    # ---- custom agents (built-ins can only be hidden, via the profile)
    def _reload():
        runtime.reload_agents()

    def _refuse_builtin(name: str) -> None:
        d = runtime.definitions.get(name)
        if d is not None and d.source is None:
            raise MisesError(ErrorCode.DENIED, f"{name!r} is a built-in agent; it can be hidden "
                             "but not changed or deleted", hint="hide it via the profile instead")

    @router.post("/agents")
    async def create_agent(body: NewAgent):
        if body.name in runtime.definitions:
            raise MisesError(ErrorCode.INVALID_INPUT, f"agent {body.name!r} already exists")
        d = _defs.write_custom_agent(_home(), name=body.name, description=body.description,
                                     model=body.model, tools=body.tools, body=body.body,
                                     create=True)
        _reload()
        return {"name": d.name, "description": d.description, "model": d.model, "tools": d.tools,
                "builtin": False, "hidden": False}

    @router.get("/agents/{name}")
    async def get_agent(name: str):
        """One agent with its instructions (`body`), for the app's edit form. A custom agent's
        body is the user's own file in the Mises home; a built-in's prompt isn't returned (null)."""
        d = runtime.definitions.get(name)
        if d is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no agent {name!r}")
        hidden = name in set(runtime.profile().hidden_agents)
        return {"name": d.name, "description": d.description, "model": d.model, "tools": d.tools,
                "builtin": d.source is None, "hidden": hidden,
                # stripped like the writer (render_definition), so it round-trips exactly
                "body": d.body.strip() if d.source is not None else None}

    @router.put("/agents/{name}")
    async def update_agent(name: str, body: EditAgent):
        _refuse_builtin(name)
        text = body.body
        if text is None:                    # a PUT without a body keeps the current instructions
            current = runtime.definitions.get(name)
            if current is None:
                raise MisesError(ErrorCode.NOT_FOUND, f"no custom agent {name!r}")
            text = current.body
        d = _defs.write_custom_agent(_home(), name=name, description=body.description,
                                     model=body.model, tools=body.tools, body=text,
                                     create=False)
        _reload()
        hidden = name in set(runtime.profile().hidden_agents)
        return {"name": d.name, "description": d.description, "model": d.model, "tools": d.tools,
                "builtin": False, "hidden": hidden}

    @router.delete("/agents/{name}")
    async def delete_agent(name: str):
        _refuse_builtin(name)
        _defs.delete_custom_agent(_home(), name)
        _reload()
        # a deleted agent also drops out of the profile's hidden list
        prof = load_profile(_home())
        if name in prof.hidden_agents:
            prof.hidden_agents = [a for a in prof.hidden_agents if a != name]
            save_profile(_home(), prof)
        return {"ok": True}

    return router
