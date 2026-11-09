"""impact_study and descent_estimate: drop / crash / payload-protection dynamics (impact.py).

impact_study runs the descent (drag, parachute) to get the impact speed, then the transient
impact of the build as a stack of parts on a rigid floor: the fast lumped model always, and
CalculiX *DYNAMIC with contact when fidelity="fem". It reports the payload's peak g against a
limit (pass / fail), peak stress and where, rebound, and the energy each part absorbs.
"""
from __future__ import annotations

import json
import math
import secrets
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, ToolOutput
from .impact import (ASSUMPTIONS, CUSHIONS, EGG_MASS_KG, FIDELITY, Layer, check_g, descent,
                     egg_g_limit, lumped_dict, lumped_impact)

FEM_TIMEOUT_S = {"coarse": 240.0, "fine": 480.0}
FEM_DURATION_FACTOR = 1.4      # the CalculiX run covers 1.4x the lumped contact time
FEM_MAX_DURATION_S = 0.05


class LayerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60, description="part name, e.g. 'feet', "
                      "'crumple_zone', 'cradle', 'egg'")
    part_id: str | None = Field(None, description="a built part: mass, footprint and height "
                                "default from its bounding box, volume and material")
    mass_g: float | None = Field(None, gt=0, le=1e6, description="mass carried by this part")
    footprint_mm: list[float] | None = Field(
        None, min_length=1, max_length=2, description="[width, depth] of the load path at "
        "this part (one number = a square, or the contact patch of legs summed)")
    thickness_mm: float | None = Field(None, gt=0, le=5000, description="height along the "
                                       "drop: the crush stroke for a cushion")
    cushion: str | None = Field(None, description="crushable / elastic cushion: one of "
                                + ", ".join(CUSHIONS))
    e_mpa: float | None = Field(None, gt=0, le=5e5, description="a solid part's Young's "
                                "modulus (PLA ~3500, PETG ~2000, TPU ~30)")
    material: str | None = Field(None, description="a sim material (sim_types_list) instead "
                                 "of e_mpa")
    hyperelastic: bool = Field(False, description="a rubbery solid part (TPU, silicone): "
                               "large-strain Neo-Hooke in the CalculiX run")


class ImpactArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layers: list[LayerSpec] = Field(min_length=1, max_length=8, description="the build "
                                    "bottom first: what hits the floor first, up to the "
                                    "payload and anything above it")
    payload: str | None = Field(None, description="the layer name to protect (the egg); "
                                "default the top layer")
    drop_height_m: float | None = Field(None, gt=0, le=1000)
    impact_velocity_m_s: float | None = Field(None, gt=0, le=200, description="instead of "
                                              "a height (e.g. a crash speed)")
    cd_area_m2: float = Field(0.0, ge=0, le=50, description="body Cd x frontal area")
    parachute_diameter_m: float = Field(0.0, ge=0, le=20)
    parachute_cd: float = Field(1.5, gt=0, le=3)
    g_limit: float | None = Field(None, gt=0, le=1e5, description="pass if the payload's "
                                  "peak g is below this; default for an egg payload: the "
                                  "~35 N shell failure load / egg weight (~59 g)")
    fidelity: Literal["lumped", "fem"] = Field(
        "lumped", description="lumped: 1D transient, instant; fem: also CalculiX *DYNAMIC "
        "with contact (a minute or two, coarse mesh)")
    quality: Literal["coarse", "fine"] = "coarse"

    @model_validator(mode="after")
    def _v(self):
        if (self.drop_height_m is None) == (self.impact_velocity_m_s is None):
            raise ValueError("give exactly one of drop_height_m or impact_velocity_m_s")
        names = [L.name for L in self.layers]
        if len(set(names)) != len(names):
            raise ValueError("layer names must be unique")
        if self.payload is not None and self.payload not in names:
            raise ValueError(f"payload {self.payload!r} is not a layer name")
        return self


def _part_layer(ctx: ToolContext, spec: LayerSpec) -> dict:
    """Defaults from a built part: mass from volume x density, footprint and height from the
    bounding box (z is the drop axis)."""
    from ..cad.tools import _existing
    from .materials import get_material
    from .studies import _home, default_material
    d = _existing(ctx, spec.part_id)
    metrics = json.loads((Path(d) / "metrics.json").read_text())
    size = metrics.get("bbox", {}).get("size") or [0, 0, 0]
    mat = get_material(spec.material or default_material(ctx, spec.part_id), _home(ctx))
    return {"mass_g": metrics.get("volume_mm3", 0) * mat.density_g_cm3 / 1000.0,
            "footprint_mm": [size[0], size[1]], "thickness_mm": size[2],
            "e_mpa": mat.e_mpa * mat.stiffness_factor, "yield_mpa": mat.yield_mpa}


