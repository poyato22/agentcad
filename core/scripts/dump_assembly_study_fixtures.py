"""Write real server fixtures for the Studies page's whole-assembly studies (Plan 13 H4 contract
tests): a throwaway Runtime (temp MISES_HOME) with the quadruped test fixture as a build, a walk
study (real MuJoCo) and a drop study run through the routes, then what they return:

    uv run python scripts/dump_assembly_study_fixtures.py --out tests/fixtures/ui/assembly_studies

assembly_study_kinds.json (GET /assembly_studies/kinds), assembly_study_start.json (POST),
assembly_studies.json (GET /assembly_studies), assembly_study_walk.json /
assembly_study_drop.json (GET /assembly_studies/{id}), assembly_study_jobs.json (GET /jobs
while the walk runs, when it is caught) and assembly_study_events.jsonl
(assembly_study_progress and sim_finished).
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from dyn import quad

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-asmstudy-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    sub = rt.events.subscribe()
    h = {"Authorization": "Bearer tok"}
    out.mkdir(parents=True, exist_ok=True)

    def write(name, data):
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    pid = rt.store.create_project("robot dog").id
    quad.write_parts(rt.home)
    quad.assembly(pid, rt.assembly_store)
    with TestClient(create_app(rt, "tok")) as c:
        def done(n):
            for _ in range(600):
                rows = c.get(f"/assembly_studies?project_id={pid}", headers=h).json()
                if len(rows) >= n and all(r["status"] != "running" for r in rows):
                    return rows
                time.sleep(0.1)
            raise SystemExit("timed out")
        write("assembly_study_kinds.json",
              c.get(f"/assembly_studies/kinds?project_id={pid}", headers=h).json())
        r = c.post("/assembly_studies", headers=h, json={
            "project_id": pid, "kind": "walk",
            "settings": {"cycles": 2, "rom_poses": 200, "min_travel_mm": 0}})
        r.raise_for_status()
        write("assembly_study_start.json", r.json())
        for _ in range(50):
            jobs = c.get("/jobs", headers=h).json()
            if jobs:
                write("assembly_study_jobs.json", jobs)
                break
            time.sleep(0.02)
        walk = done(1)[0]
        r = c.post("/assembly_studies", headers=h, json={
            "project_id": pid, "kind": "drop", "settings": {"drop_m": 1.0, "max_g": 8}})
        r.raise_for_status()
        rows = done(2)
        write("assembly_studies.json", rows)
        drop = next(x for x in rows if x["kind"] == "drop")
        write("assembly_study_walk.json",
              c.get(f"/assembly_studies/{walk['study_id']}", headers=h).json())
        write("assembly_study_drop.json",
              c.get(f"/assembly_studies/{drop['study_id']}", headers=h).json())
    with (out / "assembly_study_events.jsonl").open("w") as f:
        while not sub.queue.empty():
            e = sub.queue.get_nowait()
            if e.type in ("assembly_study_progress", "sim_finished"):
                f.write(json.dumps({"type": e.type, "run_id": e.run_id, "data": e.data},
                                   ensure_ascii=False) + "\n")
    print(f"wrote assembly study fixtures to {out} ({pid=})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    dump(ap.parse_args().out)
