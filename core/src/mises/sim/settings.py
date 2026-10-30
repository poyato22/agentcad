"""Study settings: one pydantic model per study type. Their JSON schemas (GET /sims/types) carry
defaults, ranges, units (`unit`) and docs (`description`), so the Studies page renders the forms
generically, and the same models validate every POST /sims and sim_run call (bad ranges are
rejected before anything runs). Units: mm, N, MPa, s, degC, W unless a field says otherwise.
"""
from __future__ import annotations

import math
import os
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .environments import DIRECTIONS, FACE_PRESETS
from .mesh import FACE_ID_RE, is_face_spec

Coord = Annotated[float, Field(ge=-1e6, le=1e6, allow_inf_nan=False)]
Vec3 = tuple[Coord, Coord, Coord]   # finite: NaN / inf never reach a deck
DirectionName = Literal["+x", "-x", "+y", "-y", "+z", "-z"]


def _u(unit: str, **kw) -> dict:
    return {"json_schema_extra": {"unit": unit, **kw}}


def check_face(v: str) -> str:
    if not is_face_spec(v):
        raise ValueError(f"face must be one of {list(FACE_PRESETS)}, a face id like 'f3' or a "
                         "tagged face like 'tag:motor_mount'")
    return v


def _unit_vec(v) -> Vec3:
    if isinstance(v, str):
        if v not in DIRECTIONS:
            raise ValueError(f"direction must be one of {list(DIRECTIONS)} or [x, y, z]")
        return DIRECTIONS[v]
    x, y, z = (float(c) for c in v)
    n = math.sqrt(x * x + y * y + z * z)
    if not math.isfinite(n) or n < 1e-9:
        raise ValueError("direction must be a non-zero vector")
    return (x / n, y / n, z / n)


Face = Annotated[str, Field(description="a preset (bottom, top, x-min, x-max, y-min, y-max, "
                                        "z-min, z-max) or a face id like 'f3' (mirror_faces)")]
Direction = Annotated[Union[DirectionName, Vec3],
                      Field(description="'+x' ... '-z' or a vector [x, y, z] (normalized)")]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _FaceM(_M):
    @field_validator("face", check_fields=False)
    @classmethod
    def _face(cls, v: str) -> str:
        return check_face(v)


# ---- shared blocks

class MeshSettings(_M):
    size_mm: float | None = Field(None, gt=0, le=1000, description="element size; null = auto "
                                  "(from the part's size, within the node budget)", **_u("mm"))
    refine_faces: list[Face] = Field(default_factory=list, max_length=20,
                                     description="faces to mesh finer (local refinement)")
    refine_factor: float = Field(0.4, ge=0.05, le=1.0, description="local element size on the "
                                 "refined faces, as a fraction of the element size")
    second_order: bool = Field(True, description="quadratic (10-node) tetrahedra; first-order "
                               "tetrahedra are much too stiff in bending: use only to preview")

    @field_validator("refine_faces")
    @classmethod
    def _faces(cls, v: list[str]) -> list[str]:
        return [check_face(f) for f in v]


class FdmSettings(_M):
    infill_pct: float = Field(100, ge=5, le=100, description="infill density", **_u("%"))
    walls: int = Field(3, ge=1, le=20, description="perimeter count")
    layer_orientation: Literal["xy", "z"] = Field(
        "xy", description="'z' when the load runs across the layers (weakest direction)")


class CustomMaterial(_M):
    name: str = Field(min_length=1, max_length=40)
    e_mpa: float = Field(gt=1, le=5e5, description="Young's modulus", **_u("MPa"))
    nu: float = Field(ge=0.0, lt=0.5, description="Poisson's ratio")
    yield_mpa: float = Field(gt=0, le=1e4, description="tensile yield or strength", **_u("MPa"))
    density_g_cm3: float = Field(gt=0.01, le=25, description="density", **_u("g/cm3"))
    k_w_mk: float = Field(0.2, gt=0, le=500, description="thermal conductivity", **_u("W/(m K)"))
    alpha_per_k: float = Field(7e-5, ge=0, le=1e-3, description="thermal expansion",
                               **_u("1/K"))
    hdt_c: float = Field(60, ge=-50, le=1000, description="heat deflection temperature",
                         **_u("degC"))
    source: str = Field("", max_length=200)


class MaterialSettings(_M):
    name: str | None = Field(None, max_length=40, description="a built-in or saved material; "
                             "null = the profile's default material")
    custom: CustomMaterial | None = Field(None, description="an inline material (overrides name)")
    fdm: FdmSettings | None = Field(None, description="print settings -> approximate knock-downs")


class SolverSettings(_M):
    timeout_s: float = Field(300, ge=5, le=3600, description="per solve", **_u("s"))
    threads: int | None = Field(None, ge=1, le=64, description="null = up to 4 cores")

    @field_validator("threads")
    @classmethod
    def _threads(cls, v):
        return None if v is None else min(v, os.cpu_count() or 1)


