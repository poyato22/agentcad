"""The error log routes (admin token only: the bearer middleware lets the MCP-only token reach
/mcp and /health, nothing else).

GET  /logs/errors          recent entries, newest first (filters: source, q, limit) + the file path
POST /logs/errors          the app reports an error it rendered (error card, red text, toast...)
POST /logs/errors/reveal   show the log file in Finder"""
from __future__ import annotations

import subprocess
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..error_log import SOURCES


class AppError(BaseModel):
    source: Literal["app", "core", "agent", "tool", "study"] = "app"
    message: str = Field(max_length=20000)
    code: str | None = Field(default=None, max_length=200)
    hint: str | None = Field(default=None, max_length=5000)
    stack: str | None = Field(default=None, max_length=50000)
    build_id: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    tool: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=100)
    where: str | None = Field(default=None, max_length=300)   # the component / selector it came from


def build_logs_router(runtime) -> APIRouter:
    r = APIRouter()

    @r.get("/logs/errors")
    async def errors(limit: int = 200, source: str | None = None, q: str | None = None):
        log = runtime.error_log
        src = source if source in SOURCES else None
        return {"path": str(log.path), "entries": log.recent(min(max(limit, 1), 1000), src, q)}

    @r.post("/logs/errors")
    async def report(body: AppError):
        e = runtime.error_log.record(
            body.source, body.message, code=body.code, hint=body.hint, stack=body.stack,
            build_id=body.build_id, run_id=body.run_id, tool=body.tool, version=body.version,
            extra={"where": body.where} if body.where else None)
        return {"ok": e is not None, "count": (e or {}).get("count", 0)}

    @r.post("/logs/errors/reveal")
    async def reveal():
        log = runtime.error_log
        log.folder.mkdir(parents=True, exist_ok=True)
        target = log.path if log.path.exists() else log.folder
        try:
            subprocess.Popen(["/usr/bin/open", "-R", str(target)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            return {"ok": False, "path": str(log.path)}
        return {"ok": True, "path": str(log.path)}

    return r
