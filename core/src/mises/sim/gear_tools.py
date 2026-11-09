"""The `gear_strength` agent tool (GEARS): tooth bending, contact, contact ratio, safety factors,
life class, efficiency and backlash for a gear pair, a compound train or a simple planetary.
An analytic estimate (sim/gears.py); it is listed by sim_types_list under `analytic`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import gears as G


class CustomGearMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["metal", "plastic"] = "plastic"
    e_mpa: float = Field(gt=10, le=500000)
    nu: float = Field(0.35, gt=0, lt=0.5)
    bending_allowable_mpa: float = Field(gt=0, le=2000)
    contact_allowable_mpa: float = Field(gt=0, le=3000)
    mu_dry: float = Field(0.3, gt=0, le=1)
    source: str = Field("user-supplied", max_length=300)


class GearStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    z1: int = Field(ge=6, le=400, description="driver teeth (on the previous stage's shaft)")
    z2: int = Field(ge=6, le=400, description="driven teeth")
    module_mm: float | None = Field(None, gt=0.1, le=50, description="default: module_mm")
    face_width_mm: float | None = Field(None, gt=0.2, le=1000)
    material_1: str | None = None
    material_2: str | None = None


class GearStrengthArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pair", "train", "planetary"] = "pair"
    module_mm: float = Field(gt=0.1, le=50, description="normal module, mm")
    face_width_mm: float = Field(gt=0.2, le=1000)
    z1: int | None = Field(None, ge=6, le=400, description="pair: driver (input) teeth")
    z2: int | None = Field(None, ge=6, le=400, description="pair: driven teeth")
    stages: list[GearStage] | None = Field(None, min_length=1, max_length=8,
                                           description="train: stages, input first")
    z_sun: int | None = Field(None, ge=6, le=400)
    z_planet: int | None = Field(None, ge=6, le=400)
    z_ring: int | None = Field(None, ge=12, le=800)
    n_planets: int | None = Field(None, ge=1, le=12)
    pressure_angle_deg: float = Field(20.0, ge=14.5, le=30)
    helix_angle_deg: float = Field(0.0, ge=0, le=45, description="0 = spur")
    torque_nm: float | None = Field(None, gt=0, le=1e6, description="input shaft torque "
                                    "(pair / train driver, planetary sun)")
    power_w: float | None = Field(None, gt=0, le=1e7, description="instead of torque_nm, with rpm")
    rpm: float | None = Field(None, gt=0, le=200000, description="input shaft speed; sets the "
                              "dynamic factor and life hours")
    material: str = Field("PLA", description="all gears unless overridden: PLA, PETG, ASA, "
                          "PA12 (nylon), PA-CF, PA6-CF, POM, POM (printed), Steel 1018, "
                          "Steel 4140, Steel 4140 QT, Brass C360, Al 6061-T6, Al 7075-T6, or a "
                          "custom_materials name")
    material_1: str | None = Field(None, description="pair: driver gear material")
    material_2: str | None = Field(None, description="pair: driven gear material")
    material_sun: str | None = None
    material_planet: str | None = None
    material_ring: str | None = None
    hardness_hb: float | None = Field(None, ge=80, le=700, description="steel Brinell hardness "
                                      "(re-derives AGMA allowables)")
    custom_materials: list[CustomGearMaterial] = Field(default_factory=list, max_length=6)
    application: Literal["uniform", "light_shock", "moderate_shock", "heavy_shock"] = Field(
        "uniform", description="overload K_o: uniform 1.0, light 1.25, moderate 1.5 (legged "
        "robots, start-stop), heavy 1.75")
    overload_factor: float | None = Field(None, ge=1, le=4, description="overrides application")
    quality_qv: float | None = Field(None, ge=3, le=12, description="AGMA quality; default: "
                                     "printed = Barth cast profile, metal = Qv 7")
    mounting: Literal["open", "commercial", "precision", "extra_precision"] = "open"
    overhung: bool = Field(False, description="gear overhangs its bearings (cantilever)")
    lubrication: Literal["auto", "dry", "grease", "oil"] = "auto"
    reliability: float = Field(0.99, ge=0.5, le=0.9999)
    life_hours: float | None = Field(None, gt=0, le=1e6, description="required running hours "
                                     "(with rpm); metals then get Y_N / Z_N at those cycles")
    load_sharing: float | None = Field(None, ge=1, le=2, description="planetary K_gamma "
                                       "(default by planet count)")
    target_sf: float = Field(1.5, ge=1, le=5, description="minimum of S_F and S_H^2 to pass")

    @model_validator(mode="after")
    def _shape(self):
        need = {"pair": ("z1", "z2"), "train": ("stages",),
                "planetary": ("z_sun", "z_planet", "z_ring", "n_planets")}[self.kind]
        missing = [k for k in need if getattr(self, k) is None]
        if missing:
            raise ValueError(f"{self.kind} needs {', '.join(missing)}")
        if self.torque_nm is None and (self.power_w is None or self.rpm is None):
            raise ValueError("give torque_nm, or power_w with rpm")
        return self


def run_gear_strength(a: GearStrengthArgs) -> dict:
    custom = {c.name: G.GearMaterial(
        c.name, c.kind, c.e_mpa, c.nu, c.bending_allowable_mpa, c.contact_allowable_mpa,
        c.mu_dry, c.mu_dry / 2, None, 1e7 if c.kind == "metal" else 1e6, c.source)
        for c in a.custom_materials}

    def mat(name: str | None):
        try:
            return G.gear_material(name or a.material, custom, a.hardness_hb)
        except KeyError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, str(e.args[0])) from None

    opt = G.MeshOptions(
        pressure_angle_deg=a.pressure_angle_deg, helix_angle_deg=a.helix_angle_deg,
        overload=a.overload_factor or G.OVERLOAD[a.application], qv=a.quality_qv,
        mounting=a.mounting, overhung=a.overhung, reliability=a.reliability,
        life_hours=a.life_hours, lubrication=a.lubrication, target_sf=a.target_sf)
    t_in = G._torque(a.torque_nm, a.power_w, a.rpm)
    extra: dict = {}
    if a.kind == "pair":
        meshes = G.pair(a.z1, a.z2, a.module_mm, a.face_width_mm, mat(a.material_1),
                        mat(a.material_2), t_in, a.rpm, opt)
        ratio = a.z2 / a.z1
    elif a.kind == "train":
        st = [{"z1": s.z1, "z2": s.z2, "module": s.module_mm or a.module_mm,
               "face_width": s.face_width_mm or a.face_width_mm,
               "mat1": mat(s.material_1), "mat2": mat(s.material_2)} for s in a.stages]
        meshes = G.train(st, t_in, a.rpm, opt)
        ratio = 1.0
        for s in a.stages:
            ratio *= s.z2 / s.z1
    else:
        meshes, extra = G.planetary(a.z_sun, a.z_planet, a.z_ring, a.n_planets, a.module_mm,
                                    a.face_width_mm, mat(a.material_sun), mat(a.material_planet),
                                    mat(a.material_ring), t_in, a.rpm, opt, a.load_sharing)
        ratio = extra["ratio"]
    summary = G.summarize(meshes, a.target_sf)
    eff = extra.get("efficiency", summary["efficiency_estimate"])
    summary["efficiency_estimate"] = eff
    warnings = list(dict.fromkeys(w for m in meshes for w in m["warnings"]))
    warnings += [f"planetary: {p}" for p in extra.get("assembly_problems", [])]
    if a.rpm is None:
        warnings.append("no rpm: dynamic factor 1 (quasi-static) and no life hours")
    return {
        "estimate": True, "fidelity": G.FIDELITY, "fidelity_label": G.FIDELITY_LABEL,
        "method": G.METHOD, "kind": a.kind, "input_torque_nm": round(t_in, 5),
        "input_rpm": a.rpm, "ratio": round(ratio, 4),
        "output_torque_nm": extra.get("output_torque_nm", round(t_in * ratio * eff, 4)),
        "output_rpm": None if a.rpm is None else round(a.rpm / ratio, 3),
        **summary, **({"planetary": extra} if extra else {}),
        "meshes": meshes, "warnings": warnings,
        "materials": {m.name: {"kind": m.kind, "bending_allowable_mpa": m.bend_allow_mpa,
                               "contact_allowable_mpa": m.contact_allow_mpa,
                               "rated_cycles": m.rated_cycles, "source": m.source}
                      for mesh in meshes for g in mesh["gears"]
                      for m in [mat(g["material"])]},
        "caveats": G.CAVEATS,
    }


GEAR_STUDY = {
    "id": "gear_strength", "tool": "gear_strength", "name": "Gear tooth strength",
    "fidelity_class": G.FIDELITY, "solver": G.METHOD, "needs_part": False,
    "doc": "spur / helical pair, compound train or planetary (each mesh): Lewis bending with "
           "AGMA factors (J, K_v, K_o, K_m, K_s), Hertz contact, contact ratio, safety factors "
           "vs allowables (printed plastics and metals), weakest gear, life class, efficiency, "
           "backlash, undercut. Run it for every geared drive."}


@tool("gear_strength", safety=SafetyClass.READ,
      description="Gear tooth strength study (analytic estimate, no part needed): a spur or "
                  "helical pair, a compound train (stages) or a simple planetary (sun, planet, "
                  "ring, n_planets; each mesh checked) from module, teeth, face width, pressure "
                  "angle, torque (or power + rpm) and material (PLA, PETG, ASA, PA12, PA-CF, "
                  "POM, steels, brass, aluminium, or custom). Returns Lewis bending with AGMA "
                  "factors and Hertz contact stress per gear, safety factors vs allowables, the "
                  "weakest gear, contact ratio, life class, efficiency, backlash and undercut "
                  "warnings. Run it for every geared drive before reporting, and quote it as an "
                  "estimate with its safety factors.")
async def gear_strength(ctx: ToolContext, args: GearStrengthArgs) -> ToolOutput:
    r = run_gear_strength(args)
    w = r["weakest"]
    return ToolOutput(
        summary=(f"gears {r['verdict']} (analytic estimate): weakest {w['gear']} "
                 f"({w['teeth']}T {w['material']}, {w['governing']}) bending SF "
                 f"{w['bending_sf']:.2f}, contact SF {w['contact_sf']:.2f}; efficiency "
                 f"~{r['efficiency_estimate']:.1%}; {len(r['warnings'])} warnings"),
        data=r, step_kind=StepKind.MEASURE)


def register_gear_tools(registry: ToolRegistry) -> None:
    if not registry.list([gear_strength.name]):
        registry.register(gear_strength)
