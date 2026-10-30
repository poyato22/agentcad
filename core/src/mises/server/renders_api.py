"""Render images (admin token): `GET /renders/{id}.png` serves a render that render_views,
critique_model or present_view wrote to `<home>/renders/`. Thread cards carry the render id
(the file stem), never a file path. The id is validated and the file read without following
links (safe_io), so the route can only ever serve a PNG from that one directory."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import Response

from ..cad import safe_io
from ..cad.views import RENDER_ID_RE, renders_dir
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

_PNG = b"\x89PNG\r\n\x1a\n"


def build_renders_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/renders/{name}")
    async def get_render(name: str):
        stem, dot, ext = name.rpartition(".")
        if not dot or ext != "png" or not RENDER_ID_RE.match(stem):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad render id: {name!r}",
                             hint="render ids look like pv_<call>_iso and end in .png")
        d = renders_dir(runtime.home)
        try:
            data = safe_io.read_bytes(d, f"{stem}.png") if d.is_dir() else None
        except (OSError, MisesError):
            data = None
        if not data or not data.startswith(_PNG):
            raise MisesError(ErrorCode.NOT_FOUND, f"unknown render: {stem}")
        return Response(content=data, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})

    return router
