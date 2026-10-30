"""Material calibration from the user's own tests, per material and printer profile.

A calibration record keeps its inputs (the tests are its source) and the fit:
- tensile bars: break force over the cross-section -> tensile strength (in-plane "xy" bars
  printed flat, or "z" bars printed upright, pulled across the layers); with an elongation at
  break, a secant modulus (a lower bound of E, used only when nothing better measures E);
- three-point bends: E = P L^3 / (48 delta I), I = b h^3 / 12, from a load and the mid-span
  deflection it gives (elastic range); with a break load, the flexural strength
  3 P L / (2 b h^2), reported but not used as the tensile allowable (it reads high);
- part load tests: a known load and the deflection measured at a point of a part that has a
  static study; the study's displacement there, scaled to the test load, gives
  E_part = E_study x delta_study / delta_measured (linear elasticity: delta ~ 1 / E).

E is the least-squares fit of the deflections, with relative residuals so that a bar and a
part weigh alike: minimise sum (1 - (c_i / E) / delta_i)^2 over every E test (c_i = delta_i
x E_i), whose solution is E = sum E_i^2 / sum E_i. Strength is the least-squares constant (the
mean) of the bar strengths. The knock-down that turns the mean into an allowable is fitted to
the scatter: (mean - 2 sd) / mean with 3 or more bars (about the 2.3% lower tail of a normal
spread), else 0.85 x the weakest / mean, within [0.4, 1.0]. The ratio of the mean to the data
sheet is kept as `datasheet_ratio`.

Every record stores n, the scatter (sd, coefficient of variation) and a confidence: high (n >= 5,
CV <= 5%), medium (n >= 3, CV <= 15%) or low. A calibrated material is labelled "calibrated
(n tests)". The file is <home>/sim_calibrations.json.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .materials import Material
from .types import SimError

CAL_FILE = "sim_calibrations.json"
CAL_ID_RE = re.compile(r"^cal_[0-9a-f]{10}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,39}$")
DEFAULT_PRINTER = "default"
MAX_RECORDS = 200
MAX_TESTS = 100
LOW_SAMPLE_FACTOR = 0.85
KNOCKDOWN_RANGE = (0.4, 1.0)
_LOCK = threading.Lock()  # the store's read-modify-write (a tool and a route at once)

Orientation = Literal["xy", "z"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TensileTest(_M):
    force_n: float = Field(gt=0, le=1e6, description="break (peak) force")
    width_mm: float | None = Field(None, gt=0, le=1000)
    thickness_mm: float | None = Field(None, gt=0, le=1000)
    area_mm2: float | None = Field(None, gt=0, le=1e6, description="or the cross-section")
    elongation_pct: float | None = Field(None, gt=0, le=1000, description="strain at break")
    orientation: Orientation = Field("xy", description="xy: bar printed flat (pulled along the "
                                     "layers); z: printed upright (pulled across them)")
    note: str = Field("", max_length=200)

    @model_validator(mode="after")
    def _area(self):
        if self.area_mm2 is None and (self.width_mm is None or self.thickness_mm is None):
            raise ValueError("give area_mm2 or width_mm and thickness_mm")
        return self

    @property
    def area(self) -> float:
        return self.area_mm2 if self.area_mm2 is not None else self.width_mm * self.thickness_mm

    @property
    def strength(self) -> float:
        return self.force_n / self.area


class BendTest(_M):
    span_mm: float = Field(gt=0, le=2000, description="support span L")
    width_mm: float = Field(gt=0, le=1000, description="bar width b")
    thickness_mm: float = Field(gt=0, le=1000, description="bar depth h (in the load direction)")
    load_n: float = Field(gt=0, le=1e6, description="a load in the elastic range")
    deflection_mm: float = Field(gt=0, le=1000, description="mid-span deflection at load_n")
    break_load_n: float | None = Field(None, gt=0, le=1e6, description="optional: the load at "
                                       "break (flexural strength)")
    orientation: Orientation = Field("xy", description="xy: bar printed flat (bending stress "
                                     "along the layers); z: printed upright (across them)")
    note: str = Field("", max_length=200)

    @property
    def modulus(self) -> float:
        inertia = self.width_mm * self.thickness_mm ** 3 / 12
        return self.load_n * self.span_mm ** 3 / (48 * self.deflection_mm * inertia)

    @property
    def flexural_strength(self) -> float | None:
        if self.break_load_n is None:
            return None
        return 3 * self.break_load_n * self.span_mm / (2 * self.width_mm * self.thickness_mm ** 2)


class PartTest(_M):
    sim_id: str = Field(pattern=r"^sim_[0-9a-f]{12}$", description="a finished static study of "
                        "the part")
    point: tuple[float, float, float] = Field(description="where the deflection was measured "
                                              "(part coordinates, mm)")
    load_n: float = Field(gt=0, le=1e6, description="the test load")
    deflection_mm: float = Field(gt=0, le=1000, description="the measured deflection there")
    direction: tuple[float, float, float] | None = Field(None, description="the measured "
                                                         "component; null = magnitude")
    study_load_n: float | None = Field(None, gt=0, le=1e6, description="the study's load the "
                                       "test load compares with; null = the sum of the force "
                                       "loads of the study's governing case")
    note: str = Field("", max_length=200)


class CalibrationInput(_M):
    material: str = Field(min_length=1, max_length=40)
    printer: str = Field(DEFAULT_PRINTER, min_length=1, max_length=40, description="printer "
                         "profile key (a printer id, a slicer profile name, or 'default')")
    tensile: list[TensileTest] = Field(default_factory=list, max_length=MAX_TESTS)
    bend: list[BendTest] = Field(default_factory=list, max_length=MAX_TESTS)
    part: list[PartTest] = Field(default_factory=list, max_length=MAX_TESTS)
    note: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _some(self):
        for k in (self.material, self.printer):
            if not KEY_RE.fullmatch(k.strip()):
                raise ValueError(f"bad name {k!r}: 1-40 letters, digits, spaces or ._+()-")
        if not (self.tensile or self.bend or self.part):
            raise ValueError("give at least one tensile, bend or part test")
        return self


# ---- fitting

# Plausible fitted / data-sheet ratios by material class (lo, hi). Printed polymers land well
# inside these; a value outside is almost always a typo (a decimal point, N vs kN) or a test
# that went wrong, and such a fit is saved as "rejected - implausible" and never applied.
PLAUSIBLE = {
    "unfilled": {"e": (0.3, 2.0), "strength": (0.3, 1.5), "e_z": (0.1, 1.2),
                 "z_strength": (0.05, 1.5)},
    "fibre-filled": {"e": (0.2, 2.0), "strength": (0.2, 1.5), "e_z": (0.1, 1.2),
                     "z_strength": (0.05, 1.5)},
}
TEST_RATIO = (0.1, 3.0)        # one test / data sheet: outside it the test is an outlier
E_Z_MAX_RATIO = 1.2            # E_z / E (between-layer stiffness above in-plane: implausible)
OUTLIER_REL = 0.5              # |x / median - 1| above this: outlier (with 3 or more tests)
OUTLIER_MZ = 3.5               # modified z-score (Iglewicz & Hoaglin) with a 15% floor
OUTLIER_MZ_MIN_REL = 0.15
HIGH_CV = 0.25                 # scatter above this: low confidence and a warning
MIN_SPAN_DEPTH = 10.0          # three-point bend: span / depth (ASTM D790 uses 16)
MAX_DEFLECTION_SPAN = 0.1      # small-deflection range of P L^3 / 48 E I
REJECTED = "rejected"
REJECTED_LABEL = "rejected — implausible"


def material_class(m: Material) -> str:
    n = m.name.upper()
    return "fibre-filled" if re.search(r"(^|[-_ ])(CF|GF)\b|CARBON|GLASS", n) else "unfilled"


def _stats(vals: list[float]) -> dict:
    a = np.array(vals, dtype=float)
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if len(a) > 1 else None
    return {"n": len(a), "mean": mean, "sd": sd,
            "cv": (sd / mean) if sd is not None and mean > 0 else None,
            "min": float(a.min()), "max": float(a.max())}


def _confidence(n: int, cv: float | None) -> str:
    if n >= 5 and cv is not None and cv <= 0.05:
        return "high"
    if n >= 3 and cv is not None and cv <= 0.15:
        return "medium"
    return "low"


def screen(points: list[tuple[str, int, float, str | None]], ref: float, what: str,
           outliers: list[dict]) -> list[float]:
    """Outlier rejection of one property's per-test values: points are (kind, index, value,
    reason or None from the per-test geometry checks). A test is dropped (and listed) when its
    geometry is out of range, when it is outside TEST_RATIO of the data sheet, or, with 2 or
    more tests, when it is far from the median (|x / median - 1| > 0.5, or a modified z-score
    above 3.5 with at least a 15% deviation; median absolute deviation, robust to the outlier
    itself). Returns the kept values."""
    kept = []
    for kind, i, x, why in points:
        r = x / ref if ref > 0 else float("inf")
        if why is None and not TEST_RATIO[0] <= r <= TEST_RATIO[1]:
            why = (f"{x:.4g} MPa is {r:.3g}x the data sheet ({ref:.4g} MPa): outside "
                   f"{TEST_RATIO[0]:g}-{TEST_RATIO[1]:g}x, likely a typo")
        if why is not None:
            outliers.append({"test": kind, "index": i, "property": what, "value_mpa": x,
                             "reason": why})
        else:
            kept.append((kind, i, x))
    if len(kept) >= 2:
        vals = np.array([x for *_, x in kept])
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        good = []
        for kind, i, x in kept:
            rel = abs(x / med - 1)
            mz = 0.6745 * abs(x - med) / mad if mad > 0 else 0.0
            if len(kept) >= 3 and (rel > OUTLIER_REL or (mz > OUTLIER_MZ
                                                         and rel > OUTLIER_MZ_MIN_REL)):
                outliers.append({"test": kind, "index": i, "property": what, "value_mpa": x,
                                 "reason": f"{x:.4g} MPa is {100 * rel:.0f}% from the median "
                                           f"{med:.4g} MPa of the {len(kept)} tests"})
            else:
                good.append(x)
        return good
    return [x for *_, x in kept]


def fit_modulus(estimates: list[float]) -> dict:
    """E from per-test estimates E_i by least squares on the relative deflection residuals
    (module doc): E = sum E_i^2 / sum E_i."""
    e = np.array(estimates, dtype=float)
    fit = float((e ** 2).sum() / e.sum())
    st = _stats(estimates)
    resid = [float(x / fit - 1) for x in e]   # (delta_fit - delta_i) / delta_i = E_i / E - 1
    return {**st, "value_mpa": fit, "residuals": resid,
            "confidence": _confidence(st["n"], st["cv"])}


def fit_strength(strengths: list[float], warnings: list[str] | None = None,
                 what: str = "strength") -> dict:
    st = _stats(strengths)
    if st["n"] >= 3 and st["sd"] is not None:
        kd, how = (st["mean"] - 2 * st["sd"]) / st["mean"], "(mean - 2 sd) / mean"
    else:
        kd, how = LOW_SAMPLE_FACTOR * st["min"] / st["mean"], "0.85 x weakest / mean (n < 3)"
    if kd < KNOCKDOWN_RANGE[0] and warnings is not None:
        warnings.append(f"{what}: the scatter gives a knock-down of {kd:.2f}, raised to the "
                        f"floor {KNOCKDOWN_RANGE[0]:g}; the allowable is not conservative; "
                        "test more bars")
    kd = min(KNOCKDOWN_RANGE[1], max(KNOCKDOWN_RANGE[0], kd))
    return {**st, "value_mpa": st["mean"], "knockdown": kd, "knockdown_rule": how,
            "confidence": _confidence(st["n"], st["cv"])}


def _bend_reason(b: BendTest) -> str | None:
    if b.span_mm / b.thickness_mm < MIN_SPAN_DEPTH:
        return (f"span / depth {b.span_mm / b.thickness_mm:.3g} < {MIN_SPAN_DEPTH:g}: shear "
                "deflection spoils P L^3 / 48 E I (ASTM D790 uses 16)")
    if b.deflection_mm / b.span_mm > MAX_DEFLECTION_SPAN:
        return (f"deflection / span {b.deflection_mm / b.span_mm:.3g} > "
                f"{MAX_DEFLECTION_SPAN:g}: outside the small-deflection range")
    return None


def fit(inp: CalibrationInput, base: Material, part_estimates: list[dict] | None = None) -> dict:
    """The fitted parameters of one material and printer, screened for plausibility.
    part_estimates: [{e_mpa, ...}] from part_estimate() for every part test.

    `status` is "ok" or "rejected" (label "rejected - implausible", with `reasons`): a
    rejected fit is saved for the user to see and fix but never applied to a study."""
    outliers: list[dict] = []
    warnings: list[str] = []
    reasons: list[str] = []
    cls = material_class(base)
    bounds = PLAUSIBLE[cls]
    e_pts = [("bend", i, b.modulus, _bend_reason(b)) for i, b in enumerate(inp.bend)
             if b.orientation == "xy"]
    e_pts += [("part", i, p["e_mpa"], None) for i, p in enumerate(part_estimates or [])]
    ez_pts = [("bend", i, b.modulus, _bend_reason(b)) for i, b in enumerate(inp.bend)
              if b.orientation == "z"]
    s_pts = [("tensile", i, t.strength, None) for i, t in enumerate(inp.tensile)
             if t.orientation == "xy"]
    sz_pts = [("tensile", i, t.strength, None) for i, t in enumerate(inp.tensile)
              if t.orientation == "z"]
    e_xy = screen(e_pts, base.e_mpa, "e", outliers)
    e_z = screen(ez_pts, base.e_mpa, "e_z", outliers)
    s_xy = screen(s_pts, base.yield_mpa, "strength", outliers)
    s_z = screen(sz_pts, base.yield_mpa, "z_strength", outliers)
    for what, pts, kept in (("E", e_pts, e_xy), ("E_z", ez_pts, e_z),
                            ("strength", s_pts, s_xy), ("Z strength", sz_pts, s_z)):
        if pts and not kept:
            reasons.append(f"every {what} test was rejected as an outlier (see outliers)")
    out: dict = {"material_class": cls, "outliers": outliers, "warnings": warnings}
    if e_xy:
        out["e"] = fit_modulus(e_xy) | {"method": "least squares: three-point bend / part "
                                                  "load tests"}
    secant = [t.strength / (t.elongation_pct / 100) for t in inp.tensile
              if t.orientation == "xy" and t.elongation_pct]
    if secant:  # reported only: a secant at break is far below E for a ductile polymer
        out["e_secant"] = _stats(secant) | {
            "method": "secant modulus at break (a lower bound of E; not applied)"}
    if e_z:
        out["e_z"] = fit_modulus(e_z) | {"method": "least squares: three-point bend (z)"}
    if s_xy:
        out["strength"] = fit_strength(s_xy, warnings)
        out["datasheet_ratio"] = out["strength"]["mean"] / base.yield_mpa
    if s_z:
        out["z_strength"] = fit_strength(s_z, warnings, "Z strength")
    flex = [b.flexural_strength for b in inp.bend if b.flexural_strength is not None]
    if flex:
        out["flexural_strength"] = _stats(flex)
    # the fitted values against the data sheet
    e_ref = out["e"]["value_mpa"] if "e" in out else base.e_mpa
    checks = [("e", "E", base.e_mpa), ("strength", "strength", base.yield_mpa),
              ("e_z", "E_z", base.e_mpa), ("z_strength", "Z strength", base.yield_mpa)]
    for key, label, ref in checks:
        if key not in out:
            continue
        r = out[key]["value_mpa"] / ref
        out[key]["datasheet_ratio"] = r
        lo, hi = bounds[key]
        if not lo <= r <= hi:
            reasons.append(f"fitted {label} {out[key]['value_mpa']:.4g} MPa is {r:.3g}x the "
                           f"{base.name} data sheet ({ref:.4g} MPa); plausible for "
                           f"{cls} filaments: {lo:g}-{hi:g}x")
    if "e_z" in out and out["e_z"]["value_mpa"] / e_ref > E_Z_MAX_RATIO:
        reasons.append(f"E_z / E {out['e_z']['value_mpa'] / e_ref:.3g} > {E_Z_MAX_RATIO:g}: "
                       "printed parts are not stiffer across the layers")
    kds = [out[k]["knockdown"] for k in ("strength", "z_strength") if k in out]
    if kds:
        out["knockdown"] = min(kds)
    props = [k for k in ("e", "e_z", "strength", "z_strength") if k in out]
    for k in props:
        if out[k]["cv"] is not None and out[k]["cv"] > HIGH_CV:
            out[k]["confidence"] = "low"
            warnings.append(f"{k}: scatter (CV) {100 * out[k]['cv']:.0f}% > "
                            f"{100 * HIGH_CV:.0f}%: low confidence")
        if out[k]["n"] < 3:
            warnings.append(f"{k}: only {out[k]['n']} test(s): low confidence")
    order = {"low": 0, "medium": 1, "high": 2}
    out["confidence"] = min((out[k]["confidence"] for k in props), key=order.get) \
        if props else "low"
    if not props:
        reasons.append("no property could be fitted")
    out["n_tests"] = sum(out[k]["n"] for k in props)  # the tests the applied values rest on
    out["n_tests_given"] = len(inp.tensile) + len(inp.bend) + len(inp.part)
    out["calibrated"] = props
    out["status"] = REJECTED if reasons else "ok"
    out["status_label"] = REJECTED_LABEL if reasons else "ok"
    out["reasons"] = reasons
    if part_estimates:
        out["part_tests"] = part_estimates
    return out


def part_estimate(rec: dict, fields, t: PartTest) -> dict:
    """E from one part load test against its static study (inverse comparison).
    rec: the study record; fields: its Fields."""
    if rec.get("type") not in ("static", None) or rec.get("status") != "done":
        raise SimError("bad_setup", f"{t.sim_id} is not a finished static study",
                       hint="run a static study of the part first")
    if _nonlinear(rec):
        raise SimError("bad_setup", f"{t.sim_id} is a nonlinear study (large deflection, "
                       "plasticity or contact): its deflection does not scale with 1 / E",
                       hint="compare the load test with a linear static study of the part")
    mat = rec.get("material") or {}
    e_study = mat.get("e_eff_mpa") or mat.get("e_mpa")
    if not e_study:
        raise SimError("bad_setup", f"{t.sim_id} records no material modulus")
    load = t.study_load_n or study_load(rec)
    if not load:
        raise SimError("bad_setup", f"{t.sim_id}: the governing case has no force load to "
                       "compare with", hint="give study_load_n")
    if "displacement" not in fields.ch:
        raise SimError("bad_setup", f"{t.sim_id} has no displacement field")
    u = point_value(fields, t.point)
    if t.direction is not None:
        d = np.asarray(t.direction, dtype=float)
        n = float(np.linalg.norm(d))
        if n < 1e-9:
            raise SimError("bad_setup", "direction must be a non-zero vector")
        delta_sim = abs(float(np.dot(u, d / n)))
    else:
        delta_sim = float(np.linalg.norm(u))
    if delta_sim <= 0:
        raise SimError("bad_setup", f"{t.sim_id}: the study shows no deflection at that point",
                       hint="pick a point that moves under the load")
    pred = delta_sim * t.load_n / load
    e_eff = e_study * pred / t.deflection_mm
    stiff = mat.get("stiffness_factor") or 1.0
    out = {"sim_id": t.sim_id, "e_mpa": e_eff / stiff, "e_study_mpa": e_study / stiff,
           "predicted_deflection_mm": pred, "measured_deflection_mm": t.deflection_mm,
           "study_load_n": load}
    if mat.get("build_dir"):
        out["note"] = ("approximate: an orthotropic study; only the in-plane E is scaled by "
                       "the deflection ratio (E_z keeps its ratio)")
    return out


def _nonlinear(rec: dict) -> bool:
    """A study run with any nonlinear option on (S1: large deflection, plasticity, contact)."""
    if rec.get("fidelity_class") == "fem_nonlinear":  # S1's record marks every nonlinear study
        return True
    nl = (rec.get("settings") or {}).get("nonlinear")
    if nl is True:
        return True
    if isinstance(nl, dict):
        return any(v is True or (isinstance(v, list) and v) for v in nl.values())
    return False


def study_load(rec: dict) -> float | None:
    s = rec.get("settings") or {}
    cases = s.get("load_cases") or []
    gov = (rec.get("results") or {}).get("governing_case")
    case = next((c for c in cases if c.get("name") == gov), cases[0] if cases else None)
    if case is None:
        return None
    total = sum(float(ld.get("newtons") or 0) for ld in case.get("loads") or []
                if ld.get("kind", "force") in ("force", "bearing", "remote_force"))
    return total or None


def point_value(fields, point) -> np.ndarray:
    """The displacement at a point: interpolated inside the mesh, else the nearest node
    within 2% of the part's size (a point picked on the surface)."""
    p = np.asarray(point, dtype=float)
    r = fields.probe([p])[0]
    if r.get("inside"):
        return np.asarray(r["values"]["displacement"], dtype=float)
    d = np.linalg.norm(fields.coords - p, axis=1)
    i = int(np.argmin(d))
    lo, hi = fields.bounds()
    if d[i] > 0.02 * float(np.linalg.norm(hi - lo)) + 1e-9:
        raise SimError("bad_setup", f"the point {list(map(float, p))} is not on the part",
                       hint="give a point on or inside the part (part coordinates, mm)")
    return np.asarray(fields.ch["displacement"][i], dtype=float)


