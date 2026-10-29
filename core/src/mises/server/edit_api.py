"""Live part editing routes (admin token only).

These are the user's edits: changing a part's variables or whole script, or applying a quick
feature op from a face/edge pick. Each one runs through the Gate on the shared long-lived "ui"
run (runtime.ui_tool_call), exactly like an agent's edit, so the same checks and the same
`part_changed` event apply and the user's and the agent's edits share one source of truth.

Every mutating edit takes a `base_version` (the version the UI last read). A stale version is
refused with 409 `{current_version}` so a concurrent edit is never silently overwritten.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..cad.tools import PART_ID_RE
from ..edits import OP_KINDS
from ..sketch import SketchIn, _sketch_dir
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

_STATUS = {"not_found": 404, "invalid_input": 400, "stale_version": 409, "denied": 403,
           "out_of_scope": 403, "needs_approval": 409, "cancelled": 409, "budget_exhausted": 429,
           "transient": 503, "tool_failed": 500}


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _check_part_id(part_id: str) -> None:
    if not PART_ID_RE.fullmatch(part_id or ""):
        raise MisesError(ErrorCode.NOT_FOUND, f"unknown part {part_id!r}",
                         hint=f"part ids match {PART_ID_RE.pattern}")


def _base_version(body: dict) -> int | None:
    bv = body.get("base_version")
    if bv is None:
        return None
    if isinstance(bv, bool) or not isinstance(bv, int):
        raise _bad("base_version must be an integer")
    return bv


def build_edit_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    async def _body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise _bad("body must be JSON") from None
        if not isinstance(body, dict):
            raise _bad("body must be a JSON object")
        return body

    async def _run(tool: str, args: dict) -> JSONResponse:
        """Drive a mirror tool on the ui run and translate its result into an HTTP response."""
        call = runtime.ui_tool_call(tool, args)
        if call.task is not None:
            await call.task
        if call.status == "cancelled":
            raise MisesError(ErrorCode.CANCELLED, "the edit was cancelled")
        if call.error is not None:
            code = call.error.get("error", "tool_failed")
            return JSONResponse(call.error, status_code=_STATUS.get(code, 400))
        out = call.output or {}
        data = out.get("data") or {}
        if not out.get("ok", False):
            if data.get("conflict"):
                return JSONResponse(
                    {"error": "stale_version", "message": out.get("summary", "version conflict"),
                     "current_version": data.get("current_version"), "retryable": False,
                     "vars": data.get("vars"), "source": data.get("source")},
                    status_code=409)
            return JSONResponse(
                {"error": "build_failed", "message": out.get("summary", "the build failed"),
                 "retryable": False, "stdout_tail": data.get("stdout_tail")}, status_code=422)
        return JSONResponse(data)

    @router.get("/parts/{part_id}/source")
    async def get_source(part_id: str):
        _check_part_id(part_id)
        return await _run("mirror_part_get_source", {"part_id": part_id})

    @router.post("/parts/{part_id}/vars")
    async def set_vars(part_id: str, request: Request):
        _check_part_id(part_id)
        body = await _body(request)
        vars_ = body.get("vars")
        if not isinstance(vars_, dict) or not vars_:
            raise _bad("vars must be a non-empty object of name -> number")
        return await _run("mirror_part_set_vars",
                          {"part_id": part_id, "vars": vars_, "base_version": _base_version(body)})

    @router.put("/parts/{part_id}/source")
    async def put_source(part_id: str, request: Request):
        _check_part_id(part_id)
        body = await _body(request)
        source = body.get("source")
        if not isinstance(source, str) or not source.strip():
            raise _bad("source is required")
        args = {"part_id": part_id, "source": source, "base_version": _base_version(body)}
        if isinstance(body.get("vars"), dict):
            args["vars"] = body["vars"]
        return await _run("mirror_part_set_source", args)

    @router.post("/parts/{part_id}/ops")
    async def apply_op(part_id: str, request: Request):
        _check_part_id(part_id)
        body = await _body(request)
        op = body.get("op")
        if op not in OP_KINDS:
            raise _bad(f"op must be one of {', '.join(OP_KINDS)}")
        target = body.get("target") or {}
        params = body.get("params") or {}
        if not isinstance(target, dict) or not isinstance(params, dict):
            raise _bad("target and params must be objects")
        if op == "extrude" and "profile" not in params and params.get("sketch_id"):
            params = {**params, "profile": _sketch_profile(runtime, params["sketch_id"])}
        return await _run("mirror_part_op",
                          {"part_id": part_id, "op": op, "target": target, "params": params,
                           "base_version": _base_version(body)})

    return router


def _sketch_profile(runtime: Runtime, sketch_id: str) -> list[list[float]]:
    """Resolve a sketch's first closed stroke into an [x, y] profile (mm) for an extrude op."""
    d = _sketch_dir(runtime.home, sketch_id)
    s = SketchIn.model_validate_json((d / "sketch.json").read_text())
    for stroke in s.strokes:
        if stroke.closed and len(stroke.points) >= 3:
            return [[float(p[0]), float(p[1])] for p in stroke.points]
    raise MisesError(ErrorCode.INVALID_INPUT,
                     f"sketch {sketch_id} has no closed outline to extrude",
                     hint="draw a closed profile, or pass an explicit `profile`")


__all__ = ["build_edit_router"]
