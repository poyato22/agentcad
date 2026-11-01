"""Rocketry tools (Plan 11 F6): rocket_analyze, rocket_flight and rocket_motor.

- rocket_analyze: Barrowman CP, CG from the build's parts (B-rep mass at their solved pose),
  static margin in calibers at launch and burnout, NACA TN-4197 fin flutter speed against the
  maximum flight speed.
- rocket_flight: 3-DOF ascent and recovery on one or more motors: apogee, time to apogee,
  flight time, velocities, accelerations (the payload's peak g at boost and chute opening),
  descent rate vs parachute size, wind drift; optional ballast tuning to a target apogee.
- rocket_motor: the thrust curves Mises knows; fetch one from ThrustCurve.org (or its
  OpenRocket mirror) into the user's cache, or save a pasted RASP .eng.

Results are Simulations-stream cards ({kind, title, status, metrics, rows, charts, warnings,
accuracy}) with the same chart shape as the study charts (sim/charts.py).
"""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import aero, flutter, motors as motorlib
from .flight import FlightResult, FlightSetup, chute_for, simulate
from .geometry import (Built, MassItem, MassModel, MassRow, RocketGeometry, build_body,
                       from_skeleton, gather)

FT = 0.3048
MARGIN_MIN, MARGIN_TARGET_HI, MARGIN_MAX = 1.0, 2.0, 3.0
RAIL_EXIT_MIN = 13.4          # m/s (30 mph): the usual minimum for a stable rail exit
DEPLOY_FAST = 15.0            # m/s: a chute opening faster than this risks a zipper / egg shock
CHART_POINTS = 90
ROUGHNESS = {"polished": 2e-6, "smooth": 20e-6, "paint": 60e-6, "printed": 100e-6,
             "rough": 250e-6}
ACCURACY = [
    "Barrowman CP: subsonic, small angle of attack, body lift ignored (it adds stability at "
    "larger angles); matches OpenRocket's CP within ~1% on the reference rockets.",
    "Drag: component build-up (skin friction for the stated finish, fin edge and base drag); "
    "printed surfaces, seams and rail buttons vary. Use cd_override with a CFD (wind study) "
    "Cd when you have one.",
    "Flight: 3-DOF point mass, weathercocking assumed; agrees with OpenRocket 24.12 apogee "
    "within ~1% on the reference flights. Real flights land 5-15% lower (OpenRocket's own "
    "validation: +7% over measured): trim against a test flight.",
    "Flutter: NACA TN-4197 screening estimate; printed fins are weaker across layers.",
]


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


# ---- arguments

class RocketArgs(BaseModel):
    project_id: str | None = Field(None, description="Default: this run's build.")
    geometry: RocketGeometry | None = Field(
        None, description="The outer shape. Default: read from the build's skeleton when it "
        "uses the rocket names (body_od, nose_len, nose_base_z, fin_count, fin_root, fin_tip, "
        "fin_span, fin_sweep, fin_t, fin_root_start, optional boattail_len/boattail_aft_od, "
        "nose_shape 0 ogive / 1 conical / 2 parabolic / 3 elliptical; nose up +z, body from "
        "z = body_aft_z (default 0) to nose_base_z).")
    mass_overrides_g: dict[str, float] = Field(
        default_factory=dict, description="part id (or instance name) -> its real mass in g "
        "per instance: payload stand-ins (a raw egg is ~57-60 g), parachutes, shock cords")
    extra_masses: list[MassItem] = Field(default_factory=list, max_length=20,
                                         description="point masses not in the assembly "
                                         "(ballast, altimeter, glue)")
    exclude_parts: list[str] = Field(default_factory=list, max_length=50)
    motor_parts: list[str] | None = Field(None, max_length=8, description="the build's motor "
                                          "placeholder part ids (default: a part named motor)")
    motor_aft_mm: float | None = Field(None, ge=0, description="the motor's aft end from the "
                                       "nose tip (default: the motor part's aft end, else the "
                                       "rocket's aft end)")
    dry_mass_g: float | None = Field(None, gt=0, le=100000, description="override: the whole "
                                     "rocket without motor (needs cg_mm)")
    cg_mm: float | None = Field(None, ge=0, description="override: the dry rocket's CG from "
                                "the nose tip")
    finish: Literal["polished", "smooth", "paint", "printed", "rough"] = Field(
        "paint", description="surface roughness for skin friction (paint 60 um, printed 100 "
        "um as printed, smooth 20 um sanded + clear)")
    cd_override: float | None = Field(None, gt=0, le=3, description="a measured or CFD Cd "
                                      "(referenced to the body cross-section) instead of the "
                                      "build-up")
    launch_alt_m: float = Field(0.0, ge=-400, le=5000, description="launch site elevation")
    temp_c: float | None = Field(None, ge=-40, le=50, description="launch temperature")

    @model_validator(mode="after")
    def _override(self):
        if (self.dry_mass_g is None) != (self.cg_mm is None):
            raise ValueError("dry_mass_g and cg_mm go together")
        return self