PROPERTIES = ("e", "e_z", "strength", "z_strength")


PROPERTY_NAMES = {"e": "E", "e_z": "E_z", "strength": "strength", "z_strength": "Z strength"}


def basis_label(f: dict) -> str:
    """Which properties were calibrated and on how many tests, e.g. "calibrated E (3),
    strength (5)", with ", low confidence" when the fit is weak (few tests or a wide
    scatter). The other properties stay "datasheet / estimate" (basis_by_property)."""
    props = [k for k in PROPERTIES if k in f and not
             (k == "e" and f[k].get("method", "").startswith("secant"))]
    if not props:
        from .materials import BASIS_ESTIMATE
        return BASIS_ESTIMATE
    conf = ", low confidence" if f.get("confidence") == "low" else ""
    return "calibrated " + ", ".join(f"{PROPERTY_NAMES[k]} ({f[k]['n']})" for k in props) + conf


def fit_label(f: dict) -> str:
    if f.get("status") == REJECTED:
        return REJECTED_LABEL
    return basis_label(f)


def basis_by_property(f: dict) -> dict[str, str]:
    """Which properties the calibration measured; the rest stay datasheet / estimate."""
    from .materials import BASIS_ESTIMATE
    return {k: (f"calibrated ({f[k]['n']} test{'s' if f[k]['n'] != 1 else ''})" if k in f
                else BASIS_ESTIMATE) for k in PROPERTIES}


