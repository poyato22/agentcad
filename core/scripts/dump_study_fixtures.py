"""Write real-core fixtures of the study routes (C7: study types, settings schemas, templates,
custom materials, charts, probe, section, slices, compare, cancel) and of agent-run studies
(sim_run per study type and design_review, with their compact cards in the tool_result steps)
for the Mac app's Studies page, inline study cards and immersive view (Task A8).

    uv run python scripts/dump_study_fixtures.py --out tests/fixtures/ui

Needs CalculiX (ccx) and gmsh. Runs with a throwaway MISES_HOME (a temp dir), a scripted
FakeProvider and no network. Every file it writes starts with `study_`.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

BEAM = ('from build123d import *\nVARS = {"length": 100.0}\ndef build(v):\n'
        '    return Box(v["length"], 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n')
CUBE = ('from build123d import *\nVARS = {"s": 40.0}\ndef build(v):\n'
        '    return Box(v["s"], v["s"], v["s"])\n')
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
WIND = {"speed_m_s": 5, "refinement": 1, "domain_multiplier": 3, "iterations": 600,
        "timeout_s": 240}

# one sim_run per study type, as an agent would call them (with a reason and requirements)
AGENT_STUDIES = [
    ("static", "beam", {"load_cases": [CASE, {**CASE, "name": "side", "loads": [
        {"kind": "force", "face": "x-max", "newtons": 6, "direction": "+y"}]}]},
     "tip load on the beam under both cases",
     [{"id": "r1", "kind": "min_safety_factor", "target": 2.0},
      {"id": "r2", "kind": "max_deflection_mm", "target": 1.0}]),
    ("modal", "beam", {"supports": [{"kind": "fixed", "face": "x-min"}], "n_modes": 4,
                       "excitation": {"rpm": 12000, "blades": 2, "margin_pct": 15}},
     "keep the beam's modes clear of the 2-blade prop at 12k rpm",
     [{"id": "r3", "kind": "avoid_band_hz", "target": [340, 460]}]),
    ("buckling", "beam", {"load_case": {"name": "axial", "supports": [
        {"kind": "fixed", "face": "x-min"}], "loads": [
        {"kind": "force", "face": "x-max", "newtons": 100, "direction": "-x"}]}, "n_modes": 3},
     "axial load margin", [{"id": "r4", "kind": "min_buckling_factor", "target": 3.0}]),
    ("thermal", "beam", {"temperatures": [{"face": "x-min", "temp_c": 70}],
                         "convection": [{"face": "x-max", "h_w_m2k": 25, "ambient_c": 23}]},
     "motor heat at the root", [{"id": "r5", "kind": "max_temp_c", "target": 60}]),
    ("drop", "beam", {"impact_face": "bottom", "drop_m": 1.0, "k": 10},
     "1 m drop onto the floor", [{"id": "r6", "kind": "min_safety_factor", "target": 2.0}]),
    ("wind", "cube", {**WIND, "yaw_deg": [-15, 0, 15]}, "drag of the cube at 5 m/s",
     [{"id": "r7", "kind": "max_drag_n", "target": 0.5}]),
]
REVIEW_SPEC = {
    "goal": "a stiff 100 mm beam",
    "load_cases": [{"id": "lc1", "name": "tip", "fix": "x-min", "load": "x-max",
                    "newtons": 10, "direction": "-z", "note": "", "estimate": False}],
    "requirements": [
        {"id": "q1", "kind": "min_safety_factor", "target": 2.0, "load_case_id": "lc1",
         "priority": 5, "source": "user"},
        {"id": "q2", "kind": "max_mass_g", "target": 20, "priority": 3, "source": "user"},
        {"id": "q3", "kind": "custom", "target": {"avoid_band_hz": [340, 460]},
         "priority": 4, "source": "agent"},
    ],
}


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta, ToolCall
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.sim.ccx import find_ccx
    from mises import hardware

    if find_ccx() is None:
        raise SystemExit("CalculiX (ccx) not found: conda install -c conda-forge calculix")
    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    hardware.machine = lambda: {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 24,
                                "chip": "M5 Pro"}

    home = Path(tempfile.mkdtemp(prefix="mises-study-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    turns = []
    for i, (typ, part, settings, reason, reqs) in enumerate(AGENT_STUDIES):
        turns.append([TextDelta(text=f"Running the {typ} study."),
                      ToolCall(id=f"s{i + 1}", name="sim_run", input={
                          "type": typ, "part_id": part, "settings": settings,
                          "reason": reason, "requirements": reqs}),
                      Done(stop_reason="tool_use")])
    turns.append([ToolCall(id="rv1", name="design_review", input={
        "part_id": "beam", "spec": REVIEW_SPEC}), Done(stop_reason="tool_use")])
    turns.append([TextDelta(text="Review done: see the table."), Done(stop_reason="end_turn")])
    fake = FakeProvider(turns)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    tmp_home = str(home)
    user_home = str(Path.home())

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
            async def call(method: str, path: str, body=None, ok=True, **kw):
                r = await c.request(method, path, headers=auth, json=body, **kw)
                if ok:
                    assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text}"
                return r

            # parts, built through the Gate like an agent's call
            rid = rt.create_external_run("tester", provider="x", model="y")
            for pid, script in (("beam", BEAM), ("cube", CUBE)):
                o = await rt.gate.call(rid, "mirror_part_create", {"part_id": pid,
                                                                   "script": script},
                                       call_id=f"b-{pid}", workdir=rt.workdir(rid))
                assert o.ok, o.summary
            rt.finish_external_run(rid, "done")

            out.mkdir(parents=True, exist_ok=True)
            write("study_types.json", (await call("GET", "/sims/types")).json())
            write("study_solvers.json", (await call("GET", "/sims/solvers")).json())
            bad = await call("POST", "/sims", {"type": "modal", "part_id": "beam",
                                               "settings": {"n_modes": 500}}, ok=False)
            assert bad.status_code == 400, bad.text
            write("study_start_invalid.json", bad.json())

            # templates and custom materials
            t = await call("POST", "/sims/templates", {"name": "Tip load", "type": "static",
                                                       "settings": {"load_cases": [CASE]}})
            write("study_template_add.json", t.json())
            await call("POST", "/sims/templates", {"name": "Breeze", "type": "wind",
                                                   "settings": WIND})
            write("study_templates.json", (await call("GET", "/sims/templates")).json())
            dup = await call("POST", "/sims/templates", {"name": "tip load", "type": "static",
                                                         "settings": {"load_cases": [CASE]}},
                             ok=False)
            write("study_template_conflict.json", dup.json())
            m = {"name": "Spool PETG-CF", "e_mpa": 3800, "nu": 0.35, "yield_mpa": 45,
                 "density_g_cm3": 1.3, "k_w_mk": 0.25, "alpha_per_k": 5e-5, "hdt_c": 75,
                 "source": "datasheet"}
            write("study_material_add.json", (await call("POST", "/sims/materials", m)).json())
            write("study_materials.json", (await call("GET", "/sims/materials")).json())

            # the agent run: one sim_run per type, then design_review
            run_id = await rt.start_run("sim", "study the beam and the cube, then review the "
                                               "beam against its spec")
            while True:
                ev = await asyncio.wait_for(sub.queue.get(), 1800)
                if ev.run_id == run_id:
                    events.append(ev.model_dump_json())
                    if ev.type == "run_finished":
                        break
            write("study_agent_run.json", (await call("GET", f"/runs/{run_id}")).json())
            steps = (await call("GET", f"/runs/{run_id}/steps")).json()
            write("study_agent_steps.json", steps)
            listed = (await call("GET", f"/sims?run_id={run_id}")).json()
            write("study_list.json", listed)
            by_type: dict[str, str] = {}
            for s in sorted(listed, key=lambda s: s["created_at"]):
                if s["status"] == "done" and s["type"] not in by_type:
                    by_type[s["type"]] = s["sim_id"]
            assert set(by_type) == {"static", "modal", "buckling", "thermal", "drop", "wind"}, \
                listed
            for typ, sid in by_type.items():
                write(f"study_{typ}.json", (await call("GET", f"/sims/{sid}")).json())
                write(f"study_{typ}_charts.json", (await call("GET", f"/sims/{sid}/charts")).json())
                if typ in ("static", "modal", "wind", "thermal"):
                    (out / f"study_{typ}.glb").write_bytes(
                        (await call("GET", f"/sims/{sid}/result.glb")).content)
            s_static, s_wind = by_type["static"], by_type["wind"]
            write("study_probe.json", (await call(
                "GET", f"/sims/{s_static}/probe", params={"x": 50, "y": 5, "z": 5})).json())
            write("study_probe_wind.json", (await call(
                "GET", f"/sims/{s_wind}/probe", params={"x": 0, "y": 0, "z": 40})).json())
            write("study_section.json", (await call(
                "GET", f"/sims/{s_static}/section",
                params={"axis": "y", "offset": 5, "channel": "von_mises", "n": 16})).json())
            write("study_section_wind.json", (await call(
                "GET", f"/sims/{s_wind}/section",
                params={"axis": "y", "offset": 0, "channel": "speed", "n": 16})).json())
            write("study_section_wind_velocity.json", (await call(
                "GET", f"/sims/{s_wind}/section",
                params={"axis": "y", "offset": 0, "channel": "velocity", "n": 16})).json())
            for name in ("slice_xz", "slice_xy"):
                (out / f"study_{name}.png").write_bytes(
                    (await call("GET", f"/sims/{s_wind}/slices/{name}.png")).content)
            write("study_compare.json", (await call(
                "POST", "/sims/compare", {"ids": [s_static, by_type["drop"]]})).json())
            nr = await call("POST", f"/sims/{s_static}/cancel", ok=False)
            assert nr.status_code == 409, nr.text
            write("study_cancel_not_running.json", nr.json())

            # a UI study cancelled while it runs: POST /sims, then POST /sims/{id}/cancel
            r = await call("POST", "/sims", {"type": "wind", "part_id": "cube",
                                             "reason": "cancel me",
                                             "settings": {**WIND, "iterations": 100000,
                                                          "timeout_s": 600}})
            write("study_start.json", r.json())
            ui_run = r.json()["run_id"]
            sim_id = None
            for _ in range(600):
                ls = (await call("GET", f"/sims?run_id={ui_run}")).json()
                if ls and ls[0].get("phase") == "solve":
                    sim_id = ls[0]["sim_id"]
                    break
                await asyncio.sleep(0.2)
            assert sim_id, "the UI wind study never reached the solve phase"
            write("study_cancel.json", (await call("POST", f"/sims/{sim_id}/cancel")).json())
            for _ in range(300):
                rec = (await call("GET", f"/sims/{sim_id}")).json()
                if rec["status"] != "running":
                    break
                await asyncio.sleep(0.2)
            write("study_cancelled.json", rec)
            while not sub.queue.empty():
                ev = sub.queue.get_nowait()
                if ev.run_id == ui_run:
                    events.append(ev.model_dump_json())

            # the templates and materials deletes last (the list fixtures above keep them)
            write("study_template_delete.json",
                  (await call("DELETE", f"/sims/templates/{t.json()['id']}")).json())
            write("study_material_delete.json",
                  (await call("DELETE", f"/sims/materials/{m['name']}")).json())
            # sim_progress events: keep every one, plus the tool and run events around them
            (out / "study_events.jsonl").write_text("".join(clean(e) + "\n" for e in events))
    finally:
        sub.close()
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    t0 = time.time()
    asyncio.run(dump(args.out))
    print(f"wrote study fixtures to {args.out} in {time.time() - t0:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
