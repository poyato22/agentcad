"""Write real-core fixtures of the generative design routes (Plan 9 D14) for the Mac app's
Generative design panel and result card: POST /topopt (a 2-variant sweep), its
topopt_progress / topopt_finished events, GET /topopt and /topopt/{id} while running and done,
a variant's preview / result GLB and render, accept, the 400 / 409 answers, and an agent's
topopt_run tool steps (the thread card reads its tool_result).

    uv run python scripts/dump_topopt_fixtures.py --out tests/fixtures/ui

Needs CalculiX (ccx) and gmsh. Runs the real sandboxed optimizer on a small block with a
throwaway MISES_HOME (a temp dir), a scripted FakeProvider and no network. Every file it writes
starts with `topopt_`.
"""
import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import httpx

BLOCK = ('VARS = {}\ndef build(v):\n'
         '    return Box(24, 16, 8, align=(Align.MIN, Align.MIN, Align.MIN))\n')
BODY = {"part_id": "block", "fixtures": [{"face": "x-min"}],
        "load_cases": [{"name": "tip", "loads": [{"face": "x-max", "newtons": 40,
                                                   "direction": "-z"}]}],
        "volume_fraction": 0.35, "resolution_mm": 1.0, "material": "PLA",
        "manufacturing": {"process": "none", "symmetry": ["y"]},
        "sweep": {"volume_fraction": [0.3, 0.4]}, "max_variants": 2,
        "reason": "lightest stiff block"}


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises import hardware
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.sim.ccx import find_ccx

    if find_ccx() is None:
        raise SystemExit("CalculiX (ccx) not found: conda install -c conda-forge calculix")
    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    hardware.machine = lambda: {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 24,
                                "chip": "M5 Pro"}
    home = Path(tempfile.mkdtemp(prefix="mises-topopt-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    agent_input = {k: v for k, v in BODY.items() if k not in ("sweep", "max_variants")}
    fake = FakeProvider([
        [TextDelta(text="Starting a generative design run."),
         ToolCall(id="t1", name="topopt_run", input={**agent_input, "volume_fraction": 0.35,
                                                     "verify": False}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="It runs in the background; I will report when it lands."),
         Done(stop_reason="end_turn")]])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    tmp_home, user_home = str(home), str(Path.home())

    def clean(text: str) -> str:
        return text.replace(tmp_home, "/Users/fixture/Mises").replace(user_home, "/Users/fixture")

    def write(name: str, v) -> None:
        (out / name).write_text(clean(json.dumps(v, indent=2)) + "\n")

    sub = rt.events.subscribe()
    events: list[str] = []
    transport = httpx.ASGITransport(app=create_app(rt, token))
    async with httpx.AsyncClient(transport=transport, base_url="http://core", timeout=900) as c:
        async def call(method: str, path: str, body=None, ok=True, **kw):
            r = await c.request(method, path, headers=auth, json=body, **kw)
            if ok:
                assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text}"
            return r

        rid = rt.create_external_run("tester", provider="x", model="y")
        o = await rt.gate.call(rid, "mirror_part_create", {"part_id": "block", "script": BLOCK},
                               call_id="b-block", workdir=rt.workdir(rid))
        assert o.ok, o.summary
        rt.finish_external_run(rid, "done")
        out.mkdir(parents=True, exist_ok=True)

        bad = await call("POST", "/topopt", {**BODY, "volume_fraction": 0.95}, ok=False)
        assert bad.status_code == 400, bad.text
        write("topopt_start_invalid.json", bad.json())
        started = await call("POST", "/topopt", BODY)
        assert started.status_code == 202
        write("topopt_start.json", started.json())
        tid = started.json()["topopt_id"]
        running_written = False
        deadline = time.time() + 1500
        while time.time() < deadline:
            while not sub.queue.empty():
                ev = sub.queue.get_nowait()
                if ev.type.startswith("topopt_"):
                    events.append(ev.model_dump_json())
            rec = (await call("GET", f"/topopt/{tid}")).json()
            if not running_written and any((v.get("preview") or 0) > 0 for v in rec["variants"]):
                write("topopt_running.json", rec)
                running_written = True
            if rec["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.5)
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev.type.startswith("topopt_"):
                events.append(ev.model_dump_json())
        assert rec["status"] == "done", rec
        write("topopt_record.json", rec)
        write("topopt_list.json", (await call("GET", "/topopt", params={"part_id": "block"})).json())
        best = rec["recommended"] or rec["variants"][0]["id"]
        for name in ("preview.glb", "result.glb", "render.png"):
            data = (await call("GET", f"/topopt/{tid}/variants/{best}/{name}")).content
            (out / f"topopt_{name}").write_bytes(data)
        conflict = await call("POST", f"/topopt/{tid}/cancel", ok=False)
        assert conflict.status_code == 409
        write("topopt_cancel_conflict.json", conflict.json())
        acc = await call("POST", f"/topopt/{tid}/accept", {"variant": best, "mode": "new"})
        write("topopt_accept.json", acc.json())
        (out / "topopt_events.jsonl").write_text(clean("\n".join(events)) + "\n")

        # an agent's topopt_run: its tool_call / tool_result steps (the thread's card)
        run_id = await rt.start_run("designer", "make the block lighter with generative design")
        agent_tid = None
        while True:
            ev = await asyncio.wait_for(sub.queue.get(), 600)
            if ev.run_id == run_id and ev.type == "run_finished":
                break
        steps = (await call("GET", f"/runs/{run_id}/steps")).json()
        write("topopt_agent_steps.json", steps)
        for s in steps:
            data = ((s.get("payload") or {}).get("output") or {}).get("data") or {}
            agent_tid = agent_tid or data.get("topopt_id")
        if agent_tid:   # let it finish so the temp home can go
            for _ in range(1200):
                if (await call("GET", f"/topopt/{agent_tid}")).json()["status"] not in (
                        "queued", "running"):
                    break
                await asyncio.sleep(0.5)
    print(f"wrote topopt fixtures to {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/ui"))
    a = ap.parse_args()
    asyncio.run(dump(a.out.resolve()))


if __name__ == "__main__":
    main()
