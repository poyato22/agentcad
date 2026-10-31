"""Write real server fixtures for the app's motion and section-sweep view cards (Plan 9 G-D).

Builds a throwaway Runtime (temp MISES_HOME, a fake model that only says "ok") whose build lead
builds a `mount_plate` in the sandbox, hinges two of them on a revolute joint (as the hinge
fixture of dump_ui_fixtures.py: `lid` hangs off the grounded `base`'s +X edge, about Y, limits
0-180), then through the real Gate:

  - `assembly_animate` the lid open and shut, `name`d and `present`ed (a saved animation and its
    `view_presented` card, kind `animation`);
  - `present_view` kind `section_sweep` along X over the whole assembly.

    uv run python scripts/dump_motion_fixtures.py --out tests/fixtures/ui/motion \
        --renders ../app/test/fake-core/scenarios/renders

motion_assembly.json (GET the build's assembly), motion_projects.json, motion_steps.json (the
lead's thread: both cards as stored decision steps), motion_events.jsonl (assembly_animate and
the view_presented announcements), motion_animate_tool.json / motion_sweep_tool.json (the tools'
data). The thumbnails the cards name are copied to --renders so the fake core serves them.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PLATE = '''from build123d import *
VARS = {"thickness": 6.0}
def build(v):
    with BuildPart() as p:
        Box(40, 20, v["thickness"])
        with Locations((10, 0, 0), (-10, 0, 0)):
            Hole(2.1)
    return p.part
'''


async def dump(out: Path, renders: Path | None) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-motion-"))
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
        def ok(r):
            r.raise_for_status()
            return r.json()

        def wait(rid):
            import time
            for _ in range(400):
                if c.get(f"/runs/{rid}", headers=h).json()["status"] != "running":
                    return
                time.sleep(0.02)

        lead = ok(c.post("/runs", headers=h, json={"prompt": "a mount plate with a hinged lid",
                                                   "model_ref": "anthropic/claude-opus-5-5"})
                  )["run_id"]
        wait(lead)
        project = rt.store.get_run(lead).project_id

        async def call(name, args, cid):
            o = await rt.gate.call(lead, name, args, call_id=cid, workdir=rt.home)
            if not o.ok:
                raise SystemExit(f"{name} failed: {o.summary}")
            return o

        await call("mirror_part_create", {"part_id": "mount_plate", "script": PLATE,
                                          "vars": {"thickness": 6.0}, "place": False}, "m1")
        base_url = f"/projects/{project}/assembly"
        base = ok(c.post(f"{base_url}/instances", headers=h,
                         json={"part_id": "mount_plate", "name": "base", "fixed": True}))
        lid = ok(c.post(f"{base_url}/instances", headers=h,
                        json={"part_id": "mount_plate", "name": "lid"}))
        joint = ok(c.post(f"{base_url}/joints", headers=h, json={
            "kind": "revolute",
            "a": {"instance_id": base["id"], "frame": {"origin": [20, 0, 0], "axis": [0, 1, 0]}},
            "b": {"instance_id": lid["id"], "frame": {"origin": [-20, 0, 0], "axis": [0, 1, 0]}},
            "value": 0, "limits": [0, 180]}))
        anim = await call("assembly_animate", {
            "sequence": [{"joint_id": joint["id"], "from": 0, "to": 120, "duration_s": 2},
                         {"joint_id": joint["id"], "from": 120, "to": 0, "duration_s": 1.5}],
            "loops": 2, "name": "Open and shut the lid", "present": True,
            "caption": "The lid swings 120° on its hinge and closes again"}, "a1")
        write("motion_animate_tool.json", anim.data)
        sweep = await call("present_view", {
            "target": {"kind": "project", "id": project}, "kind": "section_sweep",
            "sweep": {"axis": "x"}, "caption": "A cut through both plates and the hinge line"},
            "s1")
        write("motion_sweep_tool.json", sweep.data)
        write("motion_assembly.json", ok(c.get(base_url, headers=h)))
        write("motion_projects.json", ok(c.get("/projects", headers=h)))
        write("motion_steps.json", ok(c.get(f"/runs/{lead}/steps", headers=h)))

    with (out / "motion_events.jsonl").open("w") as f:
        while not sub.queue.empty():
            e = sub.queue.get_nowait()
            if e.type == "assembly_animate" or (e.type == "agent_announce"
                                                and e.data.get("kind") == "view_presented"):
                f.write(json.dumps({"type": e.type, "run_id": e.run_id, "data": e.data},
                                   ensure_ascii=False) + "\n")
    if renders is not None:
        renders.mkdir(parents=True, exist_ok=True)
        for d in (anim.data, sweep.data):
            tid = (d.get("view_spec") or {}).get("thumbnail_id")
            if tid:
                shutil.copyfile(home / "renders" / f"{tid}.png", renders / f"{tid}.png")
    print(f"wrote motion fixtures to {out} ({project=}, lead={lead})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--renders", type=Path, default=None,
                    help="also copy the cards' thumbnails here (the fake core's scenarios/renders)")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(dump(a.out, a.renders))


if __name__ == "__main__":
    sys.exit(main())
