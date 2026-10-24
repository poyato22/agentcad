"""stdio MCP server that forwards tools/list and tools/call to the local Mises HTTP endpoint.

For agents that only speak stdio MCP:  python -m mises.server.stdio_proxy --run <run_id>
takes the url and MCP-only token from MISES_MCP_URL / MISES_MCP_TOKEN (how Mises launches it for
ACP agents, which run sandboxed away from the Mises home); without them it falls back to
data_home()/server.json written by `serve()` (standalone `mises mcp-stdio`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import anyio
import httpx2
import mcp_types as types
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.stdio import stdio_server

from ..platform import data_home

RUN_HEADER = "X-Mises-Run"


ENV_URL = "MISES_MCP_URL"
ENV_TOKEN = "MISES_MCP_TOKEN"


def server_url_and_token() -> tuple[str, str]:
    """(mcp url, token): from MISES_MCP_URL/MISES_MCP_TOKEN if both set, else server.json."""
    url, token = os.environ.get(ENV_URL), os.environ.get(ENV_TOKEN)
    if url and token:
        return url, token
    path = data_home() / "server.json"
    try:
        info = json.loads(path.read_text())
        return f"http://127.0.0.1:{int(info['port'])}/mcp", str(info["token"])
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"no running Mises server ({path}: {e})") from None


async def run_stdio_proxy(url: str, token: str, run_id: str) -> None:
    # no read timeout: an irreversible call waits for a human approval (up to its TTL)
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", RUN_HEADER: run_id},
                              timeout=httpx2.Timeout(30.0, read=None))
    async with http, Client(streamable_http_client(url, http_client=http), cache=None) as upstream:

        async def list_tools(_ctx: ServerRequestContext[Any, Any],
                             params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
            return await upstream.list_tools(cursor=params.cursor if params else None)

        async def call_tool(_ctx: ServerRequestContext[Any, Any],
                            params: types.CallToolRequestParams) -> types.CallToolResult:
            return await upstream.call_tool(params.name, params.arguments or {})

        local = Server("mises", version="0.1.0", instructions=upstream.instructions,
                       on_list_tools=list_tools, on_call_tool=call_tool)
        async with stdio_server() as (read, write):
            await local.run(read, write, local.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mises-mcp-stdio",
                                 description="stdio MCP proxy to the local Mises server")
    ap.add_argument("--run", required=True, help="run id to bind tool calls to")
    args = ap.parse_args(argv)
    try:
        url, token = server_url_and_token()
    except RuntimeError as e:
        print(f"mises: {e}", file=sys.stderr)
        return 1
    anyio.run(run_stdio_proxy, url, token, args.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
