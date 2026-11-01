"""Known answers for the rocketry tests (Plan 11 F6).

1. Barrowman worked example: R. F. Culp, "Cp_MinieMagg.xls" (rocketmime.com, 1999), the
   Barrowman center of pressure of an Estes Mini Mag (inches): Ln 12.5 (ogive), d 5.54, Cr 10,
   Ct 0, S 5.25, Lf 6.5, R 2.77, Xr 9, Xb 27, n 3 -> Cn_n 2, Xn 5.825, Cn_f 5.4917,
   Xf 31.6667, Cn 7.4917, X 24.7679.

2. OpenRocket 24.12 reference rockets, built in OpenRocket's own core (scratch driver ORRef.java
   on OpenRocket-24.12.jar: BarrowmanCalculator at Mach 0.3 / AoA 0, MassCalculator, and a
   simulation with a 1 m vertical rod, no wind, ISA at sea level, 10 ms steps, no recovery):
   - "A": tangent-ogive nose 200 mm x 66 mm (80 g, CG 130 mm from the tip); body tube 700 mm
     x 66 mm (250 g, CG at its middle); three trapezoidal fins root 120, tip 50, span 65,
     sweep 60, 3.2 mm square-edged, flush with the aft end (90 g, CG 70 mm aft of the fin
     root LE); a 120 g payload with its CG 350 mm from the tip; "regular paint" finish
     (60 um); the motor flush with the aft end.
   - "B": "A" plus a 34 mm conical boattail from 66 to 40 mm (20 g, CG 15 mm aft of its
     front), the motor's aft end flush with the boattail's.
"""
from __future__ import annotations

import json
from pathlib import Path

from mises.rocketry.aero import Body, Fins, Nose, Section

MINIEMAGG = dict(ln=12.5, d=5.54, cr=10.0, ct=0.0, s=5.25, lf=6.5, r=2.77, xr=9.0, xb=27.0, n=3)
MINIEMAGG_ANSWER = dict(cn_nose=2.0, x_nose=5.825, cn_fins=5.491657106397445,
                        x_fins=31.666666666666668, cn=7.491657106397445, x=24.767881444743104)

# OpenRocket 24.12 output (m, kg, s)
OR = {
    "A": {"cp": 0.66207, "cn_alpha": 8.8563, "dry_mass": 0.540, "dry_cg": 0.49333,
          "cd": {0.1: 0.5183, 0.2: 0.5227, 0.3: 0.5300, 0.5: 0.5534}},
    "B": {"cp": 0.61980, "cn_alpha": 7.5910, "dry_mass": 0.560, "dry_cg": 0.50839,
          "cd": {0.1: 0.5126, 0.2: 0.5166, 0.3: 0.5232, 0.5: 0.5445}},
}
OR_FLIGHTS = {
    # (rocket, motor): wet mass, wet CG, stability at launch (cal), apogee, max v, t apogee
    ("A", "F52C"): {"wet_mass": 0.6214, "wet_cg": 0.53933, "stability": 1.8596,
                    "apogee": 312.76, "max_v": 90.09, "t_apogee": 7.899, "rail_v": 12.78},
    ("B", "G40W"): {"wet_mass": 0.6830, "wet_cg": 0.57387, "stability": 0.6959,
                    "apogee": 477.28, "max_v": 108.89, "t_apogee": 9.535, "rail_v": 13.31},
}
# tolerances the tests hold the Mises model to against OpenRocket
TOL_CP, TOL_CD, TOL_APOGEE, TOL_VMAX, TOL_T = 0.01, 0.03, 0.03, 0.02, 0.03


def body(which: str = "A") -> Body:
    secs = [Section(0.2, 0.7, 0.066, 0.066)]
    if which == "B":
        secs.append(Section(0.9, 0.034, 0.066, 0.040))
    return Body(Nose("ogive", 0.2, 0.066), secs,
                [Fins(3, 0.12, 0.05, 0.065, 0.06, 0.0032, 0.9 - 0.12, 0.066)], 0.066)


def geometry(which: str = "A") -> dict:
    """The same rocket as rocket_* `geometry` (mm), nose up +z with the tip at z = 900."""
    secs = [{"length_mm": 700}]
    if which == "B":
        secs.append({"length_mm": 34, "aft_diameter_mm": 40})
    return {"body_diameter_mm": 66, "nose": {"shape": "ogive", "length_mm": 200},
            "sections": secs, "fins": [{"count": 3, "root_chord_mm": 120, "tip_chord_mm": 50,
                                        "span_mm": 65, "sweep_mm": 60, "thickness_mm": 3.2,
                                        "te_from_aft_mm": 0}],
            "axis": "+z", "nose_tip_mm": 900, "rail_buttons": 0}


# parts of rocket "A" in the build frame (nose up +z, tip at z = 900 mm): (mass g, CG z, bbox)
PARTS_A = {
    "nose_cone": (80.0, 770.0, ((-33, -33, 700), (33, 33, 900))),
    "body_tube": (250.0, 350.0, ((-33, -33, 0), (33, 33, 700))),
    "fins": (90.0, 50.0, ((-98, -98, 0), (98, 98, 120))),
    "payload": (120.0, 550.0, ((-25, -25, 500), (25, 25, 600))),
    "motor": (40.0, 55.5, ((-14.5, -14.5, 0), (14.5, 14.5, 111))),   # a placeholder solid
}
PLA = 1.24


def write_parts(home: Path, parts: dict = PARTS_A) -> None:
    """metrics.json per part with B-rep-style mass properties (no build needed)."""
    for pid, (g, z, (lo, hi)) in parts.items():
        d = Path(home) / "parts" / pid
        d.mkdir(parents=True, exist_ok=True)
        m = g / 1000.0
        com = [0.0, 0.0, z]
        (d / "metrics.json").write_text(json.dumps({
            "valid": True, "volume_mm3": g / PLA * 1000.0, "mass_g": g,
            "bbox": {"min": list(lo), "max": list(hi), "size": [h - l for l, h in zip(lo, hi)]},
            "center_of_mass": com,
            "inertia": {"mass_kg": m, "com_mm": com, "density_g_cm3": PLA,
                        "inertia_kgm2": [[1e-4, 0, 0], [0, 1e-4, 0], [0, 0, 1e-5]]},
            "face_list": [], "holes": []}))
