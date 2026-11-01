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

from .. import hardware, local_models
from .. import platform as _platform
from .. import projects as _projects
from ..appearance import render_params
from ..cad import thumbs
from ..cad.glb_frame import GLB_FRAME, fix_stale_glb
from ..cad.tools import PART_ID_RE
from ..claude_code.session import MODEL_ALIASES, SOURCE as CLAUDE_CODE
from ..events import Event
from ..providers.registry import DEFAULT_MODEL, MOONSHOT_MODELS, VENDORS
from ..types import Caps, ErrorCode, MisesError
from .model_lists import OLLAMA_DOWN_HINT as OLLAMA_START_HINT, ModelLists

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

UI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "http://localhost:1420",
              "http://127.0.0.1:1420"]
USER = "local"

# GET /models recommendations: one line each, from research/models/model-choice-2026-10.md
BEST = {
    "claude-code/opus": "Leads CAD code benchmarks with a build123d tool loop; uses your Claude "
                        "Code plan",
    "anthropic/claude-opus-5-5": "Leads CAD code benchmarks (CADGenBench 86.9% with a tool "
                                 "loop)",
}
MOONSHOT_NOTES = {
    "moonshot/kimi-k3.1-pro": "Strong coder, untested on CAD in Mises; thinking always on",
    "moonshot/kimi-k3.1-pro-highspeed": "Kimi K3.1 Pro, 2-3x faster, costs more",
    "moonshot/kimi-k3": "Kimi K3: 1M-token context, thinking always on",
}
FREE_NOTE = "Free on OpenRouter: 20 requests/min, one run at a time"


def add_cors(app: FastAPI) -> None:
    """Add CORS as the OUTERMOST middleware (call after the auth middleware is added) so an
    unauthenticated preflight never reaches the bearer check. Real requests still need the token."""
    app.add_middleware(CORSMiddleware, allow_origins=UI_ORIGINS, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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


def _glb_frame(part_dir: Path) -> int:
    """The frame the worker wrote the part's model.glb in (mises.cad.glb_frame): 0 before P11."""
    try:
        m = json.loads((part_dir / "metrics.json").read_text())
        return int(m.get("glb_frame") or 0) if isinstance(m, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))


