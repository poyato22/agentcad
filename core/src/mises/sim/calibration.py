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
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def fit_modulus(estimates: list[float]) -> dict:
    """E from per-test estimates E_i by least squares on the relative deflection residuals
    (module doc): E = sum E_i^2 / sum E_i."""
    e = np.array(estimates, dtype=float)
    fit = float((e ** 2).sum() / e.sum())
    st = _stats(estimates)
    resid = [float(1 - fit / x) for x in e]   # (delta_fit - delta_i) / delta_i
    return {**st, "value_mpa": fit, "residuals": resid,
            "confidence": _confidence(st["n"], st["cv"])}


def fit_strength(strengths: list[float]) -> dict:
    st = _stats(strengths)
    if st["n"] >= 3 and st["sd"] is not None:
        kd, how = (st["mean"] - 2 * st["sd"]) / st["mean"], "(mean - 2 sd) / mean"
    else:
        kd, how = LOW_SAMPLE_FACTOR * st["min"] / st["mean"], "0.85 x weakest / mean (n < 3)"
    kd = min(KNOCKDOWN_RANGE[1], max(KNOCKDOWN_RANGE[0], kd))
    return {**st, "value_mpa": st["mean"], "knockdown": kd, "knockdown_rule": how,
            "confidence": _confidence(st["n"], st["cv"])}


def fit(inp: CalibrationInput, base: Material, part_estimates: list[dict] | None = None) -> dict:
    """The fitted parameters of one material and printer. part_estimates: [{e_mpa, ...}] from
    part_estimate() for every part test."""
    out: dict = {"n_tests": len(inp.tensile) + len(inp.bend) + len(inp.part)}
    e_xy = [b.modulus for b in inp.bend if b.orientation == "xy"]
    e_xy += [p["e_mpa"] for p in part_estimates or []]
    e_z = [b.modulus for b in inp.bend if b.orientation == "z"]
    secant = [t.strength / (t.elongation_pct / 100) for t in inp.tensile
              if t.orientation == "xy" and t.elongation_pct]
    if e_xy:
        out["e"] = fit_modulus(e_xy) | {"method": "least squares: three-point bend / part "
                                                  "load tests"}
    elif secant:
        out["e"] = fit_modulus(secant) | {"method": "secant modulus at break (a lower bound)"}
    if e_z:
        out["e_z"] = fit_modulus(e_z) | {"method": "least squares: three-point bend (z)"}
    s_xy = [t.strength for t in inp.tensile if t.orientation == "xy"]
    s_z = [t.strength for t in inp.tensile if t.orientation == "z"]
    if s_xy:
        out["strength"] = fit_strength(s_xy)
        out["datasheet_ratio"] = out["strength"]["mean"] / base.yield_mpa
    if s_z:
        out["z_strength"] = fit_strength(s_z)
    flex = [b.flexural_strength for b in inp.bend if b.flexural_strength is not None]
    if flex:
        out["flexural_strength"] = _stats(flex)
    kds = [out[k]["knockdown"] for k in ("strength", "z_strength") if k in out]
    if kds:
        out["knockdown"] = min(kds)
    confs = [out[k]["confidence"] for k in ("e", "e_z", "strength", "z_strength") if k in out]
    order = {"low": 0, "medium": 1, "high": 2}
    out["confidence"] = min(confs, key=order.get) if confs else "low"
    if part_estimates:
        out["part_tests"] = part_estimates
    return out


def part_estimate(rec: dict, fields, t: PartTest) -> dict:
    """E from one part load test against its static study (inverse comparison).
    rec: the study record; fields: its Fields."""
    if rec.get("type") not in ("static", None) or rec.get("status") != "done":
        raise SimError("bad_setup", f"{t.sim_id} is not a finished static study",
                       hint="run a static study of the part first")
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
    return {"sim_id": t.sim_id, "e_mpa": e_eff / stiff, "e_study_mpa": e_study / stiff,
            "predicted_deflection_mm": pred, "measured_deflection_mm": t.deflection_mm,
            "study_load_n": load}


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


def basis_label(n: int) -> str:
    return f"calibrated ({n} test{'s' if n != 1 else ''})"


def apply(m: Material, rec: dict) -> Material:
    """m with the fitted values of a calibration record."""
    f = rec.get("fit") or {}
    kw: dict = {}
    if "e" in f:
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
    label = basis_label(n)
    return replace(m, **kw, basis=label, source=f"{m.source}; {label} on {rec['printer']}",
                   calibration={"id": rec["id"], "printer": rec["printer"], "n_tests": n,
                                "confidence": f.get("confidence"),
                                "updated_at": rec.get("updated_at")})


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


def save(home: Path, inp: CalibrationInput, fitted: dict) -> dict:
    """Create or replace the record of (material, printer)."""
    recs = load_all(home)
    old = next((r for r in recs if _same(r, inp.material, inp.printer)), None)
    if old is None and len(recs) >= MAX_RECORDS:
        raise SimError("bad_setup", f"at most {MAX_RECORDS} calibrations")
    now = time.time()
    rec = {"id": old["id"] if old else "cal_" + secrets.token_hex(5),
           "material": inp.material.strip(), "printer": inp.printer.strip(),
           "created_at": old["created_at"] if old else now, "updated_at": now,
           "note": inp.note, "basis": basis_label(fitted["n_tests"]),
           "tests": {"tensile": [t.model_dump() for t in inp.tensile],
                     "bend": [t.model_dump() for t in inp.bend],
                     "part": [t.model_dump() for t in inp.part]},
           "fit": fitted}
    _save(home, [r for r in recs if r is not old] + [rec])
    return rec


def delete(home: Path, cal_id: str) -> bool:
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
    return CalibrationInput(material=inp.material, printer=inp.printer,
                            tensile=[*t.get("tensile", []), *[x.model_dump() for x in inp.tensile]],
                            bend=[*t.get("bend", []), *[x.model_dump() for x in inp.bend]],
                            part=[*t.get("part", []), *[x.model_dump() for x in inp.part]],
                            note=inp.note or rec.get("note", ""))


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
    return (f"{rec['material']} on {rec['printer']}: {rec['basis']}, confidence "
            f"{f.get('confidence')}; " + ", ".join(bits))