class ConvergenceSettings(_M):
    enabled: bool = Field(False, description="refine until the peak changes less than tol_pct")
    tol_pct: float = Field(5, ge=0.5, le=50, **_u("%"))
    max_levels: int = Field(4, ge=2, le=6, description="meshes solved at most")


# ---- loads and supports

class ForceLoad(_FaceM):
    kind: Literal["force"] = "force"
    face: Face
    newtons: float = Field(gt=0, le=1e6, description="total force spread over the face",
                           **_u("N"))
    direction: Direction


class PressureLoad(_FaceM):
    kind: Literal["pressure"]
    face: Face
    mpa: float = Field(ge=-1e3, le=1e3, description="positive pushes into the face",
                       **_u("MPa"))

    @field_validator("mpa")
    @classmethod
    def _nz(cls, v: float) -> float:
        if v == 0:
            raise ValueError("pressure must not be 0")
        return v


class MomentLoad(_FaceM):
    kind: Literal["moment"]
    face: Face
    n_mm: float = Field(gt=0, le=1e9, description="moment magnitude", **_u("N mm"))
    axis: Direction = Field(description="moment axis (right-hand rule)")


class GravityLoad(_M):
    kind: Literal["gravity"]
    g: float = Field(1.0, gt=0, le=1000, description="acceleration in multiples of g "
                     "(9.81 m/s2)", **_u("g"))
    direction: Direction = "-z"


class BearingLoad(_FaceM):
    kind: Literal["bearing"]
    face: Face = Field(description="the bolt hole's cylindrical face")
    newtons: float = Field(gt=0, le=1e6, description="bolt force on the hole (cosine "
                           "distributed over the half it presses on)", **_u("N"))
    direction: Direction


class RemoteForce(_FaceM):
    kind: Literal["remote_force"]
    face: Face
    newtons: float = Field(gt=0, le=1e6, **_u("N"))
    direction: Direction
    point: Vec3 = Field(description="where the force acts (part coordinates)", **_u("mm"))


Load = Annotated[Union[ForceLoad, PressureLoad, MomentLoad, GravityLoad, BearingLoad,
                       RemoteForce], Field(discriminator="kind")]


class Support(_FaceM):
    kind: Literal["fixed", "pinned", "roller", "symmetry"] = Field(
        "fixed", description="fixed: all translations; pinned: radial + axial held, free to "
        "turn about the hole axis; roller / symmetry: normal displacement held (flat faces)")
    face: Face
    axis: Vec3 | None = Field(None, description="pinned: hole axis direction; null = fitted")


class LoadCase(_M):
    name: str = Field("case 1", min_length=1, max_length=60)
    supports: list[Support] = Field(min_length=1, max_length=12)
    loads: list[Load] = Field(min_length=1, max_length=12)
    note: str = Field("", max_length=300)


# ---- per-type settings

class _Common(_M):
    material: MaterialSettings = Field(default_factory=MaterialSettings)
    mesh: MeshSettings = Field(default_factory=MeshSettings)
    solver: SolverSettings = Field(default_factory=SolverSettings)


class StaticSettings(_Common):
    load_cases: list[LoadCase] = Field(min_length=1, max_length=8)
    convergence: ConvergenceSettings = Field(default_factory=ConvergenceSettings)
    environment: str | None = Field(None, max_length=40, description="Desk, Outdoor, ... "
                                    "(temperature checked against the material)")

    @model_validator(mode="after")
    def _names(self):
        names = [c.name for c in self.load_cases]
        if len(set(names)) != len(names):
            raise ValueError("load case names must be unique")
        return self


class Excitation(_M):
    rpm: float = Field(gt=0, le=1e6, description="motor speed", **_u("rpm"))
    blades: int = Field(1, ge=1, le=12, description="excitation per revolution (blade count)")
    margin_pct: float = Field(15, ge=0, le=100, description="band half-width around rpm/60 x "
                              "blades", **_u("%"))


class ModalSettings(_Common):
    supports: list[Support] = Field(default_factory=list, max_length=12, description="empty = "
                                    "free-free (the first six modes are rigid-body)")
    n_modes: int = Field(6, ge=1, le=30)
    band_hz: tuple[float, float] | None = Field(None, description="excitation band [lo, hi]",
                                                **_u("Hz"))
    excitation: Excitation | None = None

    @field_validator("band_hz")
    @classmethod
    def _band(cls, v):
        if v is not None and not (0 <= v[0] < v[1] <= 1e6):
            raise ValueError("band_hz must be [lo, hi] with 0 <= lo < hi")
        return v


class BucklingSettings(_Common):
    load_case: LoadCase
    n_modes: int = Field(3, ge=1, le=10)


class FixedTemperature(_FaceM):
    face: Face
    temp_c: float = Field(ge=-270, le=2000, **_u("degC"))


