"""Structured study setup: face presets, load directions and environment presets.

Fixtures and loads are chosen from these presets (or a face id from mirror_measure), never
authored free-form: in CADEngBench most FEA failures were mesh/boundary-condition setup, not
the solver (see research/models/README.md).
"""
from __future__ import annotations

from .types import SimError

# preset -> (axis index, "min" | "max"): every flat face lying on that side of the bounding box
FACE_PRESETS: dict[str, tuple[int, str]] = {
    "bottom": (2, "min"), "top": (2, "max"),
    "x-min": (0, "min"), "x-max": (0, "max"),
    "y-min": (1, "min"), "y-max": (1, "max"),
    "z-min": (2, "min"), "z-max": (2, "max"),
}

DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0), "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0),
}


def direction_vector(name: str) -> tuple[float, float, float]:
    try:
        return DIRECTIONS[name]
    except KeyError:
        raise SimError("bad_setup", f"unknown direction {name!r}",
                       hint=f"use one of: {', '.join(DIRECTIONS)}") from None


# Shipped presets (design §7.5). Only the static load case is solved; temperature is checked
# against the material's heat deflection temperature, vibration/UV are listed but NOT simulated.
ENVIRONMENTS: list[dict] = [
    {"name": "Desk", "temperature_c": 23, "flags": [],
     "suggested": {"fix": "bottom", "load": {"face": "top", "newtons": 20, "direction": "-z"}},
     "notes": "Indoor, room temperature; a hand pressing down (~2 kg)."},
    {"name": "Outdoor", "temperature_c": 45, "flags": ["uv"],
     "suggested": {"fix": "bottom", "load": {"face": "top", "newtons": 30, "direction": "-z"}},
     "notes": "Sun-heated surface up to ~45 C. UV ageing is not simulated."},
    {"name": "Bike mount", "temperature_c": 35, "flags": ["vibration", "uv"],
     "suggested": {"fix": "bottom", "load": {"face": "top", "newtons": 50, "direction": "-z"}},
     "notes": "Static equivalent of a ~2 g bump on a ~2.5 kg load. Vibration and fatigue are "
              "not simulated."},
    {"name": "Car dashboard 80°C", "temperature_c": 80, "flags": ["uv"],
     "suggested": {"fix": "bottom", "load": {"face": "top", "newtons": 10, "direction": "-z"}},
     "notes": "A parked car in summer reaches ~80 C; PLA and PETG soften well below that."},
]


def get_environment(name: str) -> dict:
    for e in ENVIRONMENTS:
        if e["name"].lower() == name.strip().lower():
            return e
    raise SimError("bad_setup", f"unknown environment {name!r}",
                   hint=f"use one of: {', '.join(e['name'] for e in ENVIRONMENTS)}")