class AnalyzeArgs(RocketArgs):
    motor: str | None = Field(None, description="designation, e.g. 'F52C', 'G40W' "
                              "(rocket_motor lists them); adds the loaded and burnt-out "
                              "margins, and the max velocity for the flutter check")
    motor_eng: str | None = Field(None, max_length=64000, description="a RASP .eng text "
                                  "instead of a known motor")
    max_velocity_mps: float | None = Field(None, gt=0, le=2000, description="for the flutter "
                                           "check without a motor")
    fin_material: str | None = Field(None, description="default: the fins part's material, "
                                     "else PLA")
    fin_shear_modulus_mpa: float | None = Field(None, gt=0, le=200000, description="override "
                                                "the material table (plywood ~600, G10 ~4000)")


class FlightArgs(RocketArgs):
    motors: list[str] = Field(default_factory=list, max_length=6, description="designations "
                              "to compare (one flight each)")
    motor_eng: str | None = Field(None, max_length=64000, description="a RASP .eng text, "
                                  "flown as one more motor")
    rail_length_m: float = Field(1.0, gt=0, le=10)
    rail_angle_deg: float = Field(0.0, ge=0, le=20, description="from vertical")
    wind_mps: float = Field(0.0, ge=0, le=20, description="steady wind (ARC: up to ~9 m/s)")
    delay_s: float | None = Field(None, ge=0, le=30, description="ejection delay after "
                                  "burnout; default: the chute opens at apogee (report gives "
                                  "the motor's nearest delay)")
    chute_diameter_mm: float | None = Field(None, gt=0, le=5000, description="default: sized "
                                            "for descent_mps")
    chute_cd: float = Field(0.8, gt=0, le=2.5, description="0.8 flat/hemispherical "
                            "(OpenRocket's default), 1.5 good ripstop dome")
    descent_mps: float = Field(5.0, gt=0.5, le=30, description="target descent rate when "
                               "sizing the chute (eggs: 4-6 m/s)")
    target_apogee_m: float | None = Field(None, gt=0, le=10000)
    target_apogee_ft: float | None = Field(None, gt=0, le=30000)
    target_time_s: list[float] | None = Field(None, min_length=2, max_length=2,
                                              description="flight time window [lo, hi] s")
    tune_mass: bool = Field(False, description="find the ballast (or mass to remove) that "
                            "hits the target apogee, per motor")
    ballast_at_mm: float | None = Field(None, ge=0, description="where ballast goes, from the "
                                        "tip (default: the nose base, which also adds margin)")

    @model_validator(mode="after")
    def _motors(self):
        if not self.motors and not self.motor_eng:
            raise ValueError("name at least one motor (motors) or pass motor_eng")
        if self.tune_mass and self.target_apogee_m is None and self.target_apogee_ft is None:
            raise ValueError("tune_mass needs target_apogee_m or target_apogee_ft")
        return self

    @property
    def target_m(self) -> float | None:
        if self.target_apogee_m is not None:
            return self.target_apogee_m
        return self.target_apogee_ft * FT if self.target_apogee_ft is not None else None


# ---- the shared setup

class Rocket:
    """A build's rocket: geometry, dry mass model, where the motor sits."""

    def __init__(self, built: Built, mass: MassModel, motor_aft_m: float, home: Path | None,
                 fins_material: str | None, warnings: list[str], project_id: str | None):
        self.built, self.mass, self.motor_aft_m = built, mass, motor_aft_m
        self.home, self.fins_material, self.warnings = home, fins_material, warnings
        self.project_id = project_id
        self.cp_m, self.cn, self.terms = aero.barrowman(built.body)

    @property
    def body(self) -> aero.Body:
        return self.built.body

    @property
    def ref_d(self) -> float:
        return self.body.ref_d

    def motor_cg_m(self, m: motorlib.Motor) -> float:
        return self.motor_aft_m - m.length_mm / 2000.0

    def cg_at(self, m: motorlib.Motor | None, t: float, extra: list[MassRow] = ()) -> tuple[float,
                                                                                             float]:
        """(mass kg, CG m from tip) at time t of the burn."""
        rows = [*self.mass.rows, *extra]
        tot = sum(r.mass_kg for r in rows)
        mom = sum(r.mass_kg * r.x_m for r in rows)
        if m is not None:
            mm = m.mass_kg(t)
            tot += mm
            mom += mm * self.motor_cg_m(m)
        return tot, mom / tot

    def margin(self, cg_m: float) -> float:
        return (self.cp_m - cg_m) / self.ref_d

    def setup(self, m: motorlib.Motor, args: RocketArgs, **kw) -> FlightSetup:
        t0, p0 = aero.site_air(args.launch_alt_m, args.temp_c)
        parasitic = 0.0
        if self.built.rail_buttons:
            # a rail button ~ a 10 mm x 8 mm blunt stub: Cd ~1.2 on its frontal area
            parasitic = self.built.rail_buttons * 1.2 * 8e-5 / self.body.ref_area
        return FlightSetup(self.body, m, self.mass.dry_kg + kw.pop("ballast_kg", 0.0),
                           site_t0_k=t0, site_p0_pa=p0, roughness_m=ROUGHNESS[args.finish],
                           parasitic_cd=parasitic, cd_override=args.cd_override, **kw)


def _home(ctx: ToolContext) -> Path:
    return Path(ctx.workdir)


def _project(ctx: ToolContext, requested: str | None) -> str | None:
    from ..assembly.tools import _resolve_build
    from ..catalog.tools import resolve_project
    if requested is None and getattr(ctx, "store", None) is None:
        return None                                  # a bare context (tests, overrides only)
    pid = requested or resolve_project(ctx, None)
    return _resolve_build(ctx, pid) if pid else None


