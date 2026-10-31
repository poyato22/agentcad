"""D16 proof: build the humanoid archetype end to end (script only, no model) and render it.

    cd core && uv run python evals/design/humanoid_proof.py OUT [--kind humanoid|quadruped|car_body]

Builds `ms.humanoid()` (or another archetype) in-process with the same recipe code the build
worker pre-imports, checks what makes it design-grade, and writes to OUT:
- <kind>_hero.png, _front.png, _side.png, _rear34.png (shaded, CMF roles: light shells, dark
  joints, glossy face), <kind>_hero_zebra.png (reflection stripes), <kind>_massing.png (the
  concept-stage massing in silhouette), and <kind>_sheet.png (all of them);
- <kind>_proof.json: parts and roles, proportions, and the checks: no part reads as a straight
  bar, every actuator envelope sits inside its cowl, every shell passes trust() (precise and
  mesh volumes agree), at most 3 CMF roles;
- one STL per part under <kind>_stl/ (so the product renderer or a viewer can load them).
The renders use the product renderer's styles when it has them (G-R), else preview.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import preview  # noqa: E402


def _overlap(a, b) -> float:
    both = a & b
    return 0.0 if both is None or not both.solids() else float(both.volume)


def checks(arch) -> dict:
    from mises.cad import mises_shapes as ms
    from mises.design_intensity import bar_like_solid

    bars = [n for n, p in arch.parts.items() if any(bar_like_solid(s) for s in p.solids())]
    clashes = {jn: round(_overlap(env, arch.parts[f"{jn}_cowl"]), 2)
               for jn, env in arch.envelopes.items() if f"{jn}_cowl" in arch.parts}
    untrusted = []
    for n, p in arch.parts.items():
        try:
            ms.trust(p, n)
        except ValueError as e:
            untrusted.append(str(e))
    roles = sorted(set(arch.roles.values()))
    return {"bars": bars, "actuator_cowl_clash_mm3": clashes, "untrusted": untrusted,
            "cmf_roles": roles,
            "pass": not bars and not untrusted and all(v < 1.0 for v in clashes.values())
            and len(roles) <= 3}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--kind", default="humanoid", choices=["humanoid", "quadruped", "car_body"])
    ap.add_argument("--size", type=int, default=1024)
    a = ap.parse_args(argv)
    import build123d as bd

    from mises.cad import mises_shapes as ms

    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    arch = getattr(ms, a.kind)()
    massing = getattr(ms, a.kind)(shell=False)
    built_s = round(time.monotonic() - t0, 1)
    items = [preview.Item(n, s, arch.roles[n]) for n, s in arch.parts.items()]
    stl = out / f"{a.kind}_stl"
    stl.mkdir(exist_ok=True)
    for n, s in arch.parts.items():
        bd.export_stl(s, str(stl / f"{n}.stl"), tolerance=0.2, angular_tolerance=0.2)
    upright = a.kind == "humanoid"
    hero = "hero" if upright else "hero_x"
    aspect = 0.75 if upright else 1.3
    shots = [("hero", hero, "shaded"), ("front", "front", "shaded"),
             ("side", "side" if upright else "front", "shaded"), ("rear34", "rear34", "shaded"),
             ("hero_zebra", hero, "zebra")]
    sheet = []
    for name, view, style in shots:
        png = preview.render_preview(items, view, a.size, style, aspect=aspect)
        (out / f"{a.kind}_{name}.png").write_bytes(png)
        sheet.append((f"{a.kind} {name}", png))
    m_items = [preview.Item(n, s, "joint") for n, s in massing.parts.items()]
    png = preview.render_preview(m_items, "front" if upright else "front", a.size, "silhouette",
                                 aspect=aspect, lines=False)
    (out / f"{a.kind}_massing.png").write_bytes(png)
    sheet.append((f"{a.kind} massing silhouette", png))
    (out / f"{a.kind}_sheet.png").write_bytes(preview.contact_sheet(sheet, cols=3, cell=480))
    report = {"kind": a.kind, "parts": {n: arch.roles[n] for n in arch.parts},
              "units": arch.units, "hardpoints": {k: [round(float(c), 1) for c in v]
                                                  for k, v in arch.hardpoints.items()},
              "build_seconds": built_s, "renderer": ("product" if preview.product_renderer_styles()
                                                    else "evals/design/preview.py"),
              "checks": checks(arch)}
    (out / f"{a.kind}_proof.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["checks"], indent=1))
    print(f"wrote {out}")
    return 0 if report["checks"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
