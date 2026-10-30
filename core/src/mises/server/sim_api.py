"""Studies page routes: study types and their settings schemas, start / cancel / list / read
studies, the result GLB, chart data, probe and section values, compare, saved templates and
custom materials. Admin token only (like every non-MCP route).

A UI-started study is not a side door: POST /sims creates a "studies" run (scope: that part,
classes read + local_mutate) and calls sim_run (or, for the original body shape, sim_static_run)
through the Gate, so it gets the same Steps, events, scope checks and timeouts as an agent's
call. It runs in the background; the page follows `sim_progress {sim_id, phase, pct, detail}`
/ `run_finished` events or polls GET /sims?run_id=. Cancel with POST /sims/{sim_id}/cancel or
POST /runs/{run_id}/cancel (both kill the mesher / solver).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..cad import safe_io
from ..cad.tools import PART_ID_RE
from ..sim import studies
from ..sim.ccx import solver_status
from ..sim.cfd import cfd_solver_status
from ..sim.charts import charts
from ..sim.fields import FIELDS_FILE, Fields
from ..sim.materials import (MATERIALS, custom_material, delete_custom, load_custom,
                             upsert_custom)
from ..sim.settings import CustomMaterial
from ..sim.tools import (SIM_ID_RE, StaticArgs, find_sim, legacy_settings, list_sims,
                         read_record, register_sim_tools)
from ..sim.types import SimError
from ..types import ErrorCode, MisesError, SafetyClass
from .ui_api import _etag_matches

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

UI_AGENT = "studies"
SLICES = ("slice_xz", "slice_xy")


class StartBody(BaseModel):
    type: Literal["static", "modal", "buckling", "thermal", "drop", "wind"]
    part_id: str | None = None
    project_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(None, max_length=200)
    requirements: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


class CompareBody(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=8)


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    type: Literal["static", "modal", "buckling", "thermal", "drop", "wind"]
    settings: dict[str, Any] = Field(default_factory=dict)


def _check_part(part_id: str) -> None:
    if not PART_ID_RE.fullmatch(part_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                         hint=f"part ids match {PART_ID_RE.pattern}")


def _check_sim(sim_id: str) -> None:
    if not SIM_ID_RE.fullmatch(sim_id):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad sim id: {sim_id!r}",
                         hint=f"sim ids match {SIM_ID_RE.pattern}")


MAX_SECTION_N = 64


def _finite(**vals: float) -> None:
    for k, v in vals.items():
        if not math.isfinite(v) or abs(v) > 1e7:
            raise MisesError(ErrorCode.INVALID_INPUT, f"{k} must be a finite number (mm)")


def _bad(e: SimError) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint)


def build_sim_router(runtime: Runtime) -> APIRouter:
    register_sim_tools(runtime.registry)  # no-op: Runtime registers them; kept for bare registries
    router = APIRouter()
    tasks: set[asyncio.Task] = set()

    def parts_root() -> Path:
        return Path(runtime.home) / "parts"

    def _interrupted(rec: dict) -> dict:
        # "running" on disk but its run is no longer running (Mises restarted mid-study)
        if rec.get("status") == "running" and rec.get("run_id"):
            try:
                if runtime.store.get_run(rec["run_id"]).status != "running":
                    rec = {**rec, "status": "interrupted"}
            except KeyError:
                rec = {**rec, "status": "interrupted"}
        return rec

    # ---- fixed paths first (before /sims/{sim_id})

    @router.get("/sims/solvers")
    async def solvers():
        st = await run_in_threadpool(solver_status)
        return {**st, "cfd": await run_in_threadpool(cfd_solver_status)}

    @router.get("/sims/types")
    async def types():
        return await run_in_threadpool(studies.types_list)

    @router.post("/sims/compare")
    async def compare(body: CompareBody):
        recs = []
        for sid in body.ids:
            recs.append(_interrupted(_record(_dir(sid))))
        return studies.compare(recs)

    @router.get("/sims/templates")
    async def templates():
        return studies.load_templates(runtime.home)

    @router.post("/sims/templates")
    async def add_template(body: TemplateBody):
        try:
            t = studies.add_template(runtime.home, body.name, body.type, body.settings)
        except SimError as e:
            if e.code == "conflict":
                return JSONResponse({"error": "conflict", "message": e.message,
                                     "hint": e.hint}, status_code=409)
            raise _bad(e) from None
        return JSONResponse(t, status_code=201)

    @router.delete("/sims/templates/{tpl_id}")
    async def del_template(tpl_id: str):
        if not studies.TEMPLATE_ID_RE.fullmatch(tpl_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad template id: {tpl_id!r}")
        if not studies.delete_template(runtime.home, tpl_id):
            raise MisesError(ErrorCode.NOT_FOUND, f"no template {tpl_id}")
        return {"deleted": tpl_id}

    @router.get("/sims/materials")
    async def materials():
        return {"builtin": [m.to_dict() for m in MATERIALS.values()],
                "custom": [m.to_dict() for m in load_custom(runtime.home)]}

    @router.post("/sims/materials")
    async def add_material(body: CustomMaterial):
        try:
            m = upsert_custom(runtime.home, custom_material(body.model_dump()))
        except SimError as e:
            raise _bad(e) from None
        return JSONResponse(m.to_dict(), status_code=201)

    @router.delete("/sims/materials/{name}")
    async def del_material(name: str):
        if not delete_custom(runtime.home, name):
            raise MisesError(ErrorCode.NOT_FOUND, f"no custom material {name!r}")
        return {"deleted": name}

    @router.get("/sims")
    async def sims(part_id: str | None = None, run_id: str | None = None):
        if part_id is not None:
            _check_part(part_id)
        return [_interrupted(r) for r in list_sims(parts_root(), part_id, run_id)]

    def _record(d: Path) -> dict:
        """A study record; staleness against the parts dir the study lives in (the user's
        parts, or a design-study variant's own)."""
        return read_record(d, d.parent.parent.parent)

    def _dir(sim_id: str) -> Path:
        _check_sim(sim_id)
        d = find_sim(parts_root(), sim_id)
        if d is None:
            # a design study's kept variants (C3: <home>/studies/<study>/<variant>/parts/...):
            # the spec panel's scatter opens their stress studies in A8's immersive view
            studies_root = Path(runtime.home) / "studies"
            if studies_root.is_dir():
                for proot in sorted(studies_root.glob("study_*/e*/parts")):
                    d = find_sim(proot, sim_id)
                    if d is not None:
                        break
        if d is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no study {sim_id}")
        return d

    def _file(sim_id: str, name: str, what: str) -> bytes:
        """The study file's bytes, read without following links (C6 safe_io): the sims dir
        lives in the part dir, which the build sandbox can write."""
        d = _dir(sim_id)
        try:
            data = safe_io.read_bytes(d, name)
        except safe_io.UnsafePath:
            data = None
        if data is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"study {sim_id} has no {what}",
                             hint="only finished studies have one")
        return data

    def _fields(sim_id: str) -> Fields:
        data = _file(sim_id, FIELDS_FILE, "result fields")
        try:
            return Fields(data)
        except (OSError, ValueError, KeyError) as e:
            raise MisesError(ErrorCode.NOT_FOUND, f"study {sim_id} fields unreadable: {e}") \
                from None

    def _send(data: bytes, media: str, request: Request) -> Response:
        etag = f'"{hashlib.sha256(data).hexdigest()[:24]}-{len(data)}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(data, media_type=media, headers=headers)

    @router.get("/sims/{sim_id}")
    async def sim(sim_id: str):
        rec = _interrupted(_record(_dir(sim_id)))
        return {**rec, "card": studies.card(rec)}

    @router.get("/sims/{sim_id}/result.glb")
    async def sim_glb(sim_id: str, request: Request):
        return _send(_file(sim_id, "result.glb", "result model"), "model/gltf-binary", request)

    @router.get("/sims/{sim_id}/slices/{name}.png")
    async def sim_slice(sim_id: str, name: str, request: Request):
        if name not in SLICES:
            raise MisesError(ErrorCode.NOT_FOUND, f"no slice {name!r}",
                             hint=f"slices: {', '.join(SLICES)}")
        return _send(_file(sim_id, f"{name}.png", "velocity slice"), "image/png", request)

    @router.get("/sims/{sim_id}/charts")
    async def sim_charts(sim_id: str):
        d = _dir(sim_id)
        rec = _interrupted(_record(d))
        return await run_in_threadpool(charts, rec, d)

    @router.get("/sims/{sim_id}/probe")
    async def probe(sim_id: str, x: float, y: float, z: float):
        _finite(x=x, y=y, z=z)
        f = await run_in_threadpool(_fields, sim_id)
        r = (await run_in_threadpool(f.probe, [[x, y, z]]))[0]
        return {"sim_id": sim_id, "point": [x, y, z], "units": f.units, **r}

    @router.get("/sims/{sim_id}/section")
    async def section(sim_id: str, axis: Literal["x", "y", "z"], offset: float,
                      channel: str, n: int = 32):
        _finite(offset=offset)
        if not 4 <= n <= MAX_SECTION_N:
            raise MisesError(ErrorCode.INVALID_INPUT, f"n must be 4..{MAX_SECTION_N}")
        f = await run_in_threadpool(_fields, sim_id)
        if channel not in f.ch:
            raise MisesError(ErrorCode.INVALID_INPUT, f"no channel {channel!r}",
                             hint=f"channels: {', '.join(f.channels())}")
        try:
            return await run_in_threadpool(f.section, "xyz".index(axis), offset, channel, n)
        except ValueError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None

    @router.post("/sims/{sim_id}/cancel")
    async def cancel(sim_id: str):
        rec = _record(_dir(sim_id))
        if not studies.cancel(sim_id):
            return JSONResponse({"error": "not_running", "status": rec.get("status"),
                                 "message": f"{sim_id} is not running"}, status_code=409)
        return {"sim_id": sim_id, "status": "cancelling"}

    @router.post("/sims")
    async def start(body: dict):
        if "type" in body:
            try:
                sb = StartBody.model_validate(body)
            except Exception as e:  # noqa: BLE001
                raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None
            if sb.part_id is None:
                raise MisesError(ErrorCode.INVALID_INPUT, "assembly (project_id) studies are "
                                 "not supported yet", hint="study each part by part_id")
            try:  # bad ranges are rejected here, before a run exists
                studies.validate_settings(sb.type, sb.settings)
            except SimError as e:
                return JSONResponse({"error": "invalid_input", "message": e.message,
                                     "errors": e.data.get("errors", [])}, status_code=400)
            part_id, tool = sb.part_id, "sim_run"
            payload = sb.model_dump()
            what = f"{sb.type} study"
        else:
            try:
                legacy = StaticArgs.model_validate(body)
            except Exception as e:  # noqa: BLE001
                raise MisesError(ErrorCode.INVALID_INPUT, str(e)) from None
            part_id, tool, payload, what = (legacy.part_id, "sim_static_run",
                                            legacy.model_dump(), "static study")
        _check_part(part_id)
        part = parts_root() / part_id
        if not (safe_io.exists(part, "metrics.json") and safe_io.exists(part, "model.step")):
            raise MisesError(ErrorCode.NOT_FOUND, f"no built part {part_id!r}")
        # unknown material / environment / face ids are refused before a run exists
        type_id = payload.get("type", "static")
        settings = (payload["settings"] if tool == "sim_run" else
                    legacy_settings(payload["fix"], payload["load"], payload["material"],
                                    payload["environment"]))
        try:
            await run_in_threadpool(
                studies.preflight, type_id, studies.validate_settings(type_id, settings), part,
                runtime.home, runtime.profile().default_material)
        except SimError as e:
            raise _bad(e) from None
        rid = runtime.create_external_run(
            UI_AGENT, provider="ui", model="studies-page",
            edit_scope=(part_id,), read_scope=(part_id,),
            allowed_classes={SafetyClass.READ, SafetyClass.LOCAL_MUTATE},
            task=f"Studies page: {what} of {part_id}")

        async def drive() -> None:
            status = "failed"
            try:
                out = await runtime.gate.call(rid, tool, payload,
                                              call_id=f"ui-{uuid.uuid4().hex[:12]}",
                                              workdir=runtime.workdir(rid))
                status = ("done" if out.ok else
                          "cancelled" if out.data.get("error") == "cancelled" else "failed")
            except asyncio.CancelledError:
                status = "cancelled"
            except MisesError as e:  # already recorded as an error Step by the Gate
                log.info("studies run %s: %s", rid, e.message)
            except Exception:  # noqa: BLE001
                log.exception("studies run %s failed", rid)
            finally:
                runtime.unregister_cancel_hook(rid)
                try:
                    if runtime.store.get_run(rid).status == "running":
                        runtime.finish_external_run(rid, status)
                except Exception:  # noqa: BLE001
                    log.exception("could not finish studies run %s", rid)

        task = asyncio.create_task(drive(), name=f"studies-{rid}")
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        async def cancel_run() -> None:
            task.cancel()
        runtime.register_cancel_hook(rid, cancel_run)
        return JSONResponse({"run_id": rid, "status": "running"}, status_code=202)

    return router