def _asm(ctx: ToolContext, pid: str | None):
    store = (ctx.services or {}).get("assembly")
    if store is None or pid is None:
        return None
    try:
        return store.get(pid)
    except MisesError:
        return None


def _skeleton_params(ctx: ToolContext, pid: str | None) -> dict:
    if pid is None:
        return {}
    from ..cad import skeleton as sk
    try:
        data, _v = sk.load(_home(ctx) / "parts", pid)
    except (OSError, ValueError, MisesError):
        return {}
    return dict(data.params) if data is not None else {}


def _fins_material(ctx: ToolContext, pid: str | None, asm) -> str | None:
    """The material of the build's fins part (a part named like 'fin')."""
    store = getattr(ctx, "store", None)
    if store is None or asm is None:
        return None
    from .. import mass as _mass
    for inst in getattr(asm, "instances", []):
        if "fin" in inst.part_id.lower() or "fin" in inst.name.lower():
            return _mass.part_material(store, _home(ctx), inst.part_id)
    return None


def load_rocket(ctx: ToolContext, args: RocketArgs) -> Rocket:
    pid = _project(ctx, args.project_id)
    warnings: list[str] = []
    geo = args.geometry
    if geo is None:
        params = _skeleton_params(ctx, pid)
        geo, missing = from_skeleton(params)
        if geo is None:
            raise _bad("no rocket geometry: pass `geometry`, or lay the skeleton out with the "
                       "rocket names", hint=f"the skeleton lacks: {', '.join(missing)}")
        built = build_body(geo, source="skeleton")
    else:
        built = build_body(geo, source="given")
    asm = None
    if args.dry_mass_g is not None:
        rows = [MassRow("rocket (override)", None, args.dry_mass_g / 1000.0,
                        args.cg_mm / 1000.0, "override")]
        mass = MassModel(rows, None, [], None, [])
    else:
        if pid is None:
            raise _bad("which build?", hint="pass project_id, or dry_mass_g + cg_mm")
        asm = _asm(ctx, pid)
        mass = gather(_home(ctx), getattr(ctx, "store", None), asm, built.axis,
                      built.nose_tip_axial_mm, overrides_g=args.mass_overrides_g,
                      exclude=args.exclude_parts, motor_parts=args.motor_parts)
        warnings += mass.notes
        unused = [k for k in args.mass_overrides_g
                  if not any(r.part_id == k or r.name == k for r in mass.rows)]
        if unused:
            warnings.append(f"mass_overrides_g names no part in the assembly: "
                            f"{', '.join(unused[:6])}")
    for e in args.extra_masses:
        mass.rows.append(MassRow(e.name, None, e.mass_g / 1000.0, e.at_mm / 1000.0, "added"))
    L = built.body.length
    if mass.extent_m is not None:
        lo, hi = mass.extent_m
        span = hi - lo
        if abs(span - L) > 0.05 * L:
            warnings.append(f"the parts span {span * 1000:.0f} mm along the axis but the "
                            f"geometry is {L * 1000:.0f} mm long: check the geometry (or "
                            f"nose_tip_mm) against the model")
    if args.motor_aft_mm is not None:
        aft = args.motor_aft_mm / 1000.0
    elif mass.motor_aft_m is not None:
        aft = mass.motor_aft_m
    else:
        aft = L
        if args.dry_mass_g is None:
            warnings.append("no motor part found: the motor sits flush with the aft end "
                            "(set motor_aft_mm or motor_parts)")
    return Rocket(built, mass, aft, _home(ctx), _fins_material(ctx, pid, asm), warnings, pid)


# ---- charts and cards

def _chart(cid: str, title: str, y: tuple[str, str], series: list[tuple[str, list]],
           x: tuple[str, str] = ("time", "s"), bands: list[dict] | None = None) -> dict:
    out = {"id": cid, "kind": "line", "title": title, "x": {"label": x[0], "unit": x[1]},
           "y": {"label": y[0], "unit": y[1]},
           "series": [{"name": n, "points": _thin(pts)} for n, pts in series]}
    if bands:
        out["bands"] = bands
    return out


def _thin(pts: list) -> list:
    if len(pts) > CHART_POINTS:
        step = len(pts) / CHART_POINTS
        pts = [pts[int(i * step)] for i in range(CHART_POINTS)] + [pts[-1]]
    return [[round(a, 3), round(b, 3)] for a, b in pts]


def _worst(statuses) -> str:
    order = {"fail": 3, "warn": 2, "pass": 1, "not_checked": 0}
    s = list(statuses)
    return max(s, key=lambda x: order.get(x, 0)) if s else "not_checked"


def _margin_status(m: float) -> tuple[str, str]:
    if m < MARGIN_MIN:
        return "fail", f"{m:.2f} cal: unstable or marginal (needs >= {MARGIN_MIN:g})"
    if m > MARGIN_MAX:
        return "warn", f"{m:.2f} cal: overstable (> {MARGIN_MAX:g}), weathercocks into wind"
    if m > MARGIN_TARGET_HI:
        return "pass", f"{m:.2f} cal (target 1-2; fine, a little stiff)"
    return "pass", f"{m:.2f} cal (target 1-2)"


def _metric(label: str, value, unit: str = "", status: str | None = None) -> dict:
    d = {"label": label, "value": value, "unit": unit}
    if status:
        d["status"] = status
    return d