def plausibility(rec: dict, base: Material) -> list[str]:
    """Why a record must not be applied to `base` ([] = it may): a rejected fit, or (for a
    record saved before the screen existed, or against a changed data sheet) a fitted value
    outside the plausible band."""
    f = rec.get("fit") or {}
    if f.get("status") == REJECTED:
        return list(f.get("reasons") or ["rejected"])
    bounds = PLAUSIBLE[material_class(base)]
    out = []
    for key, ref in (("e", base.e_mpa), ("strength", base.yield_mpa), ("e_z", base.e_mpa),
                     ("z_strength", base.yield_mpa)):
        v = (f.get(key) or {}).get("value_mpa")
        if v is not None and not bounds[key][0] <= v / ref <= bounds[key][1]:
            out.append(f"fitted {key} {v:.4g} MPa is {v / ref:.3g}x the data sheet")
    e = (f.get("e") or {}).get("value_mpa") or base.e_mpa
    ez = (f.get("e_z") or {}).get("value_mpa")
    if ez is not None and ez / e > E_Z_MAX_RATIO:
        out.append(f"E_z / E {ez / e:.3g} > {E_Z_MAX_RATIO:g}")
    if (f.get("e") or {}).get("method", "").startswith("secant"):
        out.append("E is a secant modulus at break (a lower bound), not applied")
    return out


