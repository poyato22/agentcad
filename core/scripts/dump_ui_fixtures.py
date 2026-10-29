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

    # Plan 4: a fixed Mac and fixed answers from Ollama, LM Studio and OpenRouter, so /models,
    # /status `parallel` and /local/status are deterministic and never touch the network
    from mises import hardware, local_models
    from mises.server import model_lists
    fixed_mac: hardware.Machine = {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 24,
                                   "chip": "M5 Pro"}
    hardware.machine = lambda: fixed_mac
    local_models.find_ollama = lambda: "/opt/fixture/ollama"

    def _net(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "http://localhost:11434/api/version":
            return httpx.Response(200, json={"version": "0.12.3"})
        if url == "http://localhost:11434/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gpt-oss:20b", "size": 13_800_000_000}]})
        if url == model_lists.OPENROUTER_URL:
            return httpx.Response(200, json={"data": [
                {"id": "qwen/qwen3-coder:free", "pricing": {"prompt": "0", "completion": "0"},
                 "supported_parameters": ["tools"]}]})
        raise httpx.ConnectError("no server here (fixture)", request=request)   # LM Studio
    model_lists.TRANSPORT = local_models.TRANSPORT = httpx.MockTransport(_net)

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
            if only == "assembly":
                await dump_assembly(out, call, get)
                return
            routes = {"status.json": "/status", "agents.json": "/agents",
                      "models.json": "/models", "parts.json": "/parts", "runs.json": "/runs",
                      "steps.json": f"/runs/{run_id}/steps", "approvals.json": "/approvals",
                      # Plan 3 (profile_api.py, context_api.py)
                      "profile.json": "/profile", "keys.json": "/keys", "setup.json": "/setup",
                      "claude_code_status.json": "/claude-code/status",
                      # Plan 4 (ui_api.py: Ollama on this Mac)
                      "local_status.json": "/local/status"}
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
                # Ollama answers (fixture): already running; nothing downloading to cancel
                "local_start.json": ("POST", "/local/start", None),
                "local_pull_cancel.json": ("POST", "/local/pull/cancel", {"model": "qwen3-coder:30b"}),
            }
            for name, (method, path, body) in writes.items():
                r = await call(method, path, body)
                (out / name).write_text(json.dumps(r.json(), indent=2) + "\n")
            (out / "mount_plate.glb").write_bytes((await get("/parts/mount_plate/model.glb")).content)
            await dump_draw_print(out, call, get, rt)
            await dump_more(out, call, get, run_id)
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


def _tiny_png(w: int = 8, h: int = 6) -> str:
    import base64
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 240, 236)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


SKETCH_BODY = {
    "sketch": {
        "units": "mm", "width_mm": 120, "height_mm": 80, "notes": "a bracket, 3 mm wall",
        "strokes": [
            {"id": "s1", "kind": "rect", "points": [[10, 10], [70, 10], [70, 40], [10, 40]],
             "closed": True, "label": "plate"},
            {"id": "s2", "kind": "circle", "points": [[27.1, 25]], "closed": True, "center": [25, 25],
             "radius_mm": 2.1, "label": "hole"},
        ],
        "dimensions": [
            {"stroke_id": "s1", "text": "60", "value_mm": 60, "kind": "length"},
            {"stroke_id": "s2", "text": "⌀4.2", "value_mm": 4.2, "kind": "diameter"},
        ],
    },
}


async def dump_more(out: Path, call, get, run_id: str) -> None:
    """Task 9: the rest of the routes the app calls (contract coverage): health, one run,
    the UI context, a key stored and removed (in-memory Keychain, set up by dump_draw_print),
    a sketch stored, read back and started as a run, a model download refused/started, a
    finished slice cancelled, an external run started and cancelled, the pending card decided,
    and cancel-all. Mutating calls come last; events.jsonl is already captured."""
    def write(name: str, data) -> None:
        (out / name).write_text(json.dumps(data, indent=2) + "\n")

    write("health.json", (await get("/health")).json())
    write("run.json", (await get(f"/runs/{run_id}")).json())
    write("context.json", (await get("/context")).json())
    write("key_set.json", (await call("POST", "/keys/openai", {"secret": "sk-fixture-0000"})).json())
    write("key_delete.json", (await call("DELETE", "/keys/openai")).json())
    (out / "drawing.dxf").write_text((await get("/parts/mount_plate/drawing.dxf")).text)

    up = (await call("POST", "/sketches", {**SKETCH_BODY, "photo": {"data_b64": _tiny_png(64, 48),
                                                                     "name": "napkin.png"},
                                           "render_png_b64": _tiny_png()})).json()
    write("sketch_upload.json", up)
    write("sketch.json", (await get(f"/sketches/{up['sketch_id']}")).json())
    sk_run = (await call("POST", "/runs", {"agent": "designer", "prompt": "make this bracket",
                                           "sketch_id": up["sketch_id"]})).json()
    write("sketch_run.json", sk_run)
    steps: list = []
    for _ in range(200):
        steps = (await get(f"/runs/{sk_run['run_id']}/steps?after_id=0")).json()
        if steps:
            break
        await asyncio.sleep(0.05)
    write("sketch_run_steps.json", steps[:1])          # the user step: words + sketch context

    write("local_pull.json", (await call("POST", "/local/pull", {"model": "qwen3-coder:30b"})).json())
    slice_id = json.loads((out / "slice_start.json").read_text())["slice_id"]
    try:                                   # the slice already finished: the core's refusal
        await call("POST", f"/slices/{slice_id}/cancel")
        raise SystemExit("cancelling a finished slice should be refused")
    except httpx.HTTPStatusError as e:
        write("slice_cancel_finished.json", {"status": e.response.status_code,
                                             "body": e.response.json()})

    ext = (await call("POST", "/runs/external", {"agent": "fixture-acp", "provider": "acp",
                                                 "model": "fixture"})).json()
    write("run_external.json", ext)
    write("run_cancel.json", (await call("POST", f"/runs/{ext['run_id']}/cancel")).json())
    card = next(a for a in (await get("/approvals")).json() if a["action"] == "fixture_merge")
    write("approval_decide.json", (await call("POST", f"/approvals/{card['id']}/decide",
                                              {"payload_hash": card["payload_hash"],
                                               "approve": False})).json())
    write("cancel_all.json", (await call("POST", "/runs/cancel_all")).json())


