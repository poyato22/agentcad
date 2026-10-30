"""POST /parts/{id}/measure: Onshape-style measure on the exact B-rep (admin token only).

Body: {refs: [{face_id} | {edge_id} | {vertex_id} | {point: [x, y, z]}], base_version?}. Face and
edge ids are the metrics ids the viewer picked (metrics.face_list / edge_list). A read: no Gate
call, no step on the ui run. With `base_version` (the version the picks were made on), a part
that changed since is refused with 409 {current_version}, since the ids may name other faces now.

Answer: {part_id, version, items: [{kind, id?, type?, ...}], values: [{key, label, value, unit}]}.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import edits as _edits
from ..cad import safe_io
from ..cad.tools import PART_ID_RE
from ..measure import MeasureError, check_refs, measure_part
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime


def build_measure_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/parts/{part_id}/measure")
    async def measure(part_id: str, request: Request):
        if not PART_ID_RE.fullmatch(part_id or ""):
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown part {part_id!r}",
                             hint=f"part ids match {PART_ID_RE.pattern}")
        try:
            body: Any = await request.json()
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be JSON") from None
        if not isinstance(body, dict):
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be a JSON object")
        d = runtime.home / "parts" / part_id
        if not safe_io.exists(d, "model.step"):
            raise MisesError(ErrorCode.NOT_FOUND, f"no built part {part_id!r}")
        try:
            refs = check_refs(body.get("refs"))
        except MeasureError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, e.message) from None
        bv = body.get("base_version")
        if bv is not None and (isinstance(bv, bool) or not isinstance(bv, int)):
            raise MisesError(ErrorCode.INVALID_INPUT, "base_version must be an integer")
        version = _edits.read_version(d)
        if bv is not None and bv != version:
            return JSONResponse({"error": "stale_version", "retryable": False,
                                 "message": f"{part_id} changed since version {bv}; pick again",
                                 "current_version": version}, status_code=409)
        try:
            res = await measure_part(d, refs)
        except MeasureError as e:
            raise MisesError(ErrorCode(e.code), e.message) from None
        return {"part_id": part_id, "version": version, **res}

    return router


__all__ = ["build_measure_router"]
