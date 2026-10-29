"""Assembly routes (admin token). Instances, joints, a kinematic drive, interference and the
combined GLB for a build. Every mutation publishes ``assembly_changed``.

The GET returns each instance's SOLVED world transform (the assembled pose), not the raw stored
transform, so the viewer can render directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ..assembly.geometry import face_resolver, run_geometry
from ..assembly.model import Instance, Joint, JointKind, JointRef
from ..assembly.solve import solve
from ..events import Event
from ..types import ErrorCode, MisesError
from .ui_api import _contained, _etag_matches

if TYPE_CHECKING:
    from ..runtime import Runtime


class InstanceBody(BaseModel):
    part_id: str
    name: str
    transform: list[list[float]] | None = None
    fixed: bool = False


class InstancePatch(BaseModel):
    part_id: str | None = None
    name: str | None = None
    transform: list[list[float]] | None = None
    fixed: bool | None = None


class JointBody(BaseModel):
    kind: JointKind
    a: JointRef
    b: JointRef
    value: float | list[float] = 0.0
    limits: list[float] | None = None


class JointPatch(BaseModel):
    kind: JointKind | None = None
    a: JointRef | None = None
    b: JointRef | None = None
    value: float | list[float] | None = None
    limits: list[float] | None = None


class DriveBody(BaseModel):
    joint_id: str
    value: float | list[float]


def build_assembly_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    store = runtime.assembly_store

    def parts_root() -> Path:
        return Path(runtime.home) / "parts"

    def _solved(project_id: str):
        asm = store.get(project_id)
        return asm, solve(asm, resolve_face=face_resolver(parts_root()))

    def _changed(project_id: str) -> None:
        runtime.events.publish(Event(type="assembly_changed", data={"project_id": project_id}))

    def _not_found(e: KeyError) -> MisesError:
        return MisesError(ErrorCode.NOT_FOUND, f"not in this assembly: {e}")

    @router.get("/projects/{project_id}/assembly")
    async def get_assembly(project_id: str):
        asm, res = _solved(project_id)
        return {
            "instances": [{"id": i.id, "part_id": i.part_id, "name": i.name,
                           "transform": res.transforms[i.id], "fixed": i.fixed}
                          for i in asm.instances],
            "joints": [j.model_dump() for j in asm.joints],
            "dof_remaining": res.dof_remaining,
        }

    @router.post("/projects/{project_id}/assembly/instances", status_code=201)
    async def add_instance(project_id: str, body: InstanceBody):
        kw = {"part_id": body.part_id, "name": body.name, "fixed": body.fixed}
        if body.transform is not None:
            kw["transform"] = body.transform
        inst = store.add_instance(project_id, Instance(**kw))
        _changed(project_id)
        return inst.model_dump()

    @router.patch("/projects/{project_id}/assembly/instances/{iid}")
    async def patch_instance(project_id: str, iid: str, body: InstancePatch):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            inst = store.update_instance(project_id, iid, **fields)
        except KeyError as e:
            raise _not_found(e)
        _changed(project_id)
        return inst.model_dump()

    @router.delete("/projects/{project_id}/assembly/instances/{iid}")
    async def delete_instance(project_id: str, iid: str):
        removed = store.delete_instance(project_id, iid)
        _changed(project_id)
        return {"removed_instance": iid, "removed_joints": removed}

    @router.post("/projects/{project_id}/assembly/joints", status_code=201)
    async def add_joint(project_id: str, body: JointBody):
        joint = Joint(kind=body.kind, a=body.a, b=body.b, value=body.value, limits=body.limits)
        asm = store.get(project_id)
        trial = asm.model_copy(update={"joints": [*asm.joints, joint]})
        solve(trial, resolve_face=face_resolver(parts_root()))  # rejects loops / unknown refs
        try:
            store.add_joint(project_id, joint)
        except KeyError as e:
            raise _not_found(e)
        _changed(project_id)
        return joint.model_dump()

    @router.patch("/projects/{project_id}/assembly/joints/{jid}")
    async def patch_joint(project_id: str, jid: str, body: JointPatch):
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            joint = store.update_joint(project_id, jid, **fields)
        except KeyError as e:
            raise _not_found(e)
        except ValueError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, str(e))
        _changed(project_id)
        return joint.model_dump()

    @router.delete("/projects/{project_id}/assembly/joints/{jid}")
    async def delete_joint(project_id: str, jid: str):
        try:
            store.delete_joint(project_id, jid)
        except KeyError as e:
            raise _not_found(e)
        _changed(project_id)
        return {"removed_joint": jid}

    @router.post("/projects/{project_id}/assembly/drive")
    async def drive(project_id: str, body: DriveBody):
        try:
            store.get_joint(project_id, body.joint_id)
        except KeyError as e:
            raise _not_found(e)
        store.update_joint(project_id, body.joint_id, value=body.value)
        asm, res = _solved(project_id)
        _changed(project_id)
        return {"applied": res.applied.get(body.joint_id, body.value),
                "transforms": {i.id: res.transforms[i.id] for i in asm.instances}}

    @router.get("/projects/{project_id}/assembly/interference")
    async def interference(project_id: str):
        asm, res = _solved(project_id)
        try:
            geo = await run_geometry(parts_root(), asm, res, want="interference")
        except MisesError as e:
            if e.code == ErrorCode.NOT_FOUND:
                return []
            raise
        return geo.interferences

    @router.get("/projects/{project_id}/assembly.glb")
    async def assembly_glb(project_id: str, request: Request):
        asm, res = _solved(project_id)
        out = Path(runtime.home) / "assembly_cache" / project_id
        geo = await run_geometry(parts_root(), asm, res, want="glb", out_dir=out)
        real = _contained(Path(runtime.home), geo.glb_path)
        if real is None or not real.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, "assembly has no renderable geometry")
        st = real.stat()
        etag = f'"{st.st_mtime_ns}-{st.st_size}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(real.read_bytes(), media_type="model/gltf-binary", headers=headers)

    return router
