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
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .types import SimError

FDM_KNOCKDOWN = 0.7  # applied to strength (not stiffness) for every FDM material


@dataclass(frozen=True)
class Material:
    name: str
    e_mpa: float          # Young's modulus
    nu: float             # Poisson's ratio
    yield_mpa: float      # tensile yield (or strength when no clear yield)
    density_g_cm3: float
    hdt_c: float          # heat deflection temperature (ISO 75, 1.8 MPa where given)
    source: str

    @property
    def allowable_mpa(self) -> float:
        return self.yield_mpa * FDM_KNOCKDOWN

    def to_dict(self) -> dict:
        return {**asdict(self), "knockdown": FDM_KNOCKDOWN, "allowable_mpa": self.allowable_mpa,
                "model": "isotropic linear elastic; strength x FDM knock-down "
                         f"{FDM_KNOCKDOWN}; printed parts are weaker across layers"}


MATERIALS: dict[str, Material] = {m.name: m for m in [
    Material("PLA", 2300.0, 0.35, 51.0, 1.24, 55.0, "Prusament PLA TDS v1.1 (printed, horizontal)"),
    Material("PETG", 1500.0, 0.38, 47.0, 1.27, 68.0, "Prusament PETG TDS v1.1 (printed, horizontal)"),
    Material("ABS", 1900.0, 0.35, 35.0, 1.04, 80.0, "generic FDM ABS, typical TDS low end (approximate)"),
    Material("ASA", 2000.0, 0.35, 40.0, 1.07, 90.0, "generic FDM ASA, typical TDS low end (approximate)"),
    Material("PA-CF", 3860.0, 0.35, 64.0, 1.06, 170.0, "Bambu PAHT-CF TDS v2 (X-Y modulus, Z strength)"),
]}


def get_material(name: str) -> Material:
    for key, m in MATERIALS.items():
        if key.lower() == (name or "").strip().lower():
            return m
    raise SimError("bad_setup", f"unknown material {name!r}",
                   hint=f"use one of: {', '.join(MATERIALS)}")
