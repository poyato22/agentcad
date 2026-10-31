"""The topopt_* tool arguments (also the HTTP bodies)."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..sim.settings import check_face

TOPOPT_ID_RE = re.compile(r"^to_[0-9a-f]{12}$")
VARIANT_ID_RE = re.compile(r"^v\d{1,2}$")
Direction = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
Axis = Literal["x", "y", "z"]
BuildDir = Literal["+x", "-x", "+y", "-y", "+z", "-z", "x", "y", "z"]
VARIANT_KEYS = {"name", "volume_fraction", "filter_radius_mm", "resolution_mm",
                "manufacturing", "max_iter"}
SWEEP_KEYS = {"volume_fraction", "filter_radius_mm", "resolution_mm"}


class Cylinder(BaseModel):
    center: list[float] = Field(min_length=3, max_length=3, description="mid-point, mm")
    axis: list[float] = Field(min_length=3, max_length=3)
    radius: float = Field(gt=0, le=1e4)
    length: float = Field(gt=0, le=1e4)

    @field_validator("axis")
    @classmethod
    def _axis(cls, v):
        if sum(c * c for c in v) < 1e-12:
            raise ValueError("axis must be a non-zero vector")
        return v


class Region(BaseModel):
    """A keep-in (stays solid) or keep-out (stays empty) region."""
    kind: Literal["faces", "part", "box", "cylinder"]
    faces: list[str] = Field(default_factory=list, max_length=20,
                             description="'faces': a band of thickness_mm around these faces "
                                         "stays solid (keep-in only)")
    thickness_mm: float = Field(2.0, gt=0, le=50)
    part_id: str | None = Field(None, description="'part': another part's solid, e.g. a "
                                                  "clearance envelope")
    box: list[float] | None = Field(None, min_length=6, max_length=6,
                                    description="[x0, y0, z0, x1, y1, z1] mm")
    cylinder: Cylinder | None = None

    @field_validator("faces")
    @classmethod
    def _faces(cls, v):
        return [check_face(f) for f in v]

    @model_validator(mode="after")
    def _shape(self):
        need = {"faces": bool(self.faces), "part": self.part_id is not None,
                "box": self.box is not None, "cylinder": self.cylinder is not None}
        if not need[self.kind]:
            raise ValueError(f"a {self.kind!r} region needs its {self.kind!r} field")
        return self


class Fixture(BaseModel):
    kind: Literal["fixed"] = "fixed"
    face: str

    @field_validator("face")
    @classmethod
    def _face(cls, v):
        return check_face(v)


class TopoptLoad(BaseModel):
    face: str
    newtons: float = Field(gt=0, le=1e6, description="total force spread over the face, N")
    direction: Direction

    @field_validator("face")
    @classmethod
    def _face(cls, v):
        return check_face(v)


class TopoptLoadCase(BaseModel):
    name: str = Field("case 1", min_length=1, max_length=60)
    loads: list[TopoptLoad] = Field(min_length=1, max_length=20)
    weight: float = Field(1.0, gt=0, le=100)


class Overhang(BaseModel):
    build_dir: BuildDir = "z"
    angle_deg: float = Field(45.0, description="self-supporting angle from the build plate; "
                                               "the voxel stencil gives 45 degrees")

    @field_validator("angle_deg")
    @classmethod
    def _angle(cls, v):
        if abs(v - 45.0) > 1e-9:
            raise ValueError("only a 45 degree overhang is supported")
        return v


class Manufacturing(BaseModel):
    process: Literal["none", "print_3d", "machined"] = Field(
        "none", description="print_3d: overhang filter along +z unless overhang is given; "
                            "machined: 2.5-D (extruded along z) unless extrusion_axis is given")
    symmetry: list[Axis] = Field(default_factory=list, max_length=3,
                                 description="mirror planes through the design space's centre")
    extrusion_axis: Axis | None = None
    overhang: Overhang | None = None
    min_member_mm: float | None = Field(None, gt=0, le=100)

    def resolved(self) -> "Manufacturing":
        m = self.model_copy()
        if m.process == "print_3d" and m.overhang is None:
            m.overhang = Overhang()
        if m.process == "machined" and m.extrusion_axis is None:
            m.extrusion_axis = "z"
        return m


class TopoptRunArgs(BaseModel):
    part_id: str = Field(description="the part whose solid is the design space")
    design_space: Literal["part", "bbox"] = Field("part", description="the part's own solid, "
                                                  "or its bounding box")
    keep: list[Region] = Field(default_factory=list, max_length=20)
    keep_out: list[Region] = Field(default_factory=list, max_length=20)
    fixtures: list[Fixture] = Field(min_length=1, max_length=10)
    load_cases: list[TopoptLoadCase] = Field(min_length=1, max_length=6)
    material: str | None = Field(None, description="default: the profile's default_material")
    objective: Literal["min_compliance", "min_mass"] = "min_compliance"
    volume_fraction: float = Field(0.25, gt=0.03, lt=0.8)
    max_deflection_mm: float | None = Field(None, gt=0, le=1e4, description="min_mass: the "
                                            "lightest verified variant within this deflection")
    min_safety_factor: float = Field(1.5, gt=0, le=20, description="variants under this "
                                     "(CalculiX, yield x FDM knock-down) never rank first")
    resolution_mm: float | None = Field(None, ge=0.2, le=20, description="voxel size; "
                                        "default: ~50k elements in the design space")
    filter_radius_mm: float | None = Field(None, gt=0, le=100, description="default: 2 voxels")
    max_iter: int = Field(60, ge=5, le=200)
    manufacturing: Manufacturing = Field(default_factory=Manufacturing)
    verify: bool = Field(True, description="verify every variant with gmsh + CalculiX")
    wait: bool = Field(False, description="false (default): return at once with topopt_id; "
                       "the run continues in the background (topopt_status / topopt_wait)")
    reason: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _objective(self):
        if self.objective == "min_mass" and self.max_deflection_mm is None:
            raise ValueError("min_mass needs max_deflection_mm (the stiffness target)")
        for r in self.keep_out:
            if r.kind == "faces":
                raise ValueError("a keep-out region is a box, cylinder or part, not faces")
        return self


class TopoptGenerateArgs(TopoptRunArgs):
    variants: list[dict[str, Any]] = Field(default_factory=list, max_length=12, description=(
        "explicit deltas, e.g. [{\"volume_fraction\": 0.15}, {\"filter_radius_mm\": 3}, "
        "{\"manufacturing\": {\"process\": \"print_3d\"}}]; keys: "
        + ", ".join(sorted(VARIANT_KEYS))))
    sweep: dict[str, list[float]] | None = Field(None, description="a grid instead, e.g. "
                                                 "{\"volume_fraction\": [0.12, 0.2, 0.3], "
                                                 "\"filter_radius_mm\": [1.5, 3]}")
    max_variants: int = Field(6, ge=1, le=12)

    @field_validator("variants")
    @classmethod
    def _variants(cls, v):
        for d in v:
            bad = set(d) - VARIANT_KEYS
            if bad:
                raise ValueError(f"unknown variant keys {sorted(bad)}; use {sorted(VARIANT_KEYS)}")
        return v

    @field_validator("sweep")
    @classmethod
    def _sweep(cls, v):
        if v is None:
            return v
        bad = set(v) - SWEEP_KEYS
        if bad:
            raise ValueError(f"unknown sweep keys {sorted(bad)}; use {sorted(SWEEP_KEYS)}")
        for k, vals in v.items():
            if not vals or len(vals) > 6:
                raise ValueError(f"sweep {k}: 1 to 6 values")
        return v


class IdArgs(BaseModel):
    topopt_id: str = Field(pattern=TOPOPT_ID_RE.pattern)


class WaitArgs(IdArgs):
    timeout_s: float = Field(300, gt=0, le=600)


class AcceptArgs(IdArgs):
    part_id: str = Field(description="the source part the generative run optimized")
    variant: str | None = Field(None, pattern=VARIANT_ID_RE.pattern,
                                description="default: the recommended variant")
    mode: Literal["new", "replace"] = Field("new", description="new: a new part beside the "
                                            "source; replace: a new version of the source part "
                                            "(needs base_version)")
    new_part_id: str | None = None
    base_version: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _replace(self):
        if self.mode == "replace" and self.base_version is None:
            raise ValueError("replace needs base_version (the source part version you read)")
        return self
