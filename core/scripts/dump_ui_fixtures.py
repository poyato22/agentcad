"""Write real server JSON/GLB fixtures for the Mac app's contract tests.

Builds a throwaway Runtime with a scripted FakeProvider, runs one agent that creates a
`mount_plate` part, changes its thickness, measures it and then asks to call a test-only
irreversible tool (so an approval is pending without Onshape), and records what the real
routes return while that approval is pending.

    uv run python scripts/dump_ui_fixtures.py --out tests/fixtures/ui

With CalculiX installed it also runs two static stress studies on that part through
`POST /sims` (one solved, one with the solver hidden) and records the sim routes, their
`sim_progress` events and the heatmap GLB (sims_*.json, sim_*.json, sim_events.jsonl,
sim_result.glb). `--only sims` writes just those files.
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


async def dump_sims(rt, c, auth, out: Path, required: bool) -> None:
    """Sim routes (core server/sim_api.py) against the real solver, on the mount_plate part."""
    import mises.sim.ccx as ccx_mod
    import mises.sim.tools as sim_tools

    if ccx_mod.find_ccx() is None:
        msg = "CalculiX (ccx) not found: conda install -c conda-forge calculix"
        if required:
            raise SystemExit(msg)
        print(f"skipping sim fixtures: {msg}", file=sys.stderr)
        return
    sub = rt.events.subscribe()
    events: list[str] = []

    async def get(path: str):
        r = await c.get(path, headers=auth)
        r.raise_for_status()
        return r

    async def study(body: dict) -> dict:
        r = await c.post("/sims", headers=auth, json=body)
        assert r.status_code == 202, r.text
        started = r.json()
        while True:     # follow the events like the page does, until the studies run finishes
            ev = await asyncio.wait_for(sub.queue.get(), 600)
            if ev.run_id == started["run_id"]:
                events.append(ev.model_dump_json())
                if ev.type == "run_finished":
                    break
        listed = (await get(f"/sims?run_id={started['run_id']}")).json()
        return {"start": started, "listed": listed}

    try:
        home = str(Path.home())

        def write(name: str, v) -> None:  # the solver path names this machine's user: anonymise it
            (out / name).write_text(json.dumps(v, indent=2).replace(home, "/Users/fixture") + "\n")
        write("sims_solvers.json", (await get("/sims/solvers")).json())
        body = {"part_id": "mount_plate", "fix": "x-min",
                "load": {"face": "x-max", "newtons": 20, "direction": "-z"},
                "material": "PETG", "environment": "Desk"}
        done = await study(body)
        write("sims_start.json", done["start"])
        sim_id = done["listed"][0]["sim_id"]
        rec = (await get(f"/sims/{sim_id}")).json()
        assert rec["status"] == "done", rec
        write("sim_done.json", rec)
        (out / "sim_result.glb").write_bytes((await get(f"/sims/{sim_id}/result.glb")).content)

        # the same study with CalculiX hidden: a failed record with the install hint
        real_find = sim_tools.find_ccx
        sim_tools.find_ccx = lambda: None
        real_status_find = ccx_mod.find_ccx
        ccx_mod.find_ccx = lambda: None
        try:
            write("sims_solvers_missing.json", (await get("/sims/solvers")).json())
            failed = await study(body)
        finally:
            sim_tools.find_ccx = real_find
            ccx_mod.find_ccx = real_status_find
        write("sim_failed.json", (await get(f"/sims/{failed['listed'][0]['sim_id']}")).json())
        write("sims_list.json", (await get("/sims?part_id=mount_plate")).json())
        env = await sim_tools.sim_env_list.handler(None, sim_tools.EnvListArgs())
        write("sim_env.json", env.data)
        (out / "sim_events.jsonl").write_text("".join(line.replace(home, "/Users/fixture") + "\n"
                                                      for line in events))
    finally:
        sub.close()


async def dump(out: Path, only: str = "all") -> None:
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
            if only in ("all", "sims"):
                await dump_sims(rt, c, auth, out, required=only == "sims")
            if only == "sims":
                return
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
        if only == "all":
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
    ap.add_argument("--only", choices=["all", "sims"], default="all",
                    help="sims: only the sim route fixtures (needs CalculiX)")
    args = ap.parse_args()
    asyncio.run(dump(args.out, args.only))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
