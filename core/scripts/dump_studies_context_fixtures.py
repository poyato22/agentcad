"""Write real-core fixtures of the studies agent's build context (Plan 9 U-D) for the Mac app's
Studies page: GET /studies/context for a small build (two built parts, a group, an assembly, a
spec with a load case, one finished static study when CalculiX is installed), before and after
the studies agent saves its own proposals with `studies_propose`, and the `studies_proposed`
event.

    uv run python scripts/dump_studies_context_fixtures.py --out tests/fixtures/ui

Runs with a throwaway MISES_HOME (a temp dir), no model and no network. Every file it writes
starts with `studies_context`.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

BRACKET = ('from build123d import *\nVARS = {"t": 6.0}\ndef build(v):\n'
           '    return Box(40, 20, v["t"])\n')
LEG = ('from build123d import *\nVARS = {"length": 80.0}\ndef build(v):\n'
       '    return Box(12, 12, v["length"])\n')
SPEC = {"goal": "a small robot dog that carries 2 kg",
        "load_cases": [{"id": "standing", "name": "standing", "fix": "bottom", "load": "top",
                        "newtons": 60, "direction": "-z", "note": "", "estimate": False}],
        "requirements": [{"id": "sf_min", "kind": "min_safety_factor", "target": 2.0,
                          "load_case_id": "standing", "priority": 5, "source": "user"},
                         {"id": "mass_max", "kind": "max_mass_g", "target": 400, "priority": 3,
                          "source": "user"}]}
MESSAGE = ("Thicken the hip bracket web from 3 mm to 4.5 mm and add a 6 mm fillet where it meets "
           "the hip plate. The stress study sim_1227ecec8686 put the peak at the inner corner with "
           "a safety factor of 1.3 at 2x body weight, under the spec's 2.0. A 4.5 mm web should "
           "bring it above 2 for about 3 g more PLA. After the change, run the same 2x body "
           "weight study again to confirm; this is a guide from a linear FEM study, not a "
           "guarantee.")


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises import hardware
    from mises.assembly.model import Instance
    from mises.server.app import create_app
    from mises.sim.ccx import find_ccx

    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    hardware.machine = lambda: {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 24,
                                "chip": "M5 Pro"}
    # the container check never runs the docker CLI here: OpenFOAM reads as not installed
    from mises.sim import cfd, foam_image
    foam_image.image_status = cfd.image_status = lambda d: "absent"

    home = Path(tempfile.mkdtemp(prefix="mises-studies-context-"))
    os.environ["MISES_HOME"] = str(home)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (None, "none"))
    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    tmp_home, user_home = str(home), str(Path.home())

    def clean(text: str) -> str:
        return text.replace(tmp_home, "/Users/fixture/Mises").replace(user_home, "/Users/fixture")

    def write(name: str, v) -> None:
        (out / name).write_text(clean(json.dumps(v, indent=2)) + "\n")

    sub = rt.events.subscribe()
    transport = httpx.ASGITransport(app=create_app(rt, token))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=900) as c:
            async def call(method: str, path: str, body=None, ok=True, **kw):
                r = await c.request(method, path, headers=auth, json=body, **kw)
                if ok:
                    assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text}"
                return r

            pid = (await call("POST", "/projects", {"name": "Robot dog"})).json()["id"]
            rid = rt.create_external_run("tester", provider="x", model="y", project_id=pid)
            for part, script in (("hip_bracket", BRACKET), ("leg_front", LEG)):
                o = await rt.gate.call(rid, "mirror_part_create", {"part_id": part,
                                                                   "script": script},
                                       call_id=f"b-{part}", workdir=rt.workdir(rid))
                assert o.ok, o.summary
            rt.finish_external_run(rid, "done")
            rt.store.update_part("hip_bracket", label="Hip bracket")
            rt.store.update_part("leg_front", label="Leg front")
            rt.store.put_spec(pid, SPEC)
            for part in ("hip_bracket", "leg_front"):
                rt.assembly_store.add_instance(pid, Instance(part_id=part, name=part))

            if find_ccx() is not None:
                # one finished stress study on the leg (the bracket keeps none, so the rules
                # propose one for it)
                r = await call("POST", "/sims", {"type": "static", "part_id": "leg_front",
                                                 "reason": "standing load", "settings": {
                    "load_cases": [{"name": "standing",
                                    "supports": [{"kind": "fixed", "face": "bottom"}],
                                    "loads": [{"kind": "force", "face": "top", "newtons": 60,
                                               "direction": "-z"}]}]}})
                run = r.json()["run_id"]
                for _ in range(1500):
                    ls = (await call("GET", f"/sims?run_id={run}")).json()
                    if ls and ls[0]["status"] != "running":
                        break
                    await asyncio.sleep(0.2)
                assert ls and ls[0]["status"] == "done", ls
            else:
                print("CalculiX not found: the context lists no past study", file=sys.stderr)

            out.mkdir(parents=True, exist_ok=True)
            write("studies_context.json",
                  (await call("GET", f"/studies/context?project_id={pid}")).json())

            # the studies agent saves its own proposals (through the Gate, like its tool call)
            agent = rt.create_external_run("simulation", provider="x", model="y",
                                           project_id=pid)
            o = await rt.gate.call(agent, "studies_propose", {"proposals": [
                {"part_id": "hip_bracket", "type": "static",
                 "title": "Stress: hip bracket at 2x body weight",
                 "why": "The hip bracket carries the dog's weight and has no stress study; at "
                        "2x body weight (60 N on the top face, bottom bolted) this shows "
                        "whether PLA holds with the spec's safety factor 2.",
                 "settings": {"load_cases": [{"name": "2x body weight",
                                              "supports": [{"kind": "fixed", "face": "bottom"}],
                                              "loads": [{"kind": "force", "face": "top",
                                                         "newtons": 60, "direction": "-z"}]}]}},
                {"part_id": "leg_front", "type": "modal", "title": "Modes: front leg",
                 "why": "The gait shakes the leg at 2-4 Hz; check that its first mode is far "
                        "above that.",
                 "settings": {"supports": [{"kind": "fixed", "face": "top"}], "n_modes": 4}}]},
                call_id="p1", workdir=rt.workdir(agent))
            assert o.ok, o.summary
            # the hand-off to the lead waits on the user's approval of the exact text: the card
            # (GET /approvals) the app shows, then rejected so nothing reaches the lead
            lead = rt.store.create_run("engineer", "fake", "m", "build a robot dog",
                                       project_id=pid).id
            rt.store.update_run(lead, role="lead")
            hand = asyncio.create_task(rt.gate.call(agent, "agent_message", {"text": MESSAGE},
                                                    call_id="m1", workdir=rt.workdir(agent)))
            for _ in range(500):
                cards = (await call("GET", "/approvals")).json()
                if cards:
                    break
                await asyncio.sleep(0.02)
            write("studies_agent_message_approval.json", cards[0])
            await call("POST", f"/approvals/{cards[0]['id']}/decide",
                       {"payload_hash": cards[0]["payload_hash"], "approve": False})
            try:
                await hand
            except Exception:  # noqa: BLE001 - rejected, as intended
                pass
            rt.finish_external_run(agent, "done")
            write("studies_context_agent.json",
                  (await call("GET", f"/studies/context?project_id={pid}")).json())
            events = []
            while not sub.queue.empty():
                ev = sub.queue.get_nowait()
                if ev.type == "studies_proposed":
                    events.append(clean(ev.model_dump_json()))
            (out / "studies_context_events.jsonl").write_text("".join(e + "\n" for e in events))
    finally:
        sub.close()
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote studies context fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
