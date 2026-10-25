"""Profile, key and setup routes for the Mac app. Admin token only (see app.py's bearer check:
the MCP-only token is refused everywhere but /mcp and /health).

Secrets go in through POST /keys/{vendor} into the Keychain and never come back out: every
response carries status only. Bodies of /keys are parsed by hand so a validation error can
never echo the submitted value (FastAPI's default 422 body includes the input).
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .. import platform as _platform
from ..profile import (ClaudeStatusFn, LocalProbeFn, Profile, SetupState, load_profile,
                       probe_local, save_profile, setup_state)
from ..providers.registry import VENDORS
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

SERVICE = "mises"
KEY_VENDORS = [*VENDORS, "onshape"]
_MAX_SECRET = 4096


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def _invalid(msg: str) -> JSONResponse:
    return JSONResponse(MisesError(ErrorCode.INVALID_INPUT, msg,
                                   hint="fix the listed fields").to_dict(), status_code=422)


def _source(vendor: str) -> str:
    """blocking (runs the `security` CLI): call from a worker thread."""
    spec = VENDORS.get(vendor)
    if spec is not None and spec.env_key and os.environ.get(spec.env_key):
        return "env"
    return "keychain" if _platform.keychain_get(SERVICE, vendor) else "none"


def _status(vendor: str) -> dict:
    src = _source(vendor)
    return {"vendor": vendor, "present": src != "none", "source": src}


def _clean_secret(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise _bad(f"{what} is required", "send a non-empty string")
    v = v.strip()
    if len(v) > _MAX_SECRET or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise _bad(f"{what} has an invalid format")
    return v


def build_profile_router(runtime: Runtime, claude_status: ClaudeStatusFn | None = None,
                         local_probe: LocalProbeFn = probe_local) -> APIRouter:
    router = APIRouter()

    def _profile() -> Profile:
        return load_profile(runtime.home)

    @router.get("/profile")
    async def get_profile():
        return _profile().model_dump(mode="json")

    @router.put("/profile")
    async def put_profile(request: Request):
        try:
            patch = await request.json()
        except ValueError:
            return _invalid("body must be JSON")
        if not isinstance(patch, dict):
            return _invalid("body must be a JSON object")
        merged = {**_profile().model_dump(), **patch}
        try:
            if "setup_completed_at" in patch:
                raise ValueError("setup_completed_at is set by POST /setup/complete")
            p = Profile.model_validate(merged)
        except (ValidationError, ValueError) as e:
            msgs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()] \
                if isinstance(e, ValidationError) else [str(e)]
            return _invalid("invalid profile: " + "; ".join(msgs))
        await run_in_threadpool(save_profile, runtime.home, p)
        return p.model_dump(mode="json")

    # ---- keys
    @router.get("/keys")
    async def list_keys():
        return await run_in_threadpool(lambda: [_status(v) for v in KEY_VENDORS])

    def _vendor(vendor: str) -> str:
        if vendor == "onshape":
            return vendor
        spec = VENDORS.get(vendor)
        if spec is None or spec.env_key is None:
            raise _bad(f"no API key can be stored for {vendor!r}",
                       hint=f"keys: {', '.join(v for v, s in VENDORS.items() if s.env_key)}, onshape")
        return vendor

    @router.post("/keys/{vendor}")
    async def set_key(vendor: str, request: Request):
        _vendor(vendor)
        try:
            body = await request.json()
        except ValueError:
            raise _bad("body must be JSON") from None
        if not isinstance(body, dict):
            raise _bad("body must be a JSON object")
        if vendor == "onshape":
            access = _clean_secret(body.get("access_key"), "access_key")
            secret = _clean_secret(body.get("secret_key"), "secret_key")
            if ":" in access:
                raise _bad("access_key has an invalid format")
            value = f"{access}:{secret}"
        else:
            value = _clean_secret(body.get("secret"), "secret")
        try:
            await run_in_threadpool(_platform.keychain_set, SERVICE, vendor, value)
        except (RuntimeError, ValueError):
            raise MisesError(ErrorCode.TOOL_FAILED, f"could not store the {vendor} key in the Keychain",
                             hint="check that the macOS Keychain is unlocked") from None
        if vendor == "onshape":
            await runtime.set_onshape_credentials(value)
        return await run_in_threadpool(_status, vendor)

    @router.delete("/keys/{vendor}")
    async def delete_key(vendor: str):
        _vendor(vendor)
        try:
            await run_in_threadpool(_platform.keychain_delete, SERVICE, vendor)
        except RuntimeError:
            raise MisesError(ErrorCode.TOOL_FAILED, f"could not remove the {vendor} key",
                             hint="check that the macOS Keychain is unlocked") from None
        if vendor == "onshape":
            await runtime.set_onshape_credentials(None)
        return await run_in_threadpool(_status, vendor)

    @router.post("/onshape/test")
    async def onshape_test():
        """One ledgered GET of /api/v17/users/sessioninfo (Onshape "Get the session information
        for an authenticated user") with the stored keys."""
        ad = runtime.gate.services.get("onshape")
        if ad is None:
            return {"ok": False, "error": "not_configured"}
        try:
            info = await ad.client.request("GET", "/api/v17/users/sessioninfo", run_id=None)
        except MisesError as e:
            code = {ErrorCode.DENIED: "denied", ErrorCode.TRANSIENT: "unreachable",
                    ErrorCode.BUDGET_EXHAUSTED: "budget"}.get(e.code, "failed")
            return {"ok": False, "error": code}
        name = info.get("name") if isinstance(info, dict) else None
        out: dict[str, Any] = {"ok": True}
        if isinstance(name, str) and name:
            out["user_name"] = name[:100]
        return out

    # ---- setup
    async def _setup() -> SetupState:
        keys = await run_in_threadpool(lambda: {v: _source(v) for v in KEY_VENDORS})
        return await setup_state(runtime.home, lambda: keys, claude_status, local_probe)

    @router.get("/setup")
    async def get_setup():
        return (await _setup()).model_dump(mode="json")

    @router.post("/setup/complete")
    async def complete_setup():
        p = _profile().model_copy(update={"setup_completed_at": time.time()})
        await run_in_threadpool(save_profile, runtime.home, p)
        return (await _setup()).model_dump(mode="json")

    return router
