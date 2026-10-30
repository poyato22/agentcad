"""Local API server: REST + WebSocket events for the Mac app and CLI, plus the MCP endpoint.

Binds 127.0.0.1 only; every HTTP route needs `Authorization: Bearer <token>`, the events
WebSocket takes `?token=`.

Two tokens: the admin token (the UI/CLI; every route) and the MCP-only token, the one handed to
agents. The MCP-only token is accepted on /mcp and /health only (403 elsewhere), so an agent can
never decide its own approvals, answer questions or drive runs through this API.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import socket
import tempfile
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..types import ErrorCode, MisesError
from .. import projects as _projects
from .mcp_server import RUN_HEADER, build_mcp_server
from ..profile import ClaudeStatusFn, LocalProbeFn, load_profile, probe_local
from .profile_api import build_profile_router
from .projects_api import build_projects_router
from .context_api import build_context_router
from .catalog_api import build_catalog_router
from .drawing_api import build_drawing_router
from .onshape_api import build_onshape_router
from .edit_api import build_edit_router
from .system_api import build_system_router
from .print_api import build_print_router
from .assembly_api import build_assembly_router
from .sim_api import build_sim_router
from .sketch_api import build_sketch_router
from .spec_api import build_spec_router
from .ui_api import add_cors, build_ui_router

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
DECIDED_BY = "local-user"

_HTTP_STATUS = {
    ErrorCode.NOT_FOUND: 404, ErrorCode.INVALID_INPUT: 400, ErrorCode.STALE_VERSION: 409,
    ErrorCode.NEEDS_APPROVAL: 409, ErrorCode.CANCELLED: 409, ErrorCode.DENIED: 403,
    ErrorCode.OUT_OF_SCOPE: 403, ErrorCode.BUDGET_EXHAUSTED: 429, ErrorCode.TRANSIENT: 503,
    ErrorCode.TOOL_FAILED: 500,
}


def _error_response(e: MisesError, status: int | None = None) -> JSONResponse:
    return JSONResponse(e.to_dict(), status_code=status or getattr(e, "http_status", None)
                        or _HTTP_STATUS.get(e.code, 400))


def _token_ok(given: str | None, token: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), token.encode())


MCP_SCOPE_PATHS = frozenset({"/mcp", "/mcp/", "/health"})


class _BearerAuth:
    """Rejects any HTTP request without a valid bearer token (WebSockets check ?token=).

    The admin token opens every route; the MCP-only token opens MCP_SCOPE_PATHS only."""

    def __init__(self, app: ASGIApp, token: str, mcp_token: str):
        self.app = app
        self.token = token
        self.mcp_token = mcp_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = Headers(scope=scope).get("authorization", "")
            given = auth[7:] if auth.lower().startswith("bearer ") else None
            resp = None
            if _token_ok(given, self.token):
                pass
            elif _token_ok(given, self.mcp_token):
                if scope.get("path") not in MCP_SCOPE_PATHS:
                    resp = JSONResponse({"error": "denied",
                                         "message": "this token only opens the MCP endpoint"},
                                        status_code=403)
            else:
                resp = JSONResponse({"error": "unauthorized", "message": "missing or bad token"},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
            if resp is not None:
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


class _McpEndpoint:
    """Requires a valid, running `X-Mises-Run` before handing the request to the MCP app."""

    def __init__(self, runtime: Runtime, inner: ASGIApp):
        self.runtime = runtime
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        run_id = Headers(scope=scope).get(RUN_HEADER)
        resp = None
        if not run_id:
            resp = _error_response(MisesError(ErrorCode.INVALID_INPUT, "missing X-Mises-Run header",
                                              hint="bind the MCP connection to a run id"))
        else:
            try:
                run = self.runtime.store.get_run(run_id)
            except KeyError:
                resp = _error_response(MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}"))
            else:
                if run.provider == "ui":    # I5: the user's own identity is never an MCP client
                    resp = _error_response(MisesError(
                        ErrorCode.DENIED, "the app's own run cannot be used over MCP",
                        hint="bind the MCP connection to your own run id"))
                elif run.status != "running":
                    resp = _error_response(MisesError(
                        ErrorCode.INVALID_INPUT, f"run {run_id} is {run.status}",
                        hint="create a new run"), status=409)
        if resp is not None:
            await resp(scope, receive, send)
            return
        await self.inner(scope, receive, send)


class NewRun(BaseModel):
    agent: str | None = None  # the profile's default_agent when omitted
    prompt: str
    model_ref: str | None = None
    edit_scope: list[str] = ["*"]
    read_scope: list[str] = ["*"]
    sketch_id: str | None = Field(default=None, pattern=r"^sk_[0-9a-f]{12}$")  # POST /sketches
    project_id: str | None = None  # the build; the active build is used when omitted


class PatchRun(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class Cleanup(BaseModel):
    project_id: str | None = None
    older_than_days: float | None = None
    status_in: list[str] = ["done", "failed", "cancelled"]
    dry_run: bool = True


class NewExternalRun(BaseModel):
    agent: str
    provider: str
    model: str
    edit_scope: list[str] = ["*"]
    read_scope: list[str] = ["*"]
    project_id: str | None = None  # the build; the active build is used when omitted


class Text(BaseModel):
    text: str


class Resume(BaseModel):
    text: str | None = None


class Decision(BaseModel):
    payload_hash: str
    approve: bool
    edited_payload: dict | None = None


def create_app(runtime: Runtime, token: str, *, mcp_token: str | None = None,
               claude_status: ClaudeStatusFn | None = None,
               local_probe: LocalProbeFn = probe_local) -> FastAPI:
    """`token` is the admin token; `mcp_token` (generated if omitted) opens /mcp and /health only."""
    if not token:
        raise ValueError("a non-empty token is required")
    mcp_token = mcp_token or secrets.token_urlsafe(32)
    if hmac.compare_digest(mcp_token.encode(), token.encode()):
        raise ValueError("the MCP-only token must differ from the admin token")
    runtime.register_secrets(token, mcp_token)  # defense in depth: scrub them from tool results
    mcp_server = build_mcp_server(runtime)
    manager = StreamableHTTPSessionManager(
        app=mcp_server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
            allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*",
                             "http://localhost", "http://localhost:*"]))

    ui_router = build_ui_router(runtime)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            async with manager.run():
                yield
        finally:
            # background work of the UI routes (OpenRouter refresh, model downloads) stops here
            await ui_router.aclose()
            # Claude Code sessions reach Mises only through this server: close them with it
            await runtime.close_claude_sessions()

    app = FastAPI(title="Mises", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.runtime = runtime
    app.state.ui_router = ui_router
    app.state.mcp_session_manager = manager
    app.add_middleware(_BearerAuth, token=token, mcp_token=mcp_token)
    add_cors(app)  # added last = outermost: preflights are answered before the bearer check
    app.include_router(ui_router)
    app.include_router(build_projects_router(runtime))
    app.include_router(build_profile_router(runtime, claude_status, local_probe))
    app.include_router(build_context_router(runtime))
    app.include_router(build_drawing_router(runtime))
    app.include_router(build_print_router(runtime))
    app.include_router(build_sim_router(runtime))
    app.include_router(build_sketch_router(runtime))
    app.include_router(build_assembly_router(runtime))
    app.include_router(build_onshape_router(runtime))
    app.include_router(build_catalog_router(runtime))
    app.include_router(build_spec_router(runtime))
    app.include_router(build_edit_router(runtime))
    app.include_router(build_system_router(runtime))

    @app.exception_handler(MisesError)
    async def _mises_error(_req, e: MisesError):
        return _error_response(e)

    def _run(run_id: str):
        try:
            return runtime.store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None

    def _refused(blocked: tuple[str, list[str]]) -> JSONResponse:
        reason, children = blocked
        return JSONResponse({"reason": reason, "children": children}, status_code=409)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/runs")
    async def create_run(body: NewRun):
        # A sketch already fixes the geometry, so a sketch run builds it with the designer rather
        # than the default engineer, whose job is to interview and spec a system from scratch.
        # The app sends the profile default explicitly, so that counts as "no choice" too.
        prof = load_profile(runtime.home)
        profile_default = prof.default_agent
        agent = body.agent or profile_default
        if body.sketch_id and agent == profile_default:
            agent = "designer"
        project_id = (_projects.check_project(runtime.store, body.project_id) if body.project_id
                      else _projects.active_project_id(runtime.store, prof.active_project_id))
        rid = await runtime.start_run(agent, body.prompt, model_ref=body.model_ref,
                                      edit_scope=tuple(body.edit_scope),
                                      read_scope=tuple(body.read_scope),
                                      sketch_id=body.sketch_id, project_id=project_id)
        return {"run_id": rid}

    @app.post("/runs/external")
    async def create_external_run(body: NewExternalRun):
        project_id = (_projects.check_project(runtime.store, body.project_id) if body.project_id
                      else None)
        rid = runtime.create_external_run(body.agent, provider=body.provider, model=body.model,
                                          edit_scope=tuple(body.edit_scope),
                                          read_scope=tuple(body.read_scope),
                                          project_id=project_id)
        return {"run_id": rid}

    @app.get("/runs")
    async def list_runs(limit: int = 50, include_ui: int = 0, project_id: str | None = None,
                        archived: int | None = None):
        """Newest first, enriched with the Plan 5 fields (title, cost, parent_run_id, …). The
        app's own "ui" run (provider "ui") is left out unless include_ui=1. Filters: project_id
        and archived=0|1."""
        arch = None if archived is None else bool(archived)
        runs = runtime.store.list_runs(limit + 50, project_id=project_id, archived=arch)
        if not include_ui:
            runs = [r for r in runs if r.provider != "ui"]
        return [_projects.run_view(r) for r in runs[:limit]]

    @app.get("/runs/tree")
    async def runs_tree(project_id: str | None = None):
        return _projects.runs_tree(runtime.store, project_id)

    @app.post("/runs/cleanup")
    async def cleanup_runs(body: Cleanup):
        ids = _projects.cleanup_candidates(runtime.store, project_id=body.project_id,
                                           older_than_days=body.older_than_days,
                                           status_in=body.status_in, busy=runtime.run_busy)
        if not body.dry_run:
            # each delete re-checks: a run that became busy since the scan is kept
            ids = [rid for rid in ids if await runtime.delete_run(rid) is None]
        return {"ids": ids, "count": len(ids)}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        d = _projects.run_view(_run(run_id))
        d["activity"] = runtime.activity_of(run_id)
        d["pending"] = _pending(run_id)
        return d

    def _pending(run_id: str) -> dict:
        return {"questions": runtime.pending_questions(run_id),
                "approvals": [a.model_dump(mode="json")
                              for a in runtime.store.pending_approvals(run_id)]}

    @app.get("/runs/{run_id}/pending")
    async def get_pending(run_id: str):
        """What the run is blocked on: its open ui_ask_user questions (the question_asked
        payloads) and pending approvals, so a reloaded app recovers the cards (QA C-2)."""
        _run(run_id)
        return _pending(run_id)

    @app.patch("/runs/{run_id}")
    async def patch_run(run_id: str, body: PatchRun):
        _run(run_id)
        fields = {k: v for k, v in (("title", body.title), ("pinned", body.pinned),
                                    ("archived", body.archived)) if v is not None}
        if fields:
            runtime.store.update_run(run_id, **fields)
        return _projects.run_view(runtime.store.get_run(run_id))

    @app.delete("/runs/{run_id}")
    async def delete_run(run_id: str):
        _run(run_id)
        blocked = await runtime.delete_run(run_id)
        if blocked is not None:
            return _refused(blocked)
        return {"ok": True}

    @app.get("/runs/{run_id}/steps")
    async def get_steps(run_id: str, after_id: int = 0):
        _run(run_id)
        return [s.model_dump(mode="json") for s in runtime.store.steps(run_id, after_id=after_id)]

    @app.post("/runs/{run_id}/message")
    async def message(run_id: str, body: Text):
        _run(run_id)
        await runtime.send(run_id, body.text)
        return {"ok": True}

    @app.post("/runs/{run_id}/resume")
    async def resume(run_id: str, body: Resume | None = None):
        """Re-spawn a Claude Code run's session (`--resume`), e.g. after Mises restarted;
        with `text`, also send it as the next message."""
        _run(run_id)
        await runtime.resume_run(run_id)
        if body is not None and body.text:
            await runtime.send(run_id, body.text)
        return {"ok": True}

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        _run(run_id)
        runtime.cancel(run_id)
        return {"ok": True}

    @app.get("/approvals")
    async def approvals(status: str = "pending", run_id: str | None = None):
        if status != "pending":
            raise MisesError(ErrorCode.INVALID_INPUT, f"unsupported status filter: {status}",
                             hint="only status=pending is supported")
        return [a.model_dump(mode="json") for a in runtime.store.pending_approvals(run_id)]

    @app.post("/approvals/{approval_id}/decide")
    async def decide(approval_id: str, body: Decision):
        try:
            a = runtime.approvals.decide(approval_id, body.payload_hash, body.approve,
                                         decided_by=DECIDED_BY, edited_payload=body.edited_payload)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown approval: {approval_id}") from None
        return a.model_dump(mode="json")

    @app.post("/questions/{question_id}/answer")
    async def answer(question_id: str, body: Text):
        runtime.answer(question_id, body.text)
        return {"ok": True}

    @app.websocket("/events")
    async def events(ws: WebSocket, token: str = "", run_id: str | None = None):
        if not _token_ok(token, app.state.token):
            await ws.accept()
            await ws.close(code=4403 if _token_ok(token, app.state.mcp_token) else 4401)
            return
        sub = runtime.events.subscribe(run_id)
        try:
            await ws.accept()

            async with anyio.create_task_group() as tg:
                async def pump() -> None:
                    try:
                        async for ev in sub:
                            await ws.send_text(ev.model_dump_json())
                    except Exception as e:  # client went away mid-send
                        log.debug("events websocket send failed: %r", e)
                    tg.cancel_scope.cancel()

                async def drain() -> None:
                    while (await ws.receive())["type"] != "websocket.disconnect":
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(pump)
                tg.start_soon(drain)
        finally:
            sub.close()

    app.state.token = token
    app.state.mcp_token = mcp_token
    app.router.routes.append(Route("/mcp", endpoint=_McpEndpoint(
        runtime, StreamableHTTPASGIApp(manager)), methods=["GET", "POST", "DELETE"]))
    return app


# ---- serving

def _bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


class _CallerSignalsServer(uvicorn.Server):
    """uvicorn server that leaves signal handling to the caller (embedded use)."""

    @contextmanager
    def capture_signals(self):
        yield


def _uvicorn(runtime: Runtime, token: str, mcp_token: str,
             cls: type[uvicorn.Server] = uvicorn.Server):
    config = uvicorn.Config(create_app(runtime, token, mcp_token=mcp_token), lifespan="on",
                            log_level="warning",
                            access_log=False, timeout_graceful_shutdown=5)
    return cls(config)


def _server_json(runtime: Runtime) -> Path:
    home = Path(runtime.home)
    home.mkdir(parents=True, exist_ok=True)
    return home / "server.json"


def _write_server_json(path: Path, info: dict[str, Any]) -> None:
    """Atomic write: 0600 temp file in the same directory, then os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".server.json.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _remove_server_json(path: Path, port: int) -> None:
    try:
        if json.loads(path.read_text()).get("port") == port:
            path.unlink()
    except (OSError, ValueError):
        pass


