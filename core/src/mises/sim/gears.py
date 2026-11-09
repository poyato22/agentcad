"""Gear tooth strength (GEARS, sim-gaps 2026-11-09 "Gear tooth strength and wear"): an ANALYTIC
ESTIMATE of a spur / helical gear pair, a compound train or a simple planetary (each mesh
checked on its own), from module, tooth counts, face width, pressure angle, torque (or power and
rpm) and material.

Method (AGMA 2001-style, as written up in Shigley's Mechanical Engineering Design, ch. 14):

- bending: sigma_F = W_t K_o K_v K_s K_m K_B / (b m J)   (Lewis with AGMA factors, J the AGMA
  geometry factor at the highest point of single-tooth contact);
- contact (Hertz): sigma_H = Z_E sqrt(W_t K_o K_v K_s K_m / (d_1 b I)),
  I = cos(phi) sin(phi) / (2 m_N) * m_G / (m_G +- 1)   (+ external, - internal);
- safety factors S_F = S_t Y_N / (K_R sigma_F), S_H = S_c Z_N / (K_R sigma_H); compare S_F with
  S_H**2 (Shigley 14-5) to say which mode governs;
- contact ratio from the line of action; efficiency from the classical sliding-loss estimate
  eta = 1 - pi mu (1/z1 +- 1/z2); backlash from the AGMA 2002 / ISO recommendation
  j_bn,min = 2/3 (0.06 + 0.0005 a + 0.03 m) mm, widened for printed gears.

Limits (also returned as caveats): no FEA of the tooth, no profile shift (undercut is flagged,
not modelled in J), no rim (K_B = 1), no tooth temperature model for plastics, plastic wear is
abrasive rather than pitting (the contact check is a proxy), and printed-plastic allowables are
conservative knock-downs of data-sheet strengths, not measured gear S-N curves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from ..mech.sizing import lewis_y

FIDELITY = "analytic estimate"
FIDELITY_LABEL = ("analytic estimate (AGMA 2001-style Lewis bending + Hertz contact, Shigley "
                  "ch. 14): not FEA, not a test")
METHOD = "AGMA 2001-style (Shigley ch. 14): Lewis bending with AGMA factors, Hertz contact"
FT_MIN_PER_M_S = 196.850394       # 1 m/s in ft/min (the AGMA K_v formula is in ft/min)


# ---- materials ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GearMaterial:
    name: str
    kind: str                   # "metal" | "plastic"
    e_mpa: float
    nu: float
    bend_allow_mpa: float       # S_t: allowable bending at `rated_cycles`, reliability 0.99
    contact_allow_mpa: float    # S_c: allowable contact at `rated_cycles`, reliability 0.99
    mu_dry: float               # sliding friction for the efficiency estimate
    mu_lube: float
    max_temp_c: float | None
    rated_cycles: float
    source: str
    hb: float | None = None     # metals: Brinell hardness the AGMA grade-1 formulas used
    notes: tuple[str, ...] = field(default_factory=tuple)
    tensile_mpa: float | None = None   # plastics: the strength a one-off peak is checked against


def agma_grade1_steel(hb: float) -> tuple[float, float]:
    """(S_t, S_c) MPa of through-hardened grade-1 steel at 1e7 cycles, R = 0.99: S_t = 0.533 HB
    + 88.3, S_c = 2.22 HB + 200 (AGMA 2001; Shigley Figs. 14-2 and 14-5, SI forms)."""
    return 0.533 * hb + 88.3, 2.22 * hb + 200.0


# Printed-plastic allowables. There are no published gear S-N curves for FDM PLA / PETG / ASA /
# PA / PA-CF, so each allowable is a conservative knock-down of the PRINTED tensile strength
# from the manufacturer's data sheet (the same values as sim/materials.py):
#   bending  S_t = 0.25 x sigma_t  (0.5 for fatigue at ~1e6 cycles, the existing printed-gear rule
#                                   in mech/sizing.gear_safety, x 0.5 for layer lines and
#                                   porosity at the tooth root, which is where printed teeth crack)
#   contact  S_c = 0.60 x sigma_t  (of the order of VDI 2736-2's surface limits for molded POM /
#                                   PA66, which run at ~0.5-0.8 of their tensile strength)
# Dry friction coefficients are typical polymer-on-polymer pin-on-disc ranges (PLA ~0.3-0.4,
# nylon ~0.2-0.3, POM ~0.15-0.25); grease roughly halves them. Treat all of these as design-guide
# style estimates and calibrate with a printed test gear when the margin is thin.
_K_BEND, _K_CONTACT = 0.25, 0.60
_PLASTIC_RATED_CYCLES = 1e6


def _printed(name: str, sigma_t: float, e: float, nu: float, mu: float, t_max: float,
             tds: str, *notes: str) -> GearMaterial:
    return GearMaterial(
        name, "plastic", e, nu, round(_K_BEND * sigma_t, 1), round(_K_CONTACT * sigma_t, 1),
        mu, round(mu / 2, 3), t_max, _PLASTIC_RATED_CYCLES,
        f"printed: S_t = 0.25 x, S_c = 0.60 x the printed tensile strength {sigma_t:g} MPa "
        f"({tds}); conservative knock-down, not gear S-N data", None, notes, sigma_t)


def _steel(name: str, hb: float, e: float, nu: float, src: str) -> GearMaterial:
    st, sc = agma_grade1_steel(hb)
    return GearMaterial(name, "metal", e, nu, round(st, 1), round(sc, 1), 0.15, 0.06, None, 1e7,
                        f"AGMA 2001 grade-1 through-hardened steel at {hb:g} HB ({src}): "
                        "S_t = 0.533 HB + 88.3, S_c = 2.22 HB + 200 MPa (Shigley Figs. 14-2, "
                        "14-5)", hb)


GEAR_MATERIALS: dict[str, GearMaterial] = {m.name: m for m in [
    _printed("PLA", 51.0, 2300.0, 0.35, 0.35, 50.0, "Prusament PLA TDS v1.1, XY",
             "brittle and creeps under steady torque; softens above ~50 C"),
    _printed("PETG", 47.0, 1500.0, 0.38, 0.35, 65.0, "Prusament PETG TDS v1.1, XY"),
    _printed("ASA", 40.0, 2000.0, 0.35, 0.30, 85.0, "generic FDM ASA TDS, low end"),
    _printed("PA12", 48.0, 1800.0, 0.39, 0.25, 80.0, "printed PA12 TDS (materials.py)",
             "nylon swells with humidity: allow extra backlash"),
    _printed("PA-CF", 64.0, 3860.0, 0.35, 0.25, 150.0, "Bambu PAHT-CF TDS v2, Z strength",
             "carbon fibre abrades a soft mating gear; pair it with PA-CF or metal"),
    _printed("PA6-CF", 48.0, 4430.0, 0.35, 0.25, 170.0, "PA6-CF TDS (materials.py), Z strength",
             "carbon fibre abrades a soft mating gear; pair it with PA-CF or metal"),
    _printed("POM (printed)", 50.0, 2800.0, 0.35, 0.20, 100.0,
             "printed POM, typical TDS ~50 MPa (approximate)",
             "POM warps and bonds poorly between layers: check the print first"),
    # machined / molded acetal: the DuPont Delrin gear design guide plots allowable bending
    # stresses of roughly 30-50 MPa for long continuous running (lower unlubricated); 25 MPa
    # bending and 40 MPa contact are taken as conservative values (approximate, not
    # re-verified here; VDI 2736-2 gives measured curves for molded POM if you have them)
    GearMaterial("POM", "plastic", 3100.0, 0.35, 25.0, 40.0, 0.20, 0.10, 100.0, 1e6,
                 "machined / molded POM: conservative values from the DuPont Delrin gear "
                 "design guide range (~30-50 MPa bending, continuous); approximate",
                 tensile_mpa=72.0),
    _steel("Steel 1018", 126.0, 205000.0, 0.29, "as-rolled"),
    _steel("Steel 4140", 197.0, 205000.0, 0.29, "annealed"),
    _steel("Steel 4140 QT", 285.0, 205000.0, 0.29, "quenched and tempered"),
    # brass is not in AGMA 2001: the tin-bronze row of Shigley Tables 14-3 / 14-6 (S_t 5.7 kpsi,
    # S_c 30 kpsi) is used for free-cutting brass as a conservative stand-in
    GearMaterial("Brass C360", "metal", 97000.0, 0.31, 39.3, 206.8, 0.20, 0.08, None, 1e7,
                 "AGMA tin-bronze allowables (Shigley Tables 14-3, 14-6: S_t 5.7 kpsi, S_c "
                 "30 kpsi) used for C360 brass, conservative stand-in"),
    # aluminium is not an AGMA gear material: bending ~0.57 x the rotating-bending fatigue
    # strength at 5e8 cycles (ASM / Aluminum Association: 6061-T6 96.5 MPa, 7075-T6 159 MPa);
    # contact is a rough low value because bare aluminium flanks gall and wear (hard-anodize)
    GearMaterial("Al 6061-T6", "metal", 68900.0, 0.33, 55.0, 150.0, 0.30, 0.08, None, 1e7,
                 "~0.57 x the 96.5 MPa fatigue strength (ASM, 5e8 cycles); contact rough, "
                 "aluminium flanks gall: approximate",
                 None, ("bare aluminium teeth wear fast: hard-anodize or mate with steel",)),
    GearMaterial("Al 7075-T6", "metal", 71700.0, 0.33, 90.0, 250.0, 0.30, 0.08, None, 1e7,
                 "~0.57 x the 159 MPa fatigue strength (ASM, 5e8 cycles); contact rough: "
                 "approximate", None,
                 ("bare aluminium teeth wear fast: hard-anodize or mate with steel",)),
]}

_ALIASES = {"pa": "PA12", "nylon": "PA12", "pa6": "PA12", "pa66": "PA12", "pacf": "PA-CF",
            "pa-cf": "PA-CF", "paht-cf": "PA-CF", "delrin": "POM", "acetal": "POM",
            "pom printed": "POM (printed)", "printed pom": "POM (printed)",
            "steel": "Steel 4140", "4140": "Steel 4140", "1018": "Steel 1018",
            "brass": "Brass C360", "aluminium": "Al 6061-T6", "aluminum": "Al 6061-T6",
            "6061": "Al 6061-T6", "7075": "Al 7075-T6"}


def gear_material(name: str, custom: dict[str, GearMaterial] | None = None,
                  hardness_hb: float | None = None) -> GearMaterial:
    """A gear material by name (case-insensitive, with aliases), or a custom one. hardness_hb
    re-derives a steel's AGMA allowables at that hardness."""
    pool = {**GEAR_MATERIALS, **(custom or {})}
    key = name.strip()
    hit = next((m for n, m in pool.items() if n.lower() == key.lower()), None)
    if hit is None:
        alias = _ALIASES.get(key.lower().replace("_", "-"))
        hit = pool.get(alias) if alias else None
    if hit is None:
        raise KeyError(f"unknown gear material {name!r}; known: {', '.join(pool)}")
    if hardness_hb and hit.kind == "metal" and hit.name.lower().startswith("steel"):
        st, sc = agma_grade1_steel(hardness_hb)
        hit = replace(hit, bend_allow_mpa=round(st, 1), contact_allow_mpa=round(sc, 1),
                      hb=hardness_hb, source=hit.source.split(" at ")[0]
                      + f" at {hardness_hb:g} HB (given)")
    return hit


