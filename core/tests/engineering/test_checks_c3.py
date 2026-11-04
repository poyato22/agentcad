"""Practical engineering checks (Plan 10 C3, research §3, §6.4): power budget + battery pick on
a fixture spec, wire gauges, lumped thermal, static actuator sizing, cable routing, the
electronics picks and the actuator table."""
import math

import pytest

from mises.engineering import actuators as act
from mises.engineering import checks, electronics


def test_wire_gauge_follows_the_ampacity_table():
    assert checks.wire_gauge(4.0)["awg"] == 22
    assert checks.wire_gauge(10.0)["awg"] == 18
    assert checks.wire_gauge(25.0)["awg"] == 12
    assert "parallel" in checks.wire_gauge(80.0)["note"]


def test_battery_pick_meets_energy_and_current():
    b = checks.pick_battery(20.0, 30.0)
    assert b["wh"] >= 20.0 and b["i_max_a"] >= 1.25 * 30.0
    # the lightest that fits: 4S 1500 (22.2 Wh, 112 A, 175 g) beats 3S 2200 (188 g)
    assert b["id"] == "lipo_4s_1500"
    assert checks.pick_battery(20.0, 30.0, cells=3)["id"] == "lipo_3s_2200"
    assert checks.pick_battery(500.0, 1.0) is None


def test_power_budget_of_an_eight_servo_quadruped():
    """8 MG996R at 0.4 N m rms, ESP32 + IMU + PCA9685, 30 min: hand numbers."""
    perf = act.resolve("mg996r")
    acts = [{"name": f"j{i}", "perf": perf, "tau_rms_nm": 0.4, "omega_rms_rad_s": 1.0,
             "tau_peak_nm": 0.8} for i in range(8)]
    el = [{"name": "esp32", "power_w": [0.6, 1.2]}, {"name": "imu", "power_w": [0.05, 0.06]}]
    pb = checks.power_budget(acts, el, runtime_h=0.5)
    i_rms = perf.current_at(0.4)          # 0.01 + 0.4/1.08 * 2.49
    assert pb["p_actuators_w"] == pytest.approx(8 * 6.0 * i_rms, rel=1e-3)
    p_avg = 8 * 6.0 * i_rms + 0.65
    assert pb["p_avg_w"] == pytest.approx(p_avg, rel=1e-3)
    assert pb["e_needed_wh"] == pytest.approx(p_avg * 0.5 / 0.8 / 0.9, rel=1e-3)
    assert pb["battery_voltage_v"] == pytest.approx(7.4)          # 2S for a 6 V servo rail
    b = pb["battery"]
    assert b["cells"] == 2 and b["wh"] >= pb["e_needed_wh"]
    assert b["i_max_a"] >= 1.25 * pb["i_peak_a"]
    assert pb["status"] == "pass" and pb["rail_note"]
    assert pb["wires"]["actuator_branch"]["awg"] == 26       # one servo: ~1.9 A peak


def test_thermal_lumped_flags_pla():
    # research §3.7: a Pi 5 at 5 W in a 100 x 70 x 40 box: dT 18-36 K
    t = checks.thermal_estimate(5.0, size_mm=[100, 70, 40], max_service_c=55, material="PLA")
    assert t["delta_t_k"][1] == pytest.approx(5.0 / (5.0 * 0.0276), rel=1e-2)
    assert t["status"] == "fail" or t["status"] == "warn"
    ok = checks.thermal_estimate(1.0, size_mm=[100, 70, 40], max_service_c=90, material="ASA")
    assert ok["status"] == "pass"


def test_static_sizing_of_a_horizontal_arm():
    """A 0.2 kg link with its com 0.1 m out on a revolute about y: 0.196 N m at horizontal."""
    insts = [{"id": "base", "mass_kg": 1.0, "com": [0, 0, 0]},
             {"id": "link", "mass_kg": 0.2, "com": [0.1, 0, 0]}]
    tree = [{"id": "sh", "kind": "revolute", "a": "base", "b": "link", "anchor": [0, 0, 0],
             "axis": [0, 1, 0], "value": 0.0, "limits": [-90, 90]}]

    def pose(values):
        v = math.radians(values["sh"])
        return ([insts[0], {"id": "link", "mass_kg": 0.2,
                            "com": [0.1 * math.cos(v), 0, -0.1 * math.sin(v)]}], tree)
    rows = checks.static_torques(insts, tree, root="base", free=False, feet=[], pose_fn=pose)
    assert rows[0]["static_nm"] == pytest.approx(0.2 * 9.81 * 0.1, rel=1e-6)
    sized = checks.size_rows(rows, {"sh": act.resolve("sg90")}, dynamic_factor=1.5)
    assert sized[0]["status"] == "fail"            # 0.29 N m vs a 0.18 N m SG90
    assert sized[0]["candidates"]
    sized = checks.size_rows(rows, {"sh": act.resolve("mg996r")})
    assert sized[0]["status"] == "pass"