def apply(m: Material, rec: dict) -> Material:
    """m with the fitted values of a calibration record (a secant modulus is never applied:
    only bend and part tests set E). Callers check plausibility() first."""
    f = rec.get("fit") or {}
    kw: dict = {}
    if "e" in f and not f["e"].get("method", "").startswith("secant"):
        kw["e_mpa"] = f["e"]["value_mpa"]
    if "e_z" in f:
        kw["e_z_mpa"] = f["e_z"]["value_mpa"]
    if "strength" in f:
        kw["yield_mpa"] = f["strength"]["value_mpa"]
    if "z_strength" in f:
        kw["z_strength_mpa"] = f["z_strength"]["value_mpa"]
    if "knockdown" in f:
        kw["knockdown"] = f["knockdown"]
    n = int(f.get("n_tests") or 0)
    label = basis_label(f)
    by = basis_by_property(f)
    notes = []
    if "e_mpa" not in kw:
        sec = (f.get("e_secant") or (f.get("e") if "e" in f else None) or {})
        if sec.get("mean") or sec.get("value_mpa"):
            notes.append(f"E stays the data sheet value; the tensile secant modulus "
                         f"{sec.get('mean') or sec.get('value_mpa'):.4g} MPa is a lower bound "
                         "only and is not applied")
    if "e_mpa" not in kw:
        from .materials import BASIS_ESTIMATE
        by["e"] = BASIS_ESTIMATE
    return replace(m, **kw, basis=label, basis_by_property=by,
                   source=f"{m.source}; {label} on {rec['printer']}",
                   calibration={"id": rec["id"], "printer": rec["printer"], "n_tests": n,
                                "confidence": f.get("confidence"),
                                "calibrated": [k for k in PROPERTIES if k in f
                                               and (k != "e" or "e_mpa" in kw)],
                                "notes": notes, "updated_at": rec.get("updated_at")})


