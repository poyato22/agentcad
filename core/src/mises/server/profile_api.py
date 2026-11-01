"""Profile, key and setup routes for the Mac app. Admin token only (see app.py's bearer check:
the MCP-only token is refused everywhere but /mcp and /health).

Secrets go in through POST /keys/{vendor} into the Keychain and never come back out: every
response carries status only. Bodies of /keys are parsed by hand so a validation error can
never echo the submitted value (FastAPI's default 422 body includes the input).
"""
from __future__ import annotations

import asyncio
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
                       mask_claude_env, missing_claude_ca_paths, probe_local, save_profile,
                       setup_state, unmask_claude_env)
from ..providers.registry import VENDORS
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

LOCAL_TEST_TIMEOUT_S = 3.0
_MAX_URL = 2048

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

    def _shown(p: Profile) -> dict:
        d = p.model_dump(mode="json")
        d["claude_code_env"] = mask_claude_env(p.claude_code_env)   # M2: no proxy credentials
        return d

    @router.get("/profile")
    async def get_profile():
        return _shown(_profile())

    @router.put("/profile")
    async def put_profile(request: Request):
        try:
            patch = await request.json()
        except ValueError:
            return _invalid("body must be JSON")
        if not isinstance(patch, dict):
            return _invalid("body must be a JSON object")
        current = _profile()
        if "claude_code_env" in patch:      # the masked proxy URL comes back: keep the stored one
            patch = {**patch, "claude_code_env": unmask_claude_env(patch["claude_code_env"],
                                                                   current.claude_code_env)}
        merged = {**current.model_dump(), **patch}
        try:
            # the app sends the whole profile back: an unchanged value is fine
            if ("setup_completed_at" in patch
                    and patch["setup_completed_at"] != current.setup_completed_at):
                raise ValueError("setup_completed_at is set by POST /setup/complete")
            if "default_material" in patch and patch["default_material"] != current.default_material:
                # Plan 9 B2: the build default weighs every part; only a table material
                # (canonical spelling) is accepted. An old stored value is left as it is.
                from .. import mass as _mass
                try:
                    merged["default_material"] = _mass.check_material(
                        patch["default_material"], runtime.home)
                except MisesError as me:
                    return _invalid(f"invalid profile: default_material: {me.message}"
                                    + (f" ({me.hint})" if me.hint else ""))
            p = Profile.model_validate(merged)
            missing = [n for n in missing_claude_ca_paths(p.claude_code_env)
                       if p.claude_code_env.get(n) != current.claude_code_env.get(n)]
            if missing:
                raise ValueError("claude_code_env: no such file or folder for "
                                 + ", ".join(missing))
        except (ValidationError, ValueError) as e:
            msgs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()] \
                if isinstance(e, ValidationError) else [str(e)]
            return _invalid("invalid profile: " + "; ".join(msgs))
        await run_in_threadpool(save_profile, runtime.home, p)
        runtime.parallel.pump()     # a higher max_parallel_agents starts queued runs now
        return _shown(p)

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

    # ---- local model servers
    @router.post("/local/test")
    async def local_test(request: Request):
        """Does an OpenAI-compatible server answer at this URL? Same rule as the profile's
        local_endpoints (http(s) on localhost/127.0.0.1 only); nothing is saved."""
        try:
            body = await request.json()
        except ValueError:
            raise _bad("body must be JSON") from None
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not url or len(url) > _MAX_URL:
            raise _bad("url is required", "send {\"url\": \"http://localhost:11434/v1\"}")
        try:
            Profile(local_endpoints={"test": url})
        except ValidationError:
            raise _bad("a local model URL must be http(s) on this Mac (localhost or 127.0.0.1)",
                       "e.g. http://localhost:11434/v1") from None
        try:
            ok = bool(await asyncio.wait_for(local_probe([url]), LOCAL_TEST_TIMEOUT_S))
        except Exception:  # noqa: BLE001 - timeout or probe failure: it did not answer
            ok = False
        return {"ok": True} if ok else {"ok": False, "error": "unreachable"}

    # ---- the user's Claude Code (status from `claude auth status`; sign-in is Anthropic's)
    def _claude_status_body(info, auth) -> dict:
        return {"installed": bool(info.path), "version": info.version,
                "signed_in": bool(info.path) and auth.signed_in,
                "auth_method": auth.auth_method if info.path else None}

    @router.get("/claude-code/status")
    async def claude_code_status():
        return _claude_status_body(*await runtime.claude_status())

    @router.post("/claude-code/login")
    async def claude_code_login():
        """Starts `claude auth login` (it opens the browser) and returns at once."""
        started = await runtime.start_claude_login()
        return {"started": started}

    @router.post("/claude-code/logout")
    async def claude_code_logout():
        await runtime.claude_logout()
        return _claude_status_body(*await runtime.claude_status())

    async def _claude_signed_in() -> bool:
        return (await runtime.claude_status_cached())[1].signed_in

    # ---- setup
    async def _setup() -> SetupState:
        keys = await run_in_threadpool(lambda: {v: _source(v) for v in KEY_VENDORS})
        return await setup_state(runtime.home, lambda: keys, claude_status or _claude_signed_in,
                                 local_probe)

    @router.get("/setup")
    async def get_setup():
        return (await _setup()).model_dump(mode="json")

    @router.post("/setup/complete")
    async def complete_setup():
        p = _profile().model_copy(update={"setup_completed_at": time.time()})
        await run_in_threadpool(save_profile, runtime.home, p)
        return (await _setup()).model_dump(mode="json")

    return router
