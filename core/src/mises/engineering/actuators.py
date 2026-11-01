"""Actuator performance data for sizing and dynamics (Plan 10 C3, research §1.4).

Each actuator has a `perf` block: stall torque, rated (continuous) torque, no-load speed,
currents, mass, gear ratio, rotor inertia. The catalog (C2's actuator rows) is read first: a
catalog item with a `perf` block wins. This module's table is the fallback, so dynamics and
sizing work before (or without) those rows. Values: vendor sheets [V] or typical [2]; every
row's `source` says which.

Margins (defaults [J], research §3.1): hobby/bus servos, steppers and geared DC motors pass at
peak <= 0.6 x stall; BLDC actuators at peak <= 0.8 x peak torque; RMS <= rated torque; the
needed speed <= 0.8 x the speed the torque-speed line allows at that torque.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Perf:
    id: str
    name: str
    type: str                     # servo | bus_servo | stepper | bldc_geared | dc_geared
    voltage_v: float
    stall_torque_nm: float        # stall (servo/DC), holding (stepper), peak (BLDC)
    rated_torque_nm: float        # continuous
    no_load_rpm: float
    stall_current_a: float
    idle_current_a: float
    mass_g: float
    envelope_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gear_ratio: float | None = None
    rotor_inertia_kgm2: float | None = None
    efficiency: float = 0.6
    source: str = "typical [2]"
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def no_load_rad_s(self) -> float:
        return self.no_load_rpm * 2 * math.pi / 60.0

    @property
    def peak_margin(self) -> float:
        """The share of stall/peak torque a design may use at its peak."""
        return 0.8 if self.type.startswith("bldc") else 0.6

    def torque_available(self, omega: float) -> float:
        """The torque-speed line: tau_stall * (1 - |w| / w0), floored at 0."""
        w0 = self.no_load_rad_s
        return max(0.0, self.stall_torque_nm * (1.0 - abs(omega) / w0)) if w0 > 0 else 0.0

    def current_at(self, tau: float) -> float:
        """I = I_idle + (|tau| / tau_stall) (I_stall - I_idle) (research §3.2, linear)."""
        if self.stall_torque_nm <= 0:
            return self.idle_current_a
        f = min(abs(tau) / self.stall_torque_nm, 1.0)
        return self.idle_current_a + f * (self.stall_current_a - self.idle_current_a)

    def armature(self) -> float:
        if self.rotor_inertia_kgm2 and self.gear_ratio:
            return self.rotor_inertia_kgm2 * self.gear_ratio ** 2
        return {"servo": 0.002, "bus_servo": 0.004, "stepper": 0.001,
                "bldc_geared": 0.006, "dc_geared": 0.002}.get(self.type, 0.002)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("aliases", None)
        d["envelope_mm"] = list(self.envelope_mm)
        return d


_RPM = lambda s_per_60deg: 60.0 / (s_per_60deg * 6.0)  # noqa: E731 - "0.1 s/60 deg" -> rpm

TABLE: tuple[Perf, ...] = (
    Perf("servo_sg90_micro", "SG90 micro servo", "servo", 4.8, 0.177, 0.06, _RPM(0.1), 0.65,
         0.01, 9, (23, 12.2, 22.7), source="datasheet [V], current typical [2]",
         aliases=("sg90",)),
    Perf("servo_mg996r_standard", "MG996R servo", "servo", 6.0, 1.08, 0.35, _RPM(0.14), 2.5,
         0.01, 55, (40.7, 19.7, 42.9), source="TowerPro sheet [V]; clones vary",
         aliases=("mg996r", "mg996")),
    Perf("servo_ds3218", "DS3218 20 kg servo", "servo", 6.8, 2.1, 0.7, _RPM(0.16), 3.0, 0.01,
         60, (40, 20, 40.5), aliases=("ds3218",)),
    Perf("servo_sts3215", "Feetech STS3215 bus servo", "bus_servo", 7.4, 1.9, 0.6,
         _RPM(0.222), 2.7, 0.05, 55, (45.2, 24.7, 35), source="Feetech [V]",
         aliases=("sts3215",)),
    Perf("dynamixel_xl430_w250", "Dynamixel XL430-W250-T", "bus_servo", 11.1, 1.4, 0.5, 57,
         1.3, 0.05, 57.2, (28.5, 46.5, 34), source="ROBOTIS e-manual [V]",
         aliases=("xl430", "xl430_w250")),
    Perf("dynamixel_xm430_w350", "Dynamixel XM430-W350", "bus_servo", 12.0, 4.1, 1.4, 46, 2.3,
         0.05, 82, (28.5, 46.5, 34), source="ROBOTIS e-manual [V]",
         aliases=("xm430", "xm430_w350")),
    Perf("stepper_nema17_40", "NEMA17 40 mm stepper", "stepper", 12.0, 0.40, 0.30, 900, 1.5,
         0.3, 280, (42.3, 42.3, 40), source="typical [V]; torque falls with speed [J]",
         aliases=("nema17", "nema17_40")),
    Perf("stepper_nema23_56", "NEMA23 56 mm stepper", "stepper", 24.0, 1.2, 0.9, 700, 2.8, 0.5,
         700, (56.4, 56.4, 56), aliases=("nema23", "nema23_56")),
    Perf("bldc_ak80_9", "CubeMars AK80-9", "bldc_geared", 24.0, 18.0, 9.0, 270, 24.0, 0.3,
         485, (98, 98, 61.5), gear_ratio=9.0, efficiency=0.85,
         source="CubeMars [V]; speed at 24 V [J]", aliases=("ak80_9", "ak80-9")),
    Perf("bldc_mini_cheetah", "Mini-Cheetah-class actuator", "bldc_geared", 24.0, 17.0, 6.0,
         380, 40.0, 0.3, 480, (96, 96, 40), gear_ratio=6.0, efficiency=0.85,
         source="Katz 2019 thesis [V]", aliases=("mini_cheetah", "minicheetah")),
    Perf("bldc_go_m8010_6", "Unitree GO-M8010-6", "bldc_geared", 24.0, 23.7, 8.0, 290, 40.0,
         0.3, 530, (96, 96, 43), gear_ratio=6.33, efficiency=0.85, source="Unitree [V]",
         aliases=("go_m8010", "go-m8010-6")),
    Perf("gearmotor_n20_298", "N20 298:1 gearmotor", "dc_geared", 6.0, 0.2, 0.06, 100, 0.36,
         0.03, 10, (12, 10, 34), aliases=("n20",)),
    Perf("gearmotor_25d_75", "Pololu 25D 75:1 gearmotor (12 V HP)", "dc_geared", 12.0, 0.83,
         0.25, 130, 5.6, 0.2, 90, (25, 25, 60), source="Pololu [V]", aliases=("25d",)),
    Perf("gearmotor_37d_50", "Pololu 37D 50:1 gearmotor", "dc_geared", 12.0, 2.06, 0.6, 200,
         5.5, 0.2, 200, (37, 37, 65), source="Pololu [V]", aliases=("37d",)),
)

_BY_KEY: dict[str, Perf] = {}
for _p in TABLE:
    for _k in (_p.id, *_p.aliases):
        _BY_KEY[_k] = _p


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _from_catalog(item_id: str) -> Perf | None:
    """C2's actuator rows: a catalog item with a `perf` block (research §1.4 shape)."""
    try:
        from ..catalog.builtin import builtin_by_id
        it = builtin_by_id().get(item_id)
    except Exception:  # noqa: BLE001 - the catalog is optional here
        return None
    perf = (it or {}).get("perf")
    if not isinstance(perf, dict) or not perf.get("stall_torque_nm"):
        return None
    dims = it.get("dims") or {}
    stall = float(perf["stall_torque_nm"])
    try:
        return Perf(
            id=item_id, name=str(it.get("name") or item_id),
            type={"bldc": "bldc_geared"}.get(perf.get("type", "servo"), perf.get("type", "servo")),
            voltage_v=float(perf.get("voltage_v") or 0.0), stall_torque_nm=stall,
            rated_torque_nm=float(perf.get("rated_torque_nm") or 0.35 * stall),
            no_load_rpm=float(perf.get("no_load_rpm") or 60.0),
            stall_current_a=float(perf.get("stall_current_a") or 1.0),
            idle_current_a=float(perf.get("idle_current_a") or 0.01),
            mass_g=float(dims.get("mass_g") or perf.get("mass_g") or 0.0),
            envelope_mm=(float(dims.get("length_mm") or 0), float(dims.get("width_mm") or 0),
                         float(dims.get("height_mm") or 0)),
            gear_ratio=perf.get("gear_ratio"),
            rotor_inertia_kgm2=perf.get("rotor_inertia_kgm2"), source="catalog")
    except (TypeError, ValueError):
        return None