# ---- factors ---------------------------------------------------------------------------

# AGMA geometry factor J, 20 deg full depth, standard addendum, load at the highest point of
# single-tooth contact: rows = the gear's own teeth, columns = the mating gear's teeth. Values
# follow the AGMA 908-B89 tables as reproduced in Norton, Machine Design (Table 12-9) and
# Shigley Fig. 14-6 (the 17-tooth row is anchored on Shigley Ex. 14-4: J = 0.30 for 17T with
# 52T, 0.40 for the 52T gear). Transcribed approximately: within ~0.01 of the source.
_J_Z = (17, 21, 26, 35, 55, 135)
_J = {17: (0.29, 0.29, 0.29, 0.30, 0.30, 0.31),
      21: (0.33, 0.33, 0.33, 0.34, 0.34, 0.35),
      26: (0.35, 0.35, 0.35, 0.36, 0.37, 0.38),
      35: (0.37, 0.37, 0.38, 0.39, 0.40, 0.41),
      55: (0.40, 0.40, 0.41, 0.42, 0.43, 0.44),
      135: (0.43, 0.43, 0.44, 0.45, 0.47, 0.49)}


def _interp_log(zs: tuple[float, ...], ys: tuple[float, ...], z: float) -> float:
    z = min(max(z, zs[0]), zs[-1])
    for (z0, y0), (z1, y1) in zip(zip(zs, ys), zip(zs[1:], ys[1:])):
        if z0 <= z <= z1:
            t = (math.log(z) - math.log(z0)) / (math.log(z1) - math.log(z0))
            return y0 + t * (y1 - y0)
    return ys[-1]