class ServerHandle:
    """A server running on the caller's event loop (from `serve_async`)."""

    def __init__(self, port: int, token: str, mcp_token: str, server: uvicorn.Server,
                 task: asyncio.Task, sock: socket.socket, info_path: Path | None,
                 runtime: Runtime):
        self.port, self.token, self.mcp_token = port, token, mcp_token
        self._runtime = runtime
        self._server, self._task, self._sock, self._info_path = server, task, sock, info_path

    @property
    def mcp_url(self) -> str:
        return f"http://{HOST}:{self.port}/mcp"

    async def stop(self) -> None:
        self._runtime.detach_mcp(self.mcp_url)
        self._server.should_exit = True
        try:
            await self._task
        finally:
            self._sock.close()
            if self._info_path is not None:
                _remove_server_json(self._info_path, self.port)


async def serve_async(runtime: Runtime, *, port: int = 0, token: str | None = None,
                      write_server_json: bool = True) -> tuple[int, str, ServerHandle]:
    """Run the server on the caller's event loop, bound to 127.0.0.1 only.

    Waits until uvicorn has started and returns (port, admin token, handle);
    `handle.mcp_token` is the MCP-only token for agents; `await handle.stop()`. With
    write_server_json (default) it writes {"port","token","pid"} to runtime.home/server.json
    (0600) and removes it on stop; pass False for an embedded server (e.g. `mises acp`) so a
    running `mises serve`'s server.json is left alone.
    """
    token = token or secrets.token_urlsafe(32)
    mcp_token = secrets.token_urlsafe(32)
    sock = _bind(port)
    bound = sock.getsockname()[1]
    server = _uvicorn(runtime, token, mcp_token, _CallerSignalsServer)
    task = asyncio.create_task(server.serve(sockets=[sock]), name=f"mises-server-{bound}")
    while not server.started:
        if task.done():
            sock.close()
            exc = task.exception()
            raise RuntimeError("mises server failed to start") from exc
        await asyncio.sleep(0.01)
    info_path = None
    if write_server_json:
        info_path = _server_json(runtime)
        _write_server_json(info_path, {"port": bound, "token": token, "pid": os.getpid()})
    handle = ServerHandle(bound, token, mcp_token, server, task, sock, info_path, runtime)
    runtime.attach_mcp(handle.mcp_url, mcp_token)
    return bound, token, handle


