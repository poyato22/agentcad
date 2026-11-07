"""POST /parts/{id}/measure: Onshape-style measure on the exact B-rep (admin token only).

Body: {refs: [{face_id} | {edge_id} | {vertex_id} | {point: [x, y, z]}], base_version?}, or
{warm: true} (no refs) when Measure mode opens: the part's warm worker loads the shape ahead of
the first pick and the answer is {part_id, warm}. Face and
edge ids are the metrics ids the viewer picked (metrics.face_list / edge_list). A read: no Gate
call, no step on the ui run. With `base_version` (the version the picks were made on), a part
that changed since is refused with 409 {current_version}, since the ids may name other faces now.

Answer: {part_id, version, items: [{kind, id?, type?, ...}], values: [{key, label, value, unit}]}.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..cad import safe_io
from ..cad.tools import PART_ID_RE
from ..measure import MeasureError, check_refs, measure_pair, measure_part, warm_part
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
        if body.get("warm") is True and body.get("refs") is None:
            # Measure mode opened: load the shape in a warm worker before the first pick
            return {"part_id": part_id, "warm": await warm_part(d)}
        try:
            refs = check_refs(body.get("refs"))
        except MeasureError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, e.message) from None
        bv = body.get("base_version")
        if bv is not None and (isinstance(bv, bool) or not isinstance(bv, int)):
            raise MisesError(ErrorCode.INVALID_INPUT, "base_version must be an integer")
        try:
            # the version is read and checked under the part lock, against the STEP measured
            res = await measure_part(d, refs, base_version=bv)
        except MeasureError as e:
            if e.code == "stale_version":
                return JSONResponse({"error": "stale_version", "retryable": False,
                                     "message": f"{part_id} changed since version {bv}; pick again",
                                     "current_version": e.current_version}, status_code=409)
            raise MisesError(ErrorCode(e.code), e.message) from None
        version = res.pop("version")
        return {"part_id": part_id, "version": version, **res}

    @router.post("/measure/pair")
    async def measure_two_parts(request: Request):
        """The build view's measure: one pick on each of two posed parts (or one part twice),
        {a: {part_id, ref, pose}, b: {...}} with poses as row-major 4 x 4 matrices in mm."""
        try:
            body: Any = await request.json()
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, "body must be JSON") from None
        sides = []
        for k in ("a", "b"):
            s = body.get(k) if isinstance(body, dict) else None
            if not isinstance(s, dict):
                raise MisesError(ErrorCode.INVALID_INPUT, f"{k} must be {{part_id, ref, pose}}")
            pid = s.get("part_id")
            if not isinstance(pid, str) or not PART_ID_RE.fullmatch(pid):
                raise MisesError(ErrorCode.NOT_FOUND, f"unknown part {pid!r}")
            d = runtime.home / "parts" / pid
            if not safe_io.exists(d, "model.step"):
                raise MisesError(ErrorCode.NOT_FOUND, f"no built part {pid!r}")
            sides.append((pid, d, s.get("ref"), s.get("pose")))
        try:
            res = await measure_pair(sides[0][1], sides[0][2], sides[0][3],
                                     sides[1][1], sides[1][2], sides[1][3])
        except MeasureError as e:
            raise MisesError(ErrorCode(e.code), e.message) from None
        return {"part_ids": [sides[0][0], sides[1][0]], **res}

    return router


__all__ = ["build_measure_router"]