def geometry_factor_j(z: float, z_mate: float) -> float:
    """AGMA bending geometry factor J for a 20 deg full-depth tooth (log-bilinear in the
    table above). Below 17 teeth (undercut) J is scaled down with the Lewis form factor: an
    estimate; above 135 teeth (and for internal teeth) the 135 row is used, which is
    conservative."""
    col = tuple(_interp_log(_J_Z, _J[r], z_mate) for r in _J_Z)
    if z >= 17:
        return _interp_log(_J_Z, col, z)
    return col[0] * lewis_y(max(12, int(z))) / lewis_y(17)


def kv_agma(v_m_s: float, qv: float) -> float:
    """AGMA dynamic factor (Shigley eq. 14-27): ((A + sqrt(V))/A)^B, V in ft/min,
    B = 0.25 (12 - Qv)^(2/3), A = 50 + 56 (1 - B); Qv clamped to 6..11 (the formula's range)."""
    q = min(11.0, max(6.0, qv))
    b = 0.25 * (12 - q) ** (2 / 3)
    a = 50 + 56 * (1 - b)
    return ((a + math.sqrt(max(0.0, v_m_s) * FT_MIN_PER_M_S)) / a) ** b


def kv_barth(v_m_s: float, profile: str = "cast") -> float:
    """Barth dynamic factor (Shigley eq. 14-4, SI): cast profile (3.05 + V)/3.05, cut or milled
    (6.1 + V)/6.1. Printed teeth are treated as cast (poor profile and spacing accuracy)."""
    c = 3.05 if profile == "cast" else 6.1
    return (c + max(0.0, v_m_s)) / c


def size_factor_ks(b_mm: float, m_mm: float, z: float) -> float:
    """AGMA size factor (Shigley eq. (a), sec. 14-10): 1.192 (F sqrt(Y) / P)^0.0535, F and 1/P
    in inches; never below 1."""
    return max(1.0, 1.192 * (b_mm * m_mm * math.sqrt(lewis_y(max(12, int(z)))) / 645.16)
               ** 0.0535)


_CMA = {"open": (0.247, 0.0167, -0.765e-4), "commercial": (0.127, 0.0158, -0.930e-4),
        "precision": (0.0675, 0.0128, -0.926e-4), "extra_precision": (0.00360, 0.0102,
                                                                       -0.822e-4)}


