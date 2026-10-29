"""The engineering spec: goal, requirements, load cases, interview answers.

A spec is the contract the engineer agent writes from the interview and then measures every
variant against. Requirements are machine-checkable where they can be; `custom`, `mount_pattern`
and `printable` are judged from geometry or left "not checked" rather than faked as a pass.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..naming import check_name
from ..types import MisesError


def _check_id(name: str) -> None:
    """check_name, but raised as ValueError so pydantic folds it into a ValidationError."""
    try:
        check_name(name)
    except MisesError as e:
        raise ValueError(str(e)) from None

RequirementKind = Literal[
    "max_mass_g", "min_safety_factor", "max_deflection_mm", "envelope_mm",
    "mount_pattern", "printable", "custom",
]
# Kinds whose target is a single positive number.
_NUMERIC_KINDS = {"max_mass_g", "min_safety_factor", "max_deflection_mm"}
# Kinds evaluated against a load-case result rather than plain geometry.
LOAD_CASE_KINDS = {"min_safety_factor", "max_deflection_mm"}

Status = Literal["pass", "warn", "fail", "not_checked"]
Direction = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


class Requirement(BaseModel):
    id: str = Field(min_length=1, max_length=48)
    kind: RequirementKind
    target: Any = None
    load_case_id: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    source: Literal["user", "agent"] = "agent"

    @model_validator(mode="after")
    def _check_target(self) -> "Requirement":
        _check_id(self.id)
        k, t = self.kind, self.target
        if k in _NUMERIC_KINDS:
            if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
                raise ValueError(f"{k} target must be a positive number")
        elif k == "envelope_mm":
            if (not isinstance(t, (list, tuple)) or len(t) != 3
                    or any((not isinstance(x, (int, float)) or isinstance(x, bool) or x <= 0)
                           for x in t)):
                raise ValueError("envelope_mm target must be three positive numbers [x, y, z]")
            self.target = [float(x) for x in t]
        elif k == "printable":
            if not isinstance(t, bool):
                raise ValueError("printable target must be a bool")
        elif k == "mount_pattern":
            if not isinstance(t, str) or not t.strip():
                raise ValueError("mount_pattern target must be a non-empty string")
        if k in LOAD_CASE_KINDS and not self.load_case_id:
            raise ValueError(f"{k} needs a load_case_id")
        return self


class LoadCase(BaseModel):
    id: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=120)
    fix: str = Field(description="face held fixed: a preset or a face id")
    load: str = Field(description="face the load is applied to: a preset or a face id")
    newtons: float = Field(gt=0, le=1e6)
    direction: Direction
    note: str = ""
    estimate: bool = False

    @model_validator(mode="after")
    def _check(self) -> "LoadCase":
        _check_id(self.id)
        if self.fix == self.load:
            raise ValueError("fix and load must be different faces")
        return self


class Spec(BaseModel):
    goal: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    load_cases: list[LoadCase] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross_check(self) -> "Spec":
        req_ids = [r.id for r in self.requirements]
        if len(set(req_ids)) != len(req_ids):
            raise ValueError("requirement ids must be unique")
        case_ids = {c.id for c in self.load_cases}
        if len(case_ids) != len(self.load_cases):
            raise ValueError("load case ids must be unique")
        for r in self.requirements:
            if r.load_case_id and r.load_case_id not in case_ids:
                raise ValueError(f"requirement {r.id} references unknown load case "
                                 f"{r.load_case_id!r}")
        return self


# ---- evaluation

def _min_sf(sim_by_case: dict, case_id: str | None) -> float | None:
    rec = sim_by_case.get(case_id) if case_id else None
    if not isinstance(rec, dict):
        return None
    sf = rec.get("safety_factor")
    return sf if isinstance(sf, (int, float)) else None


def _max_disp(sim_by_case: dict, case_id: str | None) -> float | None:
    rec = sim_by_case.get(case_id) if case_id else None
    if not isinstance(rec, dict):
        return None
    d = rec.get("max_displacement_mm")
    return d if isinstance(d, (int, float)) else None


def evaluate_requirement(req: Requirement, metrics: dict, sim_by_case: dict) -> dict:
    """One requirement against a part's metrics and its per-load-case sim results.

    Returns {id, kind, target, load_case_id, measured, status, detail}. `warn` is a near-miss
    (within a small margin); a requirement with no evidence is `not_checked`, never `pass`.
    """
    kind, target = req.kind, req.target
    measured: Any = None
    status: Status = "not_checked"
    detail = ""

    if kind == "max_mass_g":
        measured = metrics.get("mass_g")
        if isinstance(measured, (int, float)):
            status = ("pass" if measured <= target
                      else "warn" if measured <= target * 1.05 else "fail")
    elif kind == "min_safety_factor":
        measured = _min_sf(sim_by_case, req.load_case_id)
        if measured is not None:
            status = ("pass" if measured >= target
                      else "warn" if measured >= target * 0.9 else "fail")
        else:
            detail = "no stress study for this load case yet"
    elif kind == "max_deflection_mm":
        measured = _max_disp(sim_by_case, req.load_case_id)
        if measured is not None:
            status = ("pass" if measured <= target
                      else "warn" if measured <= target * 1.1 else "fail")
        else:
            detail = "no stress study for this load case yet"
    elif kind == "envelope_mm":
        size = (metrics.get("bbox") or {}).get("size")
        if isinstance(size, (list, tuple)) and len(size) == 3:
            measured = [round(float(x), 3) for x in size]
            fits = all(s <= t + 1e-6 for s, t in zip(sorted(measured), sorted(target)))
            near = all(s <= t * 1.05 for s, t in zip(sorted(measured), sorted(target)))
            status = "pass" if fits else "warn" if near else "fail"
    elif kind in ("printable", "mount_pattern", "custom"):
        detail = "needs a human or a dedicated check; not evaluated from metrics"

    return {"id": req.id, "kind": kind, "target": target, "load_case_id": req.load_case_id,
            "priority": req.priority, "measured": measured, "status": status, "detail": detail}


def evaluate_spec(spec: Spec, metrics: dict, sim_by_case: dict | None = None) -> dict:
    """Every requirement evaluated. `overall` is the worst status among the hard (checked)
    requirements: fail > warn > pass; not_checked does not make it pass."""
    sim_by_case = sim_by_case or {}
    rows = [evaluate_requirement(r, metrics, sim_by_case) for r in spec.requirements]
    order = {"fail": 3, "warn": 2, "pass": 1, "not_checked": 0}
    checked = [r for r in rows if r["status"] != "not_checked"]
    if not checked:
        overall: Status = "not_checked"
    else:
        worst = max(checked, key=lambda r: order[r["status"]])["status"]
        overall = worst
    return {"requirements": rows, "overall": overall,
            "checked": len(checked), "total": len(rows)}