def fit_input(home: Path | None, parts_root: Path, inp: CalibrationInput,
              check_record=None) -> tuple[dict, Material, Material]:
    """(fit, base material, calibrated material) of an input: every part test is compared
    with its study (read from the parts dir without following links). check_record(rec) may
    raise to refuse a study outside the caller's scope."""
    from ..cad import safe_io
    from .fields import FIELDS_FILE, Fields
    from .materials import get_material
    from .studies import find_sim, read_record
    base = get_material(inp.material, home)
    ests = []
    for t in inp.part:
        d = find_sim(parts_root, t.sim_id)
        if d is None:
            raise SimError("bad_setup", f"no study {t.sim_id}", hint="run a static study of "
                           "the tested part first")
        rec = read_record(d, d.parent.parent.parent)
        if check_record is not None:
            check_record(rec)
        if (rec.get("material") or {}).get("name", "").lower() != base.name.lower():
            raise SimError("bad_setup", f"{t.sim_id} studied {rec.get('material', {}).get('name')}"
                           f", not {base.name}", hint="compare a part with a study of the same "
                                                      "material")
        try:
            data = safe_io.read_bytes(d, FIELDS_FILE)
            fields = Fields(data) if data is not None else None
        except (OSError, ValueError, KeyError, safe_io.UnsafePath):
            fields = None
        if fields is None:
            raise SimError("bad_setup", f"{t.sim_id} has no readable result fields")
        ests.append(part_estimate(rec, fields, t))
    fitted = fit(inp, base, ests)
    preview = apply(base, {"id": "cal_preview", "printer": inp.printer, "fit": fitted})
    return fitted, base, preview