class UiRouter(APIRouter):
    """The UI routes plus the background work they own; aclose() on server shutdown."""

    def __init__(self) -> None:
        super().__init__()
        self.model_lists = ModelLists()
        self.puller = local_models.Puller()
        self.start_lock = asyncio.Lock()     # POST /local/start: one `ollama serve` at a time
        self.background: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def aclose(self) -> None:
        await self.model_lists.aclose()
        await self.puller.aclose()
        tasks = list(self.background)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def build_ui_router(runtime: Runtime) -> UiRouter:
    router = UiRouter()
    model_lists = router.model_lists

    @router.get("/status")
    async def status():
        runs = runtime.store.list_runs(10_000)
        return {
            "core_version": _core_version(),
            "onshape": "connected" if "onshape" in runtime.gate.services else "not_configured",
            "onshape_calls_year": runtime.store.ledger_used(USER, since=_year_start()),
            "onshape_cap_year": Caps().onshape_calls_year,
            "runs_running": sum(1 for r in runs if r.status == "running"),
            "runs_queued": sum(1 for r in runs if r.status == "queued"),
            "parallel": runtime.parallel_status(),
            "approvals_pending": len(runtime.store.pending_approvals()),
            "data_dir": str(runtime.home),
        }

    @router.get("/agents")
    async def agents():
        hidden = set(runtime.profile().hidden_agents)
        return [{"name": d.name, "description": d.description, "model": d.model,
                 "tools": d.tools, "builtin": d.source is None, "hidden": d.name in hidden}
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

    def _entries(vendors: list[dict]) -> None:
        """`entries`: every listed model as {model, recommended, note}; recommended is "best",
        "local" (a researched local model that fits this Mac), "free" (OpenRouter) or None."""
        local_notes = {r["name"]: r["note"] for r in
                       local_models.recommended_local(hardware.machine()) if r["fits"]}

        def local(name: str) -> str | None:
            for rec, note in local_notes.items():
                if name == rec or name.startswith(rec + "-"):
                    return note
            return None

        for v in vendors:
            vendor = v["vendor"]
            refs = [f"{CLAUDE_CODE}/{a}" for a in v.get("models", [])] \
                if vendor == CLAUDE_CODE else list(v.get("models", []))
            if vendor == "anthropic":
                refs = [r for r in BEST if r.startswith("anthropic/")]
            out = []
            for ref in refs:
                rec, note = None, None
                if ref in BEST:
                    rec, note = "best", BEST[ref]
                elif vendor == "ollama" and (n := local(ref.removeprefix("ollama/"))):
                    rec, note = "local", n
                elif vendor == "openrouter" and v.get("free"):
                    rec, note = "free", FREE_NOTE
                elif ref in MOONSHOT_NOTES:
                    note = MOONSHOT_NOTES[ref]
                out.append({"model": ref, "recommended": rec, "note": note})
            if out:
                v["entries"] = out

    @router.get("/models")
    async def models():
        vendors = await run_in_threadpool(_vendor_keys)
        for v in vendors:
            if v["vendor"] == "moonshot":
                v["models"] = list(MOONSHOT_MODELS)
        listed = [*await _with_lists(vendors), await _claude_code_vendor()]
        _entries(listed)
        order = {"best": 0, "local": 1, "free": 2}
        rec = sorted((e for v in listed for e in v.get("entries", []) if e["recommended"]),
                     key=lambda e: (order[e["recommended"]],
                                    list(BEST).index(e["model"]) if e["model"] in BEST else 0))
        return {"default": DEFAULT_MODEL, "vendors": listed, "recommended": rec}

    # ---- local models (Ollama): status, start, download
    def _ollama_base() -> str:
        return runtime.profile().local_endpoints.get("ollama") or VENDORS["ollama"].base_url or ""

    @router.get("/local/status")
    async def local_status():
        st = await local_models.ollama_status(_ollama_base())
        runtime.note_local_sizes(st["models"])
        machine = await run_in_threadpool(hardware.machine)
        return {**st, "recommended": local_models.recommended_local(machine)}

    @router.post("/local/start")
    async def local_start():
        """Start `ollama serve` (detached) when Ollama is installed but not running."""
        return await local_models.start_ollama(_ollama_base(), runtime.home,
                                               lock=router.start_lock)

    async def _pull_name(request: Request) -> str:
        try:
            body = await request.json()
        except ValueError:
            body = None
        model = body.get("model") if isinstance(body, dict) else None
        if not local_models.valid_pull_name(model):
            # never echo the submitted name: it is untrusted text
            raise MisesError(ErrorCode.INVALID_INPUT, "invalid model name",
                             hint="lowercase Ollama names like qwen3-coder:30b")
        return model

    @router.post("/local/pull/cancel")
    async def local_pull_cancel(request: Request):
        model = await _pull_name(request)
        return {"ok": await router.puller.cancel(model)}

    @router.post("/local/pull")
    async def local_pull(request: Request):
        """Download a model with Ollama in the background; progress comes as `local_pull`
        events {model, completed, total, status}. The name is validated before it is sent.
        One download per model, at most local_models.MAX_PULLS at once."""
        model = await _pull_name(request)
        if model in router.puller.active():
            return {"ok": False, "already": True}
        base = _ollama_base()
        if not await local_models.is_running(base):
            return {"ok": False, "error": "not_running", "hint": OLLAMA_START_HINT}

        def publish(data: dict) -> None:
            runtime.events.publish(Event(type="local_pull", data=data))
            if data.get("status") == "success":
                model_lists.forget_local()        # the new model shows up in /models now
                runtime.forget_local_sizes()

        started = router.puller.start(model, base, publish)
        if started == "already":
            return {"ok": False, "already": True}
        if started == "busy":
            return {"ok": False, "error": "too_many_pulls",
                    "hint": f"at most {local_models.MAX_PULLS} downloads at once"}
        return {"ok": True}

    @router.get("/parts")
    async def parts(project_id: str | None = None):
        """The part tree of a build (Plan 5 C1). Without project_id the active build is used.
        Each row carries the tree metadata (id, label, parent_id, order, color, kind, version)
        plus the on-disk vars, metrics and mtime. `part_id` mirrors `id` for older callers."""
        pid = (_projects.check_project(runtime.store, project_id) if project_id
               else _projects.active_project_id(runtime.store, runtime.profile().active_project_id))
        return _projects.list_parts(runtime.store, Path(runtime.home), pid)

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
        # P11 F4: a model.glb written before the frame fix is served fixed (drawn where the
        # core has the part); metrics.json says which frame the worker wrote
        stale = _glb_frame(real.parent) < GLB_FRAME
        etag = f'"{st.st_mtime_ns}-{st.st_size}{"-f" if stale else ""}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        data = real.read_bytes()
        if stale:
            try:
                data = fix_stale_glb(data)
            except (ValueError, KeyError, IndexError, TypeError):
                log.warning("could not fix the frame of %s's model.glb", part_id, exc_info=True)
        return Response(data, media_type="model/gltf-binary", headers=headers)

    @router.get("/parts/{part_id}/thumb.png")
    async def part_thumb(part_id: str, request: Request, size: int = thumbs.DEFAULT_SIZE):
        """A shaded thumbnail of the part, rendered in the sandboxed worker and cached by the
        part's version (so a rebuild refreshes it). 404 until the part has a built mesh."""
        if not PART_ID_RE.fullmatch(part_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad part id: {part_id!r}",
                             hint=f"part ids match {PART_ID_RE.pattern}")
        home = Path(runtime.home)
        row = runtime.store.get_part(part_id)
        version = _projects.part_version(home, row) if row is not None else 0
        sz = thumbs.clamp_size(size)
        look = render_params(row, runtime.store) if row is not None else None   # U8 / G4 look
        key = thumbs.part_key(home, part_id, version, sz, look)   # version + model.stl + look
        if key is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no thumbnail for part {part_id!r}",
                             hint="build it first")
        etag = f'"{part_id}-{key}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        png = await thumbs.part_thumb(home, part_id, version, sz, look)
        if png is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no thumbnail for part {part_id!r}",
                             hint="its preview could not be rendered")
        return Response(png, media_type="image/png", headers=headers)

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
        """Best effort: every running or queued run is tried; failures are reported, not
        raised."""
        cancelled: list[str] = []
        failed: list[dict] = []
        for run in runtime.store.list_runs(10_000):
            if run.status not in ("running", "queued"):
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
        # Plan 9 D4: an agent whose turn has ended may still own background studies, reviews
        # or design studies; stop each such family at its root (where its notes would go)
        from .. import jobs
        for root in sorted({jobs.root_of(runtime.store, j.run_id) for j in jobs.running()
                            if j.run_id}):
            try:
                runtime.stop_background(root)
            except Exception as e:  # noqa: BLE001 - keep stopping the rest
                log.exception("cancel_all: could not stop the jobs of %s", root)
                failed.append({"run_id": root, "error": f"{type(e).__name__}: {e}"})
                continue
            if root not in cancelled:
                cancelled.append(root)
        log.info("cancel_all: cancelled %d run(s), %d failed", len(cancelled), len(failed))
        return {"cancelled": cancelled, "failed": failed}

    return router
