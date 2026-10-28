"""Routes and CORS for the Mac app's webview: parts, GLB, agents, models, status, stop-all.

The webview cannot use the MCP endpoint (it needs a live run's X-Mises-Run), so these thin
read routes sit beside the run/approval API in app.py. Every route here requires the admin
bearer token (the MCP-only token agents get is refused with 403); only CORS preflights are
answered without a token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from .. import platform as _platform
from ..cad.tools import PART_ID_RE
from ..claude_code.session import MODEL_ALIASES, SOURCE as CLAUDE_CODE
from ..events import Event
from ..providers.registry import DEFAULT_MODEL, VENDORS
from ..types import Caps, ErrorCode, MisesError
from .model_lists import ModelLists

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

UI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420",
              "http://127.0.0.1:1420"]
USER = "local"


def add_cors(app: FastAPI) -> None:
    """Add CORS as the OUTERMOST middleware (call after the auth middleware is added) so an
    unauthenticated preflight never reaches the bearer check. Real requests still need the token."""
    app.add_middleware(CORSMiddleware, allow_origins=UI_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "If-None-Match"],
                       expose_headers=["ETag"], allow_credentials=False)


def _core_version() -> str:
    try:
        return metadata.version("mises")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _year_start() -> float:
    return datetime(datetime.now().year, 1, 1).timestamp()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _parts_root(runtime: Runtime) -> Path:
    return Path(runtime.home) / "parts"


def _contained(root: Path, p: Path) -> Path | None:
    """Resolved `p` if it stays inside the resolved `root` (no `..`, no symlink escape)."""
    try:
        real = p.resolve(strict=True)
        if real.is_relative_to(root.resolve(strict=True)):
            return real
    except (OSError, RuntimeError):
        pass
    return None


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))


def build_ui_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()
    model_lists = ModelLists()

    @router.get("/status")
    async def status():
        return {
            "core_version": _core_version(),
            "onshape": "connected" if "onshape" in runtime.gate.services else "not_configured",
            "onshape_calls_year": runtime.store.ledger_used(USER, since=_year_start()),
            "onshape_cap_year": Caps().onshape_calls_year,
            "runs_running": sum(1 for r in runtime.store.list_runs(10_000)
                                if r.status == "running"),
            "approvals_pending": len(runtime.store.pending_approvals()),
            "data_dir": str(runtime.home),
        }

    @router.get("/agents")
    async def agents():
        return [{"name": d.name, "description": d.description, "model": d.model}
                for d in sorted(runtime.definitions.values(), key=lambda d: d.name)]

    def _vendor_keys() -> list[dict]:
        # blocking: keychain_get runs the `security` CLI, so this runs in a worker thread
        vendors = []
        for vendor, spec in VENDORS.items():
            if spec.env_key is None:
                key = "not_needed"
            elif os.environ.get(spec.env_key):
                key = "env"
            elif _platform.keychain_get("mises", vendor):
                key = "keychain"
            else:
                key = "none"
            vendors.append({"vendor": vendor, "key": key})
        return vendors

    async def _claude_code_vendor() -> dict:
        info, auth = await runtime.claude_status_cached()
        key = ("not_installed" if not info.path else "signed_in" if auth.signed_in
               else "signed_out")
        return {"vendor": CLAUDE_CODE, "key": key, "models": list(MODEL_ALIASES)}

    async def _with_lists(vendors: list[dict]) -> list[dict]:
        """Attach live model lists: `models` (full vendor/id refs), `hint` when a local server
        is down, `free` for OpenRouter's free tool-capable models. Keys are omitted when unused."""
        endpoints = runtime.profile().local_endpoints

        def base(vendor: str) -> str:
            return endpoints.get(vendor) or VENDORS[vendor].base_url or ""

        ollama, lmstudio, free = await asyncio.gather(
            model_lists.ollama(base("ollama")), model_lists.lmstudio(base("lmstudio")),
            model_lists.openrouter())
        out = []
        for v in vendors:
            v = dict(v)
            if v["vendor"] in ("ollama", "lmstudio"):
                lst = ollama if v["vendor"] == "ollama" else lmstudio
                v["models"] = lst.models
                if lst.hint:
                    v["hint"] = lst.hint
            elif v["vendor"] == "openrouter":
                v["models"] = free
                v["free"] = True
            out.append(v)
        return out

    @router.get("/models")
    async def models():
        vendors = await run_in_threadpool(_vendor_keys)
        return {"default": DEFAULT_MODEL,
                "vendors": [*await _with_lists(vendors), await _claude_code_vendor()]}

    @router.get("/parts")
    async def parts():
        root = _parts_root(runtime)
        if not root.is_dir():
            return []
        out = []
        for d in sorted(root.iterdir(), key=lambda p: p.name):
            if not PART_ID_RE.fullmatch(d.name):
                continue
            real = _contained(root, d)
            if real is None or not real.is_dir():
                continue
            vars_ = _read_json(real / "vars.json")
            if not isinstance(vars_, dict):
                continue
            metrics = _read_json(real / "metrics.json")
            mtimes = [p.stat().st_mtime for p in real.iterdir() if p.is_file()]
            out.append({"part_id": d.name, "vars": vars_,
                        "metrics": metrics if isinstance(metrics, dict) else None,
                        "mtime": float(max(mtimes, default=real.stat().st_mtime))})
        return out

    @router.get("/parts/{part_id}/model.glb")
    async def part_glb(part_id: str, request: Request):
        if not PART_ID_RE.fullmatch(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                             hint=f"part ids match {PART_ID_RE.pattern}")
        root = _parts_root(runtime)
        real = _contained(root, root / part_id / "model.glb") if root.is_dir() else None
        if real is None or not real.is_file():
            raise MisesError(ErrorCode.NOT_FOUND, f"no model for part {part_id!r}",
                             hint="build it with mirror_part_create")
        st = real.stat()
        etag = f'"{st.st_mtime_ns}-{st.st_size}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(real.read_bytes(), media_type="model/gltf-binary", headers=headers)

    def _cancel_orphan(run_id: str) -> None:
        # "running" in the database but not driven here: nothing live to stop, fix the record
        runtime.approvals.cancel_run(run_id)
        runtime.store.release_run_reservations(run_id)
        runtime.store.update_run(run_id, status="cancelled")
        runtime.events.publish(Event(type="run_finished", run_id=run_id, data={
            "status": "cancelled", "stop_reason": "cancelled",
            "error": "orphaned run cancelled", "final_text": ""}))

    @router.post("/runs/cancel_all")
    async def cancel_all():
        """Best effort: every running run is tried; failures are reported, not raised."""
        cancelled: list[str] = []
        failed: list[dict] = []
        for run in runtime.store.list_runs(10_000):
            if run.status != "running":
                continue
            if runtime.is_cancelling(run.id):
                continue  # already stopping; keeps the call idempotent
            try:
                try:
                    # Internal runs: cancel the loop. External runs: marked cancelled, and the
                    # owner's cancel hook (e.g. AcpSession) kills the agent process.
                    runtime.cancel(run.id)
                except MisesError as e:
                    if e.code != ErrorCode.NOT_FOUND:
                        raise
                    _cancel_orphan(run.id)
            except Exception as e:  # noqa: BLE001 - keep stopping the rest
                log.exception("cancel_all: could not cancel run %s", run.id)
                failed.append({"run_id": run.id, "error": f"{type(e).__name__}: {e}"})
                continue
            cancelled.append(run.id)
        log.info("cancel_all: cancelled %d run(s), %d failed", len(cancelled), len(failed))
        return {"cancelled": cancelled, "failed": failed}

    return router
