"""Built-in isotropic filament models for static studies (until the filament service exists).

Values are for PRINTED test specimens where the vendor gives them, in the weaker (horizontal /
X-Y) direction, rounded down. They are typical values, not a guarantee for any given spool:
the user's own filament data sheet wins. Strength is further multiplied by FDM_KNOCKDOWN
because printed parts are weaker across layers and at seams than the coupons suggest.

Sources:
- PLA:   Prusament PLA technical data sheet v1.1 (2022), ISO 527-1 printed specimens:
         tensile modulus 2.3 GPa, tensile yield 51 MPa (horizontal); HDT 55 C (ISO 75);
         https://cdn.shopify.com/s/files/1/1745/6181/files/PRUSA_PLA_TechSheet_ENG.pdf
- PETG:  Prusament PETG technical data sheet v1.1 (2022): modulus 1.5 GPa, yield 47 MPa
         (horizontal); HDT 68 C; https://cdn.shopify.com/s/files/1/1745/6181/files/PETG_TechSheet_ENG.pdf
- ABS:   generic FDM ABS, typical vendor TDS range (modulus 1.9-2.3 GPa, tensile 35-45 MPa,
         HDT 80-95 C); conservative low-end values chosen. Approximate.
- ASA:   generic FDM ASA, typical vendor TDS range (modulus ~2.0 GPa, tensile 40-50 MPa,
         HDT ~95 C, e.g. https://4dfiltration.com/resources/fdm-properties). Approximate.
- PA-CF: Bambu Lab PAHT-CF technical data sheet v2: Young's modulus 3860 MPa (X-Y),
         tensile strength 88 MPa (X-Y) / 64 MPa (Z), HDT 170 C at 1.8 MPa;
         https://cdn.shopify.com/s/files/1/0380/5149/files/Bambu_PAHT-CF_Technical_Data_Sheet.pdf
         Carbon-fibre nylons are strongly anisotropic; Z strength is used here, the
         conservative choice. Nylons are also hygroscopic (dry values).
Poisson's ratios are typical literature values for the unfilled polymers (0.33-0.40).
Thermal conductivity and expansion are typical literature values for the bulk polymers
(PLA ~0.13 W/m K, PETG ~0.20, ABS/ASA ~0.17, PA-CF ~0.30; expansion 40-95 um/m K).
Approximate: printed parts conduct less (air gaps) and are anisotropic.

Custom materials (the Studies page's material editor) are saved to <home>/sim_materials.json
and used by name. FDM print settings (infill, walls, layer orientation) turn into knock-down
factors via fdm_factors(), documented as approximate.

Anisotropy (Plan 6 S3): with a print direction, a material becomes orthotropic (transversely
isotropic about the build direction): in-plane E, between-layer E_z and a tensile strength
across the layers (z_strength). FDM_Z_RATIOS are TYPICAL ratios, every one an ESTIMATE:
- PLA, ABS, ASA: Z / XY tensile strength 0.6 and E_z / E 0.9. Singh, "Mechanical and FEA
  comparison of FDM and injection-molded thermoplastics", CSU Sacramento thesis (2025): the X
  build beat the Z build in tensile strength by 24.6% (PLA), 42% (ABS) and 127.9% (PETG), i.e.
  Z / XY 0.80, 0.70 and 0.44; https://scholars.csus.edu/esploro/outputs/graduate/
  Mechanical-and-FEA-comparison-of-FDM/99258311232901671. 0.6 sits inside that spread and is
  the ratio fdm_factors() already uses. Stiffness varies far less with build direction than
  strength does.
- PETG: Z / XY strength 0.38, from Prusament PETG TDS v1.1 (last update 16-02-2022; copy at
  https://www.3dprintergear.com.au/assets/files/technical-data-sheet%20(1).pdf): tensile yield
  47 +- 2 MPa horizontal, 50 +- 1 MPa "vertical xz" (an on-edge print loaded ALONG its layers,
  not a Z build), modulus 1.5 / 1.6 GPa, and "interlayer adhesion" 18 +- 4 MPa, the only
  between-layer figure: 18 / 47 = 0.38 is the figure used. Singh's thesis (above) gives 0.44
  for PETG, so 0.38 is the conservative end. E_z / E 0.9 is the generic estimate (the TDS gives
  no between-layer modulus).
- PA-CF: E_z / E 0.565 from Bambu PAHT-CF TDS V2.0 (the Bambu-hosted copy above): Young's
  modulus 3860 +- 230 MPa X-Y and 2180 +- 130 MPa Z (a data-sheet value, not an estimate).
  yield_mpa is that copy's Z strength (64 MPa; 88 MPa X-Y), so the in-plane strength is not
  credited. COPIES DISAGREE: another copy also labelled v2 (https://uwaterloo.ca/
  rapid-prototyping-centre/sites/default/files/uploads/documents/bambu_paht-cf_technical_data_
  sheet_v2.pdf) lists Z 47 +- 5 MPa and X-Y 92, so the Z / yield ratio is 47 / 64 = 0.73, the
  conservative reading.
Shear moduli are on no FDM data sheet: G = E / (2 (1 + nu)) in-plane and E_z / (2 (1 + nu)) in
the between-layer planes (estimate).

Plan 10 E32 (research 2026-11-01 §5.3): engineering materials beyond filament, each with its
process. Printed (fdm) values are XY coupons and get FDM_KNOCKDOWN; sls_mjf gets 0.85; machined,
sheet, laminate and cast materials get 1.0 and rely on the safety factor (min_safety_factor:
1.5 metals, 2.0 printed, 3.0 brittle). `failure="brittle"` (CF plate) is checked as max
principal stress against the design strength, not von Mises. Sources per row (`source`):
- PC Blend: modulus Filament2Print (Prusament PC Blend listing); strength an ESTIMATE to verify
  against the official Prusament TDS.
- PA12 (MJF): HP 3D High Reusability PA12 datasheet (near-isotropic).
- PA6-CF: Bambu PA6-CF TDS v2: E 4430 MPa XY / 2170 Z, 102 MPa XY, 48 MPa Z (used as design).
- TPU 95A: Ultimaker TPU 95A TDS (2017): E 26 MPa (XY), yield 8.6 MPa, elongation 580%;
  hyperelastic Neo-Hooke C10 ~ E/6 (estimate), valid only in nonlinear studies.
- POM: DuPont Delrin 150 datasheet (approximate); machined (it warps when printed).
- Al 6061-T6, Al 7075-T6, 304 annealed: ASM / MatWeb. Steel 1018 CD, 4140 annealed and Q&T:
  MatWeb / AZoM. Brass C360 half hard: Copper Development Association C36000.
- CF plate (3K twill epoxy, quasi-isotropic): typical vendor sheets (e.g. Dragonplate); in-plane
  only, brittle (design 300 of 450 MPa); interlaminar ~50 MPa, conductive.

Calibration (calibration.py) replaces E, strength and the knock-down by values fitted to the
user's own coupons for one material and printer. Such a material has basis "calibrated (n
tests)"; every other one has "datasheet / estimate". Fatigue S-N data: fatigue.py.
"""
from __future__ import annotations