class _Running:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread, sock: socket.socket,
                 info_path: Path, runtime: Runtime, mcp_url: str):
        self.server, self.thread, self.sock, self.info_path = server, thread, sock, info_path
        self.runtime, self.mcp_url = runtime, mcp_url


_servers: dict[int, _Running] = {}


def serve(runtime: Runtime, *, port: int = 0, token: str | None = None,
          mcp_token: str | None = None) -> tuple[int, str]:
    """Start the server on 127.0.0.1 in a background thread; returns (port, token).

    For standalone `mises serve`. There is deliberately no host parameter. Writes
    {"port","token","pid"} to runtime.home/server.json (0600). After this call the runtime
    is driven by the server thread's event loop. `stop(port)` stops it; `wait(port)` blocks
    until it exits. Code that already runs an event loop should use `serve_async`.
    """
    token = token or secrets.token_urlsafe(32)
    mcp_token = mcp_token or secrets.token_urlsafe(32)
    sock = _bind(port)
    bound = sock.getsockname()[1]
    server = _uvicorn(runtime, token, mcp_token)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              name=f"mises-server-{bound}", daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            sock.close()
            raise RuntimeError("mises server failed to start")
        thread.join(0.01)
    info_path = _server_json(runtime)
    mcp_url = f"http://{HOST}:{bound}/mcp"
    _servers[bound] = _Running(server, thread, sock, info_path, runtime, mcp_url)
    runtime.attach_mcp(mcp_url, mcp_token)
    _write_server_json(info_path, {"port": bound, "token": token, "pid": os.getpid()})
    return bound, token


def wait(port: int) -> None:
    r = _servers.get(port)
    if r is not None:
        r.thread.join()


def stop(port: int, timeout: float = 10.0) -> None:
    r = _servers.pop(port, None)
    if r is None:
        return
    r.runtime.detach_mcp(r.mcp_url)
    r.server.should_exit = True
    r.thread.join(timeout)
    r.sock.close()
    _remove_server_json(r.info_path, port)
