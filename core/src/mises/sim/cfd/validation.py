"""Measured accuracy of the built-in wind solver, per refinement level: the model error band a
wind result is reported with (not the run's own convergence / unsteadiness band).

Validation runs (C7, 2026-10-29, this solver on an M-series Mac; sphere D = 100 mm at 3 m/s,
Re 2.0e4, reference Cd 0.47; square flat plate 100 mm normal to the flow, t/D 0.04, Re 1.0e4,
reference Cd 1.17 (Hoerner); domain_multiplier 5 unless noted). Reproduce the refinement-3 rows
with `pytest -m cfd_slow tests/sim/test_cfd.py`; the others with the same bodies at that level.
"""
from __future__ import annotations

import math

# refinement -> [(body, cells across, measured Cd, reference Cd, signed error)]
VALIDATION: dict[int, list[tuple[str, int, float, float, float]]] = {
    1: [("sphere", 8, 0.65, 0.47, +0.38)],          # C7 review spot run, default settings
    2: [("sphere", 12, 0.531, 0.47, +0.13),
        ("flat plate", 12, 0.87, 1.17, -0.26)],
    3: [("sphere", 16, 0.455, 0.47, -0.031),        # test_validation_sphere_cd_within_25_percent
        ("flat plate", 16, 1.049, 1.17, -0.10)],    # test_validation_flat_plate_cd
}
# the band reported per level: the worst measured error rounded up to 5% (refinement 1 is a
# preview, validated on one body only; refinement 4 is not validated separately and keeps the
# refinement-3 band, which a finer lattice should not exceed)
MODEL_ERROR_BAND = {1: 0.40, 2: 0.30, 3: 0.15, 4: 0.15}
PREVIEW_REFINEMENT = 1


def model_error_band(cells_across: int) -> tuple[float, int]:
    """(band, refinement level) for the lattice actually run (fit_budget may coarsen it)."""
    level = max((lv for lv, n in {1: 8, 2: 12, 3: 16, 4: 24}.items() if n <= cells_across),
                default=1)
    return MODEL_ERROR_BAND[level], level


def validation_rows(level: int) -> list[dict]:
    return [{"body": b, "cells_across": n, "cd": cd, "reference_cd": ref, "error": e}
            for b, n, cd, ref, e in VALIDATION.get(level, [])]


# ---- OpenFOAM k-omega SST (S2)
# Measured rows are recorded ONLY from real container runs (the S5 real-solver QA, with the
# user's approval to pull the image): (body, Reynolds, refinement, measured Cd, reference Cd,
# signed error). Reproduce with `MISES_OPENFOAM_QA=1 pytest -m openfoam_real
# tests/sim/test_openfoam.py`. Until a row exists the band below is the plan's ACCEPTANCE TARGET
# (sphere at Re 1e4-1e5 within 10%), and results say "validation pending", never "validated".
# Recorded 2026-10-30 (fix-bench, real opencfd/openfoam-run v2512 runs on an M-series Mac, 4
# CPUs, refinement 3, laminar below Re 1e5, bluff bodies meshed at surface level 4 all over):
# sphere D 100 mm at 3 m/s (1.13 M cells), square plate 100 mm x 4 mm at 1.5 m/s (0.95 M
# cells est.), 1500 iterations each, Cd the mean of the last half.
OPENFOAM_VALIDATION: list[tuple[str, float, int, float, float, float]] = [
    ("sphere", 2.03e4, 3, 0.445, 0.47, -0.053),
    ("flat plate", 1.02e4, 3, 1.276, 1.17, +0.091),
]
OPENFOAM_TARGET_BAND = 0.10


def openfoam_validated() -> bool:
    return bool(OPENFOAM_VALIDATION)


def openfoam_error_band() -> float:
    """The worst measured error rounded up to 5% (never below the target), else the target."""
    if not OPENFOAM_VALIDATION:
        return OPENFOAM_TARGET_BAND
    worst = max(abs(e) for *_, e in OPENFOAM_VALIDATION)
    return max(OPENFOAM_TARGET_BAND, math.ceil(worst * 20 - 1e-9) / 20)


def openfoam_validation_rows() -> list[dict]:
    return [{"body": b, "reynolds": re_, "refinement": lv, "cd": cd, "reference_cd": ref,
             "error": e} for b, re_, lv, cd, ref, e in OPENFOAM_VALIDATION]
