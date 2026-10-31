"""The `mises` MCP server: exposes the tool registry to external agents, through the gate.

Each HTTP request is bound to an existing run by the `X-Mises-Run` header (checked by the
endpoint wrapper in app.py before the request reaches this server). The run's frozen policy
decides which tools are listed, and every call goes through `runtime.gate.call`.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import mcp_types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

from ..tools.registry import ToolDef
from ..types import ErrorCode, MisesError, SafetyClass, canonical_json

if TYPE_CHECKING:
    from ..runtime import Runtime

RUN_HEADER = "x-mises-run"
# Tools no external agent ever sees over MCP, and that calling by name is not_found. Only the
# planner's plan-submission handshake stays internal: `agent_spawn`, `agent_wait`, `agent_result`
# and `orchestrator_run` are now exposed so a Claude Code run can fan work out to Mises workers
# (child policy is still enforced server-side, keyed off the caller's run id — see child_policy).
INTERNAL_ONLY = frozenset({"orchestrator_submit_plan"})


def run_id_of(ctx: ServerRequestContext[Any, Any]) -> str:
    headers = getattr(ctx.request, "headers", None)
    run_id = headers.get(RUN_HEADER) if headers is not None else None
    if not run_id:
        raise MCPError(types.INVALID_PARAMS, "missing X-Mises-Run header")
    return run_id


def annotations_for(t: ToolDef) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        read_only_hint=t.safety == SafetyClass.READ,
        destructive_hint=t.safety == SafetyClass.IRREVERSIBLE,
        # the gate dedupes only a retry of the same call; every MCP call is a new call, so a
        # repeated non-read call really runs again
        idempotent_hint=False,
        open_world_hint=t.safety in (SafetyClass.ONSHAPE_MUTATE, SafetyClass.IRREVERSIBLE)
        or t.onshape_cost > 0 or t.open_world,
    )


def build_mcp_server(runtime: Runtime) -> Server:
    async def list_tools(ctx: ServerRequestContext[Any, Any],
                         params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        run_id = run_id_of(ctx)
        try:
            policy = runtime.store.current_policy(run_id)
        except KeyError:
            raise MCPError(types.INVALID_PARAMS, f"unknown run: {run_id}") from None
        tools = []
        for t in runtime.registry.list(classes=set(policy.allowed_classes)):
            if t.name in INTERNAL_ONLY:
                continue
            schema = runtime.registry.schema(t)
            tools.append(types.Tool(name=t.name, description=t.description,
                                    input_schema=schema["input_schema"],
                                    annotations=annotations_for(t)))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx: ServerRequestContext[Any, Any],
                        params: types.CallToolRequestParams) -> types.CallToolResult:
        run_id = run_id_of(ctx)
        # JSON-RPC request ids are only unique per client connection, so they cannot key the
        # gate's per-call dedupe; every MCP call gets a fresh call id.
        call_id = f"mcp_{uuid.uuid4().hex}"
        try:
            if params.name in INTERNAL_ONLY:
                raise MisesError(ErrorCode.NOT_FOUND, f"unknown tool: {params.name}")
            out = await runtime.gate.call(run_id, params.name, dict(params.arguments or {}),
                                          call_id=call_id, workdir=runtime.workdir(run_id),
                                          agent=True)
        except MisesError as e:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=canonical_json(e.to_dict()))],
                is_error=True)
        # images (e.g. renders the caller should look at) go back as MCP image content beside
        # the JSON text, so a vision model such as Claude Code's sees the pixels directly
        content: list = [types.TextContent(
            type="text", text=canonical_json(out.model_dump(mode="json", exclude={"images"})))]
        content += [types.ImageContent(type="image", data=i.data_b64, mime_type=i.media_type)
                    for i in out.images]
        return types.CallToolResult(content=content, is_error=not out.ok)

    return Server("mises", version="0.1.0",
                  instructions="Mises CAD tools. Every call passes the Mises gate; "
                               "irreversible calls wait for a human approval in the Mises app.",
                  on_list_tools=list_tools, on_call_tool=call_tool)
