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


# ---- multirotor propulsion (static propeller coefficients) ---------------------------------

RHO_AIR = 1.225                 # kg/m^3, sea level
V_CELL = 3.7                    # LiPo nominal volts per cell
KV_LOAD = 0.8                   # loaded rpm / (KV x V): props pull a motor well under no-load
ELEC_EFF = 0.8                  # shaft power / electrical power of a small brushless motor
PROP_FORMULA = ("T = Ct rho n^2 D^4, P = Cp rho n^3 D^5 (static); Ct, Cp from pitch/diameter "
                "and blade count; n = KV x V x 0.8, capped by the motor's power")


def prop_coefficients(pitch_ratio: float, blades: int) -> tuple[float, float]:
    """Static thrust and power coefficients of a small prop: two-blade values linear in
    pitch / diameter (typical of 3-7 inch FPV / APC static data), scaled for the blade count."""
    ct2 = 0.09 + 0.05 * pitch_ratio
    cp2 = 0.035 + 0.04 * pitch_ratio
    return ct2 * (blades / 2) ** 0.6, cp2 * (blades / 2) ** 0.8


def propulsion(*, prop_diameter_in: float, blades: int, motor_kv: float, cells: int,
               motors: int, all_up_mass_g: float, pitch_in: float | None = None,
               motor_max_power_w: float | None = None) -> dict:
    """Max thrust, thrust-to-weight, hover rpm / throttle and the excitation bands (1x rotation
    and blade pass, hover to full throttle) of a multirotor. An estimate, not a thrust stand."""
    D = prop_diameter_in * 0.0254
    pr = (pitch_in / prop_diameter_in) if pitch_in else 0.8
    ct, cp = prop_coefficients(pr, blades)
    volts = cells * V_CELL
    n_max = motor_kv * volts * KV_LOAD / 60.0                       # rev/s
    limited = "voltage (KV x V)"
    if motor_max_power_w:
        n_pow = (motor_max_power_w * ELEC_EFF / (cp * RHO_AIR * D ** 5)) ** (1 / 3)
        if n_pow < n_max:
            n_max, limited = n_pow, "motor power"
    t_max = ct * RHO_AIR * n_max ** 2 * D ** 4                      # N per motor
    weight = all_up_mass_g / 1000.0 * GRAVITY
    n_h = math.sqrt(weight / motors / (ct * RHO_AIR * D ** 4))
    p_max = cp * RHO_AIR * n_max ** 3 * D ** 5 / ELEC_EFF
    p_h = cp * RHO_AIR * n_h ** 3 * D ** 5 / ELEC_EFF
    return {
        "estimate": True, "formula": PROP_FORMULA, "ct": round(ct, 5), "cp": round(cp, 5),
        "pitch_ratio": round(pr, 3), "volts": round(volts, 2), "limited_by": limited,
        "max_rpm": round(n_max * 60), "hover_rpm": round(n_h * 60),
        "hover_throttle": round(n_h / n_max, 3),
        "max_thrust_g_per_motor": round(t_max / GRAVITY * 1000, 1),
        "thrust_to_weight": round(motors * t_max / weight, 2),
        "max_power_w_per_motor": round(p_max, 1), "hover_power_w_per_motor": round(p_h, 1),
        "max_current_a": round(motors * p_max / volts, 1),
        "hover_current_a": round(motors * p_h / volts, 2),
        "rotation_band_hz": [round(n_h, 1), round(n_max, 1)],
        "blade_pass_band_hz": [round(blades * n_h, 1), round(blades * n_max, 1)],
        "note": ("estimate from static propeller coefficients; real thrust depends on the prop "
                 "model, motor and pack sag: check a thrust table when one exists. The bands "
                 "are what a frame mode should avoid: 1x rotation (imbalance) and blade pass."),
    }
