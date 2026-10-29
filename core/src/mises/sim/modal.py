"""Modal studies (CalculiX *FREQUENCY): the first N natural frequencies and mode shapes, and the
modes inside an excitation band (e.g. motor rpm / 60 x blade count for a drone)."""
from __future__ import annotations

import asyncio

import numpy as np

from .deck import Setup, frequency_step, resolve_supports, write_deck
from .fem import MAX_GLB_MODES, FemJob, mesh_level, result_glb, run_levels, save_fields, solve_deck
from .results import parse_dat_table, parse_frd_blocks
from .types import SimError

EIGEN_HEADER = "E I G E N V A L U E   O U T P U T"
RIGID_HZ = 1.0   # below this a mode of a free-free part is a rigid-body mode


def excitation_band(s) -> tuple[float, float] | None:
    if s.band_hz is not None:
        return float(s.band_hz[0]), float(s.band_hz[1])
    if s.excitation is not None:
        f = s.excitation.rpm / 60.0 * s.excitation.blades
        m = s.excitation.margin_pct / 100.0
        return f * (1 - m), f * (1 + m)
    return None


def _first_elastic(freqs: list[float]) -> float:
    return next((f for f in freqs if f > RIGID_HZ), freqs[-1] if freqs else 0.0)


async def _solve(job: FemJob, s, size, d, label, lo, hi):
    mesh = await mesh_level(job, d, size, label, lo)
    setup = None
    if s.supports:
        setup = Setup()
        resolve_supports(mesh, job.metrics, s.supports, setup)
    frd, dat = await solve_deck(
        job, d, lambda p: write_deck(p, mesh, job.material, setup.cards if setup else [],
                                     frequency_step(setup, s.n_modes), "Mises modal study"),
        label, (lo + hi) / 2, nodes=len(mesh.nodes), elements=len(mesh.tets))
    rows = await asyncio.to_thread(parse_dat_table, dat, EIGEN_HEADER, 5)
    if not rows:
        raise SimError("solve_failed", "CalculiX returned no eigenvalues",
                       hint="check that the part is one solid")
    freqs = [r[3] for r in rows]
    blocks = await asyncio.to_thread(parse_frd_blocks, frd)
    modes = [b for name, b in blocks if name == "DISP"][-len(freqs):]
    info = {"size_mm": round(size, 4), "nodes": len(mesh.nodes), "elements": len(mesh.tets),
            "order": mesh.order, "fixed_nodes": len(setup.held) if setup else 0}
    return _first_elastic(freqs), {"mesh": mesh, "freqs": freqs, "modes": modes, "info": info}


async def run_modal(job: FemJob, s, *, sim_id: str, part_id: str) -> dict:
    lv = await run_levels(job, lambda size, d, label, lo, hi: _solve(job, s, size, d, label,
                                                                      lo, hi))
    fine, coarse = lv["fine"], lv["coarse"]
    job.progress("post", phase="post", pct=92.0, detail="post-processing")
    band = excitation_band(s)
    freqs = fine["freqs"]
    modes = []
    for i, f in enumerate(freqs, 1):
        cf = coarse["freqs"][i - 1] if i - 1 < len(coarse["freqs"]) else None
        modes.append({"mode": i, "freq_hz": f, "coarse_freq_hz": cf,
                      "rigid": not s.supports and f <= RIGID_HZ,
                      "in_band": band is not None and band[0] <= f <= band[1]})
    warnings = []
    hits = [m for m in modes if m["in_band"]]
    if hits:
        warnings.append("modes inside the excitation band "
                        f"{band[0]:.0f}-{band[1]:.0f} Hz: "
                        + ", ".join(f"mode {m['mode']} at {m['freq_hz']:.0f} Hz" for m in hits)
                        + "; resonance likely: stiffen or move the mode")
    if not s.supports:
        warnings.append("free-free: no supports, so the first six modes are rigid-body modes "
                        "(~0 Hz) and are left out of first_freq_hz")
    if lv["band"] is not None and lv["band"] > 0.1:
        warnings.append(f"mesh not converged: the first frequency changed {lv['band']:.0%} "
                        "between the coarse and fine mesh")
    first = _first_elastic(freqs)
    results = {"frequencies_hz": freqs, "modes": modes, "first_freq_hz": first,
               "band_hz": list(band) if band else None, "modes_in_band": len(hits),
               "error_band": lv["band"],
               "error_band_pct": None if lv["band"] is None else round(100 * lv["band"], 1),
               "convergence": {"history": lv["history"], "converged": lv["converged"]}}
    await asyncio.to_thread(_outputs, job, fine, sim_id, part_id)
    return {"results": results, "warnings": warnings, "lv": lv,
            "mesh": {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                     "coarse": coarse["info"], "fine": fine["info"], "levels": lv["history"]}}


def _normalized(mode: dict) -> dict:
    mags = [np.linalg.norm(v[:3]) for v in mode.values()]
    peak = max(mags, default=0.0) or 1.0
    return {n: [x / peak for x in v[:3]] for n, v in mode.items()}


def _outputs(job: FemJob, fine: dict, sim_id: str, part_id: str) -> None:
    mesh, modes = fine["mesh"], fine["modes"]
    shapes = [_normalized(m) for m in modes]
    first = next((i for i, f in enumerate(fine["freqs"]) if f > RIGID_HZ), 0)
    shown = shapes[first] if shapes else {}
    mag = {n: float(np.linalg.norm(v)) for n, v in shown.items()}
    save_fields(job.root / "fields.npz", mesh,
                {f"mode_{i + 1}": sh for i, sh in enumerate(shapes[:MAX_GLB_MODES])},
                {f"mode_{i + 1}": "normalized" for i in range(min(len(shapes), MAX_GLB_MODES))})
    result_glb(job.root / "result.glb", mesh, mag, vmin=0.0, vmax=1.0,
               field_name=f"mode_{first + 1}", units="normalized",
               extras={"sim_id": sim_id, "part_id": part_id, "type": "modal",
                       "frequencies_hz": fine["freqs"]},
               channels=[{"name": f"mode_{i + 1}", "attribute": f"_MODE_{i + 1}",
                          "units": "normalized", "kind": "vector", "values": sh,
                          "freq_hz": fine["freqs"][i]}
                         for i, sh in enumerate(shapes[:MAX_GLB_MODES])])
