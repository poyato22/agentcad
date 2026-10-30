"""High-cycle fatigue ESTIMATES for static studies: a Basquin S-N curve, a load cycle and a
Goodman mean-stress correction, evaluated node by node on the solved stress field.

- Basquin (1910), "The exponential law of endurance tests", Proc. ASTM 10, 625-630: the stress
  amplitude at N cycles to failure is sigma_a = A N^b (b < 0). Written per reversal it is
  sigma_a = sigma_f' (2N)^b, so A = sigma_f' 2^b.
- Goodman mean-stress correction (Goodman, "Mechanics Applied to Engineering", 1899; as used in
  Stephens, Fatemi, Stephens & Fuchs, "Metal Fatigue in Engineering", 2nd ed., Wiley 2001,
  ch. 7): sigma_a / sigma_ar + sigma_m / S_u = 1, so the equivalent fully reversed amplitude
  is sigma_ar = sigma_a / (1 - sigma_m / S_u). A compressive mean stress is given no credit
  (sigma_ar = sigma_a), the usual conservative choice.
- The default S-N curve, an ESTIMATE: Ezeh & Susmel, "Reference strength values to design
  against static and fatigue loading polylactide additively manufactured with in-fill level
  equal to 100%", Material Design & Processing Communications 1(4), 2019
  (https://eprints.whiterose.ac.uk/id/eprint/142395/): for AM PLA a design curve with a
  negative inverse slope k = 5.5 and an endurance limit at 2e6 cycles of 10% of the ultimate
  tensile strength (probability of survival > 90%). Here b = -1/k and A = 0.1 S_u (2e6)^(1/k).
  Applied to the other FDM polymers it is an extrapolation and is labelled so. A data-sheet
  S-N curve (settings or the material) replaces it and is labelled "datasheet".

Stresses scale linearly with the load (linear static study), so a cycle between R F and F
gives sigma_max = sigma, sigma_min = R sigma at every node. The signed equivalent stress is
von Mises with the sign of the larger principal stress; with an orthotropic material the
stress across the layers is also checked against the Z strength on a scaled curve.
"""
from __future__ import annotations

import math

import numpy as np

from .fields import principal_stresses, von_mises_np
from .materials import FDM_KNOCKDOWN, Material

LABEL = "fatigue estimate"
EST_K = 5.5                 # Ezeh & Susmel 2019: negative inverse slope
EST_N_REF = 2e6             # cycles at the reference endurance limit
EST_FRACTION = 0.10         # endurance limit / UTS at EST_N_REF
EST_SOURCE = ("Ezeh & Susmel 2019 AM PLA design curve (k = 5.5, 10% of UTS at 2e6 cycles, "
              "P_s > 90%)")
N_CAP = 1e12                # cycles beyond this are reported as the cap ("no failure seen")


def fatigue_uts(m: Material) -> float:
    """The ultimate strength the S-N curve and Goodman use: the material's strength with its
    infill / orientation factors but not the generic 0.7 FDM knock-down (the design curve is
    already a 90%-survival curve)."""
    return m.yield_mpa * min(1.0, m.knockdown / FDM_KNOCKDOWN)


def sn_curve(m: Material, override=None) -> dict:
    """{a_mpa, b, uts_mpa, basis, source}: sigma_a = a N^b."""
    su = fatigue_uts(m)
    if override is not None:
        return {"a_mpa": float(override.a_mpa), "b": float(override.b), "uts_mpa": su,
                "basis": "datasheet", "source": override.source or "user"}
    if m.sn_a_mpa is not None and m.sn_b is not None:
        f = min(1.0, m.knockdown / FDM_KNOCKDOWN)
        return {"a_mpa": m.sn_a_mpa * f, "b": m.sn_b, "uts_mpa": su, "basis": "datasheet",
                "source": f"{m.name} S-N data"}
    a = EST_FRACTION * su * EST_N_REF ** (1 / EST_K)
    note = "" if m.name == "PLA" else f" applied to {m.name} (extrapolated)"
    return {"a_mpa": a, "b": -1 / EST_K, "uts_mpa": su, "basis": "estimate",
            "source": EST_SOURCE + note}


def strength_at(curve: dict, cycles: float) -> float:
    """The fully reversed stress amplitude the curve allows at `cycles` (at most S_u)."""
    return min(curve["uts_mpa"], curve["a_mpa"] * max(1.0, cycles) ** curve["b"])


def cycles_to_failure(curve: dict, sigma_ar: float) -> float:
    if sigma_ar <= 0:
        return N_CAP
    if sigma_ar >= curve["uts_mpa"]:
        return 1.0
    return min(N_CAP, max(1.0, (sigma_ar / curve["a_mpa"]) ** (1 / curve["b"])))


def goodman(sigma_a: np.ndarray, sigma_m: np.ndarray, su: float) -> np.ndarray:
    """Equivalent fully reversed amplitude; inf where the mean stress reaches S_u."""
    sm = np.maximum(sigma_m, 0.0)
    denom = 1.0 - sm / su
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denom > 0, sigma_a / np.where(denom > 0, denom, 1.0), np.inf)


def cycle_stresses(sigma: np.ndarray, r: float) -> tuple[np.ndarray, np.ndarray]:
    """(amplitude, mean) of a cycle between r sigma and sigma (sigma signed, at the max load)."""
    return np.abs(sigma) * (1 - r) / 2, sigma * (1 + r) / 2


def signed_von_mises(S: np.ndarray) -> np.ndarray:
    vm = von_mises_np(S)
    pr = principal_stresses(S)
    sign = np.where(np.abs(pr[:, 0]) >= np.abs(pr[:, 2]), np.sign(pr[:, 0]), np.sign(pr[:, 2]))
    return vm * np.where(sign == 0, 1.0, sign)