import json
import os
import re
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from .types import SimError

FDM_KNOCKDOWN = 0.7  # applied to strength (not stiffness) for every FDM material
Process = Literal["fdm", "sls_mjf", "machined", "sheet", "laminate", "cast"]
PROCESSES = ("fdm", "sls_mjf", "machined", "sheet", "laminate", "cast")
# the strength knock-down each process starts from (research 2026-11-01 §5.2; sls_mjf [J])
PROCESS_KNOCKDOWN = {"fdm": FDM_KNOCKDOWN, "sls_mjf": 0.85, "machined": 1.0, "sheet": 1.0,
                     "laminate": 1.0, "cast": 1.0}
METALLIC = ("machined", "sheet", "cast")
# Nonlinear studies with plasticity use a bilinear curve: yield (the allowable, i.e. with the
# knock-down) then the tangent modulus. Without a measured tangent the estimate is 1% of E:
# nearly perfectly plastic (printed polymers hardly harden between yield and break).
TANGENT_ESTIMATE_RATIO = 0.01
BASIS_ESTIMATE = "datasheet / estimate"
# (E_z / E, Z tensile strength / yield_mpa): typical values, ESTIMATES (sources: module doc)
FDM_Z_RATIOS: dict[str, tuple[float, float]] = {
    "PLA": (0.9, 0.6), "PETG": (0.9, 0.38), "ABS": (0.9, 0.6), "ASA": (0.9, 0.6),
    "PA-CF": (0.565, 0.73)}
E_Z_MAX_RATIO = 1.2  # E_z / E: printed parts are not stiffer across their layers
DEFAULT_Z_RATIOS = (0.9, 0.6)  # custom materials without their own Z data (estimate)