def load_distribution_km(b_mm: float, d_pinion_mm: float, mounting: str = "open",
                         overhung: bool = False, crowned: bool = False) -> dict:
    """AGMA load-distribution factor K_m = 1 + C_mc (C_pf C_pm + C_ma C_e) (Shigley eqs.
    14-30..14-35), face width and pitch diameter in inches inside."""
    f, d = b_mm / 25.4, d_pinion_mm / 25.4
    ratio = max(0.05, f / (10 * d))
    cpf = ratio - 0.025 if f <= 1 else ratio - 0.0375 + 0.0125 * f
    a, bb, c = _CMA[mounting]
    cma = a + bb * f + c * f * f
    cmc = 0.8 if crowned else 1.0
    cpm = 1.1 if overhung else 1.0
    return {"km": 1 + cmc * (cpf * cpm + cma), "cpf": cpf, "cma": cma, "cmc": cmc, "cpm": cpm}


_KR = ((0.5, 0.70), (0.9, 0.85), (0.99, 1.00), (0.999, 1.25), (0.9999, 1.50))


def reliability_kr(r: float) -> float:
    """AGMA reliability factor (Shigley Table 14-10), interpolated on log(1 - R)."""
    r = min(max(r, 0.5), 0.9999)
    xs = [math.log(1 - p) for p, _ in _KR]
    x = math.log(1 - r)
    for (x0, (_, k0)), (x1, (_, k1)) in zip(zip(xs, _KR), zip(xs[1:], _KR[1:])):
        if x1 <= x <= x0:
            return k0 + (k1 - k0) * (x - x0) / (x1 - x0)
    return _KR[-1][1]


OVERLOAD = {"uniform": 1.0, "light_shock": 1.25, "moderate_shock": 1.5, "heavy_shock": 1.75}


def _yn(n: float) -> float:
    """Steel bending life factor (Shigley Fig. 14-14): 1.3558 N^-0.0178 above 1e7 cycles,
    2.3194 N^-0.0538 (the 250 HB curve) below."""
    return 1.3558 * n ** -0.0178 if n >= 1e7 else 2.3194 * n ** -0.0538


def _zn(n: float) -> float:
    """Steel pitting life factor (Shigley Fig. 14-15): 1.4488 N^-0.023 above 1e7, 2.466 N^-0.056
    below."""
    return 1.4488 * n ** -0.023 if n >= 1e7 else 2.466 * n ** -0.056


def _cycles_for(y_needed: float, upper: tuple[float, float], lower: tuple[float, float]) -> float:
    """Cycles at which a life factor curve falls to y_needed (the stress / allowable ratio)."""
    n = (upper[0] / y_needed) ** (1 / upper[1])
    if n >= 1e7:
        return n
    return (lower[0] / y_needed) ** (1 / lower[1])


def elastic_coefficient(m1: GearMaterial, m2: GearMaterial) -> float:
    """Z_E = sqrt(1 / (pi ((1 - nu1^2)/E1 + (1 - nu2^2)/E2))), sqrt(MPa) (Shigley eq. 14-13)."""
    return math.sqrt(1 / (math.pi * ((1 - m1.nu ** 2) / m1.e_mpa + (1 - m2.nu ** 2) / m2.e_mpa)))


def lewis_bending_mpa(wt_n: float, b_mm: float, m_mm: float, y: float, kv: float = 1.0) -> float:
    """Plain Lewis equation sigma = K_v W_t / (b m Y) (Shigley eq. 14-8, SI)."""
    return kv * wt_n / (b_mm * m_mm * y)


def hertz_contact_mpa(wt_n: float, b_mm: float, phi_deg: float, d1_mm: float, d2_mm: float,
                      ze: float, k: float = 1.0, internal: bool = False) -> float:
    """Hertz flank stress from the radii of curvature at the pitch point (Shigley eq. 14-14):
    sigma = Z_E sqrt(K W_t / (b cos phi) (1/r1 +- 1/r2)), r = d sin(phi) / 2."""
    phi = math.radians(phi_deg)
    r1, r2 = d1_mm * math.sin(phi) / 2, d2_mm * math.sin(phi) / 2
    curv = 1 / r1 - 1 / r2 if internal else 1 / r1 + 1 / r2
    return ze * math.sqrt(k * wt_n / (b_mm * math.cos(phi)) * curv)


def contact_ratio(z1: int, z2: int, m_n: float, phi_n_deg: float = 20.0,
                  helix_deg: float = 0.0, b_mm: float = 0.0, internal: bool = False) -> dict:
    """Transverse (profile) contact ratio from the line of action, standard addendum m_n; for a
    helical pair also the face (overlap) ratio b tan(psi) / (pi m_n). z2 is the ring when
    internal."""
    psi = math.radians(helix_deg)
    phit = math.atan(math.tan(math.radians(phi_n_deg)) / math.cos(psi))
    mt = m_n / math.cos(psi)
    r1, r2 = mt * z1 / 2, mt * z2 / 2
    rb1, rb2 = r1 * math.cos(phit), r2 * math.cos(phit)
    ra1 = r1 + m_n
    if internal:
        ra2 = r2 - m_n
        la = math.sqrt(ra1 ** 2 - rb1 ** 2) - math.sqrt(max(ra2 ** 2 - rb2 ** 2, 0.0)) \
            + (r2 - r1) * math.sin(phit)
    else:
        ra2 = r2 + m_n
        la = math.sqrt(ra1 ** 2 - rb1 ** 2) + math.sqrt(ra2 ** 2 - rb2 ** 2) \
            - (r1 + r2) * math.sin(phit)
    eps_a = la / (math.pi * mt * math.cos(phit))
    eps_b = b_mm * math.tan(psi) / (math.pi * m_n) if helix_deg else 0.0
    return {"profile": eps_a, "face": eps_b, "total": eps_a + eps_b, "line_of_action_mm": la}


