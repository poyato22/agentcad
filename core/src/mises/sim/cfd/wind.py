"""One wind study (all yaw angles) on a triangle mesh: lattice-Boltzmann when it fits the
time and memory budget, else the empirical shape-class estimate. Runs inside the CFD worker
process (see worker.py); pure numpy, no Mises state."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .results import (CD_TABLE, EMPIRICAL_BAND, classify, friction_cd, mesh_volume_mm3,
                      slice_png, surface_cp)
from .setup import (NU_MIN_BGK, NU_MIN_LES, U_LATTICE, estimate_seconds, fit_budget,
                    frontal_area_mm2, steps_needed, surface_area_mm2, voxelize, wind_frame, yawed)
from .solver import BYTES_PER_CELL, run_lbm

LBM_LABEL = "low fidelity"
EMPIRICAL_LABEL = "estimate"


def _coeffs(F_lat, A_lat):
    return F_lat / (0.5 * U_LATTICE ** 2 * A_lat)


def empirical(tris_w: np.ndarray, s: dict, a_ref_m2: float, q: float, reason: str) -> dict:
    lo, hi = tris_w.reshape(-1, 3).min(0), tris_w.reshape(-1, 3).max(0)
    shape = s["shape_class"] if s["shape_class"] != "auto" else classify(
        hi - lo, mesh_volume_mm3(tris_w))
    cd = CD_TABLE[shape]
    return {"method": "empirical", "fidelity": EMPIRICAL_LABEL, "shape_class": shape,
            "reason": reason, "cd": cd, "cl": 0.0, "cs": 0.0, "drag_n": cd * q * a_ref_m2,
            "lift_n": 0.0, "side_n": 0.0, "error_band": EMPIRICAL_BAND,
            "moment_nm": [0.0, 0.0, 0.0], "converged": None, "history": []}


def solve_one(tris_part: np.ndarray, s: dict, direction, up, a_ref_m2: float | None,
              time_budget_s: float, progress=None, should_stop=None, keep_fields: bool = False
              ) -> dict:
    R = wind_frame(direction, up)
    tris_w = tris_part @ R.T
    U, rho_air, mu = s["speed_m_s"], s["air_density"], s["air_viscosity"]
    q = 0.5 * rho_air * U * U
    frontal = frontal_area_mm2(tris_w) * 1e-6
    a_ref = a_ref_m2 or frontal
    lo, hi = tris_w.reshape(-1, 3).min(0), tris_w.reshape(-1, 3).max(0)
    ext = hi - lo
    d_ref_m = max(ext[1], ext[2]) * 1e-3
    re = rho_air * U * max(ext[0] * 1e-3, d_ref_m) / mu
    base = {"frontal_area_m2": frontal, "ref_area_m2": a_ref, "reynolds": re,
            "dynamic_pressure_pa": q, "frame": R.tolist()}
    if s["solver"] == "empirical":
        return {**base, **empirical(tris_w, s, a_ref, q, "empirical solver chosen")}
    grid = fit_budget(lo, hi, s["refinement"], s["domain_multiplier"], s["memory_mb"])
    if grid is None:
        if s["solver"] == "lbm":
            raise ValueError(f"the lattice does not fit {s['memory_mb']} MB")
        return {**base, **empirical(tris_w, s, a_ref, q,
                                    f"the lattice does not fit {s['memory_mb']} MB")}
    want = min(s["iterations"], steps_needed(grid))
    est = estimate_seconds(grid, want)
    if est > time_budget_s and estimate_seconds(grid, want // 2) > time_budget_s:
        if s["solver"] == "lbm":
            raise ValueError(f"the lattice needs ~{est:.0f} s, over the {time_budget_s:.0f} s "
                             "budget")
        return {**base, **empirical(tris_w, s, a_ref, q,
                                    f"the lattice needs ~{est:.0f} s, over the "
                                    f"{time_budget_s:.0f} s budget")}
    solid = voxelize(tris_w, grid)
    if not solid.any():
        raise ValueError("the body is smaller than one lattice cell")
    les = s["turbulence"] == "smagorinsky"
    n = grid.cells_across
    nu = max(U_LATTICE * n / re, NU_MIN_LES if les else NU_MIN_BGK)
    re_eff = U_LATTICE * n / nu
    ref = np.argwhere(solid).mean(0)
    res = run_lbm(solid, u0=U_LATTICE, nu=nu, steps=want, smagorinsky=0.16 if les else 0.0,
                  time_budget_s=time_budget_s, moment_ref=ref, min_steps=int(want * 0.6),
                  progress=progress, should_stop=should_stop)
    h_m = grid.h_mm * 1e-3
    a_lat = a_ref / (h_m * h_m)
    cp_force = _coeffs(res.force_pressure, a_lat)
    tot = _coeffs(res.force_total, a_lat)
    wet = surface_area_mm2(tris_part) * 1e-6
    cd_f = friction_cd(re, wet, a_ref)
    cd = float(cp_force[0] + cd_f)
    cm = res.moment_pressure / (0.5 * U_LATTICE ** 2 * a_lat * n)
    moment_w = cm * q * a_ref * d_ref_m                            # N m, wind frame
    hist = [{"step": h["step"], "cd": float(_coeffs(h["px"], a_lat) + cd_f),
             "cd_lattice_total": float(_coeffs(h["fx"], a_lat)),
             "cl": float(_coeffs(h["pz"], a_lat))} for h in res.history]
    tail = [h["cd"] for h in hist[len(hist) // 2:]] or [cd]
    out = {**base, "method": "lbm", "fidelity": LBM_LABEL,
           "cd": cd, "cd_pressure": float(cp_force[0]), "cd_friction_estimate": cd_f,
           "cd_lattice_total": float(tot[0]), "cl": float(cp_force[2]), "cs": float(cp_force[1]),
           "drag_n": cd * q * a_ref, "lift_n": float(cp_force[2]) * q * a_ref,
           "side_n": float(cp_force[1]) * q * a_ref,
           "moment_nm": (R.T @ moment_w).tolist(),
           # the unsteady wake: the spread of Cd over the averaging window
           "error_band": float(np.std(tail) / abs(cd)) if cd else None,
           "converged": res.converged, "stopped": res.stopped, "steps": res.steps,
           "seconds": res.seconds, "history": hist,
           "lattice": {"shape": list(grid.shape), "cells": grid.cells, "cell_mm": grid.h_mm,
                       "cells_across": n, "u_lattice": U_LATTICE, "nu_lattice": nu,
                       "tau": 3 * nu + 0.5, "reynolds_effective": re_eff,
                       "memory_mb": grid.cells * BYTES_PER_CELL / 1e6,
                       "turbulence": "Smagorinsky LES (Cs 0.16)" if les else "none (BGK)",
                       "boundaries": "inlet equilibrium, zero-gradient outlet, periodic sides",
                       "blockage_pct": 100 * float(solid.any(0).sum()) / (grid.shape[1]
                                                                           * grid.shape[2])}}
    if keep_fields:
        p = res.rho_mean / 3.0
        p_inf = float(p[1][~solid[1]].mean())
        cp = (p - p_inf) / (0.5 * U_LATTICE ** 2)
        vel = res.u_mean / U_LATTICE * U
        vel[:, solid] = 0.0
        cp[solid] = 0.0
        out["_fields"] = {"grid": grid, "solid": solid, "cp": cp.astype(np.float32),
                          "velocity": vel.astype(np.float32), "R": R, "tris_w": tris_w}
    return out


def run_study(tris_part: np.ndarray, s: dict, out_dir: Path, progress=None,
              should_stop=None) -> dict:
    """All angles (yaw 0 first, the main result with fields), within s['timeout_s']."""
    t0 = time.monotonic()
    budget = s["timeout_s"] * 0.85
    d0, up = np.array(s["direction"], float), np.array(s["up"], float)
    angles = [0.0] + [a for a in s.get("yaw_deg", []) if abs(a) > 1e-9]
    per = budget / len(angles)
    a_ref = s.get("ref_area_m2")
    main = None
    sweep = []
    for k, ang in enumerate(angles):
        left = budget - (time.monotonic() - t0)
        if left <= 1:
            sweep.append({"yaw_deg": ang, "skipped": "time budget used up"})
            continue

        def prog(step, steps, h, _k=k):
            if progress is not None:
                progress(_k, len(angles), step, steps)

        d = yawed(d0, up, ang)
        r = solve_one(tris_part, s, d, up, a_ref if main is None else main["ref_area_m2"],
                      min(left, per if k else max(per, left / len(angles))), prog,
                      should_stop, keep_fields=main is None)
        if main is None:
            main = r
            a_ref = r["ref_area_m2"]
        sweep.append({"yaw_deg": ang, "cd": r["cd"], "cl": r["cl"], "cs": r["cs"],
                      "drag_n": r["drag_n"], "lift_n": r["lift_n"], "side_n": r["side_n"],
                      "method": r["method"]})
    fields = main.pop("_fields", None)
    main["yaw_sweep"] = sweep if len(angles) > 1 else []
    if fields is not None:
        _write_outputs(out_dir, tris_part, fields, s)
        main["outputs"] = {"glb": "result.glb", "fields": "fields.npz",
                           "slices": ["slice_xz.png", "slice_xy.png"]}
        main["cp_range"] = [float(np.min(fields["cp"])), float(np.max(fields["cp"]))]
    else:
        _write_surface_only(out_dir, tris_part, main)
        main["outputs"] = {"glb": "result.glb"}
    main["elapsed_s"] = time.monotonic() - t0
    return main


def _dedupe(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v, inv = np.unique(np.round(tris.reshape(-1, 3), 6), axis=0, return_inverse=True)
    return v, inv.reshape(-1)


def _write_outputs(out_dir: Path, tris_part, F: dict, s: dict) -> None:
    from ..fields import save_grid
    from ..results import write_glb

    grid, solid, cp, vel, R = F["grid"], F["solid"], F["cp"], F["velocity"], F["R"]
    verts, idx = _dedupe(tris_part)
    cpv = surface_cp(verts @ R.T, grid.origin, grid.h_mm, solid, cp)
    lo, hi = float(np.percentile(cpv, 2)), float(np.percentile(cpv, 98))
    lo, hi = min(lo, -0.5), max(hi, 1.0)
    write_glb(out_dir / "result.glb", verts.ravel().tolist(), idx.tolist(), color=cpv.tolist(),
              vmin=lo, vmax=hi, field="cp", units="",
              extras={"wind": {"speed_m_s": s["speed_m_s"], "direction": list(s["direction"])}},
              channels=[{"name": "cp", "attribute": "_CP", "units": "", "kind": "scalar",
                         "min": lo, "max": hi, "values": cpv.tolist()}])
    speed = np.sqrt((vel * vel).sum(0))
    save_grid(out_dir / "fields.npz", grid.origin, grid.h_mm,
              {"velocity": vel, "speed": speed, "cp": cp},
              {"velocity": "m/s", "speed": "m/s", "cp": ""}, solid=solid, rot=R)
    cy = int(np.clip(round((0.5 * (grid.body_lo[1] + grid.body_hi[1]) - grid.origin[1])
                           / grid.h_mm), 0, grid.shape[1] - 1))
    cz = int(np.clip(round((0.5 * (grid.body_lo[2] + grid.body_hi[2]) - grid.origin[2])
                           / grid.h_mm), 0, grid.shape[2] - 1))
    vmax = 1.5 * s["speed_m_s"]
    slice_png(out_dir / "slice_xz.png", speed[:, cy, :], solid[:, cy, :], vmax)
    slice_png(out_dir / "slice_xy.png", speed[:, :, cz], solid[:, :, cz], vmax)


def _write_surface_only(out_dir: Path, tris_part, main: dict) -> None:
    from ..results import write_glb

    verts, idx = _dedupe(tris_part)
    write_glb(out_dir / "result.glb", verts.ravel().tolist(), idx.tolist(),
              color=[0.0] * len(verts), vmin=0.0, vmax=1.0, field="none", units="",
              extras={"note": "empirical estimate: no surface pressure"})