def build_layers(ctx: ToolContext | None, specs: list[LayerSpec], home=None) -> list[Layer]:
    out = []
    for s in specs:
        base = _part_layer(ctx, s) if (s.part_id and ctx is not None) else {}
        mass_g = s.mass_g or base.get("mass_g")
        fp = s.footprint_mm or base.get("footprint_mm")
        th = s.thickness_mm or base.get("thickness_mm")
        if not mass_g or not fp or not th:
            raise MisesError(ErrorCode.INVALID_INPUT, f"layer {s.name!r} needs mass_g, "
                             "footprint_mm and thickness_mm (or a part_id to take them from)")
        area = (fp[0] * (fp[1] if len(fp) > 1 else fp[0])) * 1e-6
        cushion = None
        if s.cushion:
            if s.cushion not in CUSHIONS:
                raise MisesError(ErrorCode.INVALID_INPUT, f"unknown cushion {s.cushion!r}",
                                 hint="use one of: " + ", ".join(CUSHIONS))
            cushion = CUSHIONS[s.cushion]
        e = s.e_mpa
        yld = base.get("yield_mpa")
        if e is None and s.material and not base:
            from .materials import get_material
            m = get_material(s.material, home)
            e, yld = m.e_mpa * m.stiffness_factor, m.yield_mpa
        out.append(Layer(s.name, mass_g / 1000.0, area, th / 1000.0, cushion=cushion,
                         e_mpa=e or base.get("e_mpa"), yield_mpa=yld,
                         hyperelastic=s.hyperelastic))
    return out


def _is_egg(name: str) -> bool:
    return "egg" in name.lower()


async def run_impact(args: ImpactArgs, layers: list[Layer], *, workdir: Path | None = None,
                     ccx: str | None = None) -> dict:
    """The study: descent -> lumped transient -> (optionally) CalculiX transient -> verdict."""
    payload = args.payload or layers[-1].name
    total = sum(L.mass_kg for L in layers)
    if args.impact_velocity_m_s is not None:
        dsc = None
        v = args.impact_velocity_m_s
    else:
        dsc = descent(total, args.drop_height_m, cd_area_m2=args.cd_area_m2,
                      parachute_diameter_m=args.parachute_diameter_m,
                      parachute_cd=args.parachute_cd)
        v = dsc["impact_velocity_m_s"]
    pl = next(L for L in layers if L.name == payload)
    limit = args.g_limit
    limit_src = "given"
    if limit is None and _is_egg(payload):
        limit = egg_g_limit(pl.mass_kg or EGG_MASS_KG)
        limit_src = ("egg: ~35 N quasi-static shell failure / egg weight (estimate; eggs "
                     "range ~25-45 N, a padded cradle spreads the load)")
    lum = lumped_impact(layers, v, payload=payload)
    out: dict = {"impact_velocity_m_s": v, "descent": dsc, "payload": payload,
                 "lumped": lumped_dict(lum), "fem": None,
                 "assumptions": list(ASSUMPTIONS), "notes": list(lum.notes)}
    for L in layers:
        if L.yield_mpa and lum.peak_stress_mpa[L.name] > L.yield_mpa:
            out["notes"].append(f"{L.name}: the mean contact stress "
                                f"{lum.peak_stress_mpa[L.name]:.3g} MPa passes its "
                                f"{L.yield_mpa:.3g} MPa strength: it may crack or yield")
    fid = "impact_lumped"
    best_g = lum.peak_g
    if args.fidelity == "fem":
        from .ccx import INSTALL_HINT, find_ccx
        ccx = ccx or find_ccx()
        if ccx is None:
            out["notes"].append(f"CalculiX not found ({INSTALL_HINT}): lumped result only")
        elif workdir is None:
            out["notes"].append("no work directory for CalculiX: lumped result only")
        else:
            from .impact_fem import run_fem_impact
            job = Path(workdir) / "sim_tmp" / f"impact_{secrets.token_hex(6)}"
            dur = min(FEM_DURATION_FACTOR * lum.contact_time_s, FEM_MAX_DURATION_S)
            try:
                fem = await run_fem_impact(layers, v, payload=payload, duration_s=dur,
                                           job_dir=job, ccx=ccx, quality=args.quality,
                                           timeout_s=FEM_TIMEOUT_S[args.quality])
                out["fem"] = fem
                fid = "impact_fem"
                # pass / fail on the higher of the two peaks (the conservative reading)
                best_g = max(fem["peak_g"], lum.peak_g)
                if not fem["completed"]:
                    out["notes"].append(f"CalculiX stopped at {fem['end_time_s'] * 1e3:.3g} "
                                        f"of {dur * 1e3:.3g} ms: the peak may be missed")
                diff = abs(fem["peak_g"] - lum.peak_g) / max(lum.peak_g, 1e-9)
                out["fem_vs_lumped_peak_g_diff"] = diff
                if diff > 0.5:
                    out["notes"].append(f"FEM and lumped peak g differ by {diff:.0%}: the "
                                        "result is sensitive to the 3D load path; trust "
                                        "neither alone")
            except Exception as e:  # noqa: BLE001 - the lumped result still stands
                out["notes"].append(f"CalculiX impact run failed ({e}); lumped result only")
            finally:
                shutil.rmtree(job, ignore_errors=True)
    out["fidelity_class"] = fid
    out["fidelity"] = FIDELITY[fid]
    out["peak_g"] = best_g
    out["g_check"] = check_g(best_g, limit)
    if out["g_check"]:
        out["g_check"]["limit_source"] = limit_src
    out["energy_absorbed_j"] = lum.energy_absorbed_j
    out["rebound_velocity_m_s"] = lum.rebound_velocity_m_s
    if out["fem"]:
        out["energy_note"] = ("energy_absorbed_j is the lumped model's dissipated energy per "
                              "part; fem.energy_j gives each part's peak and final internal "
                              "energy from CalculiX (final = dissipated by crush plasticity)")
    return out