def undercut_min_teeth(phi_n_deg: float = 20.0) -> float:
    """Fewest teeth cut by a rack without undercut, standard addendum: 2 / sin^2(phi)."""
    return 2 / math.sin(math.radians(phi_n_deg)) ** 2


def backlash_mm(center_mm: float, m_n: float, printed: bool, phi_n_deg: float = 20.0) -> dict:
    """Recommended minimum normal backlash, AGMA 2002 / ISO practice (as tabulated in the KHK
    gear technical reference): j_bn,min = 2/3 (0.06 + 0.0005 a + 0.03 m_n) mm. FDM printed
    gears get at least 0.15 mm (typical printer flank tolerance +-0.1 mm per side, less for a
    tuned printer). The centre distance opens by j / (2 sin phi) to get it."""
    j = 2 / 3 * (0.06 + 0.0005 * center_mm + 0.03 * m_n)
    rec = max(j, 0.15) if printed else j
    return {"min_normal_backlash_mm": round(j, 3), "recommended_mm": round(rec, 3),
            "center_distance_increase_mm": round(rec / (2 * math.sin(math.radians(phi_n_deg))),
                                                 3),
            "basis": "AGMA 2002 / ISO: 2/3 (0.06 + 0.0005 a + 0.03 m) mm"
                     + ("; printed: at least 0.15 mm for FDM tolerance" if printed else "")}


# ---- one mesh ----------------------------------------------------------------------------

@dataclass
class Member:
    """One gear in a mesh."""
    name: str
    z: int
    material: GearMaterial
    rpm: float | None = None          # absolute (or carrier-relative, for a planetary member)
    contacts_per_rev: float = 1.0     # tooth loadings per revolution (planetary sun / ring: n)
    reversed_bending: bool = False    # an idler or planet: both flanks loaded, S_t x 0.7
    internal: bool = False            # a ring gear (internal teeth)


@dataclass
class MeshOptions:
    pressure_angle_deg: float = 20.0
    helix_angle_deg: float = 0.0
    overload: float = 1.0
    qv: float | None = None
    mounting: str = "open"
    overhung: bool = False
    reliability: float = 0.99
    life_hours: float | None = None
    lubrication: str = "auto"
    target_sf: float = 1.5


def _mu(a: GearMaterial, b: GearMaterial, lube: str) -> tuple[float, str]:
    if lube == "auto":
        lube = "oil" if a.kind == b.kind == "metal" else "dry"
    use_lube = lube in ("grease", "oil")
    mu = (a.mu_lube + b.mu_lube) / 2 if use_lube else (a.mu_dry + b.mu_dry) / 2
    return mu, lube