@dataclass(frozen=True)
class Material:
    name: str
    e_mpa: float          # Young's modulus
    nu: float             # Poisson's ratio
    yield_mpa: float      # tensile yield (or strength when no clear yield)
    density_g_cm3: float
    hdt_c: float          # heat deflection temperature (ISO 75, 1.8 MPa where given)
    source: str
    k_w_mk: float = 0.2               # thermal conductivity, W/(m K)
    alpha_per_k: float = 7e-5         # linear thermal expansion, 1/K
    knockdown: float = FDM_KNOCKDOWN  # strength factor (FDM settings may lower it)
    stiffness_factor: float = 1.0     # E factor from FDM infill/walls; 1 = solid
    custom: bool = False
    tangent_modulus_mpa: float | None = None  # post-yield slope (plasticity); None = estimate
    # orthotropic FDM model (build_dir None = isotropic): between-layer modulus and strength
    e_z_mpa: float | None = None
    z_strength_mpa: float | None = None
    build_dir: tuple[float, float, float] | None = None  # unit layer normal, part coordinates
    # S-N curve sigma_a = sn_a_mpa * N^sn_b (None = the estimate in fatigue.py)
    sn_a_mpa: float | None = None
    sn_b: float | None = None
    basis: str = BASIS_ESTIMATE       # or "calibrated (n tests)"
    calibration: dict | None = None   # summary of the applied calibration record
    basis_by_property: dict | None = None  # {e, e_z, strength, z_strength}: calibrated or not
    fdm_strength_factor: float = 1.0  # infill / walls / orientation share of the strength
    # Plan 10 E32: process and the properties a per-part pick needs
    process: str = "fdm"
    uts_mpa: float | None = None          # ultimate (yield_mpa stays the design strength)
    elongation_pct: float | None = None
    failure: str = "ductile"              # "brittle": max principal stress vs the strength
    max_service_c: float | None = None    # continuous use; hdt_c stays the short-term limit
    fatigue_limit_mpa: float | None = None   # metals: endurance (5e8 cycles) strength
    hyperelastic: dict | None = None      # TPU: {"model": "neo_hooke", "c10_mpa", "d1_per_mpa"}
    printable: bool = True                # False: bought / machined stock
    cost_per_kg: float | None = None      # USD, typical
    notes: str = ""

    def __post_init__(self) -> None:
        if self.process not in PROCESSES:
            raise SimError("bad_setup", f"unknown process {self.process!r}",
                           hint=f"use one of {', '.join(PROCESSES)}")
        if self.failure not in ("ductile", "brittle"):
            raise SimError("bad_setup", "failure is ductile or brittle")

    @property
    def is_fdm(self) -> bool:
        return self.process == "fdm"

    @property
    def min_safety_factor(self) -> float:
        """The margin a design in this material needs [J]: brittle 3, metals 1.5, printed 2."""
        if self.failure == "brittle":
            return 3.0
        return 1.5 if self.process in METALLIC else 2.0

    @property
    def allowable_mpa(self) -> float:
        return self.yield_mpa * self.knockdown

    @property
    def e_eff_mpa(self) -> float:
        return self.e_mpa * self.stiffness_factor

    @property
    def tangent_eff_mpa(self) -> float:
        """The post-yield slope a plastic study uses (scaled like E by infill / walls)."""
        t = (self.tangent_modulus_mpa if self.tangent_modulus_mpa is not None
             else TANGENT_ESTIMATE_RATIO * self.e_mpa)
        return min(t * self.stiffness_factor, 0.5 * self.e_eff_mpa)

    @property
    def tangent_source(self) -> str:
        return ("material data" if self.tangent_modulus_mpa is not None
                else f"estimate ({TANGENT_ESTIMATE_RATIO:.0%} of E)")

    @property
    def orthotropic(self) -> bool:
        return self.build_dir is not None

    @property
    def e_z_eff_mpa(self) -> float:
        return (self.e_z_mpa if self.e_z_mpa is not None else self.e_mpa) * self.stiffness_factor

    @property
    def allowable_z_mpa(self) -> float:
        """Allowable tension across the layers (the knock-down applies here too)."""
        z = self.z_strength_mpa if self.z_strength_mpa is not None else self.yield_mpa
        return z * self.knockdown

    def engineering_constants(self) -> tuple[float, ...]:
        """CalculiX *ELASTIC, TYPE=ENGINEERING CONSTANTS in the local system whose 3-axis is
        the build direction: (E1, E2, E3, nu12, nu13, nu23, G12, G13, G23). The shear moduli
        and out-of-plane Poisson's ratios are estimates (module doc)."""
        e1, e3, nu = self.e_eff_mpa, self.e_z_eff_mpa, self.nu
        g12, g13 = e1 / (2 * (1 + nu)), e3 / (2 * (1 + nu))
        return (e1, e1, e3, nu, nu, nu, g12, g13, g13)

    @property
    def density_t_mm3(self) -> float:  # CalculiX units (mm, N, s): tonne/mm^3
        return self.density_g_cm3 * 1e-9

    def to_dict(self) -> dict:
        if not self.is_fdm:
            model = (f"isotropic linear elastic ({self.process}); "
                     + ("brittle: max principal stress against the design strength"
                        if self.failure == "brittle" else "von Mises against yield")
                     + (f"; strength x {self.knockdown:.3g}" if self.knockdown != 1.0 else "")
                     + f"; needs a safety factor of {self.min_safety_factor:g}")
        elif self.orthotropic:
            d = self.build_dir
            model = (f"orthotropic linear elastic (FDM layers normal to [{d[0]:.3g}, {d[1]:.3g}, "
                     f"{d[2]:.3g}]): E {self.e_eff_mpa:.4g} MPa in-plane, E_z "
                     f"{self.e_z_eff_mpa:.4g} MPa across layers; strength x FDM knock-down "
                     f"{self.knockdown:.3g}; tension across layers checked against "
                     f"{self.allowable_z_mpa:.3g} MPa")
        else:
            model = (f"isotropic linear elastic; strength x FDM knock-down {self.knockdown:.3g}; "
                     "printed parts are weaker across layers")
        if self.stiffness_factor != 1.0:
            model += f"; stiffness x{self.stiffness_factor:.3g} for infill/walls (approximate)"
        out = {**asdict(self), "allowable_mpa": self.allowable_mpa, "e_eff_mpa": self.e_eff_mpa,
               "tangent_eff_mpa": self.tangent_eff_mpa, "tangent_source": self.tangent_source,
               "model": model, "min_safety_factor": self.min_safety_factor}
        if self.orthotropic:
            out.update(build_dir=list(self.build_dir), e_z_eff_mpa=self.e_z_eff_mpa,
                       allowable_z_mpa=self.allowable_z_mpa)
        return out