def _flutter_rows(r: Rocket, args: AnalyzeArgs | None, vmax: float | None,
                  alt_at_vmax: float, t0: float, p0: float) -> tuple[list[dict], list[str]]:
    rows, notes = [], []
    mat = (args.fin_material if args else None) or r.fins_material or "PLA"
    if args and args.fin_shear_modulus_mpa:
        g_pa, gnote = args.fin_shear_modulus_mpa * 1e6, f"given G = {args.fin_shear_modulus_mpa:g} MPa"
    else:
        try:
            g_pa, gnote = flutter.shear_modulus_pa(mat, r.home)
        except Exception as e:  # noqa: BLE001 - SimError: unknown material
            raise _bad(f"fin material {mat!r} is not in the materials table",
                       hint="pass fin_material (a table name) or fin_shear_modulus_mpa") from e
    notes.append(gnote)
    air = aero.isa(alt_at_vmax, t0_k=t0, p0_pa=p0)
    for i, f in enumerate(r.body.fins):
        vf = flutter.flutter_speed(g_pa, root=f.root, tip=f.tip, span=f.span,
                                   thickness=f.thickness, a=air.a, p=air.p)
        row = {"fins": f"{f.count} x {f.root * 1000:.0f}/{f.tip * 1000:.0f} mm, span "
                       f"{f.span * 1000:.0f}, t {f.thickness * 1000:.1f}",
               "flutter_mps": round(vf, 1), "at_alt_m": round(alt_at_vmax, 0)}
        if vmax:
            ratio = vf / vmax
            row.update(max_v_mps=round(vmax, 1), ratio=round(ratio, 2),
                       status="fail" if ratio <= 1.0 else "warn" if ratio < flutter.MIN_RATIO
                       else "pass")
        else:
            row["status"] = "not_checked"
        rows.append(row)
    return rows, notes


# ---- rocket_analyze

@tool("rocket_analyze", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=120,
      description="Rocket stability and fin flutter for the build: Barrowman center of "
                  "pressure (nose, transitions, fins), CG from the assembly's parts (B-rep "
                  "mass x material at their pose; the motor placeholder is replaced by the "
                  "real motor), static margin in calibers at launch and burnout (target 1-2, "
                  "fail < 1, warn > 3), and the NACA TN-4197 fin flutter speed vs the max "
                  "flight speed (material shear modulus from the materials table). Geometry "
                  "comes from the skeleton's rocket names or `geometry`. Re-weigh stand-in "
                  "parts (eggs, chute) with mass_overrides_g. Run it on every rocket build "
                  "before fins are final and after every change.")
