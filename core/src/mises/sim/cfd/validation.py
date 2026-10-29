"""Measured accuracy of the built-in wind solver, per refinement level: the model error band a
wind result is reported with (not the run's own convergence / unsteadiness band).

Validation runs (C7, 2026-10-29, this solver on an M-series Mac; sphere D = 100 mm at 3 m/s,
Re 2.0e4, reference Cd 0.47; square flat plate 100 mm normal to the flow, t/D 0.04, Re 1.0e4,
reference Cd 1.17 (Hoerner); domain_multiplier 5 unless noted). Reproduce the refinement-3 rows
with `pytest -m cfd_slow tests/sim/test_cfd.py`; the others with the same bodies at that level.
"""
from __future__ import annotations

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