MATERIALS: dict[str, Material] = {m.name: m for m in [
    Material("PLA", 2300.0, 0.35, 51.0, 1.24, 55.0, "Prusament PLA TDS v1.1 (printed, horizontal)",
             0.13, 6.8e-5),
    Material("PETG", 1500.0, 0.38, 47.0, 1.27, 68.0, "Prusament PETG TDS v1.1 (printed, horizontal)",
             0.20, 6.0e-5),
    Material("ABS", 1900.0, 0.35, 35.0, 1.04, 80.0, "generic FDM ABS, typical TDS low end (approximate)",
             0.17, 9.0e-5),
    Material("ASA", 2000.0, 0.35, 40.0, 1.07, 90.0, "generic FDM ASA, typical TDS low end (approximate)",
             0.17, 9.5e-5),
    Material("PA-CF", 3860.0, 0.35, 64.0, 1.06, 170.0, "Bambu PAHT-CF TDS v2 (X-Y modulus, Z strength)",
             0.30, 4.0e-5),
]}

# Plan 10 E32: knock-down follows the process (FDM 0.7, SLS/MJF 0.85, stock 1.0)
_PLAN10 = [
    dict(name="PC", e_mpa=1900.0, nu=0.37, yield_mpa=55.0, density_g_cm3=1.22, hdt_c=110.0,
         source="Prusament PC Blend: modulus per Filament2Print listing; strength an ESTIMATE "
                "(verify against the official TDS)", k_w_mk=0.20, alpha_per_k=6.5e-5,
         process="fdm", uts_mpa=63.0, max_service_c=100.0, cost_per_kg=40.0,
         notes="tough, hot (enclosure + 270 C nozzle); Z/XY ~0.6 typical"),
    dict(name="PA12", e_mpa=1800.0, nu=0.39, yield_mpa=48.0, density_g_cm3=1.01, hdt_c=95.0,
         source="HP 3D HR PA12 datasheet (MJF, near-isotropic)", k_w_mk=0.25,
         alpha_per_k=9e-5, process="sls_mjf", uts_mpa=48.0, elongation_pct=20.0,
         max_service_c=80.0, cost_per_kg=100.0, printable=True,
         notes="service-bureau print (MJF/SLS); no clear yield, strength = UTS"),
    dict(name="PA6-CF", e_mpa=4430.0, nu=0.35, yield_mpa=48.0, density_g_cm3=1.17, hdt_c=186.0,
         source="Bambu PA6-CF TDS v2: E 4430 MPa XY, 102 MPa XY, 48 MPa Z (Z used as design)",
         k_w_mk=0.30, alpha_per_k=4e-5, e_z_mpa=2170.0, z_strength_mpa=48.0,
         process="fdm", uts_mpa=102.0, max_service_c=120.0, cost_per_kg=70.0,
         notes="dried and annealed; loses 30-50% when wet; hardened nozzle"),
    dict(name="TPU 95A", e_mpa=26.0, nu=0.48, yield_mpa=8.6, density_g_cm3=1.22, hdt_c=60.0,
         source="Ultimaker TPU 95A TDS (2017): E 26 MPa, yield 8.6 MPa, elongation 580%",
         k_w_mk=0.20, alpha_per_k=1.5e-4, process="fdm", uts_mpa=39.0, elongation_pct=580.0,
         max_service_c=60.0, cost_per_kg=35.0,
         hyperelastic={"model": "neo_hooke", "c10_mpa": 4.3, "d1_per_mpa": 0.0,
                       "note": "C10 ~ E/6, an estimate; nonlinear studies only"},
         notes="flexible: feet, bumpers, grips, living hinges; linear studies are small-strain "
               "only"),
    dict(name="POM", e_mpa=3100.0, nu=0.35, yield_mpa=72.0, density_g_cm3=1.42, hdt_c=110.0,
         source="DuPont Delrin 150 datasheet (approximate)", k_w_mk=0.31, alpha_per_k=1.1e-4,
         process="machined", uts_mpa=72.0, elongation_pct=25.0, max_service_c=90.0,
         printable=False, cost_per_kg=12.0,
         notes="low friction: gears, bushings, slides; warps when printed"),
    dict(name="Al 6061-T6", e_mpa=68900.0, nu=0.33, yield_mpa=276.0, density_g_cm3=2.70,
         hdt_c=150.0, source="ASM / MatWeb Al 6061-T6", k_w_mk=167.0, alpha_per_k=2.36e-5,
         process="machined", uts_mpa=310.0, elongation_pct=12.0, max_service_c=150.0,
         fatigue_limit_mpa=96.5, printable=False, cost_per_kg=8.0,
         notes="plate, brackets, shafts' housings; weldable; strength drops above 150 C"),
    dict(name="Al 7075-T6", e_mpa=71700.0, nu=0.33, yield_mpa=503.0, density_g_cm3=2.81,
         hdt_c=120.0, source="ASM / MatWeb Al 7075-T6", k_w_mk=130.0, alpha_per_k=2.34e-5,
         process="machined", uts_mpa=572.0, elongation_pct=11.0, max_service_c=120.0,
         fatigue_limit_mpa=159.0, printable=False, cost_per_kg=15.0,
         notes="highly loaded arms and links; not weldable"),
    dict(name="Steel 1018", e_mpa=205000.0, nu=0.29, yield_mpa=370.0, density_g_cm3=7.87,
         hdt_c=400.0, source="MatWeb / AZoM AISI 1018 cold drawn", k_w_mk=51.9,
         alpha_per_k=1.2e-5, process="machined", uts_mpa=440.0, elongation_pct=15.0,
         max_service_c=400.0, fatigue_limit_mpa=220.0, printable=False, cost_per_kg=3.0,
         notes="shafts, pins, dowels"),
    dict(name="Steel 4140", e_mpa=205000.0, nu=0.29, yield_mpa=417.0, density_g_cm3=7.85,
         hdt_c=400.0, source="AZoM AISI 4140 annealed", k_w_mk=42.6, alpha_per_k=1.22e-5,
         process="machined", uts_mpa=655.0, elongation_pct=25.0, max_service_c=400.0,
         fatigue_limit_mpa=300.0, printable=False, cost_per_kg=4.0,
         notes="annealed; shafts and gears"),
    dict(name="Steel 4140 QT", e_mpa=205000.0, nu=0.29, yield_mpa=655.0, density_g_cm3=7.85,
         hdt_c=400.0, source="AZoM AISI 4140 quenched and tempered (~HRC 30)", k_w_mk=42.6,
         alpha_per_k=1.22e-5, process="machined", uts_mpa=1020.0, elongation_pct=18.0,
         max_service_c=400.0, fatigue_limit_mpa=450.0, printable=False, cost_per_kg=5.0,
         notes="hardened shafts, gears, pins"),
    dict(name="304 stainless", e_mpa=193000.0, nu=0.29, yield_mpa=215.0, density_g_cm3=8.00,
         hdt_c=800.0, source="ASM / MatWeb AISI 304 annealed", k_w_mk=16.2,
         alpha_per_k=1.73e-5, process="machined", uts_mpa=505.0, elongation_pct=70.0,
         max_service_c=800.0, fatigue_limit_mpa=240.0, printable=False, cost_per_kg=6.0,
         notes="corrosion resistant; low yield; work-hardens"),
    dict(name="Brass C360", e_mpa=97000.0, nu=0.31, yield_mpa=310.0, density_g_cm3=8.50,
         hdt_c=200.0, source="Copper Development Association C36000 half hard", k_w_mk=115.0,
         alpha_per_k=2.05e-5, process="machined", uts_mpa=385.0, elongation_pct=25.0,
         max_service_c=200.0, printable=False, cost_per_kg=10.0,
         notes="bushings, threaded inserts, small gears"),
    dict(name="CF plate", e_mpa=50000.0, nu=0.30, yield_mpa=300.0, density_g_cm3=1.55,
         hdt_c=120.0, source="3K twill epoxy, quasi-isotropic, typical vendor sheets (e.g. "
                             "Dragonplate); design 300 of 450-600 MPa", k_w_mk=5.0,
         alpha_per_k=2e-6, process="laminate", uts_mpa=450.0, elongation_pct=1.0,
         failure="brittle", max_service_c=90.0, printable=False, cost_per_kg=120.0,
         notes="plate only: in-plane values; interlaminar ~50 MPa; conductive (blocks RF, "
               "galvanic with aluminium)"),
]
for _d in _PLAN10:
    _m = Material(**_d)
    MATERIALS[_m.name] = replace(_m, knockdown=PROCESS_KNOCKDOWN[_m.process])
