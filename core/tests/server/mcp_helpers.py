"""Helpers shared by the MCP and render server tests (a plain module, not a test module, so
importing it never drags another test file's fixtures into a mixed-folder collection)."""
from __future__ import annotations

import struct
from contextlib import asynccontextmanager

import httpx2
import numpy as np
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mises.server.app import create_app

from .conftest import TOKEN


def mcp_client(app, run_id=None, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"}
    if run_id is not None:
        headers["X-Mises-Run"] = run_id
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers,
                              base_url="http://127.0.0.1")
    return http, Client(streamable_http_client("http://127.0.0.1/mcp", http_client=http))


@asynccontextmanager
async def running(rt):
    # entered inside the test task: the MCP session manager's task group must be exited
    # by the task that entered it, so this cannot be an async-generator fixture
    a = create_app(rt, TOKEN)
    async with a.router.lifespan_context(a):
        yield a


def cube_stl() -> bytes:
    s = 10.0
    v = {0: (-s, -s, -s), 1: (s, -s, -s), 2: (s, s, -s), 3: (-s, s, -s),
         4: (-s, -s, s), 5: (s, -s, s), 6: (s, s, s), 7: (-s, s, s)}
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    tris = np.array([[v[a], v[b], v[c]] for a, b, c, d in quads] +
                    [[v[a], v[c], v[d]] for a, b, c, d in quads], dtype=np.float32)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["v"] = tris
    return b"\0" * 80 + struct.pack("<I", len(tris)) + rec.tobytes()


def put_part(rt, part_id="cube"):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.stl").write_bytes(cube_stl())
    (d / "metrics.json").write_text("{}")
