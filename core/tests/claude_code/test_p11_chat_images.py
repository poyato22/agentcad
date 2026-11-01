"""P11 F7: images attached to a chat message: checked and shrunk (at most 5, each <= 2048 px and
< 1 MB), kept under the build (never inline in the step log), and delivered to the agent as
real image blocks (Claude Code: stream-json image content)."""
import asyncio
import base64
import io
import json
import os
import random

import httpx
import pytest

from mises import chat_images
from mises.types import MisesError, StepKind

from .test_runtime_wiring import _log, served, signed_in  # noqa: F401

HAS_SANDBOX = os.path.exists("/usr/bin/sandbox-exec")


def _img(fmt="PNG", size=(64, 48), noise=False) -> bytes:
    from PIL import Image
    im = Image.new("RGB", size, (200, 40, 40))
    if noise:
        rnd = random.Random(1)
        im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                    for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_images_are_shrunk_under_the_limits_and_kept_under_the_build(tmp_path):
    big = _img("JPEG", (3000, 1500), noise=True)                 # 3000 px of noise, MBs
    blocks, refs = chat_images.store(tmp_path, "b1", [("image/jpeg", _b64(big)),
                                                      ("image/webp", _b64(_img("WEBP")))])
    assert [r["media_type"] for r in refs] == ["image/jpeg", "image/jpeg"]   # WebP is lossy
    assert refs[0]["width"] <= 2048 and refs[0]["bytes"] < 1_000_000
    assert (refs[1]["width"], refs[1]["height"]) == (64, 48)
    assert len(blocks) == 2 and blocks[0][0] == "image/jpeg"
    p = chat_images.path_of(tmp_path, "b1", refs[0]["id"])
    assert p is not None and p.parent == tmp_path / "chat_images" / "b1"
    assert base64.b64decode(blocks[0][1]) == p.read_bytes()
    assert chat_images.path_of(tmp_path, "other", refs[0]["id"]) is None
    assert chat_images.path_of(tmp_path, "b1", "../../etc/passwd") is None


def test_a_purged_builds_chat_images_go_with_it(tmp_path):
    """Fix round 1: emptying the trash removes the build's chat images (only those)."""
    _b, refs = chat_images.store(tmp_path, "b1", [("image/png", _b64(_img()))])
    chat_images.store(tmp_path, "b2", [("image/png", _b64(_img(size=(30, 20))))])
    assert chat_images.forget_build(tmp_path, "b1") == 1
    assert chat_images.path_of(tmp_path, "b1", refs[0]["id"]) is None
    assert not (tmp_path / "chat_images" / "b1").exists()
    assert (tmp_path / "chat_images" / "b2").is_dir()
    assert chat_images.forget_build(tmp_path, "nope") == 0


@pytest.mark.parametrize("images, why", [
    ([("image/png", "")] * 6, "at most 5"),
    ([("image/png", _b64(b"not an image at all"))], "must be PNG"),
    ([("image/png", "@@@")], "base64"),
    ([("image/png", _b64(b"\x89PNG\r\n\x1a\n" + b"x" * 40))], "could not read"),
])
def test_bad_or_too_many_images_are_refused(tmp_path, images, why):
    with pytest.raises(MisesError) as e:
        chat_images.store(tmp_path, "b1", images)
    assert why in e.value.message


@pytest.mark.skipif(not HAS_SANDBOX, reason="claude runs need sandbox-exec")
async def test_a_message_with_an_image_reaches_claude_code_as_an_image_block(
        rt, claude_bin, signed_in):
    async with served(rt) as (port, token, _h):
        pid = rt.active_project_id()
        rid = await rt.start_run("designer", "hello", model_ref="claude-code/sonnet",
                                 project_id=pid)
        await asyncio.wait_for(rt.wait(rid), 15)
        png = _img()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            r = await c.post(f"/runs/{rid}/message", json={
                "text": "what is this bracket", "images": [{"media_type": "image/png",
                                                            "data_b64": _b64(png)}]})
            assert r.status_code == 200, r.text
            await asyncio.wait_for(rt.wait(rid), 15)
            sent = [x["msg"]["message"]["content"] for x in _log(rt, rid) if x["event"] == "stdin"]
            content = sent[-1]
            assert content[0] == {"type": "text", "text": "what is this bracket"}
            img = content[1]
            assert img["type"] == "image" and img["source"]["media_type"] == "image/png"
            step = [s for s in rt.store.steps(rid, [StepKind.MESSAGE])
                    if s.payload["role"] == "user"][-1].payload
            [ref] = step["images"]
            assert ref["width"] == 64 and step["text"] == "what is this bracket"
            assert img["source"]["data"] not in json.dumps(step)   # no bytes in the step log
            got = await c.get(f"/projects/{pid}/chat-images/{ref['id']}")
            assert got.status_code == 200 and got.headers["content-type"] == "image/png"
            assert _b64(got.content) == img["source"]["data"]
            assert (await c.get(f"/projects/{pid}/chat-images/{'0' * 24}")).status_code == 404
            too_many = await c.post(f"/runs/{rid}/message", json={
                "text": "x", "images": [{"media_type": "image/png", "data_b64": _b64(png)}] * 6})
            assert too_many.status_code == 422          # fix round 1: capped at the schema
