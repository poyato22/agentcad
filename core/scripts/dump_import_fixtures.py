"""Write real server fixtures for the app's drawing import (Plan 7 U5 contract tests).

Builds a throwaway Runtime (temp MISES_HOME) and runs the real routes on the core tests' fixture
drawings (tests/fixtures/drawings). The DXF path needs no model; the refused PNG uses a fake
provider that cannot see images. Nothing touches the network or the user's real home:

    uv run python scripts/dump_import_fixtures.py --out tests/fixtures/ui

drawing_import_upload.json (POST /imports/drawing, a DXF), drawing_import_done.json (its
GET /imports/{id} once done), drawing_import_refused.json (POST of a PNG with no vision model:
the 400 body) and drawing_import_original.svg (GET /imports/{id}/original).
"""
import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx

DRAWINGS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "drawings"


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-u5-"))
    os.environ["MISES_HOME"] = str(home)
    blind = FakeProvider([])                 # name "fake": not a vision provider
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (blind, "fake-model"))
    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    try:
        transport = httpx.ASGITransport(app=create_app(rt, token))
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=300) as c:
            def write(name: str, data) -> None:
                (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

            out.mkdir(parents=True, exist_ok=True)
            r = await c.post("/imports/drawing", headers=auth, files={
                "file": ("Mounting Plate.dxf", (DRAWINGS / "plate_holes.dxf").read_bytes())})
            r.raise_for_status()
            up = r.json()
            write("drawing_import_upload.json", up)
            for _ in range(1200):
                done = (await c.get(f"/imports/{up['import_id']}", headers=auth)).json()
                if done["status"] in ("done", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert done["status"] == "done", done
            write("drawing_import_done.json", done)
            orig = await c.get(f"/imports/{up['import_id']}/original", headers=auth)
            orig.raise_for_status()
            (out / "drawing_import_original.svg").write_text(orig.text)
            r = await c.post("/imports/drawing", headers=auth, files={
                "file": ("plate.png", (DRAWINGS / "plate_drawing.png").read_bytes())})
            assert r.status_code == 400, r.text
            write("drawing_import_refused.json", r.json())
    finally:
        await rt.aclose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/ui"))
    asyncio.run(dump(ap.parse_args().out))


if __name__ == "__main__":
    main()
