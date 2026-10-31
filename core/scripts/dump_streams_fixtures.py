"""Write real-core fixtures of Plan 9 D4's three streams for the Mac app (Task B3).

A build's lead (scripted FakeProvider) starts a static study of a beam with sim_run, which
returns {sim_id, status: running} at once, asks for a render in the same turn (it finishes
while the study still solves) and replies. When the study ends the core publishes
sim_finished and the lead gets a system note, which starts its next turn.

    uv run python scripts/dump_streams_fixtures.py --out tests/fixtures/ui/p9

Writes streams_steps.json (the lead's steps, each with `stream`), streams_events.jsonl (the
lead's events, each with `stream`: text, sim or view) and streams_sim.json (the finished
record), streams_jobs.json (GET /jobs while it runs) and
streams_job_cancel*.json (POST /jobs/{id}/cancel of a second study). Needs CalculiX (ccx) and gmsh; a throwaway MISES_HOME and no network.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

BEAM = ('from build123d import *\nVARS = {"length": 100.0}\ndef build(v):\n'
        '    return Box(v["length"], 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n')
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises import hardware
    from mises.orchestrator import lead as lead_mod
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.sim import studies
    from mises.sim.ccx import find_ccx

    if find_ccx() is None:
        raise SystemExit("CalculiX (ccx) not found: conda install -c conda-forge calculix")
    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    hardware.machine = lambda: {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 24,
                                "chip": "M5 Pro"}
    home = Path(tempfile.mkdtemp(prefix="mises-streams-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([
        [TextDelta(text="Starting the stress study and a render."),
         ToolCall(id="s1", name="sim_run", input={
             "type": "static", "part_id": "beam", "settings": {"load_cases": [CASE]},
             "reason": "tip load on the beam"}),
         ToolCall(id="v1", name="render_views", input={"part_id": "beam", "views": ["iso"],
                                                       "size": 256}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="The study runs in the background; the render is above."),
         Done(stop_reason="end_turn")],
        [TextDelta(text="The study finished: see its card (guide only)."),
         Done(stop_reason="end_turn")],
    ])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    token = "fixture-token"
    tmp_home, user_home = str(home), str(Path.home())

    def clean(text: str) -> str:
        return text.replace(tmp_home, "/Users/fixture/Mises").replace(user_home, "/Users/fixture")

    def write(name: str, v) -> None:
        (out / name).write_text(clean(json.dumps(v, indent=2)) + "\n")

    sub = rt.events.subscribe()
    events: list[str] = []
    transport = httpx.ASGITransport(app=create_app(rt, token))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=900) as c:
            auth = {"Authorization": f"Bearer {token}"}
            rid = rt.create_external_run("tester", provider="x", model="y")
            o = await rt.gate.call(rid, "mirror_part_create", {"part_id": "beam",
                                                               "script": BEAM},
                                   call_id="b-beam", workdir=rt.workdir(rid))
            assert o.ok, o.summary
            rt.finish_external_run(rid, "done")
            while not sub.queue.empty():
                sub.queue.get_nowait()
            # the engineer as the build's lead (it has every tool: studies and renders)
            lead = await rt.start_run("engineer", "check the beam for stress and show it",
                                      project_id=rt.active_project_id(), role=lead_mod.LEAD)
            await rt.wait(lead)
            # the lead's turn has ended; its study still runs: GET /jobs lists it
            running_jobs = (await c.get("/jobs", headers=auth,
                                        params={"project_id": rt.active_project_id()})).json()
            assert running_jobs and running_jobs[0]["status"] == "running", running_jobs
            sim_id = next(s.payload["output"]["data"]["sim_id"] for s in rt.store.steps(lead)
                          if s.payload.get("name") == "sim_run" and "output" in s.payload)
            assert studies.find_sim(home / "parts", sim_id) is not None
            # wait without reading it (sim_wait would count as read: no note)
            from mises import jobs
            await asyncio.wait({jobs.get(sim_id).task}, timeout=900)
            for _ in range(600):              # the note's turn
                await asyncio.sleep(0.1)
                notes = [s for s in rt.store.steps(lead) if s.payload.get("system_note")]
                if notes and not rt.run_busy(lead):
                    break
            assert notes, "the lead never got the study's note"
            while not sub.queue.empty():
                ev = sub.queue.get_nowait()
                if ev.run_id == lead and ev.type not in ("agent_thinking",):
                    events.append(ev.model_dump_json())
            out.mkdir(parents=True, exist_ok=True)
            steps = (await c.get(f"/runs/{lead}/steps", headers=auth)).json()
            write("streams_steps.json", steps)
            write("streams_sim.json", (await c.get(f"/sims/{sim_id}", headers=auth)).json())
            (out / "streams_events.jsonl").write_text("".join(clean(e) + "\n" for e in events))
            kinds = [json.loads(e)["type"] for e in events]
            assert "sim_finished" in kinds, kinds
            # the render finished while the study was still solving
            render_done = next(i for i, e in enumerate(events)
                               if json.loads(e)["type"] == "tool_result"
                               and json.loads(e)["data"].get("name") == "render_views")
            assert render_done < kinds.index("sim_finished"), kinds
            write("streams_jobs.json", running_jobs)
            # one job stopped from the app: POST /jobs/{id}/cancel (its note starts no turn)
            o2 = await rt.gate.call(lead, "sim_run", {"type": "static", "part_id": "beam",
                                                      "settings": {"load_cases": [CASE]}},
                                    call_id="s2", workdir=rt.workdir(lead), agent=True)
            sid2 = o2.data["sim_id"]
            await asyncio.sleep(0.2)
            r = await c.post(o2.data["cancel_url"], headers=auth)
            assert r.status_code == 200, r.text
            write("streams_job_cancel.json", r.json())
            await asyncio.wait({jobs.get(sid2).task}, timeout=60)
            fin = None
            while not sub.queue.empty():
                ev = sub.queue.get_nowait()
                if ev.type == "sim_finished" and ev.data.get("job_id") == sid2:
                    fin = json.loads(ev.model_dump_json())
            assert fin is not None and fin["data"]["status"] == "cancelled", fin
            write("streams_job_cancelled_event.json", fin)
            again = await c.post(o2.data["cancel_url"], headers=auth)
            assert again.status_code == 409, again.text
            write("streams_job_cancel_not_running.json", again.json())
    finally:
        sub.close()
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote stream fixtures to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
