"""Smoke-test core: the real Mises server and tools, with a scripted agent instead of a model.

For checking the Mac app against the real core when no model is available. Every run gets a
scripted FakeProvider that builds a 40x20x5 mm `mount_plate` with two 4.2 mm holes
(mirror_part_create, real build123d), measures it, then calls a test-only irreversible tool
(`fixture_merge`) so an approval card appears; after the decision it ends the turn.
Same contract as `mises serve`: binds 127.0.0.1, writes $MISES_HOME/server.json (admin token).

    MISES_HOME=$(mktemp -d) uv run --project ../core python scripts/smoke_core_scripted.py
Test-only: never point it at your real MISES_HOME.
"""
import argparse
import os
import signal
import sys
import threading

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--exit-on-stdin-eof", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("MISES_HOME"):
        print("set MISES_HOME to a throwaway directory", file=sys.stderr)
        return 2

    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server import app as server_app
    from mises.tools.registry import tool
    from mises.types import SafetyClass, ToolOutput

    runtime_mod.keychain_get = lambda *a: None  # no Keychain, no Onshape

    def script() -> FakeProvider:
        return FakeProvider([
            [TextDelta(text="Creating the plate."),
             ToolCall(id="c1", name="mirror_part_create", input={
                 "part_id": "mount_plate", "script": PLATE, "vars": {"thickness": 5.0}}),
             Done(stop_reason="tool_use")],
            [ToolCall(id="c2", name="mirror_measure", input={"part_id": "mount_plate"}),
             Done(stop_reason="tool_use")],
            [TextDelta(text="40 x 20 x 5 mm with two 4.2 mm holes; asking to merge."),
             ToolCall(id="c3", name="fixture_merge", input={"part_id": "mount_plate"}),
             Done(stop_reason="tool_use")],
            [TextDelta(text="Done."), Done(stop_reason="end_turn")],
        ])

    rt = runtime_mod.Runtime(provider_resolver=lambda ref: (script(), "scripted"))

    @tool("fixture_merge", description="test-only irreversible tool", safety=SafetyClass.IRREVERSIBLE)
    async def fixture_merge(ctx, a: _MergeArgs) -> ToolOutput:
        return ToolOutput(summary=f"merged {a.part_id}")
    rt.registry.register(fixture_merge)

    if args.exit_on_stdin_eof:
        def watch() -> None:
            try:
                while sys.stdin.buffer.read(4096):
                    pass
            except (OSError, ValueError):
                pass
            os.kill(os.getpid(), signal.SIGINT)
        threading.Thread(target=watch, daemon=True).start()
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    port, _token = server_app.serve(rt, port=args.port)
    print(f"scripted mises core on http://127.0.0.1:{port}; server.json in {rt.home}", flush=True)
    try:
        server_app.wait(port)
    except KeyboardInterrupt:
        pass
    finally:
        server_app.stop(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