def resolve(ref) -> Perf | None:
    """An actuator from a catalog id, a table id or alias ("mg996r"), or a joint's `actuator`
    metadata ({"catalog_id"|"id": ..., "ratio"?}). None when unknown."""
    if ref is None:
        return None
    ratio = None
    if isinstance(ref, dict) or hasattr(ref, "model_dump"):
        d = ref.model_dump() if hasattr(ref, "model_dump") else dict(ref)
        ratio = d.get("ratio")
        ref = d.get("catalog_id") or d.get("item_id") or d.get("id") or d.get("actuator")
        if ref is None:
            return None
    s = str(ref)
    # the curated table first (its own ids and aliases), then C2's catalog perf rows
    hit = _BY_KEY.get(s) or _BY_KEY.get(_norm(s)) or _from_catalog(s)
    if hit is None:
        n = _norm(s)
        for k, p in _BY_KEY.items():
            if k and (k in n or n in k) and len(n) >= 4:
                hit = p
                break
    if hit is not None and ratio and float(ratio) != 1.0:
        # an external reduction after the actuator: torque x ratio, speed / ratio
        r = float(ratio)
        hit = Perf(**{**asdict(hit), "id": f"{hit.id}@{r:g}", "name": f"{hit.name} x {r:g}:1",
                      "stall_torque_nm": hit.stall_torque_nm * r,
                      "rated_torque_nm": hit.rated_torque_nm * r,
                      "no_load_rpm": hit.no_load_rpm / r,
                      "envelope_mm": tuple(hit.envelope_mm), "aliases": ()})
    return hit


def candidates(tau_peak: float, tau_rms: float, omega_peak: float,
               kinds: tuple[str, ...] | None = None) -> list[Perf]:
    """Table actuators that meet the margins at this operating point, lightest first."""
    out = []
    for p in TABLE:
        if kinds and p.type not in kinds:
            continue
        if (tau_peak <= p.peak_margin * p.stall_torque_nm and tau_rms <= p.rated_torque_nm
                and omega_peak <= 0.8 * p.no_load_rad_s + 1e-9):
            out.append(p)
    return sorted(out, key=lambda p: p.mass_g)