FDM_Z_RATIOS["PA6-CF"] = (2170.0 / 4430.0, 48.0 / 48.0)

MATERIALS_FILE = "sim_materials.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,39}$")
MAX_CUSTOM = 100
_KEEP = ("name", "e_mpa", "nu", "yield_mpa", "density_g_cm3", "hdt_c", "source", "k_w_mk",
         "alpha_per_k", "tangent_modulus_mpa")
_KEEP_OPTIONAL = ("e_z_mpa", "z_strength_mpa", "sn_a_mpa", "sn_b")  # saved only when set


def get_material(name: str, home: Path | None = None) -> Material:
    """A built-in material, or (with home) a custom one saved from the material editor."""
    key = (name or "").strip().lower()
    for k, m in MATERIALS.items():
        if k.lower() == key:
            return m
    customs = load_custom(home)
    for c in customs:
        if c.name.lower() == key:
            return c
    raise SimError("bad_setup", f"unknown material {name!r}",
                   hint=f"use one of: {', '.join([*MATERIALS, *(c.name for c in customs)])}")


def fdm_factors(infill_pct: float, walls: int, layer_orientation: str) -> tuple[float, float]:
    """(stiffness factor, strength factor) for FDM print settings. APPROXIMATE: the
    load-bearing share of a section is taken as the walls (each ~15% of a thin section) plus
    infill for the rest; a load across the layers (orientation "z") gets 60% of the in-plane
    strength (a typical Z/XY ratio of printed coupons). Strength keeps the base knock-down."""
    infill = min(1.0, max(0.0, infill_pct / 100.0))
    solid = 1.0 - (1.0 - infill) * max(0.2, 1.0 - 0.15 * max(1, walls))
    across = 0.6 if layer_orientation == "z" else 1.0
    return solid, FDM_KNOCKDOWN * solid * across