async def rocket_analyze(ctx: ToolContext, args: AnalyzeArgs) -> ToolOutput:
    r = load_rocket(ctx, args)
    warnings = list(r.warnings)
    motor = motorlib.resolve(args.motor, args.motor_eng, r.home) \
        if (args.motor or args.motor_eng) else None
    dry_kg, dry_cg = r.cg_at(None, 0.0)
    metrics = [_metric("CP", round(r.cp_m * 1000, 1), "mm from tip"),
               _metric("CN alpha", round(r.cn, 3), "/rad"),
               _metric("dry mass", round(dry_kg * 1000, 1), "g"),
               _metric("dry CG", round(dry_cg * 1000, 1), "mm from tip")]
    statuses: list[str] = []
    charts: list[dict] = []
    t0, p0 = aero.site_air(args.launch_alt_m, args.temp_c)
    vmax, alt_vmax, fl = args.max_velocity_mps, 0.0, None
    ml = mb = cg_l = 0.0
    if motor is not None:
        m_l, cg_l = r.cg_at(motor, 0.0)
        m_b, cg_b = r.cg_at(motor, motor.burn_time_s)
        ml, mb = r.margin(cg_l), r.margin(cg_b)
        sl, tl = _margin_status(ml)
        sb, tb = _margin_status(mb)
        statuses += [sl, sb]
        metrics += [_metric("liftoff mass", round(m_l * 1000, 1), "g"),
                    _metric("CG at launch", round(cg_l * 1000, 1), "mm from tip"),
                    _metric("margin at launch", round(ml, 2), "cal", sl),
                    _metric("margin at burnout", round(mb, 2), "cal", sb)]
        steps = 40
        series = []
        for i in range(steps + 1):
            t = motor.burn_time_s * i / steps
            series.append((t, r.margin(r.cg_at(motor, t)[1])))
        charts.append(_chart("margin", f"Static margin during the burn ({motor.designation})",
                             ("margin", "cal"), [("margin", series)],
                             bands=[{"label": "target 1-2 cal", "axis": "y", "from": 1.0,
                                     "to": 2.0}]))
        fl = simulate(r.setup(motor, args))
        if vmax is None:
            vmax, alt_vmax = fl.max_v_mps, fl.max_v_alt_m
        if fl.rail_exit_mps < RAIL_EXIT_MIN:
            warnings.append(f"rail exit {fl.rail_exit_mps:.1f} m/s on {motor.designation} "
                            f"(< {RAIL_EXIT_MIN} m/s): the fins may not steer it yet; use a "
                            "longer rail or a higher-thrust motor")
        metrics.append(_metric("rail exit", round(fl.rail_exit_mps, 1), "m/s",
                               "warn" if fl.rail_exit_mps < RAIL_EXIT_MIN else "pass"))
    else:
        s, txt = _margin_status(r.margin(dry_cg))
        statuses.append(s)
        metrics.append(_metric("margin (no motor)", round(r.margin(dry_cg), 2), "cal", s))
        warnings.append("no motor given: the margin is without the motor (a motor moves the "
                        "CG aft); pass motor for the real numbers")
    frows, fnotes = _flutter_rows(r, args, vmax, alt_vmax, t0, p0)
    statuses += [x["status"] for x in frows if x["status"] != "not_checked"]
    if vmax is None:
        warnings.append("flutter: no max velocity (pass motor or max_velocity_mps)")
    status = _worst(statuses)
    terms = [{"term": t.name, "cn_alpha": round(t.cn, 3), "x_mm": round(t.x * 1000, 1)}
             for t in r.terms]
    mrows = sorted(r.mass.rows, key=lambda x: -x.mass_kg)
    mass_table = [{"part": x.name, "mass_g": round(x.mass_kg * 1000, 1),
                   "cg_mm": round(x.x_m * 1000, 1), "source": x.source} for x in mrows[:30]]
    summary = (f"rocket_analyze {status}: CP {r.cp_m * 1000:.0f} mm, "
               + (f"margin {ml:.2f} cal at launch / {mb:.2f} at burnout on "
                  f"{motor.designation}" if motor is not None
                  else f"margin {r.margin(dry_cg):.2f} cal without motor")
               + "".join(f"; flutter {x['flutter_mps']:.0f} m/s"
                         + (f" vs {x['max_v_mps']:.0f} m/s max ({x['ratio']:.1f}x)"
                            if 'ratio' in x else "") for x in frows))
    card = {"kind": "rocket_analyze", "title": "Rocket stability" + (
        f" · {motor.designation}" if motor else ""), "status": status, "metrics": metrics,
        "rows": terms, "flutter": frows, "mass": mass_table, "charts": charts,
        "warnings": warnings, "notes": fnotes + [f"geometry from the {r.built.source}"],
        "accuracy": ACCURACY[:1] + ACCURACY[3:]}
    data = {"project_id": r.project_id, "status": status, "cp_mm": round(r.cp_m * 1000, 1),
            "cn_alpha": round(r.cn, 3), "ref_diameter_mm": round(r.ref_d * 1000, 2),
            "length_mm": round(r.body.length * 1000, 1), "dry_mass_g": round(dry_kg * 1000, 1),
            "dry_cg_mm": round(dry_cg * 1000, 1), "terms": terms, "flutter": frows,
            "mass_table": mass_table, "motor": motor.summary() if motor else None,
            "motor_parts": r.mass.motor_parts, "card": card, "stream": "sim"}
    if motor is not None:
        data.update(margin_launch_cal=round(ml, 2), margin_burnout_cal=round(mb, 2),
                    cg_launch_mm=round(cg_l * 1000, 1),
                    max_v_mps=round(fl.max_v_mps, 1) if fl else None)
    return ToolOutput(summary=summary, data=data, warnings=warnings, step_kind=StepKind.SIM_RUN)


# ---- rocket_flight

def _nearest_delay(m: motorlib.Motor, ideal: float) -> float | None:
    return min(m.delays, key=lambda d: abs(d - ideal)) if m.delays else None


