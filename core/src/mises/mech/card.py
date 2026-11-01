"""The mechanism card (research 2026-11-01 §2.1): what a library mechanism tells the agent, and
the build plan a mechanism builder returns (parts, instances, joints, loops) before mech_insert
turns it into real parts and assembly rows.

A mechanism is not just a shape: its card carries ports (where it connects), joints (with axes,
limits and couplings), mobility, transmission (ratio or a sampled curve), range of motion,
sizing results, the checks that apply and warnings. Coordinates in a plan are the mechanism's
own (its root part's frame); mech_insert places the root where `at` says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

Vec3 = tuple[float, float, float]


class Port(BaseModel):
    """Where the mechanism connects to the rest of the robot."""
    name: str                                   # "input", "output", "mount", "foot"
    kind: Literal["rot", "lin", "mount", "end_effector"]
    instance: str                               # plan instance key that carries it
    origin: list[float]                         # mechanism coordinates, mm
    axis: list[float]
    note: str = ""


class MechJoint(BaseModel):
    """One joint as the card lists it (maps 1:1 to an assembly Joint or Loop)."""
    key: str
    kind: Literal["fixed", "revolute", "slider", "cylindrical", "ball", "universal",
                  "gear", "rack", "screw", "belt", "loop_connect"]
    a: str
    b: str
    limits: list[float] | None = None
    ratio: float | None = None                  # gear/belt deg/deg; rack/screw mm/deg
    partner: str | None = None                  # the joint a coupling follows
    note: str = ""


class MechanismCard(BaseModel):
    id: str
    title: str
    category: str
    params: dict[str, Any]
    parts: list[str]
    joints: list[MechJoint]
    dof: int                                    # mobility (Grubler / Kutzbach), computed
    dof_intended: int
    mobility_formula: str
    ports: list[Port]
    transmission: dict = Field(default_factory=dict)
    rom: dict = Field(default_factory=dict)
    sizing: dict = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: str = ""
    # instance pairs that touch by design (meshing teeth, a belt on its pulley, a pin in its
    # bore): the ROM sweep reports their contact separately from real collisions
    contacts: list[list[str]] = Field(default_factory=list)


# ---- build plan (host side, no geometry)

@dataclass
class PartSpec:
    key: str
    recipe: str = ""                 # ms.<recipe>(**kwargs) builds it in the sandboxed worker
    kwargs: dict = field(default_factory=dict)
    label: str = ""
    material: str | None = "PLA"     # the part row's material (mass, studies, BOM)
    catalog: str | None = None       # a built-in catalog item instead of a recipe
    color: str | None = None


@dataclass
class InstSpec:
    key: str
    part: str
    name: str
    transform: list[list[float]] | None = None   # only the root's matters (the solve places)


@dataclass
class JointSpec:
    key: str
    kind: str                        # fixed / revolute / slider / cylindrical / ball
    a: str
    fa: tuple                        # (origin, axis) in a's part coordinates
    b: str
    fb: tuple
    value: float | list[float] = 0.0
    limits: list[float] | None = None
    coupling: dict | None = None     # {"kind", "ratio", "partner": joint key, "offset"}
    design: dict | None = None       # E27 JointDesign fields


@dataclass
class LoopSpec:
    key: str
    a: str
    fa: tuple
    b: str
    fb: tuple
    type: str = "revolute"
    drivers: list[str] | None = None  # joint keys


@dataclass
class MechPlan:
    root: str                        # the grounded instance key (the frame / base)
    parts: list[PartSpec]
    instances: list[InstSpec]
    joints: list[JointSpec]
    loops: list[LoopSpec] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)      # driver joint keys
    ports: list[Port] = field(default_factory=list)
    transmission: dict = field(default_factory=dict)
    rom: dict = field(default_factory=dict)
    sizing: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    contacts: list[tuple[str, str]] = field(default_factory=list)
    planar: bool = False             # mobility by the planar Grubler formula
    dof_intended: int = 1
    notes: str = ""

    def part(self, key: str) -> PartSpec:
        return next(p for p in self.parts if p.key == key)

    def inst(self, key: str) -> InstSpec:
        return next(i for i in self.instances if i.key == key)


Z: Vec3 = (0.0, 0.0, 1.0)
O: Vec3 = (0.0, 0.0, 0.0)


def fr(origin=O, axis=Z) -> tuple:
    """A frame (origin, axis) as plans write it."""
    return (tuple(float(c) for c in origin), tuple(float(c) for c in axis))


# DOF each joint kind leaves (spatial / planar)
FREEDOM = {"fixed": 0, "revolute": 1, "slider": 1, "cylindrical": 2, "ball": 3, "universal": 2}


def mobility(plan: MechPlan) -> tuple[int, str]:
    """Kutzbach (spatial) M = 6(n-1) - sum(6 - f), or planar Grubler M = 3(n-1) - 2 j1 - j2,
    counting loops as joints and each coupling as one more constraint."""
    n = len(plan.instances)
    kinds = [j.kind for j in plan.joints] + [lp.type for lp in plan.loops]
    couplings = sum(1 for j in plan.joints if j.coupling)
    if plan.planar:
        j1 = sum(1 for k in kinds if k in ("revolute", "slider"))
        fixed = sum(1 for k in kinds if k == "fixed")
        m = 3 * (n - 1) - 2 * j1 - 3 * fixed - couplings
        return m, f"planar Grubler 3(n-1) - 2 j1 = 3x{n - 1} - 2x{j1}" + (
            f" - 3x{fixed} fixed" if fixed else "") + (f" - {couplings} couplings"
                                                      if couplings else "")
    s = sum(6 - FREEDOM.get(k, 0) for k in kinds)
    m = 6 * (n - 1) - s - couplings
    return m, f"Kutzbach 6(n-1) - sum(6-f) = 6x{n - 1} - {s}" + (
        f" - {couplings} couplings" if couplings else "")