def _need_fdm(m: Material, what: str) -> None:
    if not m.is_fdm:
        raise SimError("bad_setup", f"{m.name} is a {m.process} material: {what} applies to "
                       "FDM prints only", hint="drop the FDM print settings for this part, or "
                       "pick a printed material")


def with_fdm(m: Material, infill_pct: float, walls: int, layer_orientation: str) -> Material:
    """The FDM factors on top of the material's own knock-down (FDM_KNOCKDOWN, or the fitted
    one of a calibrated material). Refused for a non-FDM material."""
    _need_fdm(m, "infill / walls / layer orientation")
    stiff, strength = fdm_factors(infill_pct, walls, layer_orientation)
    if layer_orientation == "z" and m.z_strength_mpa is not None and m.yield_mpa > 0:
        # a measured (calibrated / custom) Z strength replaces the typical 0.6
        strength *= (m.z_strength_mpa / m.yield_mpa) / 0.6
    factor = strength / FDM_KNOCKDOWN
    strength *= m.knockdown / FDM_KNOCKDOWN
    return replace(m, knockdown=strength, stiffness_factor=stiff,
                   fdm_strength_factor=m.fdm_strength_factor * factor,
                   source=m.source + f"; FDM {infill_pct:g}% infill, {walls} walls, layers "
                                     f"{layer_orientation} (approximate)")


def unit_dir(v) -> tuple[float, float, float]:
    x, y, z = (float(c) for c in v)
    n = math.sqrt(x * x + y * y + z * z)
    if not math.isfinite(n) or n < 1e-9:
        raise SimError("bad_setup", "the print direction must be a non-zero vector")
    return (x / n, y / n, z / n)