# ---- store

def _path(home: Path) -> Path:
    return Path(home) / CAL_FILE


def load_all(home: Path | None) -> list[dict]:
    if home is None:
        return []
    try:
        d = json.loads(_path(home).read_text())
    except (OSError, ValueError):
        return []
    return [r for r in d if isinstance(r, dict) and CAL_ID_RE.fullmatch(str(r.get("id", "")))
            and isinstance(r.get("fit"), dict)] if isinstance(d, list) else []


def _save(home: Path, recs: list[dict]) -> None:
    p = _path(home)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(recs, indent=1, default=float))
    os.replace(tmp, p)


def _same(r: dict, material: str, printer: str) -> bool:
    return (str(r.get("material", "")).lower() == material.strip().lower()
            and str(r.get("printer", "")).lower() == printer.strip().lower())


def find(home: Path | None, material: str, printer: str | None = None) -> dict | None:
    printer = printer or DEFAULT_PRINTER
    return next((r for r in load_all(home) if _same(r, material, printer)), None)


def get(home: Path | None, cal_id: str) -> dict | None:
    return next((r for r in load_all(home) if r["id"] == cal_id), None)


def applicable(home: Path | None, base: Material, printer: str | None = None) -> dict | None:
    """The record a study of `base` on `printer` uses: only a plausible one (never a rejected
    or implausible fit, which stays visible in the list for the user to fix)."""
    rec = find(home, base.name, printer)
    if rec is None or plausibility(rec, base):
        return None
    return rec


