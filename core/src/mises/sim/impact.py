"""Impact / drop dynamics: a part, assembly or egg-drop build hits a rigid floor.

Three tiers, each labelled with its fidelity:

- `descent(...)`: the fall itself. Quadratic drag (body Cd x A plus a parachute), terminal
  velocity, impact speed and fall time from a drop height (closed form, no wind, sea-level air
  unless told otherwise, the parachute is taken as open from release).
- `lumped_impact(...)`: a 1D transient of the build as a stack of masses on crushable or elastic
  layers (feet, crumple zone, foam, the egg's cradle), integrated in time. Contact with the floor
  and between parts is compression only, so the build can bounce. Crushable layers load along a
  foam curve (elastic, plateau, densification) and unload elastically, so the hysteresis is the
  energy they absorb. Fast (well under a second). Fidelity `impact_lumped`.
- `run_fem_impact(...)`: the same stack as 3D hex blocks in CalculiX *DYNAMIC (implicit,
  NLGEOM) with penalty contact on a rigid floor, foam layers as *HYPERFOAM. It returns the
  payload's accelerometer trace, peak stress per part, rebound and the peak strain energy each
  part takes up. Fidelity `impact_fem`. The CalculiX hyperfoam is ELASTIC: it returns its energy
  (bar numerical damping), so the energy a crushable foam dissipates is taken from the lumped
  tier and the FEM tier is the check on the peak g and the stresses.

Units: SI in the API (m, kg, s, MPa for stresses); the CalculiX deck is mm / t / s / N / MPa.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

G = 9.81
RHO_AIR = 1.225   # kg/m3, sea level 15 C

# ---------------------------------------------------------------- descent (the fall)


def free_fall_velocity(height_m: float, g: float = G) -> float:
    """Impact speed of a drop from height_m with no drag: sqrt(2 g h)."""
    return math.sqrt(2.0 * g * max(height_m, 0.0))


def terminal_velocity(mass_kg: float, cd_area_m2: float, rho: float = RHO_AIR,
                      g: float = G) -> float:
    """v_t = sqrt(2 m g / (rho Cd A)); inf with no drag area."""
    if cd_area_m2 <= 0:
        return math.inf
    return math.sqrt(2.0 * mass_kg * g / (rho * cd_area_m2))


def parachute_cd_area(diameter_m: float, cd: float = 1.5) -> float:
    """Cd x canopy area of a round parachute of the given flat diameter."""
    return cd * math.pi * (diameter_m / 2.0) ** 2


def descent(mass_kg: float, height_m: float, *, cd_area_m2: float = 0.0,
            parachute_diameter_m: float = 0.0, parachute_cd: float = 1.5,
            rho: float = RHO_AIR, g: float = G) -> dict:
    """The fall from rest through height_m with quadratic drag F = 1/2 rho CdA v^2.

    Closed form: v(h) = v_t sqrt(1 - exp(-2 g h / v_t^2)), t(h) = (v_t / g) acosh(exp(g h /
    v_t^2)). Assumptions: still air, the parachute is open from release (no opening delay or
    shock), no tumbling (a constant CdA)."""
    cda = cd_area_m2 + (parachute_cd_area(parachute_diameter_m, parachute_cd)
                        if parachute_diameter_m > 0 else 0.0)
    v_ff = free_fall_velocity(height_m, g)
    vt = terminal_velocity(mass_kg, cda, rho, g)
    if math.isinf(vt):
        v, t = v_ff, math.sqrt(2.0 * height_m / g) if height_m > 0 else 0.0
    else:
        x = g * height_m / vt ** 2
        v = vt * math.sqrt(-math.expm1(-2.0 * x))
        # acosh(e^x) = x + ln(1 + sqrt(1 - e^-2x)), stable for large x
        t = (vt / g) * (x + math.log1p(math.sqrt(-math.expm1(-2.0 * x))))
    return {"impact_velocity_m_s": v, "free_fall_velocity_m_s": v_ff,
            "terminal_velocity_m_s": None if math.isinf(vt) else vt,
            "fraction_of_terminal": None if math.isinf(vt) else v / vt,
            "fall_time_s": t, "cd_area_m2": cda, "height_m": height_m, "mass_kg": mass_kg,
            "kinetic_energy_j": 0.5 * mass_kg * v * v,
            "model": "quadratic drag, still air, constant CdA, parachute open from release"}


def spring_mass_impact(mass_kg: float, k_n_m: float, velocity_m_s: float,
                       g: float = 0.0) -> dict:
    """A rigid mass landing at v on a linear spring k (the analytic check).

    Max compression x from 1/2 k x^2 = 1/2 m v^2 + m g x; peak spring force k x; peak
    accelerometer reading k x / m (g = 0: v sqrt(k / m)); contact time pi sqrt(m / k) (g = 0)."""
    m, k, v = mass_kg, k_n_m, velocity_m_s
    x = (m * g + math.sqrt((m * g) ** 2 + k * m * v * v)) / k
    return {"max_compression_m": x, "peak_force_n": k * x, "peak_accel_m_s2": k * x / m,
            "peak_g": k * x / (m * G), "contact_time_s": math.pi * math.sqrt(m / k)}


# ---------------------------------------------------------------- cushion materials

@dataclass(frozen=True)
class Cushion:
    """A crushable / elastic cushion material (nominal stress vs nominal strain in compression).

    sigma = E eps up to the plateau, a flat plateau to the densification strain, then the cell
    walls touch and it stiffens: sigma = plateau (1 + k_d ((eps - eps_d) / (1 - eps))^2 ...)
    (a Gibson-Ashby-like shape). crushable: unloads elastically (E) from its most crushed
    point, so the loop is absorbed energy; elastic (crushable False): unloads on the loading
    curve and gives the energy back (bar damping)."""
    name: str
    e_mpa: float
    plateau_mpa: float
    densification_strain: float
    density_kg_m3: float
    crushable: bool = True
    damping_ratio: float = 0.05
    source: str = "typical handbook value (estimate)"

    def stress(self, eps: float) -> float:
        if eps <= 0:
            return 0.0
        ey = self.plateau_mpa / self.e_mpa
        if eps <= ey:
            return self.e_mpa * eps
        if eps <= self.densification_strain:
            return self.plateau_mpa
        eps = min(eps, 0.995)
        r = (eps - self.densification_strain) / (1.0 - eps)
        return self.plateau_mpa * (1.0 + 4.0 * r + 4.0 * r * r)

    def tangent(self, eps: float, floor: bool = True) -> float:
        """d sigma / d eps (MPa); floor: at least E (the step size uses this)."""
        h = 1e-4
        d = (self.stress(eps + h) - self.stress(max(eps - h, 0.0))) / (2 * h)
        return max(self.e_mpa, d) if floor else max(d, 0.0)


# Typical values, mostly from Gibson & Ashby "Cellular Solids" and supplier data sheets; real
# foams vary a lot with density and rate: calibrate with a crush test where it matters.
CUSHIONS: dict[str, Cushion] = {c.name: c for c in [
    Cushion("eps_20", 5.0, 0.10, 0.70, 20.0, source="EPS 20 kg/m3: ~0.1 MPa at 10% "
            "(EN 13163 class EPS100), typical"),
    Cushion("eps_30", 9.0, 0.18, 0.65, 30.0, source="EPS 30 kg/m3 (EPS150-200), typical"),
    Cushion("pe_foam_30", 1.5, 0.05, 0.70, 30.0, crushable=False, damping_ratio=0.15,
            source="closed-cell polyethylene foam 30 kg/m3, typical (recovers)"),
    Cushion("pu_soft_foam", 0.05, 0.004, 0.75, 30.0, crushable=False, damping_ratio=0.2,
            source="flexible open-cell polyurethane foam, typical"),
    Cushion("eva_foam", 3.0, 0.25, 0.60, 100.0, crushable=False, damping_ratio=0.15,
            source="EVA craft foam ~100 kg/m3, typical"),
    Cushion("paper_honeycomb", 30.0, 0.35, 0.80, 40.0, source="kraft-paper honeycomb "
            "(crush strength ~0.2-0.5 MPa), typical"),
    Cushion("cardboard_crumple", 2.0, 0.03, 0.65, 50.0, source="crumpled corrugated "
            "cardboard / paper, rough estimate"),
    Cushion("tpu_lattice_20pct", 2.0, 0.08, 0.60, 240.0, crushable=False, damping_ratio=0.1,
            source="printed TPU 95A lattice at ~20% fill, rough estimate"),
    Cushion("pla_lattice_15pct", 60.0, 0.6, 0.65, 190.0, source="printed PLA lattice / "
            "crumple structure at ~15% fill (crushes, brittle), rough estimate"),
    Cushion("bubble_wrap", 0.3, 0.02, 0.85, 15.0, crushable=False, damping_ratio=0.1,
            source="bubble wrap (air cushion; bubbles that burst absorb more), rough estimate"),
]}

# The payload is a chicken egg unless told otherwise: ~35 N quasi-static shell failure
# (ideation/tools.EGG_BREAK_N); 60 g -> ~59 g of felt acceleration. Drop tests quote a wider
# range, so the default limit is stated as an estimate.
EGG_MASS_KG = 0.06
EGG_BREAK_N = 35.0


def egg_g_limit(mass_kg: float = EGG_MASS_KG, break_n: float = EGG_BREAK_N) -> float:
    """The felt acceleration (in g) at which a supported egg reaches its shell-failure load."""
    return break_n / (mass_kg * G)


# ---------------------------------------------------------------- the stack


@dataclass
class Layer:
    """One part of the build, bottom first. `mass_kg` sits on top of this layer's compliant
    body. The compliant body is a cushion (by name) or a solid (e_mpa) of footprint
    area_m2 x thickness_m; a part with neither is rigid (a stiff solid)."""
    name: str
    mass_kg: float
    area_m2: float
    thickness_m: float
    cushion: Cushion | None = None
    e_mpa: float | None = None          # a solid part (PLA ~3500, an egg's cradle...)
    nu: float = 0.3
    yield_mpa: float | None = None
    hyperelastic: bool = False          # rubbery solid (TPU, silicone): Neo-Hooke in CalculiX
    damping: float | None = None        # viscous damping ratio override (None = the default)

    @property
    def solid_e(self) -> float:
        return self.e_mpa if self.e_mpa else 3000.0

    def force(self, c: float) -> float:
        """Loading curve: force (N) at compression c (m)."""
        if c <= 0:
            return 0.0
        eps = c / self.thickness_m
        if self.cushion is not None:
            return self.cushion.stress(eps) * 1e6 * self.area_m2
        return self.solid_e * 1e6 * self.area_m2 * eps

    def stiffness(self, c: float) -> float:
        eps = max(c, 0.0) / self.thickness_m
        e = self.cushion.tangent(eps) if self.cushion is not None else self.solid_e
        return e * 1e6 * self.area_m2 / self.thickness_m

    def damping_stiffness(self, c: float) -> float:
        """The current slope (N/m) the viscous damping scales with: zero on a flat plateau, so
        a crushing foam is not damped twice (its crush already dissipates)."""
        eps = max(c, 0.0) / self.thickness_m
        e = self.cushion.tangent(eps, floor=False) if self.cushion is not None else self.solid_e
        return e * 1e6 * self.area_m2 / self.thickness_m

    def unload_stiffness(self) -> float:
        e = self.cushion.e_mpa if self.cushion is not None else self.solid_e
        return e * 1e6 * self.area_m2 / self.thickness_m

    @property
    def crushable(self) -> bool:
        return self.cushion is not None and self.cushion.crushable

    @property
    def damping_ratio(self) -> float:
        if self.damping is not None:
            return self.damping
        return self.cushion.damping_ratio if self.cushion is not None else 0.02


@dataclass
class LumpedResult:
    impact_velocity_m_s: float
    payload: str
    peak_g: float
    peak_g_time_s: float
    rebound_velocity_m_s: float
    restitution: float
    max_compression_m: dict
    max_strain: dict
    bottomed_out: list
    energy_absorbed_j: dict
    energy_in_j: float
    peak_force_n: dict
    peak_stress_mpa: dict
    contact_time_s: float
    trace: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def lumped_impact(layers: list[Layer], velocity_m_s: float, *, payload: str | None = None,
                  g: float = G, t_max_s: float = 0.5, max_steps: int = 400_000,
                  trace_points: int = 400) -> LumpedResult:
    """Integrate the stack (bottom first) from first touch at velocity_m_s downward.

    Node i carries layers[i].mass_kg; layer i's compliant body joins node i to node i-1 (the
    floor for i = 0) and only pushes (compression only: parts may separate and bounce).
    Velocity Verlet with a step of 1/40 of the shortest current period. The run stops when the
    whole build is moving up off the floor (rebound) or has come to rest on it."""
    if not layers:
        raise ValueError("no layers")
    n = len(layers)
    pi = next((i for i, L in enumerate(layers) if L.name == payload), n - 1) \
        if payload else n - 1
    m = [max(L.mass_kg, 1e-6) for L in layers]
    x = [0.0] * n                  # displacement, up positive
    v = [-velocity_m_s] * n
    cmax = [0.0] * n               # the most crushed point (crushable layers)
    absorbed = [0.0] * n
    fpeak = [0.0] * n
    comp_peak = [0.0] * n
    m_above = [sum(m[i:]) for i in range(n)]
    ke0 = 0.5 * sum(m) * velocity_m_s ** 2
    notes: list[str] = []

    def comp(i: int, xs: list[float]) -> float:
        return (xs[i - 1] if i > 0 else 0.0) - xs[i]

    def forces(xs, vs, update: bool):
        f = [0.0] * n
        for i, L in enumerate(layers):
            c = comp(i, xs)
            if c <= 0:
                continue
            fl = L.force(c)
            if L.crushable and c < cmax[i]:
                # elastic unloading from the most crushed point
                fl = min(fl, max(0.0, L.force(cmax[i]) - L.unload_stiffness() * (cmax[i] - c)))
            cdot = (vs[i - 1] if i > 0 else 0.0) - vs[i]
            kd = L.unload_stiffness() if (L.crushable and c < cmax[i]) \
                else L.damping_stiffness(c)
            fd = 2.0 * L.damping_ratio * math.sqrt(kd * min(m_above[i], m[i] * 4)) * cdot
            f[i] = max(0.0, fl + fd)
            if update and L.crushable and c > cmax[i]:
                cmax[i] = c
        return f

    def accel(f):
        return [(f[i] - (f[i + 1] if i + 1 < n else 0.0)) / m[i] - g for i in range(n)]

    t = 0.0
    f = forces(x, v, True)
    a = accel(f)
    ts, gs, fl0 = [], [], []
    peak_g, peak_t = 0.0, 0.0
    touched_end = None
    steps = 0
    prev_c = [comp(i, x) for i in range(n)]
    while t < t_max_s and steps < max_steps:
        steps += 1
        kmax = max(L.stiffness(max(comp(i, x), 0.0)) / min(m[i], m[i - 1] if i else m[i])
                   for i, L in enumerate(layers))
        dt = min(2.0 * math.pi / math.sqrt(kmax) / 40.0, 1e-4)
        x = [x[i] + v[i] * dt + 0.5 * a[i] * dt * dt for i in range(n)]
        vh = [v[i] + 0.5 * a[i] * dt for i in range(n)]
        f = forces(x, vh, True)
        a_new = accel(f)
        v = [vh[i] + 0.5 * a_new[i] * dt for i in range(n)]
        a = a_new
        t += dt
        for i in range(n):
            c = comp(i, x)
            dc = c - prev_c[i]
            absorbed[i] += f[i] * dc       # net work on the layer = what it keeps at the end
            prev_c[i] = c
            fpeak[i] = max(fpeak[i], f[i])
            comp_peak[i] = max(comp_peak[i], c)
        felt = (f[pi] - (f[pi + 1] if pi + 1 < n else 0.0)) / (m[pi] * G)
        if felt > peak_g:
            peak_g, peak_t = felt, t
        ts.append(t)
        gs.append(felt)
        fl0.append(f[0])
        vcom = sum(m[i] * v[i] for i in range(n)) / sum(m)
        if f[0] <= 0 and vcom > 0 and all(vi > -1e-3 for vi in v) and t > dt * 10:
            touched_end = t
            break
        ke = 0.5 * sum(m[i] * v[i] ** 2 for i in range(n))
        weight = sum(m) * g
        if (ke < 1e-6 * ke0 and abs(vcom) < 1e-3 and t > dt * 50
                and abs(f[0] - weight) < 0.1 * weight + 1e-9):   # settled on the floor
            touched_end = t
            break
    if touched_end is None:
        notes.append(f"stopped at {t:.3g} s before the build came to rest or left the floor")
    vcom = sum(m[i] * v[i] for i in range(n)) / sum(m)
    rebound = max(vcom, 0.0) if f[0] <= 0 else 0.0
    # energy each layer kept (hysteresis + damping): the work done on it less what it gave back
    names = [L.name for L in layers]
    k = max(1, len(ts) // trace_points)
    bottomed = [L.name for i, L in enumerate(layers)
                if L.cushion is not None
                and comp_peak[i] / L.thickness_m > L.cushion.densification_strain]
    if bottomed:
        notes.append("bottomed out (crushed past densification, the force climbs steeply): "
                     + ", ".join(bottomed))
    return LumpedResult(
        impact_velocity_m_s=velocity_m_s, payload=names[pi], peak_g=peak_g,
        peak_g_time_s=peak_t, rebound_velocity_m_s=rebound,
        restitution=rebound / velocity_m_s if velocity_m_s > 0 else 0.0,
        max_compression_m=dict(zip(names, comp_peak)),
        max_strain={L.name: comp_peak[i] / L.thickness_m for i, L in enumerate(layers)},
        bottomed_out=bottomed,
        energy_absorbed_j={nm: max(0.0, absorbed[i]) for i, nm in enumerate(names)},
        energy_in_j=ke0 + sum(m) * g * max(comp_peak[0], 0.0),
        peak_force_n=dict(zip(names, fpeak)),
        peak_stress_mpa={L.name: fpeak[i] / L.area_m2 / 1e6 for i, L in enumerate(layers)},
        contact_time_s=(touched_end if touched_end is not None else t),
        trace={"t_s": ts[::k], "payload_g": gs[::k], "floor_force_n": fl0[::k]},
        notes=notes)


# ---------------------------------------------------------------- the whole study

FIDELITY = {
    "impact_lumped": "ESTIMATE: 1D lumped transient (masses on crushable / elastic layers, "
                     "compression-only contact). Validated against the analytic spring-mass "
                     "impact; foam values are typical handbook numbers unless measured.",
    "impact_fem": "SIMULATED: CalculiX *DYNAMIC (implicit, NLGEOM) of the stack as hex blocks "
                  "with penalty contact on a rigid floor; coarse mesh, block geometry (not the "
                  "drawn CAD shape), foam from typical values. A guide, not a drop test.",
}

ASSUMPTIONS = [
    "vertical drop onto a rigid, flat floor, landing on the bottom layer (no tumble or tilt)",
    "each part is a block of its footprint area x its height along the drop; the drawn shape "
    "(ribs, cones, legs) only enters through these numbers",
    "contact between parts is compression only (an unbonded stack): parts can separate",
    "foam / cushion properties are typical values at a quasi-static rate; real foams stiffen "
    "with rate and vary with density, so calibrate with a crush test where the margin is thin",
    "the payload is a rigid mass: the reported g is what an accelerometer on it would read",
]


def check_g(peak_g: float, limit_g: float | None) -> dict | None:
    if limit_g is None:
        return None
    margin = limit_g / peak_g if peak_g > 0 else math.inf
    return {"limit_g": limit_g, "peak_g": peak_g, "margin": margin,
            "verdict": "pass" if peak_g <= limit_g else "fail"}


def lumped_dict(r: LumpedResult) -> dict:
    d = dict(r.__dict__)
    d["energy_absorbed_share"] = {k: (v / r.energy_in_j if r.energy_in_j else 0.0)
                                  for k, v in r.energy_absorbed_j.items()}
    return d
