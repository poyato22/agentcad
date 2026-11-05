"""Steady-state thermal studies (CalculiX *HEAT TRANSFER): the temperature field from fixed
temperatures, heat input (total watts or a flux) and convection film coefficients. Conduction
only inside the part (no radiation); checks the peak against the material's HDT."""
from __future__ import annotations

import asyncio

from .deck import thermal_step, write_deck
from .fem import mesh_note, FemJob, mesh_level, result_glb, run_levels, save_fields, solve_deck
from .results import parse_frd


async def _solve(job: FemJob, s, size, d, label, lo, hi):
    mesh = await mesh_level(job, d, size, label, lo)
    cards, step, held = thermal_step(mesh, job.metrics, s)
    frd, _ = await solve_deck(
        job, d, lambda p: write_deck(p, mesh, job.material, cards, step, "AgentCAD thermal study"),
        label, (lo + hi) / 2, nodes=len(mesh.nodes), elements=len(mesh.tets))
    res = await asyncio.to_thread(parse_frd, frd)
    temps = {n: v[0] for n, v in res.get("NDTEMP", {}).items()}
    if not temps:
        from .types import SimError
        raise SimError("solve_failed", "CalculiX returned no temperatures")
    n_hot = max(temps, key=temps.get)
    n_cold = min(temps, key=temps.get)
    info = {"size_mm": round(mesh.size_mm or size, 4), **mesh_note(mesh),
            "nodes": len(mesh.nodes), "elements": len(mesh.tets),
            "order": mesh.order, "fixed_temp_nodes": len(held)}
    return temps[n_hot], {"mesh": mesh, "temps": temps, "info": info,
                          "max": temps[n_hot], "min": temps[n_cold],
                          "max_at": list(mesh.nodes[n_hot]), "min_at": list(mesh.nodes[n_cold])}


async def run_thermal(job: FemJob, s, *, sim_id: str, part_id: str) -> dict:
    lv = await run_levels(job, lambda size, d, label, lo, hi: _solve(job, s, size, d, label,
                                                                      lo, hi))
    fine, coarse = lv["fine"], lv["coarse"]
    job.progress("post", phase="post", pct=92.0, detail="post-processing")
    m = job.material
    warnings = []
    if fine["max"] >= m.hdt_c - 5:
        warnings.append(f"peak {fine['max']:.1f} C is at or above {m.name}'s heat deflection "
                        f"temperature ({m.hdt_c:g} C): the part may soften or creep")
    # error band on the temperature rise (the absolute level depends on the fixed values)
    ref = min(t.temp_c for t in s.temperatures) if s.temperatures else min(
        c.ambient_c for c in s.convection)
    rise_f, rise_c = fine["max"] - ref, coarse["max"] - ref
    band = abs(rise_f - rise_c) / abs(rise_f) if abs(rise_f) > 1e-9 else None
    if band is not None and band > 0.1:
        warnings.append(f"mesh not converged: the temperature rise changed {band:.0%} between "
                        "the coarse and fine mesh")
    results = {"max_temp_c": fine["max"], "max_temp_at": fine["max_at"],
               "min_temp_c": fine["min"], "min_temp_at": fine["min_at"],
               "coarse_max_temp_c": coarse["max"], "hdt_c": m.hdt_c,
               "error_band": band, "error_band_pct": None if band is None else round(100 * band, 1),
               "convergence": {"history": lv["history"], "converged": lv["converged"]}}
    await asyncio.to_thread(_outputs, job, fine, sim_id, part_id)
    return {"results": results, "warnings": warnings, "lv": lv,
            "mesh": {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                     "coarse": coarse["info"], "fine": fine["info"], "levels": lv["history"]},
            "color_range_c": [fine["min"], fine["max"]]}


def _outputs(job: FemJob, fine: dict, sim_id: str, part_id: str) -> None:
    mesh, temps = fine["mesh"], fine["temps"]
    save_fields(job.root / "fields.npz", mesh, {"temperature": temps}, {"temperature": "degC"})
    result_glb(job.root / "result.glb", mesh, temps, vmin=fine["min"], vmax=fine["max"],
               field_name="temperature", units="degC",
               extras={"sim_id": sim_id, "part_id": part_id, "type": "thermal"},
               channels=[{"name": "temperature", "attribute": "_TEMPERATURE", "units": "degC",
                          "kind": "scalar", "values": temps}])
