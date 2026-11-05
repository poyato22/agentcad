"""Write real server fixtures of a user's build for the app's G2 / G4 tests (Plan 12).

Points a Runtime at a COPY of a Mises home (never the live one: the server may migrate its
databases) and records what the real routes return for one build: its assembly (solved
transforms), its parts (looks, materials), its saved animations (with the `follow` values of the
coupled and loop joints) and its interference list.

    uv run python scripts/dump_ornicopter_fixtures.py --home /tmp/copy-of-home \
        --project 000b9ce75f54 --out ../app/test/fixtures/ornicopter

assembly.json, parts.json, animations.json, interference.json.
"""
import argparse
import json
import os
from pathlib import Path


def dump(home: Path, pid: str, out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    os.environ["MISES_HOME"] = str(home)
    rt = runtime_mod.Runtime(home)
    out.mkdir(parents=True, exist_ok=True)
    h = {"Authorization": "Bearer tok"}

    def write(name: str, data) -> None:
        (out / name).write_text(json.dumps(data, indent=1) + "\n")

    with TestClient(create_app(rt, "tok")) as c:
        base = f"/projects/{pid}/assembly"
        for name, path in (("assembly.json", base), ("parts.json", f"/parts?project_id={pid}"),
                           ("animations.json", f"{base}/animations"),
                           ("interference.json", f"{base}/interference")):
            r = c.get(path, headers=h)
            r.raise_for_status()
            data = r.json()
            if name == "parts.json":        # the face / edge lists are megabytes: the box stays
                for p in data:
                    m = p.get("metrics") or {}
                    p["metrics"] = {k: m[k] for k in ("bbox", "volume_mm3", "mass_g") if k in m}
            write(name, data)
    print(f"wrote fixtures to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.home.resolve() == (Path.home() / "Library" / "Application Support" / "Mises").resolve():
        raise SystemExit("point --home at a copy, never the live AgentCAD home")
    dump(a.home, a.project, a.out)