def check_mesh(drv: Member, drn: Member, torque_driver_nm: float, m_n: float, b_mm: float,
               opt: MeshOptions, *, load_sharing: float = 1.0, paths: int = 1,
               rel_rpm_driver: float | None = None,
               j_override: dict[str, float] | None = None) -> dict:
    """Bending and contact of one mesh. `paths` splits the driver's torque (planets in
    parallel), `load_sharing` is the planetary mesh-load factor; rel_rpm_driver is the driver's
    speed relative to the carrier (the speed that sets pitch-line velocity). The smaller gear is
    the pinion for the AGMA I factor and the K_m pitch diameter."""
    phi_n = math.radians(opt.pressure_angle_deg)
    psi = math.radians(opt.helix_angle_deg)
    phit = math.atan(math.tan(phi_n) / math.cos(psi))
    mt = m_n / math.cos(psi)
    internal = drn.internal or drv.internal
    d = {g.name: mt * g.z for g in (drv, drn)}
    wt = 2 * torque_driver_nm * 1000.0 / d[drv.name] / paths * load_sharing
    rpm = rel_rpm_driver if rel_rpm_driver is not None else drv.rpm
    v = None if rpm is None else math.pi * d[drv.name] * abs(rpm) / 60000.0
    printed = any(g.material.kind == "plastic" for g in (drv, drn))
    if v is None:
        kv, kv_basis = 1.0, "no speed given: quasi-static (K_v = 1)"
    elif opt.qv is not None:
        kv, kv_basis = kv_agma(v, opt.qv), f"AGMA K_v, Qv {opt.qv:g}"
    elif printed:
        kv, kv_basis = kv_barth(v, "cast"), "Barth cast-profile K_v (printed / molded teeth)"
    else:
        kv, kv_basis = kv_agma(v, 7), "AGMA K_v, Qv 7 (assumed)"
    small, large = sorted((drv, drn), key=lambda g: (g.internal, g.z))
    km = load_distribution_km(b_mm, d[small.name], opt.mounting, opt.overhung)
    kr = reliability_kr(opt.reliability)
    cr = contact_ratio(small.z, large.z, m_n, opt.pressure_angle_deg, opt.helix_angle_deg, b_mm,
                       internal=internal)
    # helical: the spur J at the virtual tooth count z / cos^3(psi) and the NORMAL module, no
    # extra helical load sharing (conservative); K_s uses the pinion's Lewis Y
    zv = {g.name: g.z / math.cos(psi) ** 3 for g in (drv, drn)}
    ks_p = size_factor_ks(b_mm, m_n, zv[small.name])
    common = wt * opt.overload * kv * km["km"]
    gears = {}
    for g, mate in ((drv, drn), (drn, drv)):
        j = (j_override or {}).get(g.name)
        if j is None:
            j = geometry_factor_j(135 if g.internal else zv[g.name],
                                  135 if mate.internal else zv[mate.name])
        ks = size_factor_ks(b_mm, m_n, zv[g.name])
        sigma_f = common * ks / (b_mm * m_n * j)
        cycles = None
        if g.rpm is not None and opt.life_hours:
            cycles = abs(g.rpm) * 60 * opt.life_hours * g.contacts_per_rev
        gears[g.name] = {"member": g, "j": j, "ks": ks, "sigma_f": sigma_f, "cycles": cycles}
    # Hertz contact (Shigley eq. 14-16): one stress for both flanks
    ze = elastic_coefficient(drv.material, drn.material)
    m_g = large.z / small.z
    m_n_share = 1.0 if not opt.helix_angle_deg else 1 / (0.95 * max(cr["profile"], 1.0))
    i_fac = (math.cos(phit) * math.sin(phit) / (2 * m_n_share)
             * (m_g / (m_g - 1) if internal else m_g / (m_g + 1)))
    sigma_h = ze * math.sqrt(common * ks_p / (d[small.name] * b_mm * i_fac))
    out_gears = []
    for name, gd in gears.items():
        g: Member = gd["member"]
        mat = g.material
        n = gd["cycles"] or mat.rated_cycles
        st = mat.bend_allow_mpa * (0.7 if g.reversed_bending else 1.0)
        if mat.kind == "metal":
            yn, zn = _yn(n), _zn(n)
        else:
            yn = zn = 1.0     # no S-N data for printed plastics: allowables are at rated_cycles
        sf = st * yn / (kr * gd["sigma_f"])
        sh = mat.contact_allow_mpa * zn / (kr * sigma_h)
        mode = "bending" if sf <= sh ** 2 else "contact"
        life = _life(mat, st, gd["sigma_f"], sigma_h, kr, g, sf, sh)
        out_gears.append({
            "gear": name, "teeth": g.z, "material": mat.name, "internal": g.internal,
            "pitch_diameter_mm": round(d[name], 3), "rpm": None if g.rpm is None else
            round(g.rpm, 3), "geometry_factor_j": round(gd["j"], 3),
            "size_factor_ks": round(gd["ks"], 3),
            "bending_stress_mpa": round(gd["sigma_f"], 2),
            "bending_allowable_mpa": round(st, 2),
            "contact_stress_mpa": round(sigma_h, 2),
            "contact_allowable_mpa": round(mat.contact_allow_mpa, 2),
            "life_factors": {"y_n": round(yn, 4), "z_n": round(zn, 4), "cycles": n},
            "bending_sf": round(sf, 3), "contact_sf": round(sh, 3),
            "contact_sf_squared": round(sh ** 2, 3), "governing": mode,
            "min_sf": round(min(sf, sh ** 2), 3), "life": life,
            # a one-off peak (a stall, a jam) against the plain tensile strength: whether a
            # tooth breaks at once, not whether it lasts
            "static_bending_sf": None if not mat.tensile_mpa else
            round(mat.tensile_mpa / gd["sigma_f"], 3),
            "reversed_bending": g.reversed_bending})
    mu, lube = _mu(drv.material, drn.material, opt.lubrication)
    eta = 1 - math.pi * mu * (1 / small.z - 1 / large.z if internal else
                               1 / small.z + 1 / large.z)
    center = mt * (large.z - small.z) / 2 if internal else mt * (small.z + large.z) / 2
    warnings = _mesh_warnings(small, large, opt, cr, v, printed, internal)
    return {
        "mesh": f"{drv.name} -> {drn.name}", "driver": drv.name, "driven": drn.name,
        "internal": internal, "module_mm": m_n, "face_width_mm": b_mm,
        "pressure_angle_deg": opt.pressure_angle_deg, "helix_angle_deg": opt.helix_angle_deg,
        "transverse_pressure_angle_deg": round(math.degrees(phit), 3),
        "center_distance_mm": round(center, 3),
        "driver_torque_nm": round(torque_driver_nm, 5), "load_paths": paths,
        "load_sharing_factor": load_sharing,
        "tangential_load_n": round(wt, 2), "radial_load_n": round(wt * math.tan(phit), 2),
        "axial_load_n": round(wt * math.tan(psi), 2),
        "pitch_line_velocity_m_s": None if v is None else round(v, 4),
        "factors": {"overload_ko": opt.overload, "dynamic_kv": round(kv, 4),
                    "dynamic_basis": kv_basis, "load_distribution_km": round(km["km"], 4),
                    "km_terms": {k: round(x, 4) for k, x in km.items() if k != "km"},
                    "size_ks_pinion": round(ks_p, 4), "rim_kb": 1.0,
                    "reliability_kr": round(kr, 4), "elastic_ze_sqrt_mpa": round(ze, 2),
                    "contact_geometry_i": round(i_fac, 5)},
        "contact_ratio": {k: round(x, 3) for k, x in cr.items()},
        "efficiency": round(eta, 4), "friction_mu": round(mu, 3), "lubrication": lube,
        "backlash": backlash_mm(center, m_n, printed, opt.pressure_angle_deg),
        "gears": out_gears, "warnings": warnings}