def _summary(r: dict) -> str:
    gc = r.get("g_check")
    s = (f"impact {r['impact_velocity_m_s']:.3g} m/s, peak {r['peak_g']:.1f} g on "
         f"{r['payload']}")
    if gc:
        s += f" vs {gc['limit_g']:.0f} g limit: {gc['verdict'].upper()}"
    lum = r["lumped"]
    s += f"; rebound {lum['rebound_velocity_m_s']:.2f} m/s"
    top = max(r["energy_absorbed_j"].items(), key=lambda kv: kv[1])
    s += f"; {top[0]} absorbs {top[1]:.3g} J"
    if lum["bottomed_out"]:
        s += f"; BOTTOMED OUT: {', '.join(lum['bottomed_out'])}"
    return s + f" ({r['fidelity_class']}: {'simulated' if r['fem'] else 'estimate'})"


@tool("impact_study", safety=SafetyClass.LOCAL_MUTATE, timeout_s=600,
      description="Drop / crash / payload-protection dynamics. Use it for ANYTHING that is "
                  "dropped, lands, crashes or must protect a payload (egg drop, a dropped "
                  "phone case, a landing leg, a crash bumper). Describe the build bottom "
                  "first as layers (name, mass_g, footprint_mm, thickness_mm and a cushion "
                  "such as eps_20 / pe_foam_30 / paper_honeycomb / tpu_lattice_20pct, or "
                  "e_mpa / material / part_id for solid parts) and a payload. It runs the "
                  "descent (drag, parachute: terminal velocity, impact speed), then a "
                  "transient impact on a rigid floor with contact: peak g on the payload "
                  "with pass / fail against g_limit (default for an egg ~59 g), peak "
                  "stress, rebound, energy each part absorbs and bottoming out. "
                  "fidelity='lumped' is an instant 1D estimate; fidelity='fem' adds CalculiX "
                  "*DYNAMIC with contact and foam crush (1-3 min). Report the fidelity label "
                  "with the result.")
async def impact_study(ctx: ToolContext, args: ImpactArgs) -> ToolOutput:
    from .studies import _home
    layers = build_layers(ctx, args.layers, _home(ctx))
    r = await run_impact(args, layers, workdir=Path(ctx.workdir))
    return ToolOutput(summary=_summary(r), data=_finite(r))


class DescentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mass_kg: float = Field(gt=0, le=1000)
    drop_height_m: float = Field(gt=0, le=10000)
    cd_area_m2: float = Field(0.0, ge=0, le=100, description="body Cd x frontal area")
    parachute_diameter_m: float = Field(0.0, ge=0, le=30)
    parachute_cd: float = Field(1.5, gt=0, le=3, description="~0.8 flat sheet, 1.5 dome")
    air_density: float = Field(1.225, gt=0.01, le=5)


@tool("descent_estimate", safety=SafetyClass.READ,
      description="The fall of a dropped object: terminal velocity, impact speed and fall "
                  "time with quadratic drag (body Cd x area plus a parachute of diameter / "
                  "Cd). Closed form, still air, parachute open from release. Use it to size "
                  "a parachute or drag device; impact_study runs it for you.")
async def descent_estimate(ctx: ToolContext, args: DescentArgs) -> ToolOutput:
    r = descent(args.mass_kg, args.drop_height_m, cd_area_m2=args.cd_area_m2,
                parachute_diameter_m=args.parachute_diameter_m,
                parachute_cd=args.parachute_cd, rho=args.air_density)
    vt = r["terminal_velocity_m_s"]
    return ToolOutput(summary=(f"impact {r['impact_velocity_m_s']:.3g} m/s after "
                               f"{r['fall_time_s']:.3g} s (free fall {r['free_fall_velocity_m_s']:.3g}"
                               f" m/s" + (f", terminal {vt:.3g} m/s)" if vt else ")")),
                      data=r)


def _finite(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def register_impact_tools(registry: ToolRegistry) -> None:
    for t in (impact_study, descent_estimate):
        if not registry.list([t.name]):
            registry.register(t)