def save(home: Path, inp: CalibrationInput, fitted: dict) -> dict:
    """Create or replace the record of (material, printer). A rejected fit is saved too, with
    status "rejected" and its reasons, so the user sees why; studies never use it."""
    with _LOCK:
        return _save_locked(home, inp, fitted)


def _save_locked(home: Path, inp: CalibrationInput, fitted: dict) -> dict:
    recs = load_all(home)
    old = next((r for r in recs if _same(r, inp.material, inp.printer)), None)
    if old is None and len(recs) >= MAX_RECORDS:
        raise SimError("bad_setup", f"at most {MAX_RECORDS} calibrations")
    now = time.time()
    rec = {"id": old["id"] if old else "cal_" + secrets.token_hex(5),
           "material": inp.material.strip(), "printer": inp.printer.strip(),
           "created_at": old["created_at"] if old else now, "updated_at": now,
           "note": inp.note, "basis": fit_label(fitted),
           "status": fitted.get("status", "ok"), "status_label": fitted.get("status_label", "ok"),
           "reasons": fitted.get("reasons", []),
           "tests": {"tensile": [t.model_dump() for t in inp.tensile],
                     "bend": [t.model_dump() for t in inp.bend],
                     "part": [t.model_dump() for t in inp.part]},
           "fit": fitted}
    _save(home, [r for r in recs if r is not old] + [rec])
    return rec