def evaluate(ids: list[int], S: np.ndarray, coords: dict, m: Material, settings,
             exclude: set[int] | None = None, layer_normal: np.ndarray | None = None) -> dict:
    """The fatigue result of one load case: S is (n, 6) at nodes `ids` (at the case's load,
    the cycle's maximum). The hotspot is the node with the lowest fatigue safety factor away
    from the supported nodes (an ideal clamp's singularity, like the static peak)."""
    curve = sn_curve(m, getattr(settings, "sn", None))
    r = settings.load_ratio()
    need = float(settings.cycles)
    keep = np.array([n not in (exclude or set()) for n in ids], dtype=bool)
    if not keep.any():
        keep[:] = True
    checks = [("in-plane (von Mises)", signed_von_mises(S), curve)]
    if m.orthotropic and layer_normal is not None:
        zf = m.allowable_z_mpa / m.allowable_mpa if m.allowable_mpa > 0 else 1.0
        zc = {**curve, "a_mpa": curve["a_mpa"] * zf, "uts_mpa": curve["uts_mpa"] * zf}
        # only tension opens the layers: compression across them is not checked here
        checks.append(("across layers (Z)", np.maximum(layer_normal, 0.0), zc))
    best = None
    for mode, sig, c in checks:
        sa, sm = cycle_stresses(sig, r)
        sar = goodman(sa, sm, c["uts_mpa"])
        allow = strength_at(c, need)
        with np.errstate(divide="ignore"):
            sf = np.where(sar > 0, allow / sar, np.inf)
        sf = np.where(keep, sf, np.inf)
        i = int(np.argmin(sf))
        if best is None or sf[i] < best["sf"]:
            best = {"sf": float(sf[i]), "i": i, "mode": mode, "curve": c, "sa": float(sa[i]),
                    "sm": float(sm[i]), "sar": float(sar[i]), "allow": allow}
    i = best["i"]
    sf = best["sf"]
    n_f = 1.0 if not math.isfinite(best["sar"]) else cycles_to_failure(best["curve"],
                                                                        best["sar"])
    return {"label": LABEL, "cycles_required": need, "cycle": settings.cycle,
            "load_ratio": r, "cycles_to_failure": n_f, "runout": n_f >= N_CAP,
            "safety_factor": None if not math.isfinite(sf) else sf,
            "hotspot_at": list(coords.get(ids[i], (0, 0, 0))), "governing": best["mode"],
            "stress_amplitude_mpa": best["sa"], "mean_stress_mpa": best["sm"],
            "equivalent_amplitude_mpa": best["sar"] if math.isfinite(best["sar"]) else None,
            "allowable_amplitude_mpa": best["allow"],
            "sn_curve": {k: best["curve"][k] for k in ("a_mpa", "b", "uts_mpa", "basis",
                                                       "source")},
            "mean_stress_correction": "Goodman"}


def node_field(ids: list[int], S: np.ndarray, m: Material, settings,
               layer_normal: np.ndarray | None = None) -> dict[int, float]:
    """Per-node fatigue safety factor (capped at 10) for the probe / colour channel."""
    curve = sn_curve(m, getattr(settings, "sn", None))
    r = settings.load_ratio()
    sa, sm = cycle_stresses(signed_von_mises(S), r)
    sf = np.minimum(10.0, np.where(goodman(sa, sm, curve["uts_mpa"]) > 0,
                                   strength_at(curve, settings.cycles)
                                   / np.maximum(goodman(sa, sm, curve["uts_mpa"]), 1e-30), 10.0))
    if m.orthotropic and layer_normal is not None and m.allowable_mpa > 0:
        zf = m.allowable_z_mpa / m.allowable_mpa
        zc = {**curve, "a_mpa": curve["a_mpa"] * zf, "uts_mpa": curve["uts_mpa"] * zf}
        za, zm = cycle_stresses(np.maximum(layer_normal, 0.0), r)
        zr = goodman(za, zm, zc["uts_mpa"])
        sf = np.minimum(sf, np.where(zr > 0, strength_at(zc, settings.cycles)
                                     / np.maximum(zr, 1e-30), 10.0))
    return {n: float(sf[k]) for k, n in enumerate(ids)}


def stress_arrays(mesh, frd: dict, m: Material) -> tuple[list[int], np.ndarray, np.ndarray | None]:
    """(node ids, (n, 6) stresses, stress across the layers or None) of a solved case."""
    S = frd.get("STRESS", {})
    ids = [n for n in mesh.nodes if n in S]
    arr = np.array([S[n][:6] for n in ids], dtype=float) if ids else np.zeros((0, 6))
    normal = None
    if m.orthotropic and ids:
        from .aniso import layer_normal_stress
        normal = layer_normal_stress(arr, m.build_dir)
    return ids, arr, normal


def case_fatigue(mesh, frd: dict, m: Material, settings, exclude: set[int]) -> dict | None:
    ids, S, normal = stress_arrays(mesh, frd, m)
    if not ids:
        return None
    return evaluate(ids, S, mesh.nodes, m, settings, exclude=exclude, layer_normal=normal)


def warnings_for(f: dict, prefix: str = "") -> list[str]:
    if f["safety_factor"] is None or f["safety_factor"] >= 1:
        return []
    return [f"{prefix}FATIGUE ESTIMATE: predicted to fail after about "
            f"{f['cycles_to_failure']:.3g} cycles, before the {f['cycles_required']:.3g} "
            f"required (fatigue safety factor {f['safety_factor']:.2f}, {f['governing']}, "
            f"{f['sn_curve']['basis']} S-N curve)"]
