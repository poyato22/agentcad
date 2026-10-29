"""Write real server fixtures for the app's builds, part tree and live-edit UI (Plan 5 Task A3
contract tests).

Builds a throwaway Runtime (temp MISES_HOME; nothing touches the user's real home or the
network), builds two real parts through the app's ui run (sandboxed build123d), and records what
the Builds, Part-tree and live-edit routes return:

    uv run python scripts/dump_builds_edit_fixtures.py --out tests/fixtures/ui

projects_*.json, part_patch*.json, parts_reorder.json, parts_tree.json (+ quad_arm.glb, top_plate.glb), part_source*.json,
part_vars*.json, part_ops*.json and builds_events.jsonl (the `part_changed` events).
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

ARM = '''from build123d import *
VARS = {
    "thickness": 4.0,  # mises: min=2 max=12 step=0.5 unit=mm doc="arm wall"
    "length": 60.0,
}
def build(v):
    return Box(v["length"], 16, v["thickness"])
'''

PLATE = '''from build123d import *
VARS = {"side": 40.0, "wall": 3.0}
def build(v):
    return Box(v["side"], v["side"], v["wall"])
'''

SPHERE = '''from build123d import *
VARS = {"thickness": 4.0, "length": 60.0}
def build(v):
    return Sphere(v["length"] / 2)
'''


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-a3-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))

    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    sub = rt.events.subscribe()
    try:
        transport = httpx.ASGITransport(app=create_app(rt, token))
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=300) as c:
            async def raw(method: str, path: str, body=None, params=None):
                return await c.request(method, path, headers=auth, json=body, params=params)

            async def call(method: str, path: str, body=None, params=None):
                r = await raw(method, path, body, params)
                if r.status_code >= 400:
                    raise SystemExit(f"{method} {path}: {r.status_code} {r.text}")
                return r.json()

            def write(name: str, data) -> None:
                (out / name).write_text(json.dumps(data, indent=2) + "\n")

            async def refused(name: str, method: str, path: str, body=None) -> None:
                r = await raw(method, path, body)
                write(name, {"status": r.status_code, "body": r.json()})

            async def create(part_id: str, script: str, vars_: dict) -> None:
                ui = rt.ui_tool_call("mirror_part_create",
                                     {"part_id": part_id, "script": script, "vars": vars_})
                await ui.task
                if ui.status != "done":
                    raise SystemExit(f"create {part_id}: {ui.error or ui.output}")

            out.mkdir(parents=True, exist_ok=True)
            await create("quad_arm", ARM, {"thickness": 4.0, "length": 60.0})
            await create("top_plate", PLATE, {"side": 40.0, "wall": 3.0})

            projects = await call("GET", "/projects")
            default_id = projects[0]["id"]
            write("projects.json", projects)
            bench = await call("POST", "/projects", {"name": "Bench rig"})
            write("project_create.json", bench)
            write("project_rename.json", await call("PATCH", f"/projects/{bench['id']}",
                                                    {"name": "Bench test rig"}))
            await refused("project_create_empty.json", "POST", "/projects", {"name": "  "})

            # the part tree: recolor, nest the plate under the arm, then reorder at the root
            write("part_patch.json", await call("PATCH", "/parts/quad_arm",
                                                {"color": "#d9822b", "label": "Quad arm"}))
            write("part_patch_parent.json", await call("PATCH", "/parts/top_plate",
                                                       {"parent_id": "quad_arm"}))
            await refused("part_patch_cycle.json", "PATCH", "/parts/quad_arm",
                          {"parent_id": "top_plate"})
            await refused("part_patch_color_bad.json", "PATCH", "/parts/quad_arm",
                          {"color": "orange"})
            await call("PATCH", "/parts/top_plate", {"parent_id": None})
            write("parts_reorder.json", await call("POST", "/parts/reorder", {
                "project_id": default_id, "parent_id": None, "ids": ["top_plate", "quad_arm"]}))
            await refused("parts_reorder_bad.json", "POST", "/parts/reorder", {
                "project_id": default_id, "parent_id": None, "ids": ["top_plate", "top_plate"]})

            # live edits: read the source, change a var, stale 409, an op, a code edit
            src = await call("GET", "/parts/quad_arm/source")
            write("part_source.json", src)
            v = src["version"]
            write("part_vars.json", await call("POST", "/parts/quad_arm/vars",
                                               {"vars": {"thickness": 5.0}, "base_version": v}))
            await refused("part_vars_stale.json", "POST", "/parts/quad_arm/vars",
                          {"vars": {"thickness": 6.0}, "base_version": v})
            await refused("part_vars_no_base.json", "POST", "/parts/quad_arm/vars",
                          {"vars": {"thickness": 6.0}})
            parts = await call("GET", "/parts")
            arm = next(p for p in parts if p["id"] == "quad_arm")
            top = max(arm["metrics"]["face_list"], key=lambda f: f["center"][2])
            edge = next(e for e in arm["metrics"]["edge_list"] if e["type"] == "line")
            cur = arm["version"]
            write("part_ops.json", await call("POST", "/parts/quad_arm/ops", {
                "op": "hole", "target": {"face_id": top["id"]},
                "params": {"diameter": 3.0, "through": True, "point": top["center"]},
                "base_version": cur}))
            cur += 1
            write("part_ops_fillet.json", await call("POST", "/parts/quad_arm/ops", {
                "op": "fillet", "target": {"edge_ids": [edge["id"]]}, "params": {"radius": 1.0},
                "base_version": cur}))
            cur += 1
            await refused("part_ops_bad_face.json", "POST", "/parts/quad_arm/ops", {
                "op": "hole", "target": {"face_id": "f999"}, "params": {"diameter": 3.0},
                "base_version": cur})
            src2 = await call("GET", "/parts/quad_arm/source")
            write("part_source_ops.json", src2)
            # a code edit that removes the hole's face: the op's re-match fails and names the op
            sphere = SPHERE + src2["source"][src2["source"].index("\n\n\n# mises-op"):]
            await refused("part_source_rematch_fail.json", "PUT", "/parts/quad_arm/source",
                          {"source": sphere, "base_version": src2["version"]})
            put = src2["source"].replace("16, v[\"thickness\"]", "18, v[\"thickness\"]")
            write("part_source_put.json", await call("PUT", "/parts/quad_arm/source",
                                                     {"source": put,
                                                      "base_version": src2["version"]}))
            await refused("part_source_stale.json", "PUT", "/parts/quad_arm/source",
                          {"source": put, "base_version": src2["version"]})
            write("parts_tree.json", await call("GET", "/parts"))
            # the models those rows describe (face/edge ids in metrics match these meshes)
            for pid in ("quad_arm", "top_plate"):
                r = await raw("GET", f"/parts/{pid}/model.glb")
                r.raise_for_status()
                (out / f"{pid}.glb").write_bytes(r.content)

            # trash and restore
            write("project_trash.json", await call("DELETE", f"/projects/{bench['id']}"))
            write("projects_with_trash.json", await call("GET", "/projects"))
            write("project_restore.json", await call("POST", f"/projects/{bench['id']}/restore"))
            await call("DELETE", f"/projects/{bench['id']}")
            write("projects_trash_empty.json", await call("POST", "/projects/trash/empty"))
            write("profile_builds.json", await call("PUT", "/profile", {
                "active_project_id": default_id, "open_project_ids": [default_id]}))

        events = []
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev.type == "part_changed":
                events.append(ev.model_dump_json())
        (out / "builds_events.jsonl").write_text("".join(e + "\n" for e in events))
    finally:
        sub.close()
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