class HeatInput(_FaceM):
    face: Face
    watts: float | None = Field(None, gt=0, le=1e5, description="total power into the face",
                                **_u("W"))
    w_m2: float | None = Field(None, gt=0, le=1e8, description="or a heat flux", **_u("W/m2"))

    @model_validator(mode="after")
    def _one(self):
        if (self.watts is None) == (self.w_m2 is None):
            raise ValueError("give exactly one of watts or w_m2")
        return self


class Convection(_FaceM):
    face: Face
    h_w_m2k: float = Field(gt=0, le=1e5, description="film coefficient (still air ~5-10, "
                           "forced air ~25-100)", **_u("W/(m2 K)"))
    ambient_c: float = Field(23, ge=-100, le=2000, **_u("degC"))


class ThermalSettings(_Common):
    temperatures: list[FixedTemperature] = Field(default_factory=list, max_length=12)
    heat: list[HeatInput] = Field(default_factory=list, max_length=12)
    convection: list[Convection] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _bounded(self):
        if not self.temperatures and not self.convection:
            raise ValueError("a steady state needs a fixed temperature or a convection face")
        return self


class DropSettings(_Common):
    impact_face: Face = Field(description="the face that hits the ground")
    drop_m: float = Field(1.0, gt=0, le=50, description="drop height", **_u("m"))
    k: float = Field(10.0, ge=1, le=500, description="dynamic factor: equivalent static "
                     "load = m g k (estimate; ~5-20 for rigid floors and stiff plastic)")
    mass_g: float | None = Field(None, gt=0, le=1e6, description="null = from the volume and "
                                 "density", **_u("g"))
    convergence: ConvergenceSettings = Field(default_factory=ConvergenceSettings)

    @field_validator("impact_face")
    @classmethod
    def _f(cls, v: str) -> str:
        return check_face(v)


class WindSettings(_M):
    speed_m_s: float = Field(10.0, gt=0, le=150, description="wind speed", **_u("m/s"))
    direction: Direction = Field("+x", description="flow direction in part coordinates")
    up: Direction = Field("+z", description="lift is reported along this axis (projected "
                          "normal to the flow); yaw turns about it")
    air_density: float = Field(1.225, gt=0.01, le=20, description="sea level, 15 degC",
                               **_u("kg/m3"))
    air_viscosity: float = Field(1.81e-5, gt=1e-7, le=1e-2, description="dynamic viscosity "
                                 "(sea level, 15 degC)", **_u("Pa s"))
    ref_area_m2: float | None = Field(None, gt=0, le=1e4, description="null = the frontal "
                                      "projected area", **_u("m2"))
    domain_multiplier: float = Field(5.0, ge=3, le=10, description="tunnel cross-section "
                                     "and length in body sizes (bigger = less blockage)")
    refinement: int = Field(2, ge=1, le=4, description="cells across the body and the "
                            "validated model error: 1 = 8 (PREVIEW, ±40%), 2 = 12 (±30%), "
                            "3 = 16 (±15%), 4 = 24 (±15%); time grows ~ cells^4")
    turbulence: Literal["smagorinsky", "none"] = Field(
        "smagorinsky", description="LES sub-grid model (built-in lattice-Boltzmann solver)")
    iterations: int = Field(4000, ge=200, le=100_000, description="lattice steps cap")
    timeout_s: float = Field(600, ge=5, le=7200, **_u("s"))
    memory_mb: int = Field(1024, ge=64, le=8192, description="memory budget of the wind "
                           "solver, enforced (the run is stopped above it); also capped at "
                           "this Mac's RAM - 6 GB - running local models", **_u("MB"))
    yaw_deg: list[float] = Field(default_factory=list, max_length=13, description="yaw sweep "
                                 "angles (each one is a separate run)", **_u("deg"))
    solver: Literal["auto", "lbm", "empirical"] = Field(
        "auto", description="auto: lattice-Boltzmann, falling back to the empirical estimate "
        "when it cannot fit the time or memory budget")
    shape_class: Literal["auto", "sphere", "cube", "cylinder", "flat_plate", "streamlined",
                         "bluff"] = Field("auto", description="for the empirical estimate")

    @field_validator("yaw_deg")
    @classmethod
    def _yaw(cls, v: list[float]) -> list[float]:
        if any(not -180 <= a <= 180 for a in v):
            raise ValueError("yaw angles must be within [-180, 180] degrees")
        return v

    @model_validator(mode="after")
    def _dirs(self):
        d, up = _unit_vec(self.direction), _unit_vec(self.up)
        if abs(sum(a * b for a, b in zip(d, up))) > 0.999:
            raise ValueError("up must not be parallel to the flow direction")
        return self


def unit_vec(v) -> Vec3:
    return _unit_vec(v)


SETTINGS_MODELS: dict[str, type[BaseModel]] = {
    "static": StaticSettings, "modal": ModalSettings, "buckling": BucklingSettings,
    "thermal": ThermalSettings, "drop": DropSettings, "wind": WindSettings}
