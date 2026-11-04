"""The mechanism library registry (research 2026-11-01 §2.2): id -> builder, the functions it
serves, its parameter schema (defaults, ranges, units) and the checks that apply.

Builders live in drive.py, linkage.py, joints.py, grippers.py and legs.py; importing this
module imports them all so the registry is complete. A builder takes the resolved params and
returns a MechPlan (card.py); `card_for` turns the plan into the MechanismCard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..types import ErrorCode, MisesError
from .card import MechanismCard, MechJoint, MechPlan, mobility


@dataclass
class P:
    """One parameter: default, range (inclusive), unit and a one-line meaning."""
    default: Any
    lo: float | None = None
    hi: float | None = None
    unit: str = "mm"
    doc: str = ""
    choices: tuple | None = None

    def check(self, name: str, v: Any) -> Any:
        if self.choices is not None:
            if v not in self.choices:
                raise MisesError(ErrorCode.INVALID_INPUT, f"{name} must be one of "
                                 f"{list(self.choices)} (got {v!r})")
            return v
        if isinstance(self.default, bool):
            return bool(v)
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise MisesError(ErrorCode.INVALID_INPUT, f"{name} must be a number") from None
        if num != num or num in (float("inf"), float("-inf")):
            raise MisesError(ErrorCode.INVALID_INPUT, f"{name} must be finite")
        if (self.lo is not None and num < self.lo) or (self.hi is not None and num > self.hi):
            lo = "-inf" if self.lo is None else f"{self.lo:g}"
            hi = "inf" if self.hi is None else f"{self.hi:g}"
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"{name}={num:g} is outside {lo}..{hi} {self.unit}",
                             hint=self.doc or None)
        return int(round(num)) if isinstance(self.default, int) else num


@dataclass
class Mechanism:
    id: str
    title: str
    category: str
    summary: str
    functions: list[str]
    params: dict[str, P]
    builder: Callable[[dict], MechPlan]
    checks: list[str] = field(default_factory=list)
    motion: str = ""               # "rot->rot", "rot->lin", "lin->lin", "rot->path", ...

    def resolve(self, given: dict | None) -> dict:
        given = dict(given or {})
        unknown = set(given) - set(self.params)
        if unknown:
            raise MisesError(ErrorCode.INVALID_INPUT,
                             f"{self.id} has no parameter(s) {sorted(unknown)}",
                             hint=f"parameters: {', '.join(self.params)}")
        return {k: p.check(k, given.get(k, p.default)) for k, p in self.params.items()}

    def schema(self) -> dict:
        return {k: {"default": p.default, "min": p.lo, "max": p.hi, "unit": p.unit,
                    "doc": p.doc, **({"choices": list(p.choices)} if p.choices else {})}
                for k, p in self.params.items()}


REGISTRY: dict[str, Mechanism] = {}


def mechanism(id: str, title: str, category: str, summary: str, functions: list[str],
              params: dict[str, P], checks: list[str] | None = None, motion: str = ""):
    def wrap(fn: Callable[[dict], MechPlan]):
        if id in REGISTRY:
            raise ValueError(f"duplicate mechanism {id}")
        REGISTRY[id] = Mechanism(id=id, title=title, category=category, summary=summary,
                                 functions=functions, params=params, builder=fn,
                                 checks=checks or [], motion=motion)
        return fn
    return wrap


def get(mech_id: str) -> Mechanism:
    _load()
    m = REGISTRY.get(mech_id)
    if m is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no mechanism {mech_id!r}",
                         hint="mech_list lists them")
    return m


def all_mechanisms() -> list[Mechanism]:
    _load()
    return list(REGISTRY.values())


_LOADED = False


def _load() -> None:
    global _LOADED
    if not _LOADED:
        from . import drive, grippers, joints, legs, linkage, tvc  # noqa: F401 - register builders
        _LOADED = True


def plan_for(mech_id: str, params: dict | None = None) -> tuple[Mechanism, dict, MechPlan]:
    m = get(mech_id)
    p = m.resolve(params)
    try:
        plan = m.builder(p)
    except ValueError as e:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{mech_id}: {e}") from None
    return m, p, plan


def card_for(m: Mechanism, params: dict, plan: MechPlan) -> MechanismCard:
    dof, formula = mobility(plan)
    if plan.loops:
        # Grubler/Kutzbach miscount planar or spherical loops in a spatial chain: the rank of
        # the loop-closure Jacobian is exact (the textbook count stays in the formula text)
        from .assemble import numeric_mobility
        dof, num = numeric_mobility(plan)
        formula = f"{num} ({formula})"
    joints: list[MechJoint] = []
    for j in plan.joints:
        kind = j.kind
        if j.coupling:
            joints.append(MechJoint(key=j.key + "~", kind=j.coupling["kind"], a=j.coupling["partner"],
                                    b=j.key, ratio=j.coupling["ratio"],
                                    partner=j.coupling["partner"],
                                    note="coupling: follower value = offset + ratio x partner"))
        joints.append(MechJoint(key=j.key, kind=kind, a=j.a, b=j.b, limits=j.limits))
    for lp in plan.loops:
        joints.append(MechJoint(key=lp.key, kind="loop_connect", a=lp.a, b=lp.b,
                                note=f"{lp.type} loop closure"))
    warnings = list(plan.warnings)
    if dof != plan.dof_intended:
        warnings.append(f"mobility {dof} differs from the intended {plan.dof_intended} "
                        f"({formula})" + (" - a planar linkage counted spatially is "
                                          "over-constrained; that is expected" if not plan.planar
                                          and plan.loops else ""))
    return MechanismCard(id=m.id, title=m.title, category=m.category, params=params,
                         parts=[p.key for p in plan.parts], joints=joints, dof=dof,
                         dof_intended=plan.dof_intended, mobility_formula=formula,
                         ports=plan.ports, transmission=plan.transmission, rom=plan.rom,
                         sizing=plan.sizing, checks=list(m.checks), warnings=warnings,
                         notes=plan.notes, contacts=[list(c) for c in plan.contacts])


# ---- ranking (mech_list)

_WORD = re.compile(r"[a-z0-9]+")
_SYN = {"linear": {"lin", "translate", "translation", "straight", "slide", "push"},
        "rotation": {"rotary", "rotate", "turn", "spin", "rot"},
        "reduce": {"reduction", "reducer", "gearbox", "torque", "slow"},
        "grip": {"gripper", "grasp", "pick", "jaw", "claw", "hand"},
        "walk": {"leg", "legged", "walking", "gait", "foot", "quadruped", "biped"},
        "hinge": {"door", "lid", "flap", "pivot", "fold"},
        "belt": {"pulley", "timing", "gt2", "htd"},
        "screw": {"lead", "ball", "spindle"}}


def _words(text: str) -> set[str]:
    ws = set(_WORD.findall(text.lower()))
    for k, syn in _SYN.items():
        if ws & (syn | {k}):
            ws |= syn | {k}
    return ws


def rank(function: str = "", constraints: dict | None = None, category: str | None = None,
         limit: int = 8) -> list[dict]:
    """Mechanisms ranked by how well their functions/title/summary match `function`, filtered by
    category, with constraint notes (ratio / stroke / torque) when given."""
    want = _words(function or "")
    cons = dict(constraints or {})
    rows = []
    for m in all_mechanisms():
        if category and m.category != category:
            continue
        have = _words(" ".join([m.title, m.summary, m.id.replace("_", " "), *m.functions,
                                m.category, m.motion]))
        score = len(want & have) / max(1, len(want)) if want else 0.5
        notes = []
        ratio = cons.get("ratio")
        if ratio is not None:
            if "ratio" in m.params or any(k.startswith("teeth") or k in ("z1", "z2", "zs", "zr")
                                          for k in m.params):
                score += 0.2
                notes.append(f"set teeth for ratio {ratio:g}")
            else:
                score -= 0.2
        if cons.get("stroke_mm") is not None:
            if m.motion.endswith("lin"):
                score += 0.2
            else:
                score -= 0.1
        rows.append({"id": m.id, "title": m.title, "category": m.category,
                     "summary": m.summary, "motion": m.motion, "functions": m.functions,
                     "score": round(score, 3), "notes": notes, "checks": m.checks})
    rows.sort(key=lambda r: -r["score"])
    return rows[:max(1, limit)]
