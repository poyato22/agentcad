"""Write real server JSON/GLB fixtures for the Mac app's contract tests.

Builds a throwaway Runtime with a scripted FakeProvider, runs one agent that creates a
`mount_plate` part, changes its thickness, measures it and then asks to call a test-only
irreversible tool (so an approval is pending without Onshape), and records what the real
routes return while that approval is pending.

    uv run python scripts/dump_ui_fixtures.py --out tests/fixtures/ui
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
from pydantic import BaseModel

PLATE = '''from build123d import *
VARS = {"thickness": 5.0}
def build(v):
    with BuildPart() as p:
        Box(40, 20, v["thickness"])
        with Locations((10, 0, 0), (-10, 0, 0)):
            Hole(2.1)
    return p.part
'''


class _MergeArgs(BaseModel):
    part_id: str = "mount_plate"


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.tools.registry import tool
    from mises.types import SafetyClass, ToolOutput

    mises.platform.keychain_get = lambda *a: None  # no Keychain access, deterministic status
    runtime_mod.keychain_get = lambda *a: None
    os.environ.pop("ANTHROPIC_API_KEY", None)

    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    turns = [
        [ToolCall(id="c1", name="mirror_part_create", input={
            "part_id": "mount_plate", "script": PLATE, "vars": {"thickness": 5.0}}),
         Done(stop_reason="tool_use")],
        [ToolCall(id="c2", name="mirror_part_set_vars", input={
            "part_id": "mount_plate", "vars": {"thickness": 6.0}}),
         Done(stop_reason="tool_use")],
        [ToolCall(id="c3", name="mirror_measure", input={"part_id": "mount_plate"}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="Thickened the plate to 6 mm; asking to merge."),
         ToolCall(id="c4", name="fixture_merge", input={"part_id": "mount_plate"}),
         Done(stop_reason="tool_use")],
    ]
    fake = FakeProvider(turns)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))

    @tool("fixture_merge", description="test-only irreversible tool",
          safety=SafetyClass.IRREVERSIBLE)
    async def fixture_merge(ctx, args: _MergeArgs) -> ToolOutput:
        return ToolOutput(summary=f"merged {args.part_id}")
    rt.registry.register(fixture_merge)

    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    sub = rt.events.subscribe()
    events: list[str] = []
    run_id = None
    try:
        run_id = await rt.start_run("designer", "thicken the mount plate to 6 mm")
        approval = None
        while approval is None:
            ev = await asyncio.wait_for(sub.queue.get(), 120)
            events.append(ev.model_dump_json())
            if ev.type == "approval_requested":
                approval = ev
            elif ev.type == "run_finished":
                raise SystemExit(f"run ended early: {ev.data}")
        while not sub.queue.empty():
            events.append(sub.queue.get_nowait().model_dump_json())

        transport = httpx.ASGITransport(app=create_app(rt, token))
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as c:
            async def get(path: str):
                r = await c.get(path, headers=auth)
                r.raise_for_status()
                return r

            out.mkdir(parents=True, exist_ok=True)
            routes = {"status.json": "/status", "agents.json": "/agents",
                      "models.json": "/models", "parts.json": "/parts", "runs.json": "/runs",
                      "steps.json": f"/runs/{run_id}/steps", "approvals.json": "/approvals"}
            for name, path in routes.items():
                (out / name).write_text(json.dumps((await get(path)).json(), indent=2) + "\n")
            (out / "mount_plate.glb").write_bytes((await get("/parts/mount_plate/model.glb")).content)
        (out / "events.jsonl").write_text("".join(line + "\n" for line in events))
    finally:
        sub.close()
        if run_id is not None:
            try:
                rt.cancel(run_id)
                await asyncio.wait_for(rt.wait(run_id), 10)
            except Exception:
                pass
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