def with_anisotropy(m: Material, build_dir, e_z_ratio: float | None = None,
                    strength_z_ratio: float | None = None) -> Material:
    """The orthotropic FDM version of m, printed with its layers normal to build_dir. Z data
    the material already has (a custom material's or a calibration's) wins over the typical
    ratios; explicit ratios win over both. Refused for a non-FDM material."""
    _need_fdm(m, "a print direction (layer anisotropy)")
    typ = FDM_Z_RATIOS.get(m.name, DEFAULT_Z_RATIOS)
    e_z = (m.e_mpa * e_z_ratio if e_z_ratio is not None
           else m.e_z_mpa if m.e_z_mpa is not None else m.e_mpa * typ[0])
    z_s = (m.yield_mpa * strength_z_ratio if strength_z_ratio is not None
           else m.z_strength_mpa if m.z_strength_mpa is not None else m.yield_mpa * typ[1])
    estimated = []
    if e_z_ratio is None and m.e_z_mpa is None:
        estimated.append(f"E_z/E {typ[0]:g}")
    if strength_z_ratio is None and m.z_strength_mpa is None:
        estimated.append(f"Z/XY strength {typ[1]:g}")
    note = "; orthotropic FDM" + (f" ({', '.join(estimated)}: typical, estimate)"
                                  if estimated else "")
    out = replace(m, e_z_mpa=e_z, z_strength_mpa=z_s, build_dir=unit_dir(build_dir),
                  source=m.source + note)
    check_orthotropic(out)
    return out


def check_orthotropic(m: Material) -> None:
    """Refuse engineering constants whose stiffness is not positive definite (CalculiX runs
    them anyway and returns garbage) or that are implausible for a print: E_z / E above
    E_Z_MAX_RATIO, nu13^2 >= E1 / E3."""
    e1, e2, e3, n12, n13, n23, g12, g13, g23 = m.engineering_constants()
    if e3 / e1 > E_Z_MAX_RATIO:
        raise SimError("bad_setup", f"{m.name}: E across the layers ({e3:.4g} MPa) is "
                       f"{e3 / e1:.3g}x the in-plane E; at most {E_Z_MAX_RATIO:g}x is plausible",
                       hint="check e_z_mpa / e_z_ratio")
    if n13 ** 2 >= e1 / e3:
        raise SimError("bad_setup", f"{m.name}: nu13^2 >= E1 / E3, not a stable material")
    S = [[1 / e1, -n12 / e1, -n13 / e1], [-n12 / e1, 1 / e2, -n23 / e2],
         [-n13 / e1, -n23 / e2, 1 / e3]]
    import numpy as np
    if min(np.linalg.eigvalsh(np.array(S))) <= 0 or min(g12, g13, g23) <= 0:
        raise SimError("bad_setup", f"{m.name}: these orthotropic constants (Poisson's ratio "
                       f"{m.nu:g}, E_z / E {e3 / e1:.3g}) are not positive definite",
                       hint="lower the Poisson's ratio or E_z / E")


