"""Local API server: REST + WebSocket events for the Mac app and CLI, plus the MCP endpoint.

Binds 127.0.0.1 only; every HTTP route needs `Authorization: Bearer <token>`, the events
WebSocket takes `?token=`.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socket
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import anyio
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..platform import data_home
from ..types import ErrorCode, MisesError
from .mcp_server import RUN_HEADER, build_mcp_server

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
    return JSONResponse(e.to_dict(), status_code=status or _HTTP_STATUS.get(e.code, 400))


def _token_ok(given: str | None, token: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), token.encode())


class _BearerAuth:
    """Rejects any HTTP request without the session bearer token (WebSockets check ?token=)."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            auth = Headers(scope=scope).get("authorization", "")
            given = auth[7:] if auth.lower().startswith("bearer ") else None
            if not _token_ok(given, self.token):
                resp = JSONResponse({"error": "unauthorized", "message": "missing or bad token"},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
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
                if run.status != "running":
                    resp = _error_response(MisesError(
                        ErrorCode.INVALID_INPUT, f"run {run_id} is {run.status}",
                        hint="create a new run"), status=409)
        if resp is not None:
            await resp(scope, receive, send)
            return
        await self.inner(scope, receive, send)


class NewRun(BaseModel):
    agent: str
    prompt: str
    model_ref: str | None = None
    edit_scope: list[str] = ["*"]
    read_scope: list[str] = ["*"]


class NewExternalRun(BaseModel):
    agent: str
    provider: str
    model: str
    edit_scope: list[str] = ["*"]
    read_scope: list[str] = ["*"]


class Text(BaseModel):
    text: str


class Decision(BaseModel):
    payload_hash: str
    approve: bool
    edited_payload: dict | None = None


def create_app(runtime: Runtime, token: str) -> FastAPI:
    if not token:
        raise ValueError("a non-empty token is required")
    mcp_server = build_mcp_server(runtime)
    manager = StreamableHTTPSessionManager(
        app=mcp_server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
            allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*",
                             "http://localhost", "http://localhost:*"]))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with manager.run():
            yield

    app = FastAPI(title="Mises", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.runtime = runtime
    app.state.mcp_session_manager = manager
    app.add_middleware(_BearerAuth, token=token)

    @app.exception_handler(MisesError)
    async def _mises_error(_req, e: MisesError):
        return _error_response(e)

    def _run(run_id: str):
        try:
            return runtime.store.get_run(run_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown run: {run_id}") from None

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/runs")
    async def create_run(body: NewRun):
        rid = await runtime.start_run(body.agent, body.prompt, model_ref=body.model_ref,
                                      edit_scope=tuple(body.edit_scope),
                                      read_scope=tuple(body.read_scope))
        return {"run_id": rid}

    @app.post("/runs/external")
    async def create_external_run(body: NewExternalRun):
        rid = runtime.create_external_run(body.agent, provider=body.provider, model=body.model,
                                          edit_scope=tuple(body.edit_scope),
                                          read_scope=tuple(body.read_scope))
        return {"run_id": rid}

    @app.get("/runs")
    async def list_runs(limit: int = 50):
        return [r.model_dump(mode="json") for r in runtime.store.list_runs(limit)]

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        return _run(run_id).model_dump(mode="json")

    @app.get("/runs/{run_id}/steps")
    async def get_steps(run_id: str, after_id: int = 0):
        _run(run_id)
        return [s.model_dump(mode="json") for s in runtime.store.steps(run_id, after_id=after_id)]

    @app.post("/runs/{run_id}/message")
    async def message(run_id: str, body: Text):
        _run(run_id)
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
            await ws.close(code=4401)
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
    app.router.routes.append(Route("/mcp", endpoint=_McpEndpoint(
        runtime, StreamableHTTPASGIApp(manager)), methods=["GET", "POST", "DELETE"]))
    return app


# ---- serving

class _Running:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread, sock: socket.socket):
        self.server, self.thread, self.sock = server, thread, sock


_servers: dict[int, _Running] = {}


def _server_json():
    return data_home() / "server.json"


def _write_server_json(info: dict[str, Any]) -> None:
    path = _server_json()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(info).encode())
    finally:
        os.close(fd)


def serve(runtime: Runtime, *, port: int = 0, token: str | None = None) -> tuple[int, str]:
    """Start the server on 127.0.0.1 in a background thread; returns (port, token).

    There is deliberately no host parameter. Writes {"port","token","pid"} to
    data_home()/server.json (mode 0600). After this call the runtime is driven by the
    server thread's event loop. Stop it with `stop(port)`; `wait(port)` blocks until it exits.
    """
    token = token or secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(128)
    bound = sock.getsockname()[1]
    config = uvicorn.Config(create_app(runtime, token), lifespan="on", log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              name=f"mises-server-{bound}", daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            sock.close()
            raise RuntimeError("mises server failed to start")
        thread.join(0.01)
    _servers[bound] = _Running(server, thread, sock)
    _write_server_json({"port": bound, "token": token, "pid": os.getpid()})
    return bound, token


def wait(port: int) -> None:
    r = _servers.get(port)
    if r is not None:
        r.thread.join()


def stop(port: int, timeout: float = 10.0) -> None:
    r = _servers.pop(port, None)
    if r is None:
        return
    r.server.should_exit = True
    r.thread.join(timeout)
    r.sock.close()
    path = _server_json()
    try:
        if json.loads(path.read_text()).get("port") == port:
            path.unlink()
    except (OSError, ValueError):
        pass
