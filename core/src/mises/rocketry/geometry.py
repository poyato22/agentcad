"""A rocket's outer geometry and its mass distribution (Plan 11 F6).

Geometry: given explicitly (RocketGeometry), else read from the build's layout skeleton when it
uses the rocket names below (the convention the rocketry prompt asks agents to lay out with):

    nose up along +z; body tube(s) from z = body_aft_z (default 0) to nose_base_z;
    body_od, nose_len, nose_shape (0 ogive, 1 conical, 2 parabolic, 3 elliptical; default ogive);
    fin_count, fin_root, fin_tip, fin_span, fin_sweep (root LE to tip LE), fin_t, fin_root_start
    (the fin root's trailing edge z); optional boattail_len + boattail_aft_od below body_aft_z.

Mass: every assembly instance at its solved pose (the same B-rep mass and CG the dynamics use:
dyn.inertia, mass = volume x the part's material), projected onto the rocket axis and measured
from the nose tip. The build's motor part (a placeholder solid) is replaced by the real motor's
mass at the same aft position; parts can be re-weighed (a 3D-printed "egg" is not an egg) and
point masses added.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from ..types import ErrorCode, MisesError
from .aero import Body, Fins, Nose, Section

NoseShape = Literal["ogive", "conical", "parabolic", "elliptical", "haack"]
Profile = Literal["square", "rounded", "airfoil"]
Axis = Literal["+z", "-z", "+x", "-x", "+y", "-y"]
NOSE_CODES = {0: "ogive", 1: "conical", 2: "parabolic", 3: "elliptical", 4: "haack"}
MOTOR_RE = re.compile(r"(^|_)motor($|_\d|_case|_[a-z]?\d)", re.I)
NOT_MOTOR_RE = re.compile(r"mount|retain|tube|block|clip|hook|ring|plate", re.I)


class NoseArgs(BaseModel):
    shape: NoseShape = "ogive"
    length_mm: float = Field(gt=0, le=5000)
    diameter_mm: float | None = Field(None, gt=0, le=1000, description="base diameter; "
                                      "default the body diameter")


class SectionArgs(BaseModel):
    length_mm: float = Field(gt=0, le=10000)
    aft_diameter_mm: float | None = Field(None, gt=0, le=1000, description="a transition "
                                          "(boattail / shoulder) ends at this diameter; leave "
                                          "out for a straight tube")


class FinArgs(BaseModel):
    count: int = Field(ge=1, le=8)
    root_chord_mm: float = Field(gt=0, le=2000)
    tip_chord_mm: float = Field(ge=0, le=2000)
    span_mm: float = Field(gt=0, le=2000, description="semi-span: root to tip, outward")
    sweep_mm: float = Field(0, ge=-2000, le=2000, description="root LE to tip LE along the "
                            "axis (aft positive)")
    thickness_mm: float = Field(gt=0, le=100)
    le_from_tip_mm: float | None = Field(None, ge=0, description="root leading edge, from the "
                                         "nose tip")
    te_from_aft_mm: float | None = Field(None, ge=0, description="root trailing edge, forward "
                                         "of the aft end (0 = flush)")
    profile: Profile = "square"
    material: str | None = Field(None, description="for the flutter check; default the fins "
                                 "part's material")

    @model_validator(mode="after")
    def _where(self):
        if self.le_from_tip_mm is None and self.te_from_aft_mm is None:
            self.te_from_aft_mm = 0.0
        return self


class RocketGeometry(BaseModel):
    body_diameter_mm: float = Field(gt=0, le=1000)
    nose: NoseArgs
    sections: list[SectionArgs] = Field(min_length=1, max_length=12, description="aft of the "
                                        "nose, in order: tubes and transitions")
    fins: list[FinArgs] = Field(min_length=1, max_length=3)
    axis: Axis = Field("+z", description="the direction the nose points in the build frame")
    nose_tip_mm: float | None = Field(None, description="the nose tip's coordinate along the "
                                      "axis in the build frame (lines the parts' CG up with the "
                                      "geometry); default the assembly's furthest point")
    rail_buttons: int = Field(2, ge=0, le=6, description="rail buttons / launch lugs (each adds "
                                                         "a little drag)")


@dataclass
class Built:
    body: Body
    source: str
    nose_tip_axial_mm: float | None
    axis: np.ndarray
    fin_args: list[FinArgs]
    rail_buttons: int
    notes: list[str] = field(default_factory=list)


AXES = {"+z": (0, 0, 1), "-z": (0, 0, -1), "+x": (1, 0, 0), "-x": (-1, 0, 0),
        "+y": (0, 1, 0), "-y": (0, -1, 0)}


def build_body(g: RocketGeometry, source: str = "given") -> Built:
    d = g.body_diameter_mm / 1000.0
    nd = (g.nose.diameter_mm or g.body_diameter_mm) / 1000.0
    nose = Nose(g.nose.shape, g.nose.length_mm / 1000.0, nd)
    x, cur = nose.length, nd
    secs: list[Section] = []
    for s in g.sections:
        aft = s.aft_diameter_mm / 1000.0 if s.aft_diameter_mm else cur
        secs.append(Section(x, s.length_mm / 1000.0, cur, aft))
        x += s.length_mm / 1000.0
        cur = aft
    total = x
    # the aft end of the last straight tube (fins sit on tubes, not on a boattail)
    tube_end = max((s.x + s.length for s in secs if not s.is_transition), default=total)
    fins: list[Fins] = []
    for f in g.fins:
        root = f.root_chord_mm / 1000.0
        if f.le_from_tip_mm is not None:
            x_le = f.le_from_tip_mm / 1000.0
        else:
            x_le = tube_end - (f.te_from_aft_mm or 0.0) / 1000.0 - root
        body_d = _diameter_at(nose, secs, x_le + root / 2)
        fins.append(Fins(f.count, root, f.tip_chord_mm / 1000.0, f.span_mm / 1000.0,
                         f.sweep_mm / 1000.0, f.thickness_mm / 1000.0, x_le, body_d,
                         profile=f.profile))
    ref = max([nd, d, *(s.d_fore for s in secs), *(s.d_aft for s in secs)])
    return Built(Body(nose, secs, fins, ref), source, g.nose_tip_mm, np.array(AXES[g.axis],
                                                                                float),
                 list(g.fins), g.rail_buttons)


def _diameter_at(nose: Nose, secs: list[Section], x: float) -> float:
    for s in secs:
        if s.x <= x <= s.x + s.length:
            return s.d_fore + (s.d_aft - s.d_fore) * (x - s.x) / s.length
    return secs[-1].d_aft if secs else nose.diameter


SKELETON_NEEDS = ("body_od", "nose_len", "nose_base_z", "fin_count", "fin_root", "fin_tip",
                  "fin_span", "fin_t", "fin_root_start")


def from_skeleton(params: dict) -> tuple[RocketGeometry | None, list[str]]:
    """(geometry, missing names) from a layout skeleton that uses the rocket names."""
    missing = [n for n in SKELETON_NEEDS if n not in params]
    if missing:
        return None, missing
    p = {k: float(v) for k, v in params.items() if isinstance(v, (int, float))}
    aft_z = p.get("body_aft_z", 0.0)
    tip_z = p["nose_base_z"] + p["nose_len"]
    sections = [SectionArgs(length_mm=p["nose_base_z"] - aft_z)]
    if p.get("boattail_len", 0) > 0 and p.get("boattail_aft_od"):
        sections.append(SectionArgs(length_mm=p["boattail_len"],
                                    aft_diameter_mm=p["boattail_aft_od"]))
    le_z = p["fin_root_start"] + p["fin_root"]
    shape = NOSE_CODES.get(int(p.get("nose_shape", 0)), "ogive")
    g = RocketGeometry(
        body_diameter_mm=p["body_od"], nose=NoseArgs(shape=shape, length_mm=p["nose_len"]),
        sections=sections,
        fins=[FinArgs(count=int(p["fin_count"]), root_chord_mm=p["fin_root"],
                      tip_chord_mm=p["fin_tip"], span_mm=p["fin_span"],
                      sweep_mm=p.get("fin_sweep", 0.0), thickness_mm=p["fin_t"],
                      le_from_tip_mm=tip_z - le_z)],
        axis="+z", nose_tip_mm=tip_z)
    return g, []


# ---- mass

class MassItem(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    mass_g: float = Field(gt=0, le=100000)
    at_mm: float = Field(ge=0, description="its CG, from the nose tip along the axis")


@dataclass
class MassRow:
    name: str
    part_id: str | None
    mass_kg: float
    x_m: float              # CG from the nose tip
    source: str


@dataclass
class MassModel:
    rows: list[MassRow]
    motor_aft_m: float | None       # the motor's aft end from the tip (from the motor part)
    motor_parts: list[str]
    extent_m: tuple[float, float] | None    # the parts' span along the axis, from the tip
    notes: list[str]

    @property
    def dry_kg(self) -> float:
        return sum(r.mass_kg for r in self.rows)

    @property
    def cg_m(self) -> float:
        m = self.dry_kg
        return sum(r.mass_kg * r.x_m for r in self.rows) / m if m > 0 else 0.0


def is_motor_part(part_id: str, name: str) -> bool:
    for s in (part_id, name):
        if s and MOTOR_RE.search(s) and not NOT_MOTOR_RE.search(s):
            return True
    return False


def gather(home: Path, store, asm, axis: np.ndarray, tip_axial_mm: float | None, *,
           overrides_g: dict[str, float], exclude: list[str],
           motor_parts: list[str] | None) -> MassModel:
    """The build's parts as point masses along the axis (see the module doc)."""
    from ..dyn import export
    notes: list[str] = []
    rows_raw: list[dict] = []
    if asm is not None and getattr(asm, "instances", None):
        kin = export.kinematics(home, store, asm)
        notes += kin["notes"]
        for r in kin["instances"]:
            rows_raw.append({"name": r["name"], "part_id": r["part_id"], "mass_kg": r["mass_kg"],
                             "com_m": np.array(r["com"]), "lo": np.array(r["aabb"][0]),
                             "hi": np.array(r["aabb"][1])})
    if not rows_raw:
        raise MisesError(ErrorCode.INVALID_INPUT, "the build's assembly is empty: no parts to "
                         "weigh", hint="add the parts to the assembly, or pass dry_mass_g and "
                         "cg_mm (an OpenRocket-style override)")

    def axial(v: np.ndarray) -> float:
        return float(np.dot(v, axis)) * 1000.0          # mm along the axis

    def span(r) -> tuple[float, float]:
        corners = [np.array([a, b, c]) for a in (r["lo"][0], r["hi"][0])
                   for b in (r["lo"][1], r["hi"][1]) for c in (r["lo"][2], r["hi"][2])]
        vals = [axial(c) for c in corners]
        return min(vals), max(vals)

    spans = [span(r) for r in rows_raw]
    tip = tip_axial_mm if tip_axial_mm is not None else max(s[1] for s in spans)
    lo_all, hi_all = min(s[0] for s in spans), max(s[1] for s in spans)
    out: list[MassRow] = []
    motor_aft = None
    mparts: list[str] = []
    for r, (lo, hi) in zip(rows_raw, spans):
        pid, name = r["part_id"], r["name"]
        if pid in exclude or name in exclude:
            continue
        is_motor = (pid in motor_parts or name in motor_parts) if motor_parts is not None \
            else is_motor_part(pid, name)
        if is_motor:
            mparts.append(pid)
            aft = (tip - lo) / 1000.0
            motor_aft = aft if motor_aft is None else max(motor_aft, aft)
            continue
        x = (tip - axial(r["com_m"])) / 1000.0
        if pid in overrides_g or name in overrides_g:
            g = overrides_g.get(pid, overrides_g.get(name))
            out.append(MassRow(name, pid, float(g) / 1000.0, x, "override"))
        else:
            out.append(MassRow(name, pid, r["mass_kg"], x, "B-rep x material"))
    return MassModel(out, motor_aft, mparts, ((tip - hi_all) / 1000.0, (tip - lo_all) / 1000.0),
                     notes)
