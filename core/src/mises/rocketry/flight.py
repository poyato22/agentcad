"""Rocket flight (Plan 11 F6): a 3-DOF point-mass ascent and recovery in the vertical plane.

- On the launch rail the rocket moves along the rail. Off the rail it flies nose-into-the-
  relative-wind (a statically stable rocket weathercocks; the static margin check says whether
  it is), so thrust acts along the air-relative velocity and drag against it. Wind is a steady
  horizontal wind (+x is downwind).
- Mass: the dry rocket plus the motor, whose propellant burns in proportion to the impulse
  delivered (motors.Motor.mass_kg). Drag: aero.drag at the local Mach and Reynolds number, ISA
  atmosphere from the launch site.
- Recovery: the parachute opens at the ejection (burnout + delay) or at apogee; after it opens,
  its drag (Cd x area) adds to the rocket's. The peak load on the payload at deployment uses an
  opening-shock factor (default 1.5, a typical value for small flat / hemispherical chutes).
- RK4 at 5 ms during the burn, 20 ms after.

The model matches OpenRocket's vertical flights within a few percent (tests/rocketry: OpenRocket
24.12 reference flights); it does not model roll, coning, staging or rail friction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .aero import G0, Body, drag, isa
from .motors import Motor

DT_BURN = 0.005
DT_COAST = 0.02
T_MAX = 900.0
MAX_SAMPLES = 400


@dataclass
class FlightSetup:
    body: Body
    motor: Motor
    dry_mass_kg: float
    rail_m: float = 1.0
    rail_angle_deg: float = 0.0        # from vertical, tilted downwind
    wind_mps: float = 0.0
    site_t0_k: float = 288.15
    site_p0_pa: float = 101325.0
    roughness_m: float = 60e-6
    parasitic_cd: float = 0.0
    cd_override: float | None = None
    delay_s: float | None = None       # None: the chute opens at apogee
    chute_d_m: float | None = None     # None: no chute (ballistic)
    chute_cd: float = 0.8
    opening_shock: float = 1.5


@dataclass
class FlightResult:
    apogee_m: float = 0.0
    t_apogee_s: float = 0.0
    max_v_mps: float = 0.0
    max_mach: float = 0.0
    max_v_alt_m: float = 0.0
    max_accel_mps2: float = 0.0
    rail_exit_mps: float = 0.0
    burnout_t_s: float = 0.0
    burnout_alt_m: float = 0.0
    burnout_v_mps: float = 0.0
    deploy_t_s: float | None = None
    deploy_alt_m: float | None = None
    deploy_v_mps: float | None = None
    deploy_shock_g: float | None = None
    descent_mps: float | None = None
    flight_time_s: float = 0.0
    landing_v_mps: float = 0.0
    drift_m: float = 0.0
    liftoff_mass_kg: float = 0.0
    burnout_mass_kg: float = 0.0
    thrust_to_weight: float = 0.0
    cd_at_max_v: float = 0.0
    samples: list[tuple[float, float, float, float]] = field(default_factory=list)  # t, alt, v, a

    def summary(self) -> dict:
        out = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()
               if k != "samples"}
        return out


def _cd(s: FlightSetup, v: float, air) -> float:
    if s.cd_override is not None:
        return s.cd_override
    return drag(s.body, v, air, roughness_m=s.roughness_m, parasitic_cd=s.parasitic_cd).cd


def simulate(s: FlightSetup) -> FlightResult:
    m0 = s.dry_mass_kg + s.motor.total_kg
    res = FlightResult(liftoff_mass_kg=m0,
                       burnout_mass_kg=s.dry_mass_kg + s.motor.total_kg - s.motor.prop_kg)
    res.thrust_to_weight = s.motor.max_thrust_n / (m0 * G0)
    aref = s.body.ref_area
    chute_area = math.pi * s.chute_d_m ** 2 / 4 if s.chute_d_m else 0.0
    th = math.radians(s.rail_angle_deg)
    rail_dir = (math.sin(th), math.cos(th))
    burn = s.motor.burn_time_s
    t_deploy = burn + s.delay_s if s.delay_s is not None else None
    deployed = False
    on_rail = True
    x = z = vx = vz = 0.0
    t = 0.0
    samples: list[tuple[float, float, float, float]] = []
    lifted = False
    prev_vz = 0.0

    def accel(t_: float, x_: float, z_: float, vx_: float, vz_: float, rail: bool,
              chute: bool) -> tuple[float, float, float, float]:
        air = isa(z_, t0_k=s.site_t0_k, p0_pa=s.site_p0_pa)
        m = s.dry_mass_kg + s.motor.mass_kg(t_)
        ax_, az_ = vx_ - s.wind_mps, vz_                    # air-relative velocity
        va = math.hypot(ax_, az_)
        thrust = s.motor.thrust(t_)
        if rail or va < 1e-6:
            hx, hz = rail_dir
        else:
            hx, hz = ax_ / va, az_ / va
        cd = _cd(s, va, air)
        q = 0.5 * air.rho * va * va
        dragf = q * cd * aref + (q * s.chute_cd * chute_area if chute else 0.0)
        fx = thrust * hx - (dragf * ax_ / va if va > 1e-9 else 0.0)
        fz = thrust * hz - (dragf * az_ / va if va > 1e-9 else 0.0) - m * G0
        if rail:
            along = fx * rail_dir[0] + fz * rail_dir[1]
            along = max(along, 0.0)                    # sits on the pad until thrust > weight
            fx, fz = along * rail_dir[0], along * rail_dir[1]
        return fx / m, fz / m, cd, air.a

    while t < T_MAX:
        dt = DT_BURN if t < burn + 0.1 else DT_COAST
        chute = deployed
        a1 = accel(t, x, z, vx, vz, on_rail, chute)
        k1 = (vx, vz, a1[0], a1[1])
        a2 = accel(t + dt / 2, x + k1[0] * dt / 2, z + k1[1] * dt / 2, vx + k1[2] * dt / 2,
                   vz + k1[3] * dt / 2, on_rail, chute)
        k2 = (vx + k1[2] * dt / 2, vz + k1[3] * dt / 2, a2[0], a2[1])
        a3 = accel(t + dt / 2, x + k2[0] * dt / 2, z + k2[1] * dt / 2, vx + k2[2] * dt / 2,
                   vz + k2[3] * dt / 2, on_rail, chute)
        k3 = (vx + k2[2] * dt / 2, vz + k2[3] * dt / 2, a3[0], a3[1])
        a4 = accel(t + dt, x + k3[0] * dt, z + k3[1] * dt, vx + k3[2] * dt, vz + k3[3] * dt,
                   on_rail, chute)
        k4 = (vx + k3[2] * dt, vz + k3[3] * dt, a4[0], a4[1])
        x += dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        z += dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        vx += dt / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        vz += dt / 6 * (k1[3] + 2 * k2[3] + 2 * k3[3] + k4[3])
        t += dt
        if not lifted and (vz > 0 or z > 0):
            lifted = True
        if not lifted:
            if t > burn:
                break                                   # never left the pad
            continue
        if z < 0:
            res.flight_time_s = t
            res.landing_v_mps = math.hypot(vx, vz)
            res.drift_m = x
            break
        v = math.hypot(vx, vz)
        acc = math.hypot(a1[0], a1[1] + G0)             # what an accelerometer reads
        air_a = a1[3]
        if on_rail and (x * rail_dir[0] + z * rail_dir[1]) >= s.rail_m:
            on_rail = False
            res.rail_exit_mps = v
        if v > res.max_v_mps:
            res.max_v_mps, res.max_v_alt_m, res.cd_at_max_v = v, z, a1[2]
            res.max_mach = max(res.max_mach, v / air_a)
        if not deployed:
            res.max_accel_mps2 = max(res.max_accel_mps2, acc)
        if res.burnout_t_s == 0.0 and t >= burn:
            res.burnout_t_s, res.burnout_alt_m, res.burnout_v_mps = t, z, v
        if z > res.apogee_m:
            res.apogee_m, res.t_apogee_s = z, t
        at_apogee = prev_vz > 0 >= vz
        prev_vz = vz
        if not deployed and chute_area > 0 and (
                (t_deploy is not None and t >= t_deploy) or (t_deploy is None and at_apogee)):
            deployed = True
            air = isa(z, t0_k=s.site_t0_k, p0_pa=s.site_p0_pa)
            va = math.hypot(vx - s.wind_mps, vz)
            m = s.dry_mass_kg + s.motor.mass_kg(t)
            res.deploy_t_s, res.deploy_alt_m, res.deploy_v_mps = t, z, va
            res.deploy_shock_g = (0.5 * air.rho * va * va * s.chute_cd * chute_area
                                  * s.opening_shock) / (m * G0)
        if deployed:
            res.descent_mps = -vz
        samples.append((t, z, v, acc / G0))
    if res.flight_time_s == 0.0:
        res.flight_time_s = t
    if len(samples) > MAX_SAMPLES:
        step = len(samples) / MAX_SAMPLES
        samples = [samples[int(i * step)] for i in range(MAX_SAMPLES)] + [samples[-1]]
    res.samples = samples
    return res


def descent_rate(mass_kg: float, chute_d_m: float, chute_cd: float = 0.8,
                 rho: float = 1.225) -> float:
    """Terminal descent speed under a chute of diameter d (m/s)."""
    area = math.pi * chute_d_m ** 2 / 4
    return math.sqrt(2 * mass_kg * G0 / (rho * chute_cd * area))


def chute_for(mass_kg: float, v_mps: float, chute_cd: float = 0.8, rho: float = 1.225) -> float:
    """The chute diameter (m) that descends at v."""
    return math.sqrt(8 * mass_kg * G0 / (math.pi * rho * chute_cd * v_mps * v_mps))