def _life(mat: GearMaterial, st: float, sigma_f: float, sigma_h: float, kr: float, g: Member,
          sf: float, sh: float) -> dict:
    """Life class. Metals: the cycles at which the AGMA life factors bring S_F or S_H to 1
    (Shigley Figs. 14-14, 14-15). Plastics: only whether the tooth holds the rated ~1e6 cycles
    (no published S-N data for printed gears, so no number past that)."""
    per_hour = None if g.rpm is None else abs(g.rpm) * 60 * g.contacts_per_rev
    if mat.kind == "metal":
        nb = _cycles_for(sigma_f * kr / st, (1.3558, 0.0178), (2.3194, 0.0538))
        nh = _cycles_for(sigma_h * kr / mat.contact_allow_mpa, (1.4488, 0.023), (2.466, 0.056))
        n = min(nb, nh)
        cls = ("unlimited (> 1e10 cycles)" if n > 1e10 else "long (1e8-1e10 cycles)"
               if n >= 1e8 else "medium (1e6-1e8 cycles)" if n >= 1e6 else
               "short (1e4-1e6 cycles)" if n >= 1e4 else "fails almost at once (< 1e4 cycles)")
        return {"class": cls, "cycles_to_failure": float(f"{min(n, 1e12):.3g}"),
                "governing": "bending" if nb <= nh else "contact",
                "hours": None if not per_hour else float(f"{min(n, 1e12) / per_hour:.3g}"),
                "basis": "AGMA life factors Y_N, Z_N (Shigley Figs. 14-14, 14-15), steel curves"}
    rated = mat.rated_cycles
    worst = min(sf, sh)
    cls = (f"holds the rated ~{rated:.0e} cycles with margin; longer life not estimated"
           if worst >= 1.5 else f"about the rated ~{rated:.0e} cycles" if worst >= 1.0 else
           f"below the rated ~{rated:.0e} cycles: expect early tooth failure or wear")
    return {"class": cls, "cycles_to_failure": None,
            "rated_hours": None if not per_hour else float(f"{rated / per_hour:.3g}"),
            "basis": "printed / molded plastic: no gear S-N data, allowables are at ~1e6 cycles"}


def _mesh_warnings(small: Member, large: Member, opt: MeshOptions, cr: dict, v: float | None,
                   printed: bool, internal: bool) -> list[str]:
    w = []
    zmin = undercut_min_teeth(opt.pressure_angle_deg)
    cos3 = math.cos(math.radians(opt.helix_angle_deg)) ** 3
    for g in (small, large):
        zv = g.z / cos3
        if not g.internal and zv < zmin:
            x = 1 - zv * math.sin(math.radians(opt.pressure_angle_deg)) ** 2 / 2
            w.append(f"undercut: {g.name} has {g.z} teeth (virtual {zv:.1f}) < "
                     f"{zmin:.1f} at {opt.pressure_angle_deg:g} deg; the root is weakened "
                     f"(J estimated); a profile shift x >= {x:.2f} or more teeth avoids it")
    if internal and large.z - small.z < 12:
        w.append(f"ring - planet = {large.z - small.z} teeth < 12: internal tip interference "
                 "risk without a profile shift")
    if cr["profile"] < 1.0:
        w.append(f"profile contact ratio {cr['profile']:.2f} < 1: the teeth lose contact")
    elif cr["total"] < 1.2:
        w.append(f"contact ratio {cr['total']:.2f} < 1.2: rough, noisy running")
    if printed and v is not None and v > 2.0:
        w.append(f"pitch-line speed {v:.1f} m/s on plastic teeth: dry plastic gears heat up "
                 "above ~2 m/s and soften (tooth temperature is not modelled); grease them or "
                 "check the temperature")
    for g in (small, large):
        for note in g.material.notes:
            if note not in w:
                w.append(f"{g.material.name}: {note}")
    return w


# ---- assemblies --------------------------------------------------------------------------

PLANET_LOAD_SHARING = {1: 1.0, 2: 1.0, 3: 1.16, 4: 1.25, 5: 1.35, 6: 1.44, 7: 1.47, 8: 1.5}
# mesh-load factor K_gamma by planet count (AGMA 6123-B06 table, as recalled; approximate)


def _torque(torque_nm: float | None, power_w: float | None, rpm: float | None) -> float:
    if torque_nm is not None:
        return torque_nm
    if power_w is None or not rpm:
        raise ValueError("give torque_nm, or power_w with rpm")
    return power_w / (rpm * 2 * math.pi / 60)


def pair(z1: int, z2: int, m: float, b: float, mat1: GearMaterial, mat2: GearMaterial,
         torque_nm: float, rpm: float | None, opt: MeshOptions, **kw) -> list[dict]:
    g1 = Member("gear 1", z1, mat1, rpm)
    g2 = Member("gear 2", z2, mat2, None if rpm is None else rpm * z1 / z2)
    return [check_mesh(g1, g2, torque_nm, m, b, opt, **kw)]


