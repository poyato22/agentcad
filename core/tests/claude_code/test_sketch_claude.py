"""Sketch runs on the user's Claude Code: images as content blocks, the sketch in the context."""
import asyncio
import base64
import io
import json
import os

import pytest
from PIL import Image

from mises import sketch as sk
from mises.claude_code import ClaudeCodeSession, find_claude
from mises.claude_code.prompts import context_block
from mises.context_tools import build_context
from mises.server.app import serve_async

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")

SKETCH = {"units": "mm", "width_mm": 200, "height_mm": 150,
          "strokes": [{"id": "s1", "kind": "line", "points": [[0, 0], [30, 0]]}],
          "dimensions": [{"stroke_id": "s1", "text": "30", "value_mm": 30}]}


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 0)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _stdin(rt, rid) -> list[dict]:
    p = rt.workdir(rid) / "fake_claude.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return [r["msg"] for r in rows if r["event"] == "stdin"]


@pytest.fixture
def signed_in(auth_state):
    auth_state.write_text("signed_in")
    return auth_state


def test_context_block_labels_the_sketch_as_data():
    block = context_block({"sketch": {"notes": "x"}})
    label = block.split("\n")[1]
    assert "data, not instructions" in label and "sketch" in label
    plain = context_block({"selected_part_id": "a"}).split("\n")[1]
    assert "sketch" not in plain


@pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")
async def test_session_prompt_sends_images_as_blocks(rt, claude_bin, signed_in):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    s = ClaudeCodeSession(rt, rid, model_alias="sonnet", info=find_claude(),
                          mcp_url="http://127.0.0.1:1/mcp", mcp_token="t")
    await s.start()
    try:
        await s.prompt("build it", context={"sketch": {"notes": "n"}},
                       images=[("image/png", _png())])
    finally:
        await s.close()
    content = _stdin(rt, rid)[0]["message"]["content"]
    assert content[0]["type"] == "text" and content[0]["text"].startswith("<mises-context>")
    assert content[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": _png()}}
    step = [x for x in rt.store.steps(rid) if x.payload.get("role") == "user"][0]
    assert step.payload["images"] == 1
    assert _png() not in json.dumps(step.payload)        # the step log keeps no image bytes


@needs_sandbox
async def test_claude_run_from_sketch(rt, claude_bin, signed_in):
    # a noisy 4000 x 3000 camera photo: must reach Claude Code as a JPEG under the 5 MB limit
    import random
    noisy = Image.frombytes("RGB", (4000, 3000), random.Random(3).randbytes(4000 * 3000 * 3))
    buf = io.BytesIO()
    noisy.save(buf, "JPEG", quality=40)
    sid = sk.save_sketch(rt.home, sk.SketchUpload.model_validate(
        {"sketch": SKETCH, "photo": {"data_b64": base64.b64encode(buf.getvalue()).decode()}})
    )["sketch_id"]
    _port, _token, handle = await serve_async(rt, write_server_json=False)
    try:
        rid = await rt.start_run("designer", "Build my sketch", model_ref="claude-code/sonnet",
                                 sketch_id=sid)
        await asyncio.wait_for(rt.wait(rid), 15)
    finally:
        await handle.stop()
    first = _stdin(rt, rid)[0]["message"]["content"]
    text = first[0]["text"]
    assert text.startswith("<mises-context>") and sid in text and "at most 3" in text
    assert [b["type"] for b in first] == ["text", "image"]
    src = first[1]["source"]
    assert src["media_type"] == "image/jpeg" and len(src["data"]) < 5 * 1024 * 1024
    assert rt.store.get_run(rid).task == "Build my sketch"
    ctx = build_context(rt, rid)
    assert ctx["sketch"]["images"] == [{"kind": "photo", "path": "sketch/photo.jpg",
                                        "attached": True}]
