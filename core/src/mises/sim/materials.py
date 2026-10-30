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
- PETG: Prusament PETG TDS v1.1 printed specimens (ISO 527-1): tensile yield 47 MPa horizontal
  vs 30 MPa vertical (Z), modulus 1.5 vs 1.4 GPa -> strength 0.64, E 0.93.
- PA-CF: yield_mpa is ALREADY the Z strength (Bambu PAHT-CF TDS v2: 88 MPa X-Y, 64 MPa Z), so
  its strength ratio is 1.0 (in-plane strength is not credited). E_z / E 0.7 is an estimate
  (the fibres lie in-plane).
Shear moduli are on no FDM data sheet: G = E / (2 (1 + nu)) in-plane and E_z / (2 (1 + nu)) in
the between-layer planes (estimate).

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

from .types import SimError

FDM_KNOCKDOWN = 0.7  # applied to strength (not stiffness) for every FDM material
BASIS_ESTIMATE = "datasheet / estimate"
# (E_z / E, Z tensile strength / yield_mpa): typical values, ESTIMATES (sources: module doc)
FDM_Z_RATIOS: dict[str, tuple[float, float]] = {
    "PLA": (0.9, 0.6), "PETG": (0.93, 0.64), "ABS": (0.9, 0.6), "ASA": (0.9, 0.6),
    "PA-CF": (0.7, 1.0)}
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
    # orthotropic FDM model (build_dir None = isotropic): between-layer modulus and strength
    e_z_mpa: float | None = None
    z_strength_mpa: float | None = None
    build_dir: tuple[float, float, float] | None = None  # unit layer normal, part coordinates
    # S-N curve sigma_a = sn_a_mpa * N^sn_b (None = the estimate in fatigue.py)
    sn_a_mpa: float | None = None
    sn_b: float | None = None
    basis: str = BASIS_ESTIMATE       # or "calibrated (n tests)"
    calibration: dict | None = None   # summary of the applied calibration record

    @property
    def allowable_mpa(self) -> float:
        return self.yield_mpa * self.knockdown

    @property
    def e_eff_mpa(self) -> float:
        return self.e_mpa * self.stiffness_factor

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
        if self.orthotropic:
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
               "model": model}
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

MATERIALS_FILE = "sim_materials.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,39}$")
MAX_CUSTOM = 100
_KEEP = ("name", "e_mpa", "nu", "yield_mpa", "density_g_cm3", "hdt_c", "source", "k_w_mk",
         "alpha_per_k")
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


def with_fdm(m: Material, infill_pct: float, walls: int, layer_orientation: str) -> Material:
    """The FDM factors on top of the material's own knock-down (FDM_KNOCKDOWN, or the fitted
    one of a calibrated material)."""
    stiff, strength = fdm_factors(infill_pct, walls, layer_orientation)
    strength *= m.knockdown / FDM_KNOCKDOWN
    return replace(m, knockdown=strength, stiffness_factor=stiff,
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
    ratios; explicit ratios win over both."""
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
    return replace(m, e_z_mpa=e_z, z_strength_mpa=z_s, build_dir=unit_dir(build_dir),
                   source=m.source + note)


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
