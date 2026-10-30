"""Static studies: one or more load cases (force, pressure, moment, gravity, bolt bearing,
remote force) with fixed / pinned / roller / symmetry supports, on a coarse and a fine mesh (the
error band) or a convergence loop. Linear elastic by default; with `nonlinear` settings an
incremental solve with large deflection, plasticity and / or contact (nonlinear.py)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from . import nonlinear as nlm
from .deck import Setup, resolve_loads, resolve_supports, static_step, write_deck
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


def _only_mesh(res: dict, mesh) -> dict:
    """Result blocks on the part's own nodes (drops a rigid support's slab nodes)."""
    return {k: {n: v for n, v in blk.items() if n in mesh.nodes} for k, blk in res.items()}


def _nl_extras(res: dict, mesh, sta: list[dict], t: float | None, path: list,
               model: "nlm.NlModel", errors: list[str]) -> dict:
    pe = res.get("PE", {})
    cp = {n: v[3] for n, v in res.get("CONTACT", {}).items() if len(v) > 3}
    n_pe = max(pe, key=lambda n: pe[n][0], default=None)
    n_cp = max(cp, key=cp.get, default=None)
    reached = nlm.load_reached(sta)
    return {"load_fraction": round(reached, 6), "converged": reached >= 1 - 1e-6,
            "fields_at_fraction": t, "increments": sum(r["converged"] for r in sta),
            "cutbacks": sum(not r["converged"] for r in sta),
            "max_plastic_strain": pe[n_pe][0] if n_pe is not None else None,
            "max_plastic_strain_at": list(mesh.nodes[n_pe]) if n_pe is not None else None,
            "max_contact_pressure_mpa": cp[n_cp] if n_cp is not None else None,
            "max_contact_pressure_at": list(mesh.nodes[n_cp]) if n_cp is not None else None,
            "load_path": [[round(a, 6), b] for a, b in path], "pairs": model.pairs,
            "solver_errors": errors[-3:]}


async def solve_cases(job: FemJob, size: float, d: Path, label: str, lo: float, hi: float,
                      cases: list, nonlinear=None) -> tuple[float, dict]:
    mesh = await mesh_level(job, d, size, label, lo)
    out = []
    span = (hi - lo) / max(1, len(cases))
    plastic = (nlm.plastic_cards(job.material, nonlinear.tangent_modulus_mpa)
               if nonlinear is not None and nonlinear.plasticity else None)
    for i, case in enumerate(cases):
        setup = Setup()
        resolve_supports(mesh, job.metrics, case.supports, setup)
        resolve_loads(mesh, job.metrics, case.loads, setup)
        if not setup.cload and not setup.dload:
            raise SimError("bad_setup", f"{case.name}: every load sits on the fixed face",
                           hint="load a different face than the one held fixed")
        model = (nlm.resolve_nonlinear(mesh, job.metrics, nonlinear, job.material, setup)
                 if nonlinear is not None else None)
        cd = _case_dir(d, i)
        if model is None:
            cards, step = setup.cards, static_step(setup)
        else:
            cards, step = setup.cards + model.cards, nlm.nonlinear_step(setup, nonlinear, model)
        frd, _ = await solve_deck(
            job, cd, lambda p, c=cards, st=step: write_deck(p, mesh, job.material, c, st,
                                                            "Mises static study", plastic),
            label, lo + span * (i + 0.5), allow_partial=model is not None,
            nodes=len(mesh.nodes), elements=len(mesh.tets), case=case.name)
        extras = None
        if model is None:
            res = await asyncio.to_thread(parse_frd, frd)
        else:
            sta = nlm.parse_sta(cd / "job.sta")
            reached = nlm.load_reached(sta)
            t, res, path = await asyncio.to_thread(nlm.parse_frd_increments, frd, reached)
            if not sta or reached <= 0 or not res.get("STRESS"):
                errs = nlm.ccx_errors(cd)
                raise SimError("solve_failed", f"{case.name}: the nonlinear solve found no "
                               "equilibrium in its first increment"
                               + (f" ({errs[-1]})" if errs else ""),
                               hint="check the supports hold the part (a contact alone may "
                                    "not), use more increments, or a lower contact penalty")
            res = _only_mesh(res, mesh)
            extras = _nl_extras(res, mesh, sta, t, path, model, nlm.ccx_errors(cd))
        pk = await asyncio.to_thread(lambda r=res, s=setup: peaks(mesh, r, exclude=s.supported))
        out.append({"case": case, "setup": setup, "peaks": pk, "frd": res, "nl": extras})
    worst = max(out, key=lambda c: c["peaks"]["max_von_mises_mpa"])
    info = {"size_mm": round(size, 4), "nodes": len(mesh.nodes), "elements": len(mesh.tets),
            "order": mesh.order, "fixed_nodes": len(worst["setup"].held),
            "loaded_nodes": len(worst["setup"].cload), "dropped_load_n": worst["setup"].dropped_n}
    return worst["peaks"]["max_von_mises_mpa"], {"mesh": mesh, "cases": out, "info": info}


async def run_static(job: FemJob, cases: list, *, env: dict | None, sim_id: str,
                     part_id: str | None, nonlinear=None, extras: dict | None = None) -> dict:
    lv = await run_levels(job, lambda size, d, label, lo, hi:
                          solve_cases(job, size, d, label, lo, hi, cases, nonlinear))
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
        if fc.get("nl") is not None:
            _nl_case(per_case[-1], fc["nl"], pre, warnings)
        sf = per_case[-1]["safety_factor"]
        if sf is not None and sf < 1 and fc.get("nl") is None:
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
    if nonlinear is not None:
        band, effects = nlm.model_error_band(nonlinear)
        results["nonlinear"] = {
            "effects": effects, "description": nlm.describe(nonlinear),
            "increments": nonlinear.increments,
            "tangent_modulus_mpa": (nlm.tangent_used(m, nonlinear.tangent_modulus_mpa)
                                    if nonlinear.plasticity else None),
            "tangent_source": ((m.tangent_source if nonlinear.tangent_modulus_mpa is None
                                else "study setting") if nonlinear.plasticity else None),
            "validation": nlm.validation_rows(effects)}
        results["model_error_band"] = band
        if nonlinear.large_deflection:
            results["deformed_scale"] = 1.0  # large deflection: the true shape
        for k in ("load_fraction", "converged", "max_plastic_strain",
                  "max_contact_pressure_mpa", "load_path"):
            results[k] = gov.get(k)
    results.update(extras or {})

    gi = next(i for i, c in enumerate(fine["cases"]) if c["case"].name == gov["name"])
    await asyncio.to_thread(_outputs, job, fine["mesh"], fine["cases"][gi], gov, sim_id, part_id)
    out = {"results": results, "warnings": warnings, "lv": lv,
           "mesh": {"mesher": "gmsh", "mesher_version": fine["mesh"].gmsh_version,
                    "coarse": coarse["info"], "fine": fine["info"],
                    "levels": lv["history"]},
           "color_range_mpa": [0.0, gov["max_von_mises_mpa"]]}
    if nonlinear is not None:
        out["fidelity_class"] = "fem_nonlinear"
    return out


def _nl_case(c: dict, nl: dict, pre: str, warnings: list[str]) -> None:
    """A nonlinear case's extra numbers and warnings; its safety factor also caps at the load
    fraction reached (a part that collapses at 70% of the load has 0.7 on the load)."""
    c.update({k: nl[k] for k in ("load_fraction", "converged", "max_plastic_strain",
                                 "max_plastic_strain_at", "max_contact_pressure_mpa",
                                 "max_contact_pressure_at", "increments", "cutbacks",
                                 "load_path", "pairs")})
    frac = nl["load_fraction"]
    if not nl["converged"]:
        c["safety_factor"] = min(c["safety_factor"] or frac, frac)
        c["safety_factor_incl_fixture"] = min(c["safety_factor_incl_fixture"] or frac, frac)
        why = f" ({nl['solver_errors'][-1]})" if nl.get("solver_errors") else ""
        warnings.append(f"{pre}the solve stopped at {frac:.0%} of the load{why}: past that the "
                        "part found no equilibrium (plastic collapse, buckling, a contact "
                        "letting go) or the solver gave up; it cannot be shown to carry the "
                        "full load. Results are at the last stored converged increment")
    pe = nl.get("max_plastic_strain")
    if pe:
        warnings.append(f"{pre}yields: equivalent plastic strain up to {100 * pe:.2g}% (a "
                        "printed part may crack well before large plastic strain)")
        sf = c.get("safety_factor")
        if sf is not None and sf >= 1:  # plasticity caps the stress near the allowable
            c["safety_factor"] = 0.999


def _nl_channels(frd: dict) -> tuple[dict, dict]:
    """(channels, units) a nonlinear result adds: equivalent plastic strain and contact
    pressure (CalculiX PE and CONTACT CPRESS blocks), when the study had them."""
    ch, units = {}, {}
    if frd.get("PE"):
        ch["plastic_strain"] = {n: v[0] for n, v in frd["PE"].items()}
        units["plastic_strain"] = ""
    if frd.get("CONTACT"):
        ch["contact_pressure"] = {n: max(0.0, v[3]) for n, v in frd["CONTACT"].items()
                                  if len(v) > 3}
        units["contact_pressure"] = "MPa"
    return ch, units


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
    nl_ch, nl_units = _nl_channels(frd)
    channels.update(nl_ch)
    units.update(nl_units)
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
                          "kind": "scalar", "values": sf}])
