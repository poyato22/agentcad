"""Load estimates for the engineering loop.

Impact and crash loads are approximations, not transient simulations. We state the model
plainly: an equivalent *static* load, F = m·g·k, where k is a dynamic amplification factor the
caller chooses for the severity of the event. Every result it produces is flagged
`estimate: true` and carries the formula, so the agent and the UI can label it next to the
solver's own "guide only" wording.
"""
from __future__ import annotations

import math

GRAVITY = 9.80665  # m/s^2, standard gravity

# Typical dynamic amplification factors for a dropped/crashed part, as a starting point only.
# A hard landing onto concrete is far worse than onto grass; the caller states which it used.
DEFAULT_IMPACT_K = 10.0
FORMULA = "F = m·g·k (equivalent static load)"


def impact_equivalent(mass_kg: float, drop_m: float, k: float = DEFAULT_IMPACT_K) -> dict:
    """Equivalent static load for an impact or crash case.

    `mass_kg` is the moving mass, `drop_m` the drop height (context for how hard the hit is),
    and `k` the dynamic amplification factor. Returns newtons with the model spelled out and
    `estimate: true`; it is never a certified crash load.
    """
    if mass_kg <= 0:
        raise ValueError("mass_kg must be positive")
    if drop_m < 0:
        raise ValueError("drop_m must be >= 0")
    if k <= 0:
        raise ValueError("k must be positive")
    newtons = mass_kg * GRAVITY * k
    note = (f"Impact approximated as an equivalent static load {FORMULA} with k={k:g} the "
            f"dynamic amplification factor; a {drop_m:g} m drop sets the severity. This is an "
            "estimate, not a transient crash analysis: real peak forces depend on the stopping "
            "distance and the surface.")
    return {
        "newtons": round(newtons, 3),
        "estimate": True,
        "formula": FORMULA,
        "mass_kg": mass_kg,
        "drop_m": drop_m,
        "k": k,
        "g": GRAVITY,
        "note": note,
    }


REF_DROP_M = 1.0


def height_scaled_k(k_ref: float, drop_m: float, ref_drop_m: float = REF_DROP_M) -> float:
    """The dynamic factor at `drop_m` from `k_ref`, the factor stated for a `ref_drop_m` drop.

    A falling mass stopped by an elastic contact peaks at F = m·g·(1 + √(1 + 2h/δ)), δ the
    static deflection of the contact; for any real drop h ≫ δ, so F grows with √h (the impact
    velocity). k therefore scales by √(h / h_ref), and never drops below 1 (the part's weight).
    """
    if drop_m < 0 or ref_drop_m <= 0:
        raise ValueError("drop heights must be positive")
    return max(1.0, k_ref * math.sqrt(drop_m / ref_drop_m))
