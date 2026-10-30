"""Builds (projects), part-tree edits and custom-agent CRUD for the Mac app.

Admin token only (app.py's bearer check refuses the MCP-only token everywhere but /mcp and
/health), so no agent reaches these routes. The part-tree /source, /vars and /ops routes are
owned by Task C6; this router covers the rest of the Builds and Part-tree contracts.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import projects as _projects
from ..agent import definitions as _defs
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
        return _projects.list_projects(store)

    @router.post("/projects")
    async def create_project(body: NewProject):
        return _projects.create_project(store, body.name)

    @router.patch("/projects/{project_id}")
    async def patch_project(project_id: str, body: PatchProject):
        if body.name is None:
            return _projects.project_view(store, _projects._project_or_404(store, project_id))
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

    @router.post("/projects/{project_id}/restore")
    async def restore_project(project_id: str):
        return _projects.restore_project(store, project_id)

    @router.post("/projects/trash/empty")
    async def empty_trash():
        try:
            return runtime.empty_trash()
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
            parent_id=body["parent_id"] if "parent_id" in body else ...)

    @router.post("/parts/reorder")
    async def reorder(body: Reorder):
        return _projects.reorder_parts(store, _home(), body.project_id, body.parent_id, body.ids)

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