def train(stages: list[dict], torque_nm: float, rpm: float | None, opt: MeshOptions) -> list[dict]:
    """A compound train: stage k's driven gear shares a shaft with stage k+1's driver. Each
    stage dict: z1, z2, module, face_width, mat1, mat2."""
    out, t, n = [], torque_nm, rpm
    for i, s in enumerate(stages, 1):
        g1 = Member(f"stage {i} driver", s["z1"], s["mat1"], n)
        n2 = None if n is None else n * s["z1"] / s["z2"]
        g2 = Member(f"stage {i} driven", s["z2"], s["mat2"], n2)
        r = check_mesh(g1, g2, t, s["module"], s["face_width"], opt)
        out.append(r)
        t, n = t * s["z2"] / s["z1"] * r["efficiency"], n2
    return out


def planetary(zs: int, zp: int, zr: int, n_planets: int, m: float, b: float,
              mat_s: GearMaterial, mat_p: GearMaterial, mat_r: GearMaterial,
              torque_sun_nm: float, rpm_sun: float | None, opt: MeshOptions,
              load_sharing: float | None = None) -> tuple[list[dict], dict]:
    """Simple planetary, ring fixed, sun in, carrier out: the sun-planet and planet-ring meshes
    checked on their own with the sun torque split over the planets (x K_gamma). Speeds are
    relative to the carrier; planets see reversed bending (S_t x 0.7, AGMA idler rule)."""
    ratio = 1 + zr / zs
    k_gamma = load_sharing or PLANET_LOAD_SHARING.get(n_planets, 1.5)
    n_c = None if rpm_sun is None else rpm_sun / ratio
    rel_s = None if rpm_sun is None else rpm_sun - n_c
    rel_p = None if rel_s is None else -rel_s * zs / zp
    sun = Member("sun", zs, mat_s, rel_s, contacts_per_rev=n_planets)
    pl = Member("planet", zp, mat_p, rel_p, reversed_bending=True)
    ring = Member("ring", zr, mat_r, None if n_c is None else n_c,
                  contacts_per_rev=n_planets, internal=True)
    m1 = check_mesh(sun, pl, torque_sun_nm, m, b, opt, load_sharing=k_gamma, paths=n_planets)
    t_planet = torque_sun_nm / n_planets * zp / zs     # each planet's share, as a torque
    m2 = check_mesh(pl, ring, t_planet, m, b, opt, load_sharing=k_gamma, rel_rpm_driver=rel_p)
    # efficiency with the carrier fixed is the two meshes in series; ring-fixed sun-in
    # carrier-out (basic ratio i0 = -zr/zs): eta = (1 + (zr/zs) eta0) / (1 + zr/zs)
    eta0 = m1["efficiency"] * m2["efficiency"]
    eta = (1 + zr / zs * eta0) / (1 + zr / zs)
    from ..mech.sizing import planetary_ok
    info = {"ratio": round(ratio, 4), "carrier_rpm": None if n_c is None else round(n_c, 3),
            "load_sharing_factor": k_gamma, "efficiency": round(eta, 4),
            "output_torque_nm": round(torque_sun_nm * ratio * eta, 4),
            "assembly_problems": planetary_ok(zs, zp, zr, n_planets)}
    return [m1, m2], info


def summarize(meshes: list[dict], target_sf: float) -> dict:
    """The weakest gear across every mesh and a verdict."""
    rows = [(g, mesh) for mesh in meshes for g in mesh["gears"]]
    g, mesh = min(rows, key=lambda r: r[0]["min_sf"])
    worst = g["min_sf"]
    verdict = ("ok" if worst >= target_sf else "marginal" if worst >= 1.0 else "fails")
    eff = 1.0
    for mesh_ in meshes:
        eff *= mesh_["efficiency"]
    return {"verdict": verdict, "target_sf": target_sf,
            "weakest": {"gear": g["gear"], "mesh": mesh["mesh"], "teeth": g["teeth"],
                        "material": g["material"], "governing": g["governing"],
                        "bending_sf": g["bending_sf"], "contact_sf": g["contact_sf"],
                        "min_sf": worst, "static_bending_sf": g.get("static_bending_sf"),
                        "life": g["life"]},
            "efficiency_estimate": round(eff, 4)}


CAVEATS = [
    "Analytic estimate (AGMA 2001-style Lewis bending with AGMA factors and Hertz contact, "
    "Shigley ch. 14): not FEA of the tooth and not a test.",
    "Safety factors are stress ratios; S_F is compared with S_H squared to say which mode "
    "governs (AGMA practice).",
    "J comes from the AGMA 908 table for 20 deg full-depth teeth (transcribed approximately); "
    "other pressure angles, profile shift, stub teeth and custom profiles are not modelled "
    "(mesh-level FEA is the next step for those). Helical J is the spur J at the virtual tooth "
    "count with the normal module (conservative).",
    "Printed-plastic allowables are conservative knock-downs of the printed tensile strength "
    "(bending 0.25x, contact 0.6x), not gear S-N data; dry plastic gears usually fail by wear "
    "and heat, which the contact check only stands in for. Calibrate with a printed test gear "
    "when the margin is thin.",
    "Life for metals uses the AGMA steel life curves; plastics only get a class at the rated "
    "~1e6 cycles. Efficiency is the sliding-loss estimate without bearings or churning; "
    "the planetary load-sharing factor and brass / aluminium allowables are approximate.",
]