def _tune(r: Rocket, m: motorlib.Motor, args: FlightArgs, target: float, at_m: float) -> dict:
    """Ballast (kg, may be negative = mass to remove) that puts the apogee on target."""
    def apo(b: float) -> float:
        return simulate(r.setup(m, args, ballast_kg=b, rail_m=args.rail_length_m,
                                rail_angle_deg=args.rail_angle_deg,
                                wind_mps=args.wind_mps)).apogee_m
    dry = r.mass.dry_kg
    lo, hi = -0.5 * dry, 3.0 * dry
    a_lo, a_hi = apo(lo), apo(hi)
    if not (a_hi <= target <= a_lo):
        reach = "above" if target > a_lo else "below"
        return {"reachable": False, "note": f"{target:.0f} m is {reach} what mass alone can "
                f"reach on {m.designation} ({a_hi:.0f}-{a_lo:.0f} m): change the motor"}
    for _ in range(30):
        mid = (lo + hi) / 2
        if apo(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.0005:
            break
    b = (lo + hi) / 2
    extra = MassRow("ballast", None, b, at_m, "tuned")
    _mt, cg = r.cg_at(m, 0.0, [extra])
    return {"reachable": True, "ballast_g": round(b * 1000, 1), "at_mm": round(at_m * 1000, 1),
            "margin_launch_cal": round(r.margin(cg), 2)}


def _fly(r: Rocket, m: motorlib.Motor, args: FlightArgs, chute_d: float, ballast: float = 0.0):
    return simulate(r.setup(m, args, ballast_kg=ballast, rail_m=args.rail_length_m,
                            rail_angle_deg=args.rail_angle_deg, wind_mps=args.wind_mps,
                            delay_s=args.delay_s, chute_d_m=chute_d, chute_cd=args.chute_cd))


@tool("rocket_flight", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=240,
      description="Fly the build's rocket (3-DOF: thrust curve, drag build-up or a CFD Cd, "
                  "ISA air, rail, steady wind) on one or more motors: apogee (m and ft), time "
                  "to apogee, total flight time with the parachute, max velocity / Mach, peak "
                  "acceleration (boost and chute-opening shock in g: what an egg payload "
                  "feels), rail exit speed, descent rate vs parachute size, drift, and the "
                  "margin with each motor. Compares to target_apogee and target_time_s, and "
                  "with tune_mass finds the ballast that hits the target apogee. Same geometry "
                  "and mass rules as rocket_analyze.")
async def rocket_flight(ctx: ToolContext, args: FlightArgs) -> ToolOutput:
    r = load_rocket(ctx, args)
    warnings = list(r.warnings)
    mots = [motorlib.resolve(n, None, r.home) for n in args.motors]
    if args.motor_eng:
        mots.append(motorlib.resolve(None, args.motor_eng, r.home))
    target = args.target_m
    ballast_at = (args.ballast_at_mm / 1000.0 if args.ballast_at_mm is not None
                  else r.body.nose.length)
    rows, statuses = [], []
    flights: list[tuple[motorlib.Motor, FlightResult]] = []
    for m in mots:
        if m.diameter_mm > r.ref_d * 1000:
            warnings.append(f"{m.designation} is {m.diameter_mm:g} mm: wider than the body")
        land_kg = r.mass.dry_kg + m.total_kg - m.prop_kg
        chute = (args.chute_diameter_mm / 1000.0 if args.chute_diameter_mm
                 else chute_for(land_kg, args.descent_mps, args.chute_cd))
        fl = _fly(r, m, args, chute)
        flights.append((m, fl))
        _ml, cg = r.cg_at(m, 0.0)
        margin = r.margin(cg)
        ms, _ = _margin_status(margin)
        ideal = fl.t_apogee_s - fl.burnout_t_s
        row = {"motor": m.designation, "class": m.impulse_class,
               "impulse_ns": round(m.total_impulse_ns, 1),
               "liftoff_g": round(fl.liftoff_mass_kg * 1000, 1),
               "thrust_to_weight": round(fl.thrust_to_weight, 1),
               "apogee_m": round(fl.apogee_m, 1), "apogee_ft": round(fl.apogee_m / FT, 0),
               "t_apogee_s": round(fl.t_apogee_s, 2), "flight_time_s": round(fl.flight_time_s, 1),
               "max_v_mps": round(fl.max_v_mps, 1), "max_mach": round(fl.max_mach, 3),
               "boost_g": round(fl.max_accel_mps2 / aero.G0, 1),
               "deploy_v_mps": round(fl.deploy_v_mps or 0.0, 1),
               "deploy_shock_g": round(fl.deploy_shock_g or 0.0, 1),
               "payload_peak_g": round(max(fl.max_accel_mps2 / aero.G0, fl.deploy_shock_g or 0),
                                       1),
               "rail_exit_mps": round(fl.rail_exit_mps, 1),
               "descent_mps": round(fl.descent_mps or 0.0, 2),
               "landing_v_mps": round(fl.landing_v_mps, 2), "drift_m": round(fl.drift_m, 0),
               "chute_mm": round(chute * 1000), "margin_cal": round(margin, 2),
               "margin_status": ms, "optimal_delay_s": round(ideal, 1),
               "motor_delay_s": _nearest_delay(m, ideal), "cd": round(fl.cd_at_max_v, 3)}
        st = [ms]
        if fl.rail_exit_mps < RAIL_EXIT_MIN:
            st.append("warn")
            warnings.append(f"{m.designation}: rail exit {fl.rail_exit_mps:.1f} m/s < "
                            f"{RAIL_EXIT_MIN} m/s")
        if fl.thrust_to_weight < 5:
            st.append("warn")
            warnings.append(f"{m.designation}: peak thrust-to-weight {fl.thrust_to_weight:.1f} "
                            "(< 5): a slow, wind-sensitive lift-off")
        if fl.deploy_v_mps and fl.deploy_v_mps > DEPLOY_FAST:
            st.append("warn")
            warnings.append(f"{m.designation}: the chute opens at {fl.deploy_v_mps:.0f} m/s "
                            f"({fl.deploy_shock_g:.0f} g shock): pick a delay near "
                            f"{ideal:.1f} s")
        if target is not None:
            d = fl.apogee_m - target
            row["apogee_error_ft"] = round(d / FT, 0)
            st.append("pass" if abs(d) <= 0.05 * target else "warn")
            if args.tune_mass:
                row["tune"] = _tune(r, m, args, target, ballast_at)
        if args.target_time_s:
            lo, hi = sorted(args.target_time_s)
            ok = lo <= fl.flight_time_s <= hi
            st.append("pass" if ok else "warn")
            if not ok and fl.deploy_alt_m:
                fall = (lo + hi) / 2 - (fl.deploy_t_s or fl.t_apogee_s)
                if fall > 0:
                    v = fl.deploy_alt_m / fall
                    row["chute_for_time_mm"] = round(chute_for(land_kg, v, args.chute_cd) * 1000)
                    row["descent_for_time_mps"] = round(v, 2)
        row["status"] = _worst(st)
        statuses.append(row["status"])
        rows.append(row)
    status = _worst(statuses)
    shown = flights[:2]
    charts = [
        _chart("altitude", "Altitude", ("altitude", "m"),
               [(m.designation, [(s[0], s[1]) for s in fl.samples]) for m, fl in shown],
               bands=([{"label": f"target {target:.0f} m", "axis": "y", "from": target * 0.98,
                        "to": target * 1.02}] if target else None)),
        _chart("velocity", "Speed", ("speed", "m/s"),
               [(m.designation, [(s[0], s[2]) for s in fl.samples if s[0] <= fl.t_apogee_s + 2])
                for m, fl in shown]),
        _chart("acceleration", "Acceleration (payload)", ("acceleration", "g"),
               [(m.designation, [(s[0], s[3]) for s in fl.samples
                                 if s[0] <= fl.burnout_t_s + 1]) for m, fl in shown]),
    ]
    metrics = []
    if rows:
        b = rows[0]
        metrics = [_metric("apogee", b["apogee_ft"], "ft"), _metric("apogee", b["apogee_m"], "m"),
                   _metric("time to apogee", b["t_apogee_s"], "s"),
                   _metric("flight time", b["flight_time_s"], "s"),
                   _metric("max speed", b["max_v_mps"], "m/s"),
                   _metric("payload peak", b["payload_peak_g"], "g"),
                   _metric("margin", b["margin_cal"], "cal", b["margin_status"]),
                   _metric("descent", b["descent_mps"], "m/s")]
    tgt = []
    if target is not None:
        tgt.append(f"target {target / FT:.0f} ft")
    if args.target_time_s:
        tgt.append(f"time {min(args.target_time_s):g}-{max(args.target_time_s):g} s")
    summary = (f"rocket_flight {status}: " + "; ".join(
        f"{x['motor']} apogee {x['apogee_ft']:.0f} ft ({x['apogee_m']:.0f} m) at "
        f"{x['t_apogee_s']:.1f} s, flight {x['flight_time_s']:.1f} s, max {x['max_v_mps']:.0f} "
        f"m/s, margin {x['margin_cal']:.2f} cal"
        + (f", ballast {x['tune']['ballast_g']:+.0f} g for target"
           if x.get("tune", {}).get("reachable") else "") for x in rows)
        + (f" ({', '.join(tgt)})" if tgt else ""))
    conditions = {"rail_m": args.rail_length_m, "rail_angle_deg": args.rail_angle_deg,
                  "wind_mps": args.wind_mps, "launch_alt_m": args.launch_alt_m,
                  "temp_c": args.temp_c, "finish": args.finish,
                  "cd": "override" if args.cd_override else "build-up",
                  "deploy": f"{args.delay_s:g} s after burnout" if args.delay_s is not None
                  else "at apogee", "chute_cd": args.chute_cd}
    card = {"kind": "rocket_flight", "title": "Rocket flight · " + ", ".join(
        m.designation for m in mots), "status": status, "metrics": metrics, "rows": rows,
        "charts": charts, "warnings": warnings, "conditions": conditions,
        "target": {"apogee_m": target, "time_s": args.target_time_s}, "accuracy": ACCURACY}
    data = {"project_id": r.project_id, "status": status, "flights": rows,
            "conditions": conditions, "dry_mass_g": round(r.mass.dry_kg * 1000, 1),
            "motors": [m.summary() for m in mots], "card": card, "stream": "sim"}
    return ToolOutput(summary=summary, data=data, warnings=warnings, step_kind=StepKind.SIM_RUN)


# ---- rocket_motor

class MotorArgs(BaseModel):
    action: Literal["list", "get", "fetch", "save"] = Field(
        "list", description="list: every curve Mises has; get: one curve's numbers; fetch: "
        "download a curve from ThrustCurve.org (or its OpenRocket mirror) into your library; "
        "save: keep a RASP .eng text you opened (e.g. on thrustcurve.org)")
    name: str | None = Field(None, max_length=40, description="designation, e.g. 'F39T', "
                             "'G64W', 'F15'")
    manufacturer: str | None = Field(None, max_length=40, description="fetch: e.g. AeroTech, "
                                     "Estes")
    eng: str | None = Field(None, max_length=64000, description="save: the RASP .eng text")


TC_API = "https://www.thrustcurve.org/api/v1/"
MIRROR_LIST = ("https://api.github.com/repos/openrocket/motor-database/contents/data/"
               "thrustcurve.org/{mfr}")
MFR_DIRS = {"aerotech": "AeroTech", "estes": "Estes Industries", "cesaroni": "Cesaroni Technology",
            "cti": "Cesaroni Technology", "apogee": "Apogee Components", "quest": "Quest Aerospace",
            "loki": "Loki Research", "aerotech/rcs": "AeroTech"}


async def _get(ctx: ToolContext, url: str, types=("application/json", "text/plain")) -> str:
    from ..catalog.importer import fetch
    http = (ctx.services or {}).get("browser_http") or (ctx.services or {}).get(
        "catalog_http") or {}
    page = await fetch(url, types=types, user_agent="Mises-rocketry/0.1",
                       accept="application/json,text/plain;q=0.9", **http)
    return page.text


async def _fetch_thrustcurve(ctx: ToolContext, name: str, mfr: str | None) -> tuple[str, dict]:
    from urllib.parse import urlencode
    q = {"commonName": motorlib.parse_common(name), "maxResults": 20}
    if mfr:
        q["manufacturer"] = mfr
    found = json.loads(await _get(ctx, TC_API + "search.json?" + urlencode(q)))
    want = motorlib._norm(motorlib._strip_mfr(name))
    cands = [x for x in found.get("results", []) if isinstance(x, dict)]
    cands.sort(key=lambda x: (motorlib._norm(str(x.get("designation", ""))) != want,
                              x.get("availability") != "regular"))
    for c in cands[:3]:
        dl = json.loads(await _get(ctx, TC_API + "download.json?" + urlencode(
            {"motorId": c.get("motorId"), "format": "RASP", "data": "file"})))
        for f in dl.get("results", []):
            raw = f.get("data")
            if not isinstance(raw, str):
                continue
            text = base64.b64decode(raw).decode("utf-8", "replace")
            sim = f.get("simfileId")
            return text, {"source": f"thrustcurve.org ({f.get('source') or 'simfile'})",
                          "license": f.get("license"),
                          "url": f"https://www.thrustcurve.org/simfiles/{sim}/" if sim else None}
    raise MisesError(ErrorCode.NOT_FOUND, f"ThrustCurve has no RASP file for {name!r}")


async def _fetch_mirror(ctx: ToolContext, name: str, mfr: str | None) -> tuple[str, dict]:
    from urllib.parse import quote
    common = motorlib.parse_common(name)
    dirs = [MFR_DIRS.get((mfr or "").lower(), mfr)] if mfr else ["AeroTech", "Estes Industries"]
    for d in [x for x in dirs if x]:
        listing = json.loads(await _get(ctx, MIRROR_LIST.format(mfr=quote(d))))
        hits = [x for x in listing if isinstance(x, dict)
                and str(x.get("name", "")).startswith(common + "_")
                and str(x.get("name", "")).endswith(".eng")]
        for h in hits:
            url = h.get("download_url")
            if not isinstance(url, str):
                continue
            text = await _get(ctx, url, types=("text/plain", "application/octet-stream"))
            try:
                m = motorlib.parse_eng(text)
            except MisesError:
                continue
            if motorlib._norm(m.designation).startswith(motorlib._norm(
                    motorlib._strip_mfr(name))) or len(hits) == 1:
                sim = str(h["name"]).rsplit("_", 1)[-1].split(".")[0]
                return text, {"source": "thrustcurve.org via the OpenRocket motor-database",
                              "license": None,
                              "url": f"https://www.thrustcurve.org/simfiles/{sim}/"}
    raise MisesError(ErrorCode.NOT_FOUND, f"no RASP file for {name!r} in the mirror")


@tool("rocket_motor", safety=SafetyClass.READ, open_world=True, timeout_s=60,
      description="Rocket motor thrust curves (RASP .eng). list: the curves Mises has "
                  "(bundled public-domain F44W, F52C, F67C, G25W, G40W, plus any you "
                  "fetched); get: one motor's impulse, thrust, burn time, masses, delays; "
                  "fetch: download a curve by designation from ThrustCurve.org (falls back to "
                  "its OpenRocket mirror) into the user's library; save: keep a .eng text you "
                  "opened yourself (web_open on thrustcurve.org). rocket_flight and "
                  "rocket_analyze then fly it by name.")
async def rocket_motor(ctx: ToolContext, args: MotorArgs) -> ToolOutput:
    home = _home(ctx)
    if args.action == "list":
        lib = motorlib.library(home)
        rows = [{k: v for k, v in m.summary().items() if k not in ("url",)} for m in lib]
        return ToolOutput(summary=f"{len(lib)} motors: " + ", ".join(
            f"{m.designation} ({m.total_impulse_ns:.0f} Ns, {m.source})" for m in lib),
            data={"motors": rows})
    if args.action == "get":
        m = motorlib.resolve(args.name, None, home)
        return ToolOutput(summary=f"{m.designation}: {m.total_impulse_ns:.1f} Ns, avg "
                                  f"{m.avg_thrust_n:.1f} N, burn {m.burn_time_s:.2f} s",
                          data={**m.summary(), "curve": [list(p) for p in m.curve]})
    if args.action == "save":
        if not args.eng:
            raise _bad("save needs eng (the RASP text)")
        m = motorlib.parse_eng(args.eng, source="saved (.eng text)")
        p = motorlib.save(home, m, source="saved by the agent (.eng text)")
        return ToolOutput(summary=f"saved {m.designation} ({m.total_impulse_ns:.1f} Ns) to the "
                                  "motor library", data={**m.summary(), "file": p.name},
                          step_kind=StepKind.DIFF)
    if not args.name:
        raise _bad("fetch needs name (e.g. 'F39T')")
    errors = []
    for how in (_fetch_thrustcurve, _fetch_mirror):
        try:
            text, meta = await how(ctx, args.name, args.manufacturer)
            m = motorlib.parse_eng(text, **meta)
            motorlib.save(home, m, **meta)
            return ToolOutput(summary=f"fetched {m.designation} ({m.total_impulse_ns:.1f} Ns, "
                                      f"{m.burn_time_s:.2f} s) from {meta['source']}",
                              data=m.summary(), step_kind=StepKind.DIFF)
        except (MisesError, ValueError, KeyError) as e:
            errors.append(f"{how.__name__.strip('_')}: {getattr(e, 'message', None) or e}")
    raise MisesError(ErrorCode.NOT_FOUND, f"could not fetch {args.name!r}: " + "; ".join(errors),
                     hint="open the motor's RASP file on thrustcurve.org with web_open and "
                          "pass its text to rocket_motor (action 'save') or as motor_eng")


TOOLS = (rocket_analyze, rocket_flight, rocket_motor)


def register_rocket_tools(registry: ToolRegistry) -> None:
    for t in TOOLS:
        registry.register(t)
