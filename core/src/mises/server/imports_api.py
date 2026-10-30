"""Drawing import routes (admin token).

`POST /imports/drawing` (multipart: `file`, optional `project_id`, `thickness_mm`, `model_ref`)
stores a technical drawing (PDF, PNG, JPEG or DXF, at most 20 MB, sniffed by content) and starts
making the part from it; `GET /imports/{id}` is its progress and, when done, the dimension match
table; `GET /imports/{id}/original` is the original drawing as a picture (page 1 of a PDF or
image, an SVG of a DXF) for the side-by-side view. See mises.imports.drawing_import.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from starlette.datastructures import UploadFile

from .. import projects as _projects
from ..imports.drawing_import import MAX_UPLOAD_BYTES, current, import_dir, start_import
from ..profile import load_profile
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime

_CSP = "default-src 'none'; style-src 'unsafe-inline'"
_FORM_SLACK = 64 * 1024          # multipart boundaries and the small fields around the file
_FIELDS = ("project_id", "thickness_mm", "model_ref")


def build_imports_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/imports/drawing")
    async def post_drawing(request: Request):
        # refused from the header, before the multipart body is spooled to disk
        try:
            length = int(request.headers.get("content-length", ""))
        except ValueError:
            raise MisesError(ErrorCode.INVALID_INPUT, "the upload needs a Content-Length",
                             hint="send the file as a normal (non-chunked) form upload") from None
        if length > MAX_UPLOAD_BYTES + _FORM_SLACK:
            raise MisesError(ErrorCode.INVALID_INPUT, "the drawing is larger than 20 MB",
                             hint="export a smaller file (20 MB at most)")
        form = await request.form(max_files=1, max_fields=8)
        file, fields = form.get("file"), {k: form.get(k) for k in _FIELDS}
        if not isinstance(file, UploadFile):
            raise MisesError(ErrorCode.INVALID_INPUT, "the upload has no `file`")
        project_id, model_ref = fields["project_id"] or None, fields["model_ref"] or None
        try:
            thickness_mm = float(fields["thickness_mm"]) if fields["thickness_mm"] else None
        except (TypeError, ValueError):
            raise MisesError(ErrorCode.INVALID_INPUT, "thickness_mm must be a number") from None
        if not all(v is None or isinstance(v, str) for v in (project_id, model_ref)):
            raise MisesError(ErrorCode.INVALID_INPUT, "bad form field")
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        pid = (_projects.check_project(runtime.store, project_id) if project_id
               else _projects.active_project_id(runtime.store,
                                                load_profile(runtime.home).active_project_id))
        return await start_import(runtime, raw, file_name=file.filename or "drawing",
                                  project_id=pid, thickness_mm=thickness_mm,
                                  model_ref=model_ref or None)

    @router.get("/imports/{import_id}")
    async def get_import(import_id: str):
        return await current(runtime, import_id)

    @router.get("/imports/{import_id}/original")
    async def get_original(import_id: str):
        d = import_dir(runtime.home, import_id)
        svg = d / "original.svg"
        if svg.is_file():
            return Response(svg.read_bytes(), media_type="image/svg+xml", headers={
                "Content-Security-Policy": _CSP, "Cache-Control": "private, no-cache"})
        for name, media in (("page-1.png", "image/png"), ("page-1.jpg", "image/jpeg")):
            f = d / "pages" / name
            if f.is_file():
                return Response(f.read_bytes(), media_type=media,
                                headers={"Cache-Control": "private, no-cache"})
        raise MisesError(ErrorCode.NOT_FOUND, "this import has no picture of its drawing yet")

    return router
