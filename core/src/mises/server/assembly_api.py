"""Assembly routes (admin token). Instances, joints, a kinematic drive, interference and the
combined GLB for a build. Every mutation publishes ``assembly_changed``.

The GET returns each instance's SOLVED world transform (the assembled pose), not the raw stored
transform, so the viewer can render directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from .. import mass as mass_mod
from ..assembly.geometry import face_resolver, part_dir, run_geometry
from ..assembly.model import (Coupling, Instance, Joint, JointDesign, JointKind, JointRef,
                              check_part_id)
from ..assembly.solve import SolveResult, solve, solve_from
from ..events import Event
from ..types import ErrorCode, MisesError, canonical_json, sha256_hex
from .ui_api import _contained, _etag_matches

if TYPE_CHECKING:
    from ..runtime import Runtime


class InstanceBody(BaseModel):
    part_id: str
    name: str
    transform: list[list[float]] | None = None
    fixed: bool = False

    @field_validator("part_id")
    @classmethod
    def _part_id(cls, v: str) -> str:
        return check_part_id(v)


class RemoveParts(BaseModel):
    part_ids: list[str]


class RestoreBody(BaseModel):
    """remove_parts' `undo`, sent back as is (the app's own answer; admin token only)."""
    instances: list[dict] = []
    joints: list[dict] = []


class InstancePatch(BaseModel):
    part_id: str | None = None
    name: str | None = None
    transform: list[list[float]] | None = None
    fixed: bool | None = None

    @field_validator("part_id")
    @classmethod
    def _part_id(cls, v: str | None) -> str | None:
        return None if v is None else check_part_id(v)


class JointBody(BaseModel):
    kind: JointKind
    a: JointRef
    b: JointRef
    value: float | list[float] = 0.0
    limits: list[float] | None = None
    coupling: Coupling | None = None       # Plan 10
    design: JointDesign | None = None


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
        from ..assembly.pose import solve_assembly       # the one pose (P11 F4)
        asm = store.get(project_id)
        return asm, solve_assembly(asm, parts_root())

    def _changed(project_id: str) -> None:
        runtime.events.publish(Event(type="assembly_changed", data={"project_id": project_id}))

    def _not_found(e: KeyError) -> MisesError:
        return MisesError(ErrorCode.NOT_FOUND, f"not in this assembly: {e}")

    @router.get("/projects/{project_id}/assembly")
    async def get_assembly(project_id: str):
        try:
            asm, res = _solved(project_id)
            solve_error = None
        except Exception as e:  # noqa: BLE001 - Plan 10: a bad joint never blanks the view
            asm = store.get(project_id)
            res = SolveResult(transforms={i.id: i.transform for i in asm.instances},
                              dof_remaining=0)
            solve_error = getattr(e, "message", None) or str(e)
        return {
            "instances": [{"id": i.id, "part_id": i.part_id, "name": i.name,
                           "transform": res.transforms[i.id], "fixed": i.fixed}
                          for i in asm.instances],
            "joints": [j.model_dump() for j in asm.joints],
            # Plan 10: closed chains (only when the build has some, so older payloads keep shape)
            **({"loops": [lp.model_dump() for lp in asm.loops],
                "loop_errors": res.loop_errors} if asm.loops else {}),
            "dof_remaining": res.dof_remaining,
            "overconstrained": res.overconstrained,
            **({"solve_error": solve_error} if solve_error else {}),
            # Plan 9 B2: each instance x its part's tree mass; parts with no known mass listed
            **await run_in_threadpool(mass_mod.assembly_total, runtime.store,
                                      Path(runtime.home), project_id, asm.instances),
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
        except ValueError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, str(e))
        _changed(project_id)
        return inst.model_dump()

    @router.delete("/projects/{project_id}/assembly/instances/{iid}")
    async def delete_instance(project_id: str, iid: str):
        try:
            removed = store.delete_instance(project_id, iid)
        except KeyError as e:
            raise _not_found(e)
        _changed(project_id)
        return {"removed_instance": iid, "removed_joints": removed}

    # Plan 9 D29: the parts tree's "Remove from assembly" and its Undo (exact: ids, places, the
    # placed-by-core mark, and every joint that came loose)
    @router.post("/projects/{project_id}/assembly/remove_parts")
    async def remove_parts(project_id: str, body: RemoveParts):
        taken = store.take_out(project_id, body.part_ids)
        if taken["instances"]:
            _changed(project_id)
        return {"removed_instances": [i["id"] for i in taken["instances"]],
                "removed_joints": [j["id"] for j in taken["joints"]], "undo": taken}

    @router.post("/projects/{project_id}/assembly/restore")
    async def restore(project_id: str, body: RestoreBody):
        ni, nj = store.put_back(project_id, {"instances": body.instances, "joints": body.joints})
        if ni or nj:
            _changed(project_id)
        return {"restored_instances": ni, "restored_joints": nj}

    @router.post("/projects/{project_id}/assembly/joints", status_code=201)
    async def add_joint(project_id: str, body: JointBody):
        joint = Joint(kind=body.kind, a=body.a, b=body.b, value=body.value, limits=body.limits,
                      coupling=body.coupling, design=body.design)
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
        before = {j.id: j.value for j in store.get(project_id).joints}
        store.update_joint(project_id, body.joint_id, value=body.value)
        asm = store.get(project_id)
        res = solve_from(asm, before, resolve_face=face_resolver(parts_root()))
        if res.solved:              # Plan 10: coupled / loop joints follow the driven one
            store.set_values(project_id, res.solved)
        _changed(project_id)
        return {"applied": res.applied.get(body.joint_id, body.value),
                "transforms": {i.id: res.transforms[i.id] for i in asm.instances}}

    @router.get("/projects/{project_id}/assembly/animations")
    async def animations(project_id: str):
        """Plan 9 G-D: the build's saved animations, oldest first (Play plays the newest)."""
        return store.list_animations(project_id)

    @router.get("/projects/{project_id}/assembly/interference")
    async def interference(project_id: str):
        """G2: the same list assembly_health reports (assembly/interference.py): pairs over
        the tolerance with ids, volume and where; a declared contact carries `intended`."""
        from ..assembly.interference import interferences
        asm, res = _solved(project_id)
        try:
            return await interferences(store, parts_root(), project_id, asm=asm, res=res)
        except MisesError as e:
            if e.code == ErrorCode.NOT_FOUND:
                return []
            raise

    def _glb_key(asm, res) -> str:
        """Content hash of everything the GLB depends on: each instance's name, part and solved
        transform, plus the part's version (its built model.step's mtime_ns and size; any rebuild
        changes it). A cache hit therefore can never be stale."""
        items = []
        for i in asm.instances:
            step = part_dir(parts_root(), i.part_id) / "model.step"
            st = step.stat() if step.is_file() else None
            items.append({"name": i.name or i.id, "part_id": i.part_id,
                          "transform": res.transforms[i.id],
                          "part_version": [st.st_mtime_ns, st.st_size] if st else None})
        return sha256_hex(canonical_json(items))[:32]

    @router.get("/projects/{project_id}/assembly.glb")
    async def assembly_glb(project_id: str, request: Request):
        asm, res = _solved(project_id)
        key = _glb_key(asm, res)
        etag = f'"{key}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        out = Path(runtime.home) / "assembly_cache" / key
        cached = out / "assembly.glb"
        if not cached.is_file():
            geo = await run_geometry(parts_root(), asm, res, want="glb", out_dir=out)
            cached = geo.glb_path
        real = _contained(Path(runtime.home), cached) if cached is not None else None
        if real is None or not real.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, "assembly has no renderable geometry")
        return Response(real.read_bytes(), media_type="model/gltf-binary", headers=headers)

    return router
