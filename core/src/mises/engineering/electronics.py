"""Electronics estimator (Plan 10 C3, E17; research §3.6): requirements -> component picks
(controller, drivers / ESCs, radio + antenna, GPS, IMU, BMS, power rails, switch, e-stop,
connectors, battery) with envelopes, masses, power and the reason for each pick.

Pure: `Needs` comes from the tool (the build's actuated joints and the spec's words, plus the
agent's explicit answers); `estimate(needs)` returns the list. The battery comes from the
power budget (engineering.checks.power_budget) of the actuators plus these electronics.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import actuators as act
from .checks import item, power_budget

LAMBDA_4_MM = {2.4: 31.0, 0.915: 82.0, 0.868: 86.0, 5.8: 13.0}


@dataclass
class Needs:
    hobby_servos: int = 0            # PWM servos
    bus_servos: int = 0              # Dynamixel / Feetech bus
    steppers: int = 0
    bldc: int = 0                    # BLDC joints (FOC) or drone motors (ESC)
    brushed_a: list[float] = field(default_factory=list)   # stall current per brushed motor
    vision: bool = False             # camera / SLAM / ROS -> SBC
    wifi: bool = False
    realtime: bool = False           # hard real-time multi-axis control
    drone: bool = False
    rc: bool = False                 # an RC link (ExpressLRS)
    telemetry_km: float = 0.0        # long-range telemetry
    outdoor: bool = False            # -> GPS
    imu: bool = True
    legs: bool = False               # -> e-stop
    runtime_h: float = 0.5
    enclosure_material: str | None = None   # "CF", "aluminium" -> antenna outside
    actuators: list[dict] = field(default_factory=list)  # power_budget actuator rows
    notes: list[str] = field(default_factory=list)


_WORDS = {
    "vision": r"\b(camera|vision|slam|ros2?|lidar|object detection|face|track(ing)? (people|objects))\b",
    "wifi": r"\b(wi-?fi|app control|web|stream(ing)?|phone)\b",
    "realtime": r"\b(real-?time|balanc(e|ing)|force control|high[- ]rate)\b",
    "drone": r"\b(drone|quadcopter|multirotor|hexacopter|uav|fpv)\b",
    "rc": r"\b(rc|remote control|radio control|transmitter|controller pad)\b",
    "outdoor": r"\b(outdoor|gps|field|garden|terrain|navigat\w*)\b",
}


def needs_from_text(text: str) -> dict:
    """Spec words -> needs flags (the agent can override every one)."""
    t = (text or "").lower()
    return {k: bool(re.search(p, t)) for k, p in _WORDS.items()}


def _pick(role_id: str, qty: int, reason: str, **extra) -> dict:
    it = item(role_id)
    if it is None:  # pragma: no cover - table and code agree
        raise KeyError(role_id)
    return {"role": it["role"], "item_id": it["id"], "name": it["name"], "qty": qty,
            "size_mm": it["size_mm"], "mass_g": it["mass_g"], "power_w": it["power_w"],
            "mount": it.get("mount"), "source": it.get("source"), "reason": reason, **extra}


def estimate(n: Needs) -> dict:
    picks: list[dict] = []
    warnings: list[str] = []
    pwm = n.hobby_servos
    # ---- controller (research §3.6 rules)
    if n.drone:
        picks.append(_pick("fc_f7_30x30", 1, "drone: a flight controller runs the attitude loop"))
        if n.vision:
            picks.append(_pick("rpi_zero2w", 1, "companion computer for the camera"))
    elif n.vision:
        picks.append(_pick("rpi5", 1, "vision / SLAM / ROS needs a Linux SBC (Pi 5)"))
        if n.realtime or pwm + n.bus_servos + n.steppers + n.bldc > 0:
            picks.append(_pick("teensy41" if n.realtime else "esp32_s3_devkitc", 1,
                               "a microcontroller runs the joints in real time beside the SBC"))
    elif n.realtime:
        picks.append(_pick("teensy41", 1, "hard real-time multi-axis control"))
    elif n.wifi or pwm <= 16:
        picks.append(_pick("esp32_s3_devkitc", 1, "Wi-Fi/BLE and up to 16 PWM channels"))
    else:
        picks.append(_pick("teensy41", 1, "many channels; real-time loop"))
    # ---- joint drivers
    if pwm > 0:
        boards = math.ceil(pwm / 16)
        if pwm > 8 or not n.drone:
            picks.append(_pick("pca9685", boards, f"{pwm} PWM servos: {boards} x 16-channel "
                               "driver (offloads servo timing from the controller)"))
    if n.bus_servos:
        picks.append(_pick("u2d2", 1, f"{n.bus_servos} bus servos on one TTL/RS-485 bus"))
    if n.steppers:
        picks.append(_pick("tmc2209", n.steppers, "one TMC2209 per stepper axis (quiet, "
                           "2 A rms)"))
    if n.bldc:
        if n.drone:
            q = math.ceil(n.bldc / 4)
            picks.append(_pick("esc_4in1_45a", q, f"{n.bldc} drone motors: 4-in-1 ESC"))
        else:
            picks.append(_pick("moteus_r411", n.bldc, "one FOC controller per BLDC joint"))
    for k, a in enumerate(n.brushed_a):
        rid = "tb6612" if a < 1.2 else "drv8871" if a < 3.6 else "bts7960"
        picks.append(_pick(rid, 1, f"brushed motor {k + 1}: {a:g} A stall"))
    # ---- radio + antenna
    enclosed = (n.enclosure_material or "").lower()
    conductive = any(w in enclosed for w in ("cf", "carbon", "alumin", "steel", "metal", "brass"))
    if n.rc or n.drone:
        picks.append(_pick("elrs_rx_nano", 1, "RC link (ExpressLRS 2.4 GHz)",
                           antenna_clearance_mm=LAMBDA_4_MM[2.4]))
    if n.telemetry_km >= 1.0:
        picks.append(_pick("rfd900x", 1, f"telemetry over {n.telemetry_km:g} km (915 MHz)",
                           antenna_clearance_mm=LAMBDA_4_MM[0.915]))
    if conductive and (n.rc or n.drone or n.telemetry_km or n.wifi or n.vision):
        warnings.append(f"{n.enclosure_material} blocks RF: put the antennas outside the "
                        "enclosure, >= 31 mm (2.4 GHz) / 82 mm (915 MHz) from conductive parts")
    # ---- sensors
    if n.outdoor:
        picks.append(_pick("holybro_m10" if n.drone else "m10q_20x20", 1,
                           "outdoor position: GPS on top with a clear sky view, compass >= "
                           "50 mm from high-current wires", keepout_mm=50))
    if n.imu:
        picks.append(_pick("bno085", 1, "orientation: IMU near the centre of mass" +
                           (", soft-mounted" if n.drone else "")))
    # ---- power: budget -> battery, rails, switch, BMS, connectors
    el = [{"name": p["name"], "power_w": [p["power_w"][0] * p["qty"], p["power_w"][1] * p["qty"]]}
          for p in picks]
    pb = power_budget(n.actuators, el, runtime_h=n.runtime_h)
    bat = pb["battery"]
    if bat:
        picks.append({"role": "battery", "item_id": bat["id"], "name": bat["name"], "qty": 1,
                      "size_mm": bat["size_mm"], "mass_g": bat["mass_g"], "power_w": [0, 0],
                      "reason": pb["reason"]})
    else:
        warnings.append(pb["reason"])
    i_peak = pb["i_peak_a"]
    cells = (bat or {}).get("cells") or round(pb["battery_voltage_v"] / 3.7)
    logic_a = sum(p["power_w"][1] * p["qty"] for p in picks if p["role"] != "battery") / 5.0
    picks.append(_pick("buck_5v5a", 1, f"5 V logic rail: {logic_a:.1f} A peak, sized 1.5x"))
    rail = sum(r.get("current_peak_a", 0.0) for r in pb["actuators"]) * 0.6
    if pwm or n.bus_servos:
        v_act = max((r.get("volts", 0.0) for r in pb["actuators"]), default=6.0)
        if v_act < pb["battery_voltage_v"] - 0.5:
            need = 1.5 * rail
            rid = "ubec_6v3a" if need <= 3 else "ubec_6v10a" if need <= 10 else "ubec_6v20a"
            if need > 20:
                warnings.append(f"servo rail needs {need:.0f} A: split it over two BECs")
            picks.append(_pick(rid, 1 if need <= 20 else 2, f"servo rail {v_act:g} V: "
                               f"{rail:.1f} A peak x 1.5 (never 4+ servos from the 5 V rail)"))
    if cells >= 3 and (bat or {}).get("chem") == "liion":
        picks.append(_pick("bms_3s4s_20a", 1, "Li-ion pack: a BMS balances and protects it"))
    if i_peak > 30 or cells > 3:
        picks.append(_pick("switch_xt90s", 1, f"{i_peak:.0f} A peak above 3S: anti-spark "
                           "switch"))
    else:
        picks.append(_pick("switch_rocker_20a", 1, "main power switch"))
    if cells * 3.7 > 24 or n.legs:
        picks.append(_pick("estop_22mm", 1, "legs or > 24 V: an e-stop that cuts the "
                           "actuator rail"))
    conn = "xt30" if i_peak <= 15 else "xt60" if i_peak <= 30 else "xt90"
    picks.append(_pick(conn, 1, f"battery connector for {i_peak:.0f} A peak"))
    n_sig = pwm + n.bus_servos + n.steppers + n.bldc + len(n.brushed_a)
    if n_sig:
        picks.append(_pick("jst_xh", n_sig, "signal / power plugs per actuator (serviceable)"))
    mass = sum(p["mass_g"] * p["qty"] for p in picks)
    wiring = round(0.08 * mass, 1)
    return {"components": picks, "total_mass_g": round(mass, 1),
            "wiring_allowance_g": wiring, "power_budget": pb, "warnings": warnings,
            "notes": n.notes,
            "reserve_boxes": [{"name": f"{p['name']}" + (f" x{p['qty']}" if p["qty"] > 1 else ""),
                               "size": _stack(p)} for p in picks if p["role"] not in
                              ("connector", "signal_connector")]}


def _stack(p: dict) -> list[float]:
    """The space qty copies take: stacked along the thinnest side (+2 mm between)."""
    l, w, h = (float(x) for x in p["size_mm"])
    q = int(p.get("qty") or 1)
    if q <= 1:
        return [l, w, h]
    return [l, w, h * q + 2.0 * (q - 1)]


def needs_from_actuators(perfs: list[act.Perf | None], unassigned_as: str = "servo") -> dict:
    out = {"hobby_servos": 0, "bus_servos": 0, "steppers": 0, "bldc": 0, "brushed_a": []}
    for p in perfs:
        t = p.type if p else unassigned_as
        if t == "servo":
            out["hobby_servos"] += 1
        elif t == "bus_servo":
            out["bus_servos"] += 1
        elif t == "stepper":
            out["steppers"] += 1
        elif t.startswith("bldc"):
            out["bldc"] += 1
        elif t == "dc_geared":
            out["brushed_a"].append(p.stall_current_a if p else 1.0)
    return out
