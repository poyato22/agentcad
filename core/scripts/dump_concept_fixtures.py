"""Write real server fixtures for the app's concept-sketch cards (Plan 9 G-A contract tests).

Builds a throwaway Runtime (temp MISES_HOME, a scripted fake model) whose build lead pitches two
directions for a robot dog (real sandboxed massing builds and renders), the user picks one, the
lead sketches it (v1), the user circles, lassos and pins it with comments, the lead answers
with v2; then the user approves. It records what the real routes return:

    uv run python scripts/dump_concept_fixtures.py --out tests/fixtures/ui/concept \
        --renders ../app/test/fake-core/scenarios/renders

concept_view_pitching.json, concept_view.json (v1 + v2, v1's feedback), concept_view_approved.json,
concept_sketch.json (GET v1 with its feedback), concept_feedback.json (POST answer),
concept_choose.json, concept_approve.json, concept_skip.json, concept_status_tool.json (the
agent tool's data) and concept_events.jsonl (concept_* events and their announcements). Every
render the sketches name is copied to --renders so the fake core serves the real images.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DOG = '''VARS = {{"body_l": {body_l}, "leg": {leg}}}
def build(v):
    body = Pos(0, 0, v["leg"] + 90) * Box(220, v["body_l"], 150)
    head = Pos(0, -v["body_l"] / 2 - 70, v["leg"] + 150) * Box(120, 130, 110)
    legs = [Pos(x, y, v["leg"] / 2) * Cylinder(28, v["leg"])
            for x in (-80, 80) for y in (-v["body_l"] / 2 + 50, v["body_l"] / 2 - 50)]
    out = body + head
    for l in legs:
        out = out + l
    return out
'''


def option(name, mood, body_l, leg, **kw):
    return {"name": name, "mood": mood, "massing_script": DOG.format(body_l=body_l, leg=leg),
            "proportion_note": f"body {body_l} mm, legs {leg} mm",
            "cmf": [{"name": "Shell", "color": "#efeee9", "finish": "gloss"},
                    {"name": "Joints", "color": "#2b2d31", "finish": "matte"}],
            "tradeoffs": {"mass_delta": kw.pop("mass", "0%"), "part_count": kw.pop("parts", 18),
                          "molding": kw.pop("molding", "2-part shells"),
                          "cost_tier": kw.pop("cost", "$$"),
                          "risk": kw.pop("risk", "hip cover pinch at full flexion")}, **kw}


async def dump(out: Path, renders: Path | None) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-concept-"))
    os.environ["MISES_HOME"] = str(home)

    class Ok:
        name = "fake"

        async def stream(self, req):
            async for ev in FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]]
                                         ).stream(req):
                yield ev

    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (Ok(), "fake-model"))
    sub = rt.events.subscribe()
    h = {"Authorization": "Bearer tok"}

    def write(name, data):
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    with TestClient(create_app(rt, "tok")) as c:
        def wait(rid):
            import time
            for _ in range(400):
                if c.get(f"/runs/{rid}", headers=h).json()["status"] != "running":
                    time.sleep(0.05)
                    return
                time.sleep(0.02)

        r = c.post("/runs", headers=h, json={"prompt": "a friendly robot dog, sleek covers",
                                             "model_ref": "anthropic/claude-opus-5-5"})
        r.raise_for_status()
        lead = r.json()["run_id"]
        wait(lead)
        project = rt.store.get_run(lead).project_id

        async def call(name, args, cid):
            o = await rt.gate.call(lead, name, args, call_id=cid, workdir=rt.home)
            if not o.ok:
                raise SystemExit(f"{name} failed: {o.summary}")
            return o

        pitch = await call("pitch_options", {"question": "Pick a direction or mix them", "options": [
            option("Companion", ["friendly", "soft", "appliance"], 520, 260, recommended=True,
                   reason="reads friendly at home scale", mass="+6%", parts=22),
            option("Athlete", ["taut", "fast", "exposed joints"], 600, 340, mass="+11%",
                   parts=26, cost="$$$", molding="4-part shells, 2 undercuts"),
            option("Honest", ["plain", "light", "cheap"], 500, 280, baseline=True, mass="0%",
                   parts=14, cost="$", molding="flat panels")]}, "p1")
        write("concept_view_pitching.json", c.get(f"/projects/{project}/concept",
                                                  headers=h).json())
        chose = c.post(f"/concept/pitches/{pitch.data['pitch_id']}/choose", headers=h,
                       json={"mix": [{"option": "Companion", "take": "body"},
                                     {"option": "Athlete", "take": "legs"}],
                             "comment": "keep it under 6 kg"})
        chose.raise_for_status()
        write("concept_choose.json", chose.json())
        wait(lead)
        v1 = await call("concept_sketch", {
            "massing_script": DOG.format(body_l=540, leg=320), "option": "Companion + Athlete",
            "notes": "Companion body on Athlete legs",
            "callouts": [{"view": "side", "x": 0.08, "y": 0.12, "text": "long legs",
                          "to": [0.4, 0.7]}]}, "s1")
        fb = c.post(f"/concept/sketches/{v1.data['sketch_id']}/feedback", headers=h, json={
            "comment": "Love the stance; make the head friendlier.",
            "marks": [
                {"kind": "circle", "cx": 0.5, "cy": 0.36, "r": 0.09, "comment": "rounder head"},
                {"kind": "lasso", "image": 1, "comment": "shorter body",
                 "points": [[0.3, 0.42], [0.62, 0.4], [0.66, 0.55], [0.32, 0.58]]},
                {"kind": "pin", "x": 0.42, "y": 0.78, "comment": "thicker legs"}]})
        fb.raise_for_status()
        write("concept_feedback.json", fb.json())
        wait(lead)
        await call("concept_sketch", {"massing_script": DOG.format(body_l=480, leg=320),
                                      "notes": "Shorter body, rounder head",
                                      "parent_sketch_id": v1.data["sketch_id"]}, "s2")
        status = await call("concept_status", {}, "st")
        write("concept_status_tool.json", status.data)
        write("concept_view.json", c.get(f"/projects/{project}/concept", headers=h).json())
        write("concept_sketch.json", c.get(f"/concept/sketches/{v1.data['sketch_id']}",
                                           headers=h).json())
        ap = c.post(f"/projects/{project}/concept/approve", headers=h, json={})
        ap.raise_for_status()
        write("concept_approve.json", ap.json())
        wait(lead)
        write("concept_view_approved.json", c.get(f"/projects/{project}/concept",
                                                  headers=h).json())
        sk = c.post(f"/projects/{project}/concept/skip", headers=h)
        sk.raise_for_status()
        write("concept_skip.json", sk.json())
        wait(lead)

    keep = {"concept_sketch", "concept_pitch", "concept_updated", "concept_feedback"}
    with (out / "concept_events.jsonl").open("w") as f:
        while not sub.queue.empty():
            e = sub.queue.get_nowait()
            if e.type in keep or (e.type == "agent_announce"
                                  and str(e.data.get("kind", "")).startswith("concept_")):
                f.write(json.dumps({"type": e.type, "run_id": e.run_id, "data": e.data},
                                   ensure_ascii=False) + "\n")
    if renders is not None:
        renders.mkdir(parents=True, exist_ok=True)
        for png in (home / "renders").glob("ck*.png"):
            shutil.copyfile(png, renders / png.name)
    print(f"wrote concept fixtures to {out} ({project=}, lead={lead})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--renders", type=Path, default=None,
                    help="also copy the sketch renders here (the fake core's scenarios/renders)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(dump(a.out, a.renders))


if __name__ == "__main__":
    sys.exit(main())