def test_cable_route_goes_around_a_block():
    block = [([-10, -50, -50], [10, 50, 50])]
    r = checks.route([-50, 0, 0], [50, 0, 0], block, voxel=5.0, clearance=2.0)
    assert r["path"] is not None
    straight = 100.0
    assert r["length_mm"] > straight + 50          # it has to go over or around
    for p in r["path"]:
        assert not (-12 < p[0] < 12 and -52 < p[1] < 52 and -52 < p[2] < 52)
    assert checks.service_loop_mm(2.0, 90) == pytest.approx(8 * 2.0 * math.pi / 2 + 20)


def test_electronics_for_a_camera_quadruped():
    perfs = [act.resolve("mg996r")] * 8
    n = electronics.Needs(**electronics.needs_from_actuators(perfs), vision=True, legs=True,
                          runtime_h=0.5,
                          actuators=[{"name": f"j{i}", "perf": p, "tau_rms_nm": 0.4,
                                      "omega_rms_rad_s": 1.0, "tau_peak_nm": 0.8}
                                     for i, p in enumerate(perfs)])
    out = electronics.estimate(n)
    roles = {c["role"]: c for c in out["components"]}
    controllers = [c["item_id"] for c in out["components"] if c["role"] == "controller"]
    assert controllers == ["rpi5", "esp32_s3_devkitc"]     # SBC + a joint microcontroller
    assert roles["servo_driver"]["item_id"] == "pca9685"
    assert roles["battery"]["item_id"] in ("lipo_2s_2200", "liion_2s2p_p28a", "lipo_2s_5000")
    assert roles["estop"] and roles["imu"]["item_id"] == "bno085"
    assert roles["ubec"]["item_id"] in ("ubec_6v10a", "ubec_6v20a")
    assert out["total_mass_g"] > 0 and out["reserve_boxes"]
    for c in out["components"]:
        assert c["reason"] and c["size_mm"] and c["mass_g"] >= 0


def test_electronics_drone_and_rf_warning():
    n = electronics.Needs(bldc=4, drone=True, outdoor=True, enclosure_material="CF plate",
                          actuators=[])
    out = electronics.estimate(n)
    roles = [c["role"] for c in out["components"]]
    assert "esc" in roles and "gps" in roles and "radio" in roles
    assert any("blocks RF" in w for w in out["warnings"])
    assert electronics.needs_from_text("a camera robot for the garden")["vision"]
    assert electronics.needs_from_text("a camera robot for the garden")["outdoor"]


def test_actuator_table_lookup_and_torque_speed_line():
    p = act.resolve("servo_mg996r_standard")
    assert p.stall_torque_nm == 1.08 and act.resolve("MG996R") is p
    assert p.torque_available(0.0) == 1.08 and p.torque_available(p.no_load_rad_s) == 0.0
    geared = act.resolve({"catalog_id": "mg996r", "ratio": 3})
    assert geared.stall_torque_nm == pytest.approx(3.24)
    assert act.resolve("no_such_thing_xyz") is None
    c = act.candidates(1.5, 0.5, 2.0)
    assert c and all(x.stall_torque_nm * x.peak_margin >= 1.5 for x in c)


def test_a_micro_servo_carries_a_micro_servos_rotor():
    # the TVC demo: an SG90 modelled with a standard servo's 0.002 kg m2 armature could not
    # slew its own horn; the class default now scales with stall torque below the MG996R
    sg90, mg996 = act.resolve("servo_sg90_micro"), act.resolve("servo_mg996r_standard")
    assert mg996.armature() == pytest.approx(0.002)
    assert sg90.armature() == pytest.approx(0.002 * 0.177 / 1.08, rel=1e-3)
