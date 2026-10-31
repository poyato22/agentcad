"""GET /studies/context?project_id=: the studies agent's build context (Plan 9 U-D) for the
Studies page: parts with material and mass, assembly, loads, spec status, past studies,
calibrations, solvers with their accuracy bands, and the proposed next studies (the studies
agent's own when it saved some, else the rule proposals). Admin token only, read only."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from ..sim.studies_agent import PROJECT_ID_RE, context_for, register_studies_agent_tools
from ..types import ErrorCode, MisesError

if TYPE_CHECKING:
    from ..runtime import Runtime


def build_studies_context_router(runtime: Runtime) -> APIRouter:
    register_studies_agent_tools(runtime.registry)  # no-op: Runtime registers them
    router = APIRouter()

    @router.get("/studies/context")
    async def studies_context(project_id: str):
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad project id: {project_id!r}")
        try:
            runtime.store.get_project(project_id)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"no build {project_id}") from None
        return await run_in_threadpool(context_for, runtime, project_id)

    return router
