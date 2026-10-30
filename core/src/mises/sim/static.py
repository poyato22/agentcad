"""Static linear-elastic studies: one or more load cases (force, pressure, moment, gravity,
bolt bearing, remote force) with fixed / pinned / roller / symmetry supports, on a coarse and a
fine mesh (the error band) or a convergence loop."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from .aniso import layer_peaks
from .deck import Setup, resolve_loads, resolve_supports, static_step, write_deck
from .fatigue import case_fatigue, node_field, stress_arrays
from .fatigue import warnings_for as fatigue_warnings
from .fem import FemJob, mesh_level, result_glb, run_levels, save_fields, solve_deck
from .fields import principal_stresses, von_mises_np
from .results import parse_frd, peaks
from .types import SimError

DROPPED_LOAD_WARN = 0.01  # share of the load that may land on clamped nodes before we warn
SF_FIELD_CAP = 10.0       # the safety-factor channel is clipped here (huge in unloaded areas)


def _case_dir(d: Path, i: int) -> Path:
    if i == 0:
        return d  # the first case solves in the level dir itself
    p = d / f"case{i + 1}"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def solve_cases(job: FemJob, size: float, d: Path, label: str, lo: float, hi: float,
                      cases: list) -> tuple[float, dict]:
    mesh = await mesh_level(job, d, size, label, lo)
    out = []
    span = (hi - lo) / max(1, len(cases))
    for i, case in enumerate(cases):
        setup = Setup()
        resolve_supports(mesh, job.metrics, case.supports, setup)
        resolve_loads(mesh, job.metrics, case.loads, setup)
        if not setup.cload and not setup.dload:
            raise SimError("bad_setup", f"{case.name}: every load sits on the fixed face",
                           hint="load a different face than the one held fixed")
        cd = _case_dir(d, i)
        frd, _ = await solve_deck(
            job, cd, lambda p, s=setup: write_deck(p, mesh, job.material, s.cards,
                                                   static_step(s), "Mises static study"),
            label, lo + span * (i + 0.5), nodes=len(mesh.nodes), elements=len(mesh.tets),
            case=case.name)
        res = await asyncio.to_thread(parse_frd, frd)
        pk = await asyncio.to_thread(lambda r=res, s=setup: peaks(mesh, r, exclude=s.supported))
        pk.update(layer_peaks(mesh, res, job.material, setup.supported))  # S3: {} if isotropic
        out.append({"case": case, "setup": setup, "peaks": pk, "frd": res})
    worst = max(out, key=lambda c: c["peaks"]["max_von_mises_mpa"])
    info = {"size_mm": round(size, 4), "nodes": len(mesh.nodes), "elements": len(mesh.tets),
            "order": mesh.order, "fixed_nodes": len(worst["setup"].held),
            "loaded_nodes": len(worst["setup"].cload), "dropped_load_n": worst["setup"].dropped_n}
    return worst["peaks"]["max_von_mises_mpa"], {"mesh": mesh, "cases": out, "info": info}


async def run_static(job: FemJob, cases: list, *, env: dict | None, sim_id: str,
                     part_id: str) -> dict:
    lv = await run_levels(job, lambda size, d, label, lo, hi:
                          solve_cases(job, size, d, label, lo, hi, cases))
    fine, coarse = lv["fine"], lv["coarse"]
    job.progress("post", phase="post", pct=92.0, detail="post-processing")
    m = job.material
    warnings: list[str] = []
    single = len(lv["history"]) == 1          # coarse only: no second mesh, so no error band
    if single and not (job.convergence is not None and job.convergence.enabled):
        warnings.append("coarse mesh only (screening solve): no error band; confirm with a "
                        "fine run before relying on it")
    per_case = []
    for fc, cc in zip(fine["cases"], coarse["cases"]):
        pf, pc = fc["peaks"], cc["peaks"]
        peak = pf["max_von_mises_mpa"]
        fx = pf["max_von_mises_at_fixture_mpa"]
        worst = max(peak, fx or 0.0)
        band = (abs(peak - pc["max_von_mises_mpa"]) / peak) if peak > 0 and not single else None
        name = fc["case"].name
        per_case.append({"name": name, "max_von_mises_mpa": peak,
                         "max_von_mises_at": pf["max_von_mises_at"],
                         "max_von_mises_at_fixture_mpa": fx,
                         "max_displacement_mm": pf["max_displacement_mm"],
                         "max_displacement_at": pf["max_displacement_at"],
                         "safety_factor": m.allowable_mpa / peak if peak > 0 else None,
                         "safety_factor_incl_fixture": m.allowable_mpa / worst if worst else None,
                         "coarse_max_von_mises_mpa": pc["max_von_mises_mpa"],
                         "coarse_max_displacement_mm": pc["max_displacement_mm"],
                         "error_band": band,
                         "peak_excludes": "nodes on the fixed face" if pf["excludes_fixture"]
                         else None})
        pre = f"{name}: " if len(fine["cases"]) > 1 else ""
        # S3: tension across the layers (orthotropic) and fatigue, when asked for
        per_case[-1].update(layer_and_fatigue(job, fine["mesh"], fc, per_case[-1], warnings, pre))
        sf = per_case[-1]["safety_factor"]
        if sf is not None and sf < 1:
            warnings.append(f"{pre}predicted to yield: safety factor {sf:.2f} < 1")
        if band is not None and band > 0.1:
            warnings.append(f"{pre}mesh not converged: peak stress changed {band:.0%} between "
                            "the coarse and fine mesh; treat the peak as uncertain")
        st = fc["setup"]
        if st.total_n and st.dropped_n > DROPPED_LOAD_WARN * st.total_n:
            warnings.append(f"{pre}{st.dropped_n:.3g} N of the {st.total_n:g} N load sits on "
                            "nodes shared with the fixed face and goes straight into the "
                            "support; the result under-reports that share")
        if fx is not None and fx > 1.5 * peak:
            warnings.append(f"{pre}stress at the fixed face reaches {fx:.3g} MPa (an idealised "
                            "clamp exaggerates it); real fixtures spread load")
    if env and env["temperature_c"] >= m.hdt_c - 5:
        warnings.append(f"{env['name']}: {env['temperature_c']} C is at or above {m.name}'s "
                        f"heat deflection temperature ({m.hdt_c:g} C): the part may soften or "
                        "creep; this result does not cover it")
    if lv["converged"] is False:
        warnings.append("convergence study stopped at the last allowed mesh level before the "
                        "peak settled")
    gov = max(per_case, key=lambda c: c["max_von_mises_mpa"])
    results = {k: gov[k] for k in gov if k != "name"}
    results["allowable_mpa"] = m.allowable_mpa
    results["error_band_pct"] = None if gov["error_band"] is None else round(
        100 * gov["error_band"], 1)
    results["governing_case"] = gov["name"]
    results["cases"] = per_case
    results["sf_by_case"] = {c["name"]: c["safety_factor"] for c in per_case}
    fats = [c for c in per_case if c.get("fatigue")]
    if fats:  # S3: the fatigue estimate of the worst case
        w = min(fats, key=lambda c: (c["fatigue_safety_factor"]
                                     if c["fatigue_safety_factor"] is not None else float("inf")))
        results.update(fatigue={**w["fatigue"], "case": w["name"]},
                       fatigue_safety_factor=w["fatigue_safety_factor"],
                       cycles_to_failure=w["cycles_to_failure"])
    # QA B1-3: the per-case table the card renders. Case names are unique (StaticSettings) and
    # are what a spec's load_case_id matches, so the name is the case id.
    results["governing_case_id"] = gov["name"]
    results["per_case"] = [{"case_id": c["name"], "name": c["name"],
                            "max_vm_mpa": c["max_von_mises_mpa"],
                            "max_disp_mm": c["max_displacement_mm"], "sf": c["safety_factor"]}
                           for c in per_case]
    results["convergence"] = {"history": lv["history"], "converged": lv["converged"]}
    # the viewer's default deformed-shape scale: the peak displacement drawn at 5% of the part
    size = job.metrics["bbox"]["size"]
    diag = sum(float(s) ** 2 for s in size) ** 0.5
    peak_d = gov["max_displacement_mm"]
    results["deformed_scale"] = 0.05 * diag / peak_d if peak_d > 0 else 1.0

    gi = next(i for i, c in enumerate(fine["cases"]) if c["case"].name == gov["name"])
    await asyncio.to_thread(_outputs, job, fine["mesh"], fine["cases"][gi], gov, sim_id, part_id)
    return {"results": results, "warnings": warnings, "lv": lv,
            "mesh": {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                     "coarse": coarse["info"], "fine": fine["info"],
                     "levels": lv["history"]},
            "color_range_mpa": [0.0, gov["max_von_mises_mpa"]]}


def layer_and_fatigue(job: FemJob, mesh, fc: dict, row: dict, warnings: list[str],
                      pre: str) -> dict:
    """S3 additions to one case's row: with an orthotropic material the safety factor is the
    lower of von Mises / in-plane allowable and Z strength / peak tension across the layers;
    with fatigue settings, the hotspot's cycles to failure and fatigue safety factor."""
    m, pf, out = job.material, fc["peaks"], {}
    if m.orthotropic:
        lsf = pf.get("layer_safety_factor")
        sf = row["safety_factor"]
        out.update(safety_factor_von_mises=sf,
                   max_layer_tension_mpa=pf.get("max_layer_tension_mpa"),
                   max_layer_tension_at=pf.get("max_layer_tension_at"),
                   allowable_z_mpa=pf.get("allowable_z_mpa"), layer_safety_factor=lsf,
                   governing_failure="in-plane (von Mises)")
        if lsf is not None and (sf is None or lsf < sf):
            out.update(safety_factor=lsf, governing_failure="across layers (Z tension)")
    fat = job.extra.get("fatigue")
    if fat is not None:
        f = case_fatigue(mesh, fc["frd"], m, fat, fc["setup"].supported)
        if f is not None:
            out.update(fatigue=f, fatigue_safety_factor=f["safety_factor"],
                       cycles_to_failure=f["cycles_to_failure"])
            warnings += fatigue_warnings(f, pre)
    return out


