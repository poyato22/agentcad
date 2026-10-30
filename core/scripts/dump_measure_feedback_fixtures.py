"""Write real server fixtures for the viewer's measure and feedback-pin UI (Plan 7 Task U2a
contract tests).

Builds a throwaway Runtime (temp MISES_HOME; nothing touches the user's real home or the
network), builds a 40 × 20 × 5 plate with two Ø4.2 holes through the app's ui run (sandboxed
build123d), and records what the measure and feedback routes return:

    uv run python scripts/dump_measure_feedback_fixtures.py --out tests/fixtures/ui

measure_parts.json (metrics with hole radii), part_measure_*.json and project_feedback*.json.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

MOUNT_PLATE = '''from build123d import *
VARS = {"thickness": 5.0}
def build(v):
    with BuildPart() as p:
        Box(40, 20, v["thickness"])
        with Locations((10, 0, 0), (-10, 0, 0)):
            Hole(2.1)
    return p.part
'''


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-u2a-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]] * 4)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))

    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
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
                (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

            async def refused(name: str, method: str, path: str, body=None) -> None:
                r = await raw(method, path, body)
                write(name, {"status": r.status_code, "body": r.json()})

            out.mkdir(parents=True, exist_ok=True)
            ui = rt.ui_tool_call("mirror_part_create", {"part_id": "mount_plate",
                                                        "script": MOUNT_PLATE, "vars": {}})
            await ui.task
            if ui.status != "done":
                raise SystemExit(f"create: {ui.error or ui.output}")

            parts = await call("GET", "/parts")
            write("measure_parts.json", parts)
            plate = next(p for p in parts if p["part_id"] == "mount_plate")
            m, v = plate["metrics"], plate["version"]
            circle = next(e for e in m["edge_list"] if e["type"] == "circle")
            line = next(e for e in m["edge_list"] if e["type"] == "line")
            top = next(f for f in m["face_list"] if f["normal"] == [0.0, 0.0, 1.0])
            bottom = next(f for f in m["face_list"] if f["normal"] == [0.0, 0.0, -1.0])
            url = "/parts/mount_plate/measure"
            write("part_measure_edge.json", await call("POST", url, {
                "refs": [{"edge_id": circle["id"]}], "base_version": v}))
            write("part_measure_line.json", await call("POST", url, {
                "refs": [{"edge_id": line["id"]}], "base_version": v}))
            write("part_measure_face.json", await call("POST", url, {
                "refs": [{"face_id": top["id"]}], "base_version": v}))
            write("part_measure_two_faces.json", await call("POST", url, {
                "refs": [{"face_id": top["id"]}, {"face_id": bottom["id"]}], "base_version": v}))
            write("part_measure_points.json", await call("POST", url, {
                "refs": [{"point": [-20, -10, 2.5]}, {"point": [20, 10, 2.5]}]}))
            await refused("part_measure_stale.json", "POST", url,
                          {"refs": [{"face_id": top["id"]}], "base_version": v + 5})
            await refused("part_measure_bad_face.json", "POST", url, {"refs": [{"face_id": "f999"}]})

            pid = plate["project_id"]
            write("project_feedback.json", await call("POST", f"/projects/{pid}/feedback", {
                "part_id": "mount_plate", "point": [7.0, 0.0, 2.5], "face_id": top["id"],
                "text": "make these holes M3 clearance"}))
            await refused("project_feedback_empty.json", "POST", f"/projects/{pid}/feedback", {
                "part_id": "mount_plate", "point": [7.0, 0.0, 2.5], "text": " "})
    finally:
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
