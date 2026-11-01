"""Dump the P11 steps the app renders, from the real core code paths, into tests/fixtures/ui/p11/.

    uv run python scripts/dump_p11_fixtures.py [out_dir]

A temp MISES_HOME, no network, no model: the step payloads come from the functions that write
them in a real run.
- recovery_steps.json   F9: a recoverable failure, "Recovered: ..." notes, a stuck note, and the
                        card at the end of the recovery budget
- feedback_pose.json    F4: an assembly pin with the drawn box, the model's box and pose_check
- chat_image_step.json  F7: a user message step with its image refs (and the image's bytes in
                        chat_image.jpg, as GET /projects/{id}/chat-images/{id} serves them)
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "ui" / "p11"


def _steps(rt, rid) -> list[dict]:
    return [s.model_dump(mode="json") for s in rt.store.steps(rid)]


async def main(out: Path) -> None:
    import os
    home = Path(tempfile.mkdtemp(prefix="mises-p11-"))
    os.environ["MISES_HOME"] = str(home)
    from mises import chat_images
    from mises.assembly.model import Instance
    from mises.claude_code.session import ClaudeCodeSession
    from mises.claude_code.detect import ClaudeInfo
    from mises.orchestrator import lead as lead_mod
    from mises.runtime import Runtime
    from mises.types import StepKind

    rt = Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))
    out.mkdir(parents=True, exist_ok=True)
    pid = rt.active_project_id()

    # F9: what a Claude Code run that recovered twice, got stuck once and then ran out writes
    rid = rt.create_external_run("engineer", provider="claude-code", model="sonnet",
                                 project_id=pid)
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "make the fins"})
    s = ClaudeCodeSession(rt, rid, model_alias="sonnet", info=ClaudeInfo(path=None, version=None),
                          mcp_url="http://127.0.0.1:1/mcp", mcp_token="t")
    s._error("Claude Code exited (code 3) before finishing the turn", failure="exit",
             stderr_tail="")
    rt._recovery_note(rid, "Claude Code exited (code 3) before finishing the turn",
                      "resumed the session", 1)
    s._error("Claude Code: error_during_execution", failure="error_during_execution",
             subtype="error_during_execution")
    rt._recovery_note(rid, "Claude Code: error_during_execution", "resumed the session", 2)
    rt.store.append_step(rid, StepKind.DECISION, {"stuck": {
        "kind": "repeated_failure", "tool": "mirror_part_set_vars", "times": 3,
        "error": "fin_span must be positive", "note": "This exact call has now failed 3 times."}})
    s._error("Claude Code exited (code 3) before finishing the turn", failure="exit",
             stderr_tail="")
    rt._recovery_exhausted(rid, "Claude Code exited (code 3) before finishing the turn", [
        {"cause": "Claude Code exited (code 3) before finishing the turn",
         "action": "resumed the session"},
        {"cause": "Claude Code: error_during_execution", "action": "resumed the session"},
        {"cause": "Claude Code exited (code 3) before finishing the turn",
         "action": "started a fresh session with a recap of the thread"}])
    rt.unregister_cancel_hook(rid)
    (out / "recovery_steps.json").write_text(json.dumps(_steps(rt, rid), indent=1))

    # F4: the coupler pin (drawn 360 mm off the axis, as before the GLB frame fix)
    rt.store.register_part("coupler", pid)
    d = home / "parts" / "coupler"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"bbox": {
        "min": [-31.6, -31.6, 360], "max": [31.6, 31.6, 480], "size": [63.2, 63.2, 120]},
        "face_list": []}))
    inst = rt.assembly_store.add_instance(pid, Instance(part_id="coupler", name="coupler",
                                                        fixed=True))
    rt.lead_queues = getattr(rt, "lead_queues", {})

    async def no_submit(*_a, **_k):
        from types import SimpleNamespace
        return {"run_id": rid, "routed": SimpleNamespace(model_dump=dict), "queued": False}
    orig = lead_mod.submit
    lead_mod.submit = no_submit
    try:
        res = await lead_mod.submit_feedback(
            rt, pid, part_id="coupler", point=[31.5248, -362.7227, 59.1526],
            text="what is this doing here", instance_id=inst.id,
            world_point=[31.5248, -362.7227, 59.1526],
            world_bbox={"min": [-31.6, -391.6, 0], "max": [31.6, -328.4, 120]})
    finally:
        lead_mod.submit = orig
    step = rt.store.get_step(res["step_id"]).model_dump(mode="json")
    (out / "feedback_pose.json").write_text(json.dumps(step, indent=1))

    # F7: a message with an attached image, as the chat route stores it
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 200), (90, 120, 160)).save(buf, "PNG")
    blocks, refs = chat_images.store(home, pid, [("image/png",
                                                  base64.b64encode(buf.getvalue()).decode())])
    rid2 = rt.create_external_run("engineer", provider="claude-code", model="sonnet",
                                  project_id=pid)
    msg = rt.store.append_step(rid2, StepKind.MESSAGE, {
        "role": "user", "text": "why does the lid look offset here?", "images": refs})
    (out / "chat_image_step.json").write_text(json.dumps(
        {"project_id": pid, "step": msg.model_dump(mode="json")}, indent=1))
    p = chat_images.path_of(home, pid, refs[0]["id"])
    (out / ("chat_image" + p.suffix)).write_bytes(p.read_bytes())
    await rt.aclose()
    print(f"wrote {sorted(x.name for x in out.iterdir())}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT))
