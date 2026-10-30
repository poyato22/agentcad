"""Write real-core fixtures of the Plan 6 S1 (nonlinear / contact) and S2 (OpenFOAM) shapes for
the Mac app's Studies page (the int-678 merge: the app's fake core is checked against these):

    uv run python scripts/dump_p6_fixtures.py --out tests/fixtures/ui

Needs CalculiX (ccx) and gmsh. Runs with a throwaway MISES_HOME and no network. No container
is started and no image is pulled: the OpenFOAM study replays the recorded sphere case
(tests/fixtures/openfoam/sphere_re2e4) through the same fake runner the S2 tests use, and the
solver rows are built for each install state from the core's own row function. Files:
- s1_types.json: GET /sims/types (static has `nonlinear`, wind has solver "openfoam");
- s1_static_nonlinear.json (+ _charts, s1_probe.json): a cantilever with large deflection and
  plasticity (it yields);
- s1_static_contact.json (+ s1_contact_probe.json): a block pressed on a rigid floor (contact);
- s2_wind_openfoam.json: a wind study on OpenFOAM (recorded output), validation pending;
- s2_openfoam_rows.json: the /sims/solvers cfd OpenFOAM rows for {ok, image_missing,
  daemon_down, no_runtime};
- s3_cal_fit_rejected.json, s3_cal_save_rejected.json: S3's screen on a decimal-point slip
  (every tensile bar 10x): the strength tests are outliers, the fit is "rejected - implausible"
  (saved with 201 so the user sees why; never applied).
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

CORE = Path(__file__).resolve().parents[1]
BEAM = ('from build123d import *\nVARS = {"length": 100.0}\ndef build(v):\n'
        '    return Box(v["length"], 10, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n')
BLOCK = ('from build123d import *\nVARS = {}\ndef build(v):\n'
         '    return Box(20, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n')
BALL = ('from build123d import *\nVARS = {"r": 20.0}\ndef build(v):\n'
        '    return Sphere(v["r"])\n')
NL_STATIC = {"load_cases": [{"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
                             "loads": [{"kind": "force", "face": "x-max", "newtons": 120,
                                        "direction": "-z"}]}],
             "material": {"name": "PLA"}, "mesh": {"size_mm": 5},
             "nonlinear": {"large_deflection": True, "plasticity": True, "increments": 10,
                           "min_increment": 1e-4}}
CONTACT_STATIC = {"load_cases": [{"name": "press",
                                  "supports": [{"kind": "symmetry", "face": "x-min"},
                                               {"kind": "symmetry", "face": "y-min"}],
                                  "loads": [{"kind": "force", "face": "top", "newtons": 200,
                                             "direction": "-z"}]}],
                  "material": {"name": "PLA"}, "mesh": {"size_mm": 4},
                  "nonlinear": {"rigid_supports": [{"face": "bottom", "normal": "+z",
                                                    "friction": 0.3}], "increments": 4}}


async def dump(out: Path) -> None:
    import mises.platform
    import mises.runtime as runtime_mod
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.sim import cfd
    from mises.sim.ccx import find_ccx
    from mises.sim.cfd import openfoam

    if find_ccx() is None:
        raise SystemExit("CalculiX (ccx) not found: conda install -c conda-forge calculix")
    sys.path.insert(0, str(CORE))
    from tests.sim.test_openfoam import DOCKER, FakeRunner  # the S2 tests' recorded replay

    mises.platform.keychain_get = lambda *a: None
    runtime_mod.keychain_get = lambda *a: None
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(k, None)
    home = Path(tempfile.mkdtemp(prefix="mises-p6-fixtures-"))
    os.environ["MISES_HOME"] = str(home)
    # S2: OpenFOAM "available" and replaying the recorded sphere case; nothing is started
    openfoam.make_runner = lambda docker: FakeRunner("sphere_re2e4")
    cfd.openfoam_available = lambda: (DOCKER, None)
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
            async def call(method: str, path: str, body=None, **kw):
                r = await c.request(method, path, headers=auth, json=body, **kw)
                assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text}"
                return r

            async def study(typ: str, part: str, settings: dict) -> str:
                r = await call("POST", "/sims", {"type": typ, "part_id": part,
                                                 "settings": settings})
                run = r.json()["run_id"]
                for _ in range(4000):
                    ls = (await call("GET", f"/sims?run_id={run}")).json()
                    if ls and ls[0]["status"] != "running":
                        assert ls[0]["status"] == "done", ls[0]
                        return ls[0]["sim_id"]
                    await asyncio.sleep(0.2)
                raise SystemExit(f"the {typ} study never finished")

            rid = rt.create_external_run("tester", provider="x", model="y")
            for pid, script in (("beam", BEAM), ("block", BLOCK), ("ball", BALL)):
                o = await rt.gate.call(rid, "mirror_part_create", {"part_id": pid,
                                                                   "script": script},
                                       call_id=f"b-{pid}", workdir=rt.workdir(rid))
                assert o.ok, o.summary
            rt.finish_external_run(rid, "done")
            out.mkdir(parents=True, exist_ok=True)
            write("s1_types.json", (await call("GET", "/sims/types")).json())

            sid = await study("static", "beam", NL_STATIC)
            write("s1_static_nonlinear.json", (await call("GET", f"/sims/{sid}")).json())
            write("s1_static_nonlinear_charts.json",
                  (await call("GET", f"/sims/{sid}/charts")).json())
            write("s1_probe.json", (await call("GET", f"/sims/{sid}/probe",
                                               params={"x": 2, "y": 5, "z": 9.5})).json())

            sid = await study("static", "block", CONTACT_STATIC)
            write("s1_static_contact.json", (await call("GET", f"/sims/{sid}")).json())
            write("s1_contact_probe.json", (await call("GET", f"/sims/{sid}/probe",
                                                       params={"x": 10, "y": 10, "z": 0})).json())

            sid = await study("wind", "ball", {"solver": "openfoam", "speed_m_s": 3})
            write("s2_wind_openfoam.json", (await call("GET", f"/sims/{sid}")).json())

            typo = {"material": "PLA", "printer": "MK4",
                    "tensile": [{"force_n": f, "width_mm": 10, "thickness_mm": 4}
                                for f in (13000, 11800, 12400)],
                    "bend": [{"span_mm": 64, "width_mm": 10, "thickness_mm": 4, "load_n": 40,
                              "deflection_mm": d} for d in (2.0, 2.1)]}
            write("s3_cal_fit_rejected.json",
                  (await call("POST", "/sims/calibrations/fit", typo)).json())
            write("s3_cal_save_rejected.json",
                  (await call("POST", "/sims/calibrations", typo)).json())
    finally:
        await rt.aclose()
    rows = {"ok": cfd.openfoam_rows(DOCKER, "present"),
            "image_missing": cfd.openfoam_rows(DOCKER, "missing"),
            "daemon_down": cfd.openfoam_rows(DOCKER, "daemon_down"),
            "no_runtime": cfd.openfoam_rows(None, None)}
    write("s2_openfoam_rows.json", rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    t0 = time.time()
    asyncio.run(dump(args.out))
    print(f"wrote S1 / S2 fixtures to {args.out} in {time.time() - t0:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
