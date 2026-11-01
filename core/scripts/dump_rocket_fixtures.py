"""Write real core fixtures for Plan 11 F6's rocket cards (the app's Simulations stream).

Throwaway Runtime (temp MISES_HOME); rocket_analyze and rocket_flight run through the real Gate
on the OpenRocket reference rocket "A" (tests/rocketry/reference.py) with the mass override,
so no part has to be built. Writes GET /runs/{id}/steps of that run:

  rocket_steps.json     the tool_call + result steps of rocket_analyze (F52C) and rocket_flight
                        (F52C + G40W against a 750 ft / 36-39 s target, tune_mass)

    uv run python scripts/dump_rocket_fixtures.py --out tests/fixtures/ui/p11
"""
import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

GEOMETRY = {"body_diameter_mm": 66, "nose": {"shape": "ogive", "length_mm": 200},
            "sections": [{"length_mm": 700}],
            "fins": [{"count": 3, "root_chord_mm": 120, "tip_chord_mm": 50, "span_mm": 65,
                      "sweep_mm": 60, "thickness_mm": 3.2, "te_from_aft_mm": 0}],
            "rail_buttons": 0}
MASS = {"dry_mass_g": 540, "cg_mm": 493.33}


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from mises.types import Caps, SafetyClass as C

    runtime_mod.keychain_get = lambda *a: None
    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-rocket-"))
    os.environ["MISES_HOME"] = str(home)
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (None, "none"))
    proj = rt.store.create_project("Rocket").id
    run = rt.store.create_run("engineer", "fake", "m", "fly the rocket")
    rt.store.update_run(run.id, project_id=proj)
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    calls = [("rocket_analyze", {"project_id": proj, "geometry": GEOMETRY, "motor": "F52C",
                                 **MASS}),
             ("rocket_flight", {"project_id": proj, "geometry": GEOMETRY,
                                "motors": ["F52C", "G40W"], "target_apogee_ft": 750,
                                "target_time_s": [36, 39], "tune_mass": True, **MASS})]
    for i, (name, args) in enumerate(calls):
        o = await rt.gate.call(run.id, name, args, call_id=f"rk{i}", workdir=home)
        assert o.ok and o.data.get("card"), (name, o.summary)
    steps = [s.model_dump(mode="json") for s in rt.store.steps(run.id)]
    out.mkdir(parents=True, exist_ok=True)
    (out / "rocket_steps.json").write_text(json.dumps(steps, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(steps)} steps to {out / 'rocket_steps.json'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("tests/fixtures/ui/p11"))
    asyncio.run(dump(p.parse_args().out))
