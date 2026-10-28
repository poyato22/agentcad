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

    from mises.claude_code import detect

    mises.platform.keychain_get = lambda *a: None  # no Keychain access, deterministic status
    runtime_mod.keychain_get = lambda *a: None
    for spec_env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(spec_env, None)

    # never the user's real `claude`: a signed-in stand-in, so /models and /claude-code/* show
    # the signed-in shapes (the fields are the same when signed out)
    detect.find_claude = lambda: detect.ClaudeInfo(path="/opt/fixture/claude", version="2.1.4")

    async def _auth(info):
        return detect.AuthStatus(signed_in=True, auth_method="claude.ai", email=None,
                                 config_dir=None, raw_ok=True)

    async def _logout(info):
        return True
    detect.auth_status = _auth
    detect.logout = _logout

    async def _no_local(urls):          # no local model server answers
        return False

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

        async def _login(*, console: bool = False) -> bool:   # no browser, no `claude auth login`
            return True
        rt.start_claude_login = _login

        transport = httpx.ASGITransport(app=create_app(rt, token, local_probe=_no_local))
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as c:
            async def call(method: str, path: str, body=None):
                r = await c.request(method, path, headers=auth, json=body)
                r.raise_for_status()
                return r

            async def get(path: str):
                return await call("GET", path)

            out.mkdir(parents=True, exist_ok=True)
            routes = {"status.json": "/status", "agents.json": "/agents",
                      "models.json": "/models", "parts.json": "/parts", "runs.json": "/runs",
                      "steps.json": f"/runs/{run_id}/steps", "approvals.json": "/approvals",
                      # Plan 3 (profile_api.py, context_api.py)
                      "profile.json": "/profile", "keys.json": "/keys", "setup.json": "/setup",
                      "claude_code_status.json": "/claude-code/status"}
            for name, path in routes.items():
                (out / name).write_text(json.dumps((await get(path)).json(), indent=2) + "\n")
            writes = {
                "profile_put.json": ("PUT", "/profile", {"display_name": "Fixture User"}),
                "local_test.json": ("POST", "/local/test", {"url": "http://localhost:11434/v1"}),
                "onshape_test.json": ("POST", "/onshape/test", None),
                "claude_code_login.json": ("POST", "/claude-code/login", None),
                "claude_code_logout.json": ("POST", "/claude-code/logout", None),
                "context_put.json": ("PUT", "/context", {
                    "selected_part_id": "mount_plate", "selected_run_id": run_id, "picks": []}),
                "setup_complete.json": ("POST", "/setup/complete", None),
            }
            for name, (method, path, body) in writes.items():
                r = await call(method, path, body)
                (out / name).write_text(json.dumps(r.json(), indent=2) + "\n")
            (out / "mount_plate.glb").write_bytes((await get("/parts/mount_plate/model.glb")).content)
            await dump_draw_print(out, call, get, rt)
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


def _load_fake_prusalink():
    """The fake PrusaLink server from the core tests (stdlib only), loaded by path."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "tests" / "print" / "fake_prusalink.py"
    spec = importlib.util.spec_from_file_location("_fake_prusalink", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def dump_draw_print(out: Path, call, get, rt) -> None:
    """Plan 4: drawing, printer, slice and print shapes. PrusaSlicer is the core tests' fake
    slicer (writes tests/print/fixtures/sample.gcode); the printer is the tests' fake PrusaLink;
    printer keys go to an in-memory Keychain."""
    import stat

    import mises.platform
    from mises.print import tools as print_tools

    def write(name: str, data) -> None:
        (out / name).write_text(json.dumps(data, indent=2) + "\n")

    async def poll(path: str, until=("done", "failed", "cancelled")) -> dict:
        for _ in range(400):
            body = (await get(path)).json()
            if body["status"] in until:
                return body
            await asyncio.sleep(0.05)
        raise SystemExit(f"{path} never finished: {body}")

    write("drawing.json", (await get("/parts/mount_plate/drawing.json")).json())
    (out / "drawing.svg").write_text((await get("/parts/mount_plate/drawing.svg")).text)

    keys: dict[str, str] = {}
    mises.platform.keychain_get = lambda svc, acct: keys.get(f"{svc}/{acct}")
    mises.platform.keychain_set = lambda svc, acct, v: keys.__setitem__(f"{svc}/{acct}", v)
    mises.platform.keychain_delete = lambda svc, acct: keys.pop(f"{svc}/{acct}", None) is not None
    print_tools.keychain_get = mises.platform.keychain_get

    sample = Path(__file__).resolve().parents[1] / "tests" / "print" / "fixtures" / "sample.gcode"
    slicer_bin = Path(tempfile.mkdtemp(prefix="mises-fake-slicer-")) / "PrusaSlicer"
    slicer_bin.write_text(f"#!{sys.executable}\nimport sys\nargs=sys.argv[1:]\n"
                          f"out=args[args.index('--output')+1]\n"
                          f"open(out,'w').write(open({str(sample)!r}).read())\n")
    slicer_bin.chmod(slicer_bin.stat().st_mode | stat.S_IXUSR)
    os.environ["MISES_PRUSASLICER"] = str(slicer_bin)

    fpl = _load_fake_prusalink()
    with fpl.FakePrusaLink("IDLE") as printer:
        caps = (await get("/print/capabilities")).json()
        caps["slicer"]["path"] = "/opt/fixture/PrusaSlicer"  # the temp fake's path is random
        write("print_capabilities.json", caps)
        write("printer_add.json", (await call("POST", "/printers", {
            "name": "Bench MK4", "host": printer.host, "api_key": fpl.API_KEY})).json())
        write("printers.json", (await get("/printers")).json())
        write("printer_status.json", (await get("/printers/bench_mk4/status")).json())
        start = (await call("POST", "/slices", {"part_id": "mount_plate",
                                                "printer_id": "bench_mk4"})).json()
        write("slice_start.json", start)
        write("slice.json", await poll(f"/slices/{start['slice_id']}"))
        sent = (await call("POST", "/prints", {"slice_id": start["slice_id"],
                                               "printer_id": "bench_mk4"})).json()
        write("print_start.json", sent)
        card = None
        for _ in range(200):
            card = next((a for a in (await get("/approvals")).json()
                         if a["action"] == "print_send"), None)
            if card:
                break
            await asyncio.sleep(0.05)
        if card is None:
            raise SystemExit("no print_send approval card")
        write("print_approval.json", card)
        write("print_running.json", (await get(f"/prints/{sent['call_id']}")).json())
        await call("POST", f"/approvals/{card['id']}/decide",
                   {"payload_hash": card["payload_hash"], "approve": True})
        write("print_done.json", await poll(f"/prints/{sent['call_id']}"))
        printer.state = "PRINTING"
        write("printer_status_printing.json", (await get("/printers/bench_mk4/status")).json())
    write("printer_status_unreachable.json", (await get("/printers/bench_mk4/status")).json())
    write("printer_delete.json", (await call("DELETE", "/printers/bench_mk4")).json())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
