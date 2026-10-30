"""Write real-core fixtures of the Plan 6 S3 routes and records (material calibration,
anisotropy, fatigue) for the Mac app's Studies page (Task S4 fix round 1):

    uv run python scripts/dump_s3_fixtures.py --out tests/fixtures/ui

Needs CalculiX (ccx) and gmsh. Runs with a throwaway MISES_HOME (a temp dir) and no network.
Every file it writes starts with `s3_`:
- s3_types.json, s3_materials.json: GET /sims/types and /sims/materials;
- s3_static.json (+ _charts, .glb, s3_probe.json): a static study of the beam with an
  orthotropic PLA (print direction +x) and a fatigue cycle;
- s3_cal_fit.json (POST /sims/calibrations/fit), s3_cal_save.json (POST /sims/calibrations),
  s3_cal_list.json, s3_cal_invalid.json (422), s3_cal_delete.json, s3_materials_after.json;
- s3_static_calibrated.json: the same study with the calibrated material on printer "MK4".
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
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
STATIC = {"load_cases": [CASE],
          "material": {"name": "PLA", "anisotropy": {"print_direction": "+x"}},
          "fatigue": {"cycles": 1e6, "cycle": "zero_to_max"}}
TENSILE = [{"force_n": f, "width_mm": 10, "thickness_mm": 4} for f in (1300, 1180, 1240)]
TENSILE_Z = [{"force_n": 720, "width_mm": 10, "thickness_mm": 4, "orientation": "z"}]
BEND = [{"span_mm": 64, "width_mm": 10, "thickness_mm": 4, "load_n": 40, "deflection_mm": d}
        for d in (2.0, 2.1)]


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.sim.ccx import find_ccx

    if find_ccx() is None:
        raise SystemExit("CalculiX (ccx) not found: conda install -c conda-forge calculix")
    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    home = Path(tempfile.mkdtemp(prefix="mises-s3-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    tmp_home, user_home = str(home), str(Path.home())

    def clean(text: str) -> str:
        return text.replace(tmp_home, "/Users/fixture/Mises").replace(user_home, "/Users/fixture")

    def write(name: str, v) -> None:
        (out / name).write_text(clean(json.dumps(v, indent=2)) + "\n")

    transport = httpx.ASGITransport(app=create_app(rt, token))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=900) as c:
            async def call(method: str, path: str, body=None, ok=True, **kw):
                r = await c.request(method, path, headers=auth, json=body, **kw)
                if ok:
                    assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text}"
                return r

            async def study(settings: dict) -> str:
                r = await call("POST", "/sims", {"type": "static", "part_id": "beam",
                                                 "settings": settings})
                run = r.json()["run_id"]
                for _ in range(3000):
                    ls = (await call("GET", f"/sims?run_id={run}")).json()
                    if ls and ls[0]["status"] != "running":
                        assert ls[0]["status"] == "done", ls[0]
                        return ls[0]["sim_id"]
                    await asyncio.sleep(0.2)
                raise SystemExit("the static study never finished")

            rid = rt.create_external_run("tester", provider="x", model="y")
            o = await rt.gate.call(rid, "mirror_part_create", {"part_id": "beam", "script": BEAM},
                                   call_id="b-beam", workdir=rt.workdir(rid))
            assert o.ok, o.summary
            rt.finish_external_run(rid, "done")
            out.mkdir(parents=True, exist_ok=True)
            write("s3_types.json", (await call("GET", "/sims/types")).json())
            write("s3_materials.json", (await call("GET", "/sims/materials")).json())

            sid = await study(STATIC)
            rec = (await call("GET", f"/sims/{sid}")).json()
            write("s3_static.json", rec)
            write("s3_static_charts.json", (await call("GET", f"/sims/{sid}/charts")).json())
            (out / "s3_static.glb").write_bytes((await call("GET", f"/sims/{sid}/result.glb")).content)
            write("s3_probe.json", (await call("GET", f"/sims/{sid}/probe",
                                               params={"x": 50, "y": 5, "z": 5})).json())

            # a plain (isotropic) study the part load test compares with
            plain = await study({"load_cases": [CASE], "material": {"name": "PLA"}})
            body = {"material": "PLA", "printer": "MK4", "tensile": TENSILE + TENSILE_Z,
                    "bend": BEND, "part": [{"sim_id": plain, "point": [100, 5, 10],
                                            "load_n": 10, "deflection_mm": 1.9}]}
            write("s3_cal_fit.json", (await call("POST", "/sims/calibrations/fit", body)).json())
            bad = await call("POST", "/sims/calibrations/fit",
                             {"material": "PLA", "printer": "MK4", "tensile": [{"force_n": 5}]},
                             ok=False)
            assert bad.status_code in (400, 422), bad.text
            write("s3_cal_invalid.json", bad.json())
            saved = await call("POST", "/sims/calibrations", body)
            assert saved.status_code == 201, saved.text
            write("s3_cal_save.json", saved.json())
            write("s3_cal_list.json", (await call("GET", "/sims/calibrations")).json())
            write("s3_materials_after.json", (await call("GET", "/sims/materials")).json())
            cal_sid = await study({**STATIC, "material": {**STATIC["material"], "printer": "MK4"}})
            write("s3_static_calibrated.json", (await call("GET", f"/sims/{cal_sid}")).json())
            write("s3_cal_delete.json", (await call(
                "DELETE", f"/sims/calibrations/{saved.json()['id']}")).json())
    finally:
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    t0 = time.time()
    asyncio.run(dump(args.out))
    print(f"wrote S3 fixtures to {args.out} in {time.time() - t0:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