def _outputs(job: FemJob, mesh, case: dict, gov: dict, sim_id: str, part_id: str) -> None:
    frd = case["frd"]
    S = frd.get("STRESS", {})
    D = frd.get("DISP", {})
    ids = [n for n in mesh.nodes if n in S]
    arr = np.array([S[n][:6] for n in ids]) if ids else np.zeros((0, 6))
    vm = dict(zip(ids, von_mises_np(arr))) if ids else {}
    pr = principal_stresses(arr) if ids else np.zeros((0, 3))
    allow = job.material.allowable_mpa
    sf = {n: min(SF_FIELD_CAP, allow / v) if v > 0 else SF_FIELD_CAP for n, v in vm.items()}
    disp = {n: D[n][:3] for n in D}
    mag = {n: float(np.linalg.norm(v)) for n, v in disp.items()}
    channels = {"von_mises": vm, "displacement": disp, "displacement_magnitude": mag,
                "s1": {n: pr[i, 0] for i, n in enumerate(ids)},
                "s3": {n: pr[i, 2] for i, n in enumerate(ids)},
                "safety_factor": sf}
    units = {"von_mises": "MPa", "displacement": "mm", "displacement_magnitude": "mm",
             "s1": "MPa", "s3": "MPa", "safety_factor": ""}
    extra = s3_channels(job, mesh, frd, sf)  # S3: layer tension, fatigue; sf takes the Z check
    channels.update({c["name"]: c["values"] for c in extra})
    units.update({c["name"]: c["units"] for c in extra})
    save_fields(job.root / "fields.npz", mesh, channels, units)
    peak = gov["max_von_mises_mpa"]
    result_glb(job.root / "result.glb", mesh, vm, vmin=0.0, vmax=peak, field_name="von_mises",
               units="MPa", extras={"sim_id": sim_id, "part_id": part_id, "type": "static",
                                    "case": gov["name"]},
               channels=[{"name": "von_mises", "attribute": "_VON_MISES", "units": "MPa",
                          "kind": "scalar", "values": vm},
                         {"name": "displacement", "attribute": "_DISPLACEMENT", "units": "mm",
                          "kind": "vector", "values": disp},
                         {"name": "s1", "attribute": "_S1", "units": "MPa", "kind": "scalar",
                          "values": channels["s1"]},
                         {"name": "s3", "attribute": "_S3", "units": "MPa", "kind": "scalar",
                          "values": channels["s3"]},
                         {"name": "safety_factor", "attribute": "_SAFETY_FACTOR", "units": "",
                          "kind": "scalar", "values": sf}, *extra])


def s3_channels(job: FemJob, mesh, frd: dict, sf: dict[int, float]) -> list[dict]:
    """Extra result channels: `layer_tension` (stress across the layers, orthotropic) and
    `fatigue_safety_factor` (with fatigue settings). The safety-factor field `sf` is lowered
    in place where tension across the layers governs."""
    m = job.material
    fat = job.extra.get("fatigue")
    if not m.orthotropic and fat is None:
        return []
    ids, arr, normal = stress_arrays(mesh, frd, m)
    out = []
    if normal is not None:
        out.append({"name": "layer_tension", "attribute": "_LAYER_TENSION", "units": "MPa",
                    "kind": "scalar", "values": {n: float(normal[i]) for i, n in enumerate(ids)}})
        for i, n in enumerate(ids):
            if normal[i] > 0:
                sf[n] = min(sf.get(n, SF_FIELD_CAP), m.allowable_z_mpa / normal[i])
    if fat is not None and ids:
        out.append({"name": "fatigue_safety_factor", "attribute": "_FATIGUE_SAFETY_FACTOR",
                    "units": "", "kind": "scalar",
                    "values": node_field(ids, arr, m, fat, layer_normal=normal)})
    return out