def delete(home: Path, cal_id: str) -> bool:
    with _LOCK:
        recs = load_all(home)
        left = [r for r in recs if r["id"] != cal_id]
        if len(left) == len(recs):
            return False
        _save(home, left)
        return True


def merged(rec: dict | None, inp: CalibrationInput) -> CalibrationInput:
    """inp with the tests already in rec in front (append mode)."""
    if rec is None:
        return inp
    t = rec.get("tests") or {}
    try:
        return CalibrationInput(
            material=inp.material, printer=inp.printer,
            tensile=[*t.get("tensile", []), *[x.model_dump() for x in inp.tensile]],
            bend=[*t.get("bend", []), *[x.model_dump() for x in inp.bend]],
            part=[*t.get("part", []), *[x.model_dump() for x in inp.part]],
            note=inp.note or rec.get("note", ""))
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:4])
        raise SimError("bad_setup", f"at most {MAX_TESTS} tests of each kind ({msg})",
                       hint="replace the saved tests (append=false) or delete some") from None


def summary(rec: dict) -> str:
    f = rec["fit"]
    bits = []
    if "e" in f:
        bits.append(f"E {f['e']['value_mpa']:.4g} MPa (n {f['e']['n']})")
    if "e_z" in f:
        bits.append(f"E_z {f['e_z']['value_mpa']:.4g} MPa (n {f['e_z']['n']})")
    if "strength" in f:
        s = f["strength"]
        cv = "n/a" if s["cv"] is None else f"{100 * s['cv']:.1f}%"
        bits.append(f"strength {s['value_mpa']:.3g} MPa (n {s['n']}, CV {cv})")
    if "z_strength" in f:
        bits.append(f"Z strength {f['z_strength']['value_mpa']:.3g} MPa")
    if "knockdown" in f:
        bits.append(f"knock-down {f['knockdown']:.3g}")
    if f.get("status") == REJECTED:
        return (f"{rec['material']} on {rec['printer']}: {REJECTED_LABEL} (NOT applied to "
                f"studies): " + "; ".join(f.get("reasons") or []))
    if f.get("outliers"):
        bits.append(f"{len(f['outliers'])} outlier test(s) excluded")
    if f.get("e_secant") and "e" not in f:
        bits.append(f"tensile secant modulus {f['e_secant']['mean']:.4g} MPa is a lower bound "
                    "only (E stays the data sheet value)")
    conf = "" if "low confidence" in rec["basis"] else f", confidence {f.get('confidence')}"
    return (f"{rec['material']} on {rec['printer']}: {rec['basis']}{conf}; "
            + ", ".join(bits))
