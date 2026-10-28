"""Printer, slice and print routes (admin token only, like every non-MCP route).

Printer API keys go in through POST /printers into the Keychain (`mises` / `printer:<id>`) and
never come back out. Starting a print (POST /prints) runs `print_send` through the Gate under a
"ui" run, so the approval card appears and nothing is sent until the user approves it on the card.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .. import platform as _platform
from ..cad.tools import PART_ID_RE
from ..print import slicer
from ..print.prusalink import SENDABLE_STATES
from ..print.tools import (KEYCHAIN_SERVICE, SLICE_ID_RE, client_for, find_printer,
                           keychain_account, new_slice_id, read_slice, refuse_unless_sendable,
                           slice_dir, write_slice)
from ..profile import PRINTER_ID_RE, PrinterEntry, load_profile, save_profile
from ..types import ErrorCode, MisesError
from .profile_api import _clean_secret

if TYPE_CHECKING:
    from ..runtime import Runtime

KINDS = [{"kind": "prusalink", "available": True, "note": None},
         {"kind": "bambu", "available": False, "note": "coming later"}]
_MAX_CALLS = 200


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _printer_json(p: PrinterEntry, has_key: bool) -> dict:
    return {**p.model_dump(mode="json"), "has_key": has_key}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
    return s if re.match(r"^[a-z]", s) else ("p_" + s)[:30]


def build_print_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    slice_calls: dict[str, str] = {}  # slice_id -> ui call id (this process only)

    async def _has_key(pid: str) -> bool:
        return bool(await run_in_threadpool(_platform.keychain_get, KEYCHAIN_SERVICE,
                                            keychain_account(pid)))

    async def _body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise _bad("body must be JSON") from None
        if not isinstance(body, dict):
            raise _bad("body must be a JSON object")
        return body

    def _trim_calls() -> None:
        done = [k for k, c in runtime.ui_calls.items() if c.status != "running"]
        for k in done[:max(0, len(runtime.ui_calls) - _MAX_CALLS)]:
            runtime.ui_calls.pop(k, None)

    @router.get("/print/capabilities")
    async def capabilities():
        return {"slicer": slicer.slicer_status(), "kinds": KINDS,
                "models": list(slicer.PRINTER_MODELS),
                "materials": [{"name": n, "density_g_cm3": m.density_g_cm3,
                               "usd_per_kg": m.usd_per_kg} for n, m in slicer.MATERIALS.items()]}

    # ---- printers
    @router.get("/printers")
    async def list_printers():
        return [_printer_json(p, await _has_key(p.id)) for p in load_profile(runtime.home).printers]

    @router.post("/printers")
    async def add_printer(request: Request):
        body = await _body(request)
        kind = body.get("kind", "prusalink")
        if kind == "bambu":
            raise _bad("Bambu printers are coming later", "only PrusaLink printers work today")
        key = _clean_secret(body.get("api_key"), "api_key")
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _bad("name is required")
        pid = body.get("id") or _slug(name)
        try:
            entry = PrinterEntry(id=pid, kind=kind, host=str(body.get("host", "")).strip(),
                                 name=name.strip(), model=body.get("model", "MK4"))
        except ValidationError as e:
            raise _bad("invalid printer: " + "; ".join(
                f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
        prof = load_profile(runtime.home)
        if any(p.id == entry.id for p in prof.printers):
            raise _bad(f"a printer with id {entry.id!r} already exists",
                       "pick another name, or delete the old one first")
        try:
            await run_in_threadpool(_platform.keychain_set, KEYCHAIN_SERVICE,
                                    keychain_account(entry.id), key)
        except (RuntimeError, ValueError):
            raise MisesError(ErrorCode.TOOL_FAILED, "could not store the printer key in the Keychain",
                             hint="check that the macOS Keychain is unlocked") from None
        await run_in_threadpool(save_profile, runtime.home,
                                prof.model_copy(update={"printers": [*prof.printers, entry]}))
        return _printer_json(entry, True)

    @router.delete("/printers/{printer_id}")
    async def delete_printer(printer_id: str):
        prof = load_profile(runtime.home)
        if not any(p.id == printer_id for p in prof.printers):
            raise MisesError(ErrorCode.NOT_FOUND, f"no printer {printer_id!r}")
        await run_in_threadpool(save_profile, runtime.home, prof.model_copy(
            update={"printers": [p for p in prof.printers if p.id != printer_id]}))
        try:
            await run_in_threadpool(_platform.keychain_delete, KEYCHAIN_SERVICE,
                                    keychain_account(printer_id))
        except RuntimeError:
            raise MisesError(ErrorCode.TOOL_FAILED, "printer removed, but its Keychain item could "
                             "not be deleted", hint="remove `printer:" + printer_id +
                             "` from Keychain Access") from None
        return {"ok": True}

    @router.get("/printers/{printer_id}/status")
    async def printer_status(printer_id: str):
        printer = find_printer(runtime.home, printer_id)
        out: dict[str, Any] = {"printer_id": printer_id, "reachable": False, "state": None,
                               "sendable": False, "temp_nozzle": None, "temp_bed": None,
                               "job": None, "error": None}
        try:
            st = await (await client_for(printer)).status()
        except MisesError as e:
            return {**out, "error": e.message, "hint": e.hint}
        return {**out, "reachable": True, **st.to_dict()}

    # ---- slices
    @router.post("/slices")
    async def create_slice(request: Request):
        body = await _body(request)
        part_id, printer_id = body.get("part_id"), body.get("printer_id")
        if not isinstance(part_id, str) or not PART_ID_RE.fullmatch(part_id):
            raise _bad("part_id is required", f"part ids match {PART_ID_RE.pattern}")
        if not isinstance(printer_id, str) or not PRINTER_ID_RE.match(printer_id):
            raise _bad("printer_id is required")
        if not (runtime.home / "parts" / part_id / "model.stl").is_file():
            raise MisesError(ErrorCode.NOT_FOUND, f"no built part {part_id!r}",
                             hint="build it with mirror_part_create")
        printer = find_printer(runtime.home, printer_id)
        material = str(body.get("material") or load_profile(runtime.home).default_material).upper()
        slicer.material_of(material)
        if slicer.find_slicer() is None:
            return JSONResponse({"error": "tool_failed", "message": "PrusaSlicer is not installed",
                                 "hint": f"install it with: {slicer.INSTALL_COMMAND}",
                                 "retryable": False}, status_code=424)
        sid = new_slice_id()
        write_slice(runtime.home, sid, {
            "slice_id": sid, "status": "running", "part_id": part_id, "printer_id": printer.id,
            "material": material, "model": printer.model, "created_at": time.time(),
            "estimate": None, "preview": False, "error": None, "hint": None})
        call = runtime.ui_tool_call("print_slice", {"part_id": part_id, "printer_id": printer.id,
                                                    "material": material},
                                    meta={"slice_id": sid})
        slice_calls[sid] = call.call_id
        _trim_calls()
        return {"slice_id": sid, "status": "running", "run_id": call.run_id,
                "call_id": call.call_id}

    def _slice_view(sid: str) -> dict:
        rec = read_slice(runtime.home, sid)
        cid = slice_calls.get(sid)
        call = runtime.ui_calls.get(cid) if cid else None
        if rec.get("status") == "running" and (call is None or call.status != "running"):
            # the process that was slicing is gone (restart) or its call already ended
            rec = {**rec, "status": "failed" if call is None or call.status != "cancelled"
                   else "cancelled", "error": rec.get("error") or "slice was interrupted"}
        return {k: rec.get(k) for k in ("slice_id", "status", "part_id", "printer_id", "material",
                                        "model", "created_at", "estimate", "preview", "error",
                                        "hint")} | {"sent_to": rec.get("sent_to")}

    @router.get("/slices/{slice_id}")
    async def get_slice(slice_id: str):
        return _slice_view(slice_id)

    @router.get("/slices/{slice_id}/preview.png")
    async def slice_preview(slice_id: str):
        png = slice_dir(runtime.home, slice_id) / "preview.png"
        if not png.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, "this slice has no preview image",
                             hint="PrusaSlicer embeds a thumbnail only when it can render one")
        return Response(png.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "private, no-cache"})

    @router.post("/slices/{slice_id}/cancel")
    async def cancel_slice(slice_id: str):
        read_slice(runtime.home, slice_id)
        call = runtime.ui_calls.get(slice_calls.get(slice_id, ""))
        if call is None or call.status != "running":
            raise MisesError(ErrorCode.NOT_FOUND, "that slice is not running")
        runtime.cancel_ui_call(call.call_id)
        return {"ok": True}

    # ---- prints (irreversible: approval card)
    @router.post("/prints")
    async def start_print(request: Request):
        body = await _body(request)
        sid, printer_id = body.get("slice_id"), body.get("printer_id")
        if not isinstance(sid, str) or not SLICE_ID_RE.match(sid):
            raise _bad("slice_id is required")
        if not isinstance(printer_id, str):
            raise _bad("printer_id is required")
        rec = read_slice(runtime.home, sid)
        if rec.get("status") != "done":
            raise _bad(f"slice {sid} is {rec.get('status')}", "wait for the slice to finish")
        printer = find_printer(runtime.home, printer_id)
        if rec.get("model") != printer.model:
            raise MisesError(ErrorCode.DENIED, f"this slice is for a {rec.get('model')}, but "
                             f"{printer.name!r} is a {printer.model}", hint="slice again for this printer")
        st = await (await client_for(printer)).status()
        try:
            refuse_unless_sendable(printer, st)
        except MisesError as e:
            return JSONResponse(e.to_dict() | {"state": st.state}, status_code=409)
        call = runtime.ui_tool_call("print_send", {
            "slice_id": sid, "printer_id": printer.id, "gcode_sha256": rec.get("gcode_sha256"),
            "file_name": rec.get("file_name")})
        _trim_calls()
        return {"call_id": call.call_id, "run_id": call.run_id, "status": "running"}

    @router.get("/prints/{call_id}")
    async def get_print(call_id: str):
        call = runtime.ui_calls.get(call_id)
        if call is None or call.tool != "print_send":
            raise MisesError(ErrorCode.NOT_FOUND, f"no print request {call_id!r}")
        return call.to_dict()

    return router


__all__ = ["build_print_router", "SENDABLE_STATES"]
