"""Over MCP, renders reach a vision-capable client (Claude Code) as image content blocks, so
the calling model critiques its own renders: no second vision model, no "unavailable"."""
import base64
import io
import json
import struct

import numpy as np
from mcp_types import ImageContent, TextContent

from mises.claude_code.session import SOURCE as CLAUDE_CODE

from .test_mcp import mcp_client, running


def _cube_stl() -> bytes:
    s = 10.0
    v = {0: (-s, -s, -s), 1: (s, -s, -s), 2: (s, s, -s), 3: (-s, s, -s),
         4: (-s, -s, s), 5: (s, -s, s), 6: (s, s, s), 7: (-s, s, s)}
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    tris = np.array([[v[a], v[b], v[c]] for a, b, c, d in quads] +
                    [[v[a], v[c], v[d]] for a, b, c, d in quads], dtype=np.float32)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["v"] = tris
    return b"\0" * 80 + struct.pack("<I", len(tris)) + rec.tobytes()


def _put_part(rt, part_id="cube"):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.stl").write_bytes(_cube_stl())
    (d / "metrics.json").write_text("{}")


def _png_size(b64: str):
    from PIL import Image
    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        assert im.format == "PNG"
        return im.size


async def test_render_views_returns_image_blocks_to_claude_code(rt):
    _put_part(rt)
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider=CLAUDE_CODE, model="opus")
        assert rt.run_sees_images(rid)
        http, client = mcp_client(app, rid)
        async with http, client:
            res = await client.call_tool("render_views", {"part_id": "cube", "size": 1024})
    assert res.is_error is False
    texts = [c for c in res.content if isinstance(c, TextContent)]
    images = [c for c in res.content if isinstance(c, ImageContent)]
    assert len(texts) == 1 and len(images) == 4
    assert all(i.mime_type == "image/png" and max(_png_size(i.data)) <= 512 for i in images)
    body = json.loads(texts[0].text)
    assert body["ok"] and "images" not in body                 # pixels never in the JSON text


async def test_critique_model_self_critique_over_mcp(rt):
    _put_part(rt)
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider=CLAUDE_CODE, model="opus")
        http, client = mcp_client(app, rid)
        async with http, client:
            first = await client.call_tool("critique_model", {"part_id": "cube",
                                                              "request": "a B-2 bomber"})
            images = [c for c in first.content if isinstance(c, ImageContent)]
            body = json.loads(first.content[0].text)
            assert len(images) == 4 and body["data"]["mode"] == "self"
            assert body["data"]["awaiting_report"] is True and body["data"]["available"] is True
            report = await client.call_tool("critique_model", {
                "part_id": "cube", "request": "a B-2 bomber",
                "report": {"score": 0.92, "issues": []}})
            done = json.loads(report.content[0].text)["data"]
    assert done["done"] is True and done["stop_reason"] == "score_threshold"


async def test_blind_native_run_gets_no_images(rt):
    _put_part(rt)
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider="ollama", model="qwen3")
        assert not rt.run_sees_images(rid)
        http, client = mcp_client(app, rid)
        async with http, client:
            res = await client.call_tool("render_views", {"part_id": "cube"})
    assert not [c for c in res.content if isinstance(c, ImageContent)]
    assert json.loads(res.content[0].text)["handles"]
