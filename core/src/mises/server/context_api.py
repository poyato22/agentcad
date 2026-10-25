"""`PUT /context`: the app tells Mises what the user has selected (admin token, like every
non-MCP route). `GET /context` returns what agents see. POST is accepted as an alias of PUT
because the webview's CORS policy (ui_api.add_cors) only allows GET and POST."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from ..cad.tools import PART_ID_RE
from ..context_tools import MAX_PICKS, build_context, register_context_tools, ui_context

if TYPE_CHECKING:
    from ..runtime import Runtime


class ContextIn(BaseModel):
    selected_part_id: str | None = None
    selected_run_id: str | None = Field(default=None, max_length=64)
    picks: list[Any] = Field(default_factory=list, max_length=MAX_PICKS)

    @field_validator("selected_part_id")
    @classmethod
    def _part_id(cls, v: str | None) -> str | None:
        if v is not None and not PART_ID_RE.match(v):
            raise ValueError(f"part id must match {PART_ID_RE.pattern}")
        return v


def build_context_router(runtime: Runtime) -> APIRouter:
    register_context_tools(runtime.registry)
    router = APIRouter()

    async def put_context(body: ContextIn):
        ui_context(runtime).update(selected_part_id=body.selected_part_id,
                                   selected_run_id=body.selected_run_id, picks=body.picks)
        return build_context(runtime)

    router.add_api_route("/context", put_context, methods=["PUT", "POST"])

    @router.get("/context")
    async def get_context():
        return build_context(runtime)

    return router
