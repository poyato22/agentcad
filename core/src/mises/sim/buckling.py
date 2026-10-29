"""Linear buckling studies (CalculiX *BUCKLE): load multipliers at which the part buckles under
one load case, with the mode shapes. Linear (eigenvalue) buckling ignores imperfections and
plasticity, so real parts buckle earlier: treat a factor below ~3 as a warning."""
from __future__ import annotations

import asyncio

import numpy as np

from .deck import Setup, buckle_step, resolve_loads, resolve_supports, write_deck
from .fem import MAX_GLB_MODES, FemJob, mesh_level, result_glb, run_levels, save_fields, solve_deck
from .results import parse_dat_table, parse_frd_blocks
from .types import SimError

BUCKLE_HEADER = "B U C K L I N G   F A C T O R   O U T P U T"
KNOCKDOWN_WARN = 3.0


async def _solve(job: FemJob, s, size, d, label, lo, hi):
    mesh = await mesh_level(job, d, size, label, lo)
    setup = Setup()
    resolve_supports(mesh, job.metrics, s.load_case.supports, setup)
    resolve_loads(mesh, job.metrics, s.load_case.loads, setup)
    if not setup.cload and not setup.dload:
        raise SimError("bad_setup", "every load sits on the fixed face",
                       hint="load a different face than the one held fixed")
    frd, dat = await solve_deck(
        job, d, lambda p: write_deck(p, mesh, job.material, setup.cards,
                                     buckle_step(setup, s.n_modes), "Mises buckling study"),
        label, (lo + hi) / 2, nodes=len(mesh.nodes), elements=len(mesh.tets))
    rows = await asyncio.to_thread(parse_dat_table, dat, BUCKLE_HEADER, 2)
    if not rows:
        raise SimError("solve_failed", "CalculiX returned no buckling factors",
                       hint="the load must compress some part of the model")
    factors = [r[1] for r in rows]
    blocks = await asyncio.to_thread(parse_frd_blocks, frd)
    # *BUCKLE writes the static pre-load displacement first, then one block per mode
    modes = [b for name, b in blocks if name == "DISP"][-len(factors):]
    info = {"size_mm": round(size, 4), "nodes": len(mesh.nodes), "elements": len(mesh.tets),
            "order": mesh.order, "fixed_nodes": len(setup.held),
            "applied_n": setup.total_n}
    return factors[0], {"mesh": mesh, "factors": factors, "modes": modes, "info": info}


async def run_buckling(job: FemJob, s, *, sim_id: str, part_id: str) -> dict:
    lv = await run_levels(job, lambda size, d, label, lo, hi: _solve(job, s, size, d, label,
                                                                      lo, hi))
    fine, coarse = lv["fine"], lv["coarse"]
    job.progress("post", phase="post", pct=92.0, detail="post-processing")
    f = fine["factors"]
    applied = fine["info"]["applied_n"]
    warnings = []
    if f[0] < 1:
        warnings.append(f"buckles under this load: factor {f[0]:.2f} < 1")
    elif f[0] < KNOCKDOWN_WARN:
        warnings.append(f"buckling factor {f[0]:.2f} < {KNOCKDOWN_WARN:g}: linear buckling "
                        "over-predicts real (imperfect) parts; add margin")
    if lv["band"] is not None and lv["band"] > 0.1:
        warnings.append(f"mesh not converged: the first factor changed {lv['band']:.0%} "
                        "between the coarse and fine mesh")
    results = {"factors": f, "first_factor": f[0],
               "critical_load_n": f[0] * applied if applied else None,
               "applied_load_n": applied or None,
               "coarse_factors": coarse["factors"], "error_band": lv["band"],
               "error_band_pct": None if lv["band"] is None else round(100 * lv["band"], 1),
               "convergence": {"history": lv["history"], "converged": lv["converged"]}}
    await asyncio.to_thread(_outputs, job, fine, sim_id, part_id)
    return {"results": results, "warnings": warnings, "lv": lv,
            "mesh": {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                     "coarse": coarse["info"], "fine": fine["info"], "levels": lv["history"]}}


def _outputs(job: FemJob, fine: dict, sim_id: str, part_id: str) -> None:
    mesh = fine["mesh"]
    shapes = []
    for m in fine["modes"][:MAX_GLB_MODES]:
        peak = max((np.linalg.norm(v[:3]) for v in m.values()), default=0.0) or 1.0
        shapes.append({n: [x / peak for x in v[:3]] for n, v in m.items()})
    mag = {n: float(np.linalg.norm(v)) for n, v in (shapes[0] if shapes else {}).items()}
    save_fields(job.root / "fields.npz", mesh,
                {f"mode_{i + 1}": sh for i, sh in enumerate(shapes)},
                {f"mode_{i + 1}": "normalized" for i in range(len(shapes))})
    result_glb(job.root / "result.glb", mesh, mag, vmin=0.0, vmax=1.0, field_name="mode_1",
               units="normalized",
               extras={"sim_id": sim_id, "part_id": part_id, "type": "buckling",
                       "factors": fine["factors"]},
               channels=[{"name": f"mode_{i + 1}", "attribute": f"_MODE_{i + 1}",
                          "units": "normalized", "kind": "vector", "values": sh,
                          "factor": fine["factors"][i]} for i, sh in enumerate(shapes)])