def custom_material(d: dict) -> Material:
    """A Material from the editor's fields (ranges are checked by the settings schema)."""
    name = str(d.get("name", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise SimError("bad_setup", f"bad material name {name!r}",
                       hint="1-40 letters, digits, spaces or ._+()-")
    return Material(name=name, e_mpa=float(d["e_mpa"]), nu=float(d["nu"]),
                    yield_mpa=float(d["yield_mpa"]), density_g_cm3=float(d["density_g_cm3"]),
                    hdt_c=float(d.get("hdt_c") or 60.0),
                    source=str(d.get("source") or "user-defined (Studies page)")[:200],
                    k_w_mk=float(d.get("k_w_mk") or 0.2),
                    alpha_per_k=float(d.get("alpha_per_k") or 7e-5), custom=True,
                    tangent_modulus_mpa=(None if d.get("tangent_modulus_mpa") is None
                                         else float(d["tangent_modulus_mpa"])),
                    **{k: float(d[k]) for k in _KEEP_OPTIONAL if d.get(k) is not None})


def load_custom(home: Path | None) -> list[Material]:
    if home is None:
        return []
    try:
        raw = json.loads((Path(home) / MATERIALS_FILE).read_text())
    except (OSError, ValueError):
        return []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(custom_material(d))
        except (SimError, KeyError, TypeError, ValueError):
            continue
    return out


def _save_custom(home: Path, mats: list[Material]) -> None:
    p = Path(home) / MATERIALS_FILE
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps([{**{k: getattr(m, k) for k in _KEEP},
                                **{k: getattr(m, k) for k in _KEEP_OPTIONAL
                                   if getattr(m, k) is not None}} for m in mats], indent=1))
    os.replace(tmp, p)


def upsert_custom(home: Path, m: Material) -> Material:
    if any(k.lower() == m.name.lower() for k in MATERIALS):
        raise SimError("bad_setup", f"{m.name} is a built-in material",
                       hint="pick another name for your material")
    mats = [c for c in load_custom(home) if c.name.lower() != m.name.lower()]
    if len(mats) >= MAX_CUSTOM:
        raise SimError("bad_setup", f"at most {MAX_CUSTOM} custom materials")
    _save_custom(home, [*mats, m])
    return m


def delete_custom(home: Path, name: str) -> bool:
    mats = load_custom(home)
    left = [c for c in mats if c.name.lower() != name.strip().lower()]
    if len(left) == len(mats):
        return False
    _save_custom(home, left)
    return True


# ---- Plan 10 E32: the agent's per-part material pick

def _score(m: Material, req: dict) -> tuple[float, list[str], list[str]]:
    """(score, reasons, refusals) for one material against requirements."""
    why, no = [], []
    prio = req.get("priority") or "balanced"
    temp = req.get("max_temp_c")
    service = m.max_service_c if m.max_service_c is not None else m.hdt_c
    if temp is not None and service < float(temp):
        no.append(f"service limit {service:g} C < {float(temp):g} C")
    if req.get("printable") is True and not m.printable:
        no.append("not printable (bought / machined stock)")
    if req.get("printable") is False and m.printable and m.process == "fdm":
        why.append("printed although stock was asked for")
    procs = req.get("processes")
    if procs and m.process not in procs:
        no.append(f"process {m.process} not available")
    if req.get("flexible") and (m.elongation_pct or 0) < 100:
        no.append("not flexible")
    if not req.get("flexible") and (m.elongation_pct or 0) > 100:
        no.append("flexible (elastomer)")
    if req.get("min_yield_mpa") is not None and m.allowable_mpa < float(req["min_yield_mpa"]):
        no.append(f"allowable {m.allowable_mpa:.3g} MPa < {float(req['min_yield_mpa']):g} MPa")
    if req.get("min_e_mpa") is not None and m.e_mpa < float(req["min_e_mpa"]):
        no.append(f"E {m.e_mpa:g} MPa < {float(req['min_e_mpa']):g} MPa")
    if req.get("conductive") is False and (m.process in METALLIC or m.name == "CF plate"):
        no.append("electrically conductive (an antenna / RF window must not be)")
    form = req.get("form")
    if m.process == "laminate" and form is not None and form != "plate":
        no.append("plate stock only (in-plane laminate)")
    if req.get("low_friction") and m.name in ("POM", "Brass C360", "PA12"):
        why.append("low friction (gears, bushings)")
    rho = m.density_g_cm3
    stiff, strong = m.e_mpa / rho, m.allowable_mpa / rho
    cost = m.cost_per_kg or 50.0
    if prio == "stiffness":
        score = stiff / 1000.0
        why.append(f"specific stiffness E/rho {stiff:,.0f}")
    elif prio == "strength":
        score = strong
        why.append(f"specific strength {strong:.3g} MPa per g/cm3")
    elif prio == "cost":
        score = 100.0 / cost
        why.append(f"~{cost:g} USD/kg")
    elif prio == "mass":
        score = strong / rho
        why.append(f"strength per density^2 {strong / rho:.3g}")
    else:
        score = strong * 0.5 + stiff / 2000.0 + 50.0 / cost
        why.append(f"specific strength {strong:.3g}, stiffness {stiff:,.0f}, ~{cost:g} USD/kg")
    if m.failure == "brittle":
        why.append("brittle: design with SF >= 3")
    return score, why, no


def rank_materials(req: dict | None = None, home: Path | None = None,
                   limit: int = 6) -> list[dict]:
    """Materials ranked for a part's requirements: {priority: stiffness|strength|mass|cost|
    balanced, max_temp_c, printable, processes, flexible, min_yield_mpa, min_e_mpa, conductive,
    low_friction, form: plate|shaft|block|shell}. Each row: name, process, score, reasons, the key numbers; materials that fail
    a hard requirement come last with `rejected` reasons."""
    req = dict(req or {})
    rows = []
    for m in [*MATERIALS.values(), *load_custom(home)]:
        score, why, no = _score(m, req)
        rows.append({"name": m.name, "process": m.process, "score": round(score, 3),
                     "reasons": why, "rejected": no, "e_mpa": m.e_mpa,
                     "allowable_mpa": round(m.allowable_mpa, 3),
                     "density_g_cm3": m.density_g_cm3,
                     "max_service_c": m.max_service_c if m.max_service_c is not None else m.hdt_c,
                     "printable": m.printable, "failure": m.failure,
                     "min_safety_factor": m.min_safety_factor, "cost_per_kg": m.cost_per_kg,
                     "notes": m.notes})
    rows.sort(key=lambda r: (bool(r["rejected"]), -r["score"]))
    return rows[:max(1, limit)]
