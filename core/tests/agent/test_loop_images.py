"""Tool-returned images (renders) reach the model: as image blocks after the tool results on
native runs, never inside the JSON text, past the Gate's 16 KB text cap, and pruned from older
turns so each critique round does not pay for every earlier round's renders."""
import base64
import json

from mises.agent import loop as loop_mod
from mises.gate import MAX_IMAGES, MAX_OUTPUT_BYTES
from mises.tools.registry import tool
from mises.types import ImageBlock, SafetyClass as C, StepKind, ToolImage, ToolOutput, \
    ToolResultBlock

from .test_loop import Env, In, call, text_turn, tool_turn

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 3000).decode()


def _add_render_tool(env, *, images=4, big_data=False):
    @tool("fake_render", description="returns renders", safety=C.READ)
    async def f(ctx, args: In) -> ToolOutput:
        data = {"n": args.n, "blob": "y" * (MAX_OUTPUT_BYTES + 100)} if big_data else {"n": args.n}
        return ToolOutput(summary="rendered", data=data, images=[
            ToolImage(media_type="image/png", data_b64=PNG, label=f"v{i}") for i in range(images)])
    env.reg.register(f)


async def test_images_go_to_the_model_as_image_blocks(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "fake_render", n=1)), text_turn("seen")])
    _add_render_tool(env)
    res = await env.loop().run("render it")
    assert res.stop_reason == "end_turn"
    sent = env.provider.requests[1].messages[-1]           # the tool-results message
    assert isinstance(sent.content[0], ToolResultBlock)    # results lead the message
    imgs = [b for b in sent.content if isinstance(b, ImageBlock)]
    assert len(imgs) == 4 and imgs[0].data_b64 == PNG
    body = json.loads(sent.content[0].content)
    assert "images" not in body and PNG not in sent.content[0].content   # not in the JSON text


async def test_images_survive_the_16kb_text_cap_and_stay_out_of_the_step_log(tmp_path):
    env = Env(tmp_path, [tool_turn(call("c1", "fake_render", n=1)), text_turn("ok")])
    _add_render_tool(env, images=MAX_IMAGES + 3, big_data=True)
    await env.loop().run("render it")
    sent = env.provider.requests[1].messages[-1]
    body = json.loads(sent.content[0].content)
    assert body["handles"] and body["data"] == {}          # the text part was spilled
    imgs = [b for b in sent.content if isinstance(b, ImageBlock)]
    assert len(imgs) == MAX_IMAGES                         # images kept, up to their own cap
    assert any("dropped by the image cap" in w for w in body["warnings"])
    steps = [s for s in env.store.steps(env.run_id) if s.payload.get("name") == "fake_render"
             and s.kind == StepKind.TOOL_RESULT]
    assert steps and PNG not in json.dumps(steps[-1].payload)
    assert len(steps[-1].payload["images"]) == MAX_IMAGES


async def test_older_render_images_are_pruned_from_history(tmp_path):
    turns = [tool_turn(call(f"c{i}", "fake_render", n=i)) for i in range(4)] + [text_turn("ok")]
    env = Env(tmp_path, turns)
    _add_render_tool(env, images=1)
    await env.loop().run("iterate")
    last = env.provider.requests[-1].messages
    with_pixels = [m for m in last if any(isinstance(b, ImageBlock) for b in m.content)]
    assert len(with_pixels) == loop_mod.KEEP_IMAGE_MESSAGES
    placeholders = sum(1 for m in last for b in m.content
                       if getattr(b, "text", "") == "[earlier render image omitted]")
    assert placeholders == 4 - loop_mod.KEEP_IMAGE_MESSAGES


def test_image_tokens_estimated_by_pixels_not_base64():
    from mises.types import Message
    m = Message(role="user", content=[ImageBlock(media_type="image/png", data_b64="A" * 400_000)])
    assert loop_mod._estimate_tokens([m]) < 1000          # ~350 per image, not 100k