async def dump_assembly(out: Path, call, get) -> None:
    """Plan 5 A4: the assembly routes the app calls, on a hinge of two mount plates in the
    active build. `base` is grounded; `lid` hangs off its +X edge on a revolute joint about Y
    with limits [0, 180] deg. Driven to 90 deg the lid stands up through the base's edge (a real
    interference); driven to 0 it lies flat beside it (touching, no interference); 270 is
    clamped to 180. Then a joint and an instance are patched and deleted."""
    def write(name: str, data) -> None:
        (out / name).write_text(json.dumps(data, indent=2) + "\n")

    project = (await get("/projects")).json()[0]["id"]
    base_url = f"/projects/{project}/assembly"
    write("assembly_parts.json", (await get(f"/parts?project_id={project}")).json())
    base = (await call("POST", f"{base_url}/instances",
                       {"part_id": "mount_plate", "name": "base", "fixed": True})).json()
    lid = (await call("POST", f"{base_url}/instances",
                      {"part_id": "mount_plate", "name": "lid"})).json()
    write("assembly_instance_add.json", lid)
    joint = (await call("POST", f"{base_url}/joints", {
        "kind": "revolute",
        "a": {"instance_id": base["id"], "frame": {"origin": [20, 0, 0], "axis": [0, 1, 0]}},
        "b": {"instance_id": lid["id"], "frame": {"origin": [-20, 0, 0], "axis": [0, 1, 0]}},
        "value": 0, "limits": [0, 180]})).json()
    write("assembly_joint_add.json", joint)
    drive = f"{base_url}/drive"
    write("assembly_drive.json",
          (await call("POST", drive, {"joint_id": joint["id"], "value": 90})).json())
    write("assembly.json", (await get(base_url)).json())
    write("assembly_interference.json", (await get(f"{base_url}/interference")).json())
    write("assembly_drive_clamped.json",
          (await call("POST", drive, {"joint_id": joint["id"], "value": 270})).json())
    await call("POST", drive, {"joint_id": joint["id"], "value": 0})
    write("assembly_interference_none.json", (await get(f"{base_url}/interference")).json())
    write("assembly_joint_patch.json", (await call("PATCH", f"{base_url}/joints/{joint['id']}",
                                                   {"limits": [0, 120]})).json())
    moved = [[1, 0, 0, 0], [0, 1, 0, 30], [0, 0, 1, 0], [0, 0, 0, 1]]
    write("assembly_instance_patch.json", (await call(
        "PATCH", f"{base_url}/instances/{base['id']}", {"transform": moved})).json())
    try:                                    # a joint that would close a loop is refused
        await call("POST", f"{base_url}/joints", {
            "kind": "fixed",
            "a": {"instance_id": lid["id"], "frame": {"origin": [0, 0, 0], "axis": [0, 0, 1]}},
            "b": {"instance_id": base["id"], "frame": {"origin": [0, 0, 0], "axis": [0, 0, 1]}}})
        raise SystemExit("a loop joint should be refused")
    except httpx.HTTPStatusError as e:
        write("assembly_joint_loop.json", {"status": e.response.status_code,
                                           "body": e.response.json()})
    write("assembly_joint_delete.json",
          (await call("DELETE", f"{base_url}/joints/{joint['id']}")).json())
    write("assembly_instance_delete.json",
          (await call("DELETE", f"{base_url}/instances/{lid['id']}")).json())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", choices=["all", "sims", "assembly"], default="all",
                    help="sims: only the sim route fixtures (needs CalculiX); assembly: only the "
                         "assembly route fixtures")
    args = ap.parse_args()
    asyncio.run(dump(args.out, args.only))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
