"""DOG demo: a joint driven through an external reduction (a 2:1 belt after the servo) must be
checked against the reduced actuator. dyn_torque's actuator map keeps the reduced perf's id
("<id>@2"); resolving that id again dropped the reduction, so a fixed drivetrain still failed."""
import pytest

from mises.dyn import checks
from mises.engineering import actuators as act


def test_resolve_keeps_a_reduction_suffix():
    base = act.resolve("mg996r")
    reduced = act.resolve({"id": "mg996r", "ratio": 2})
    again = act.resolve(reduced.id)
    assert again is not None
    assert again.stall_torque_nm == reduced.stall_torque_nm == 2 * base.stall_torque_nm
    assert again.no_load_rpm == base.no_load_rpm / 2


def test_waveshare_st3215_servos_are_known():
    """DOG demo: the lead had to check its ST3215-HS legs against an MG996R stand-in."""
    hs = act.resolve("servo_st3215_hs")
    assert hs.stall_torque_nm == 1.96 and hs.no_load_rpm == 106
    assert act.resolve("ST3215-HS bus servo").id == "servo_st3215_hs"
    assert act.resolve("st3215").id == "servo_st3215"
    assert act.resolve("sts3215").id == "servo_sts3215"


WAVESHARE_ITEM = {
    "id": "url_ab2ca371799eed72", "category": "motor", "source": "url", "vendor": "Waveshare",
    "name": "20kg.cm Bus Servo Motor, 106PRM High Speed, Large Torque",
    "specs": [{"key": "weight", "value": 68.0, "unit": "g"},
              {"key": "product_type", "value": "ST3215-HS bus servo motor", "unit": None},
              {"key": "torque", "value": "20kg.cm@12V", "unit": None},
              {"key": "no_load_speed", "value": "0.094sec / 60° (106RPM)", "unit": None},
              {"key": "no_load_current", "value": 240.0, "unit": "mA"},
              {"key": "locked_rotor_current", "value": 2.4, "unit": "A"}]}


def test_an_imported_catalog_servo_resolves(monkeypatch):
    """DOG demo: every joint's actuator was the URL-imported ST3215-HS item, which resolve()
    could not read, so the walk study ran with no actuators ("no actuator assigned")."""
    items = {WAVESHARE_ITEM["id"]: WAVESHARE_ITEM}
    monkeypatch.setattr(act, "_item_lookup", items.get)
    p = act.resolve({"item_id": WAVESHARE_ITEM["id"], "ratio": 1.24})
    assert p is not None and p.stall_torque_nm == 1.96 * 1.24       # the curated row, reduced
    unknown = {**WAVESHARE_ITEM, "id": "url_x", "specs": [
        s for s in WAVESHARE_ITEM["specs"] if s["key"] != "product_type"]}
    unknown["name"] = "Bus servo"
    items["url_x"] = unknown
    q = act.resolve("url_x")                                         # parsed from its specs
    assert q.stall_torque_nm == round(20 * 0.0980665, 4) and q.no_load_rpm == 106
    assert q.stall_current_a == 2.4 and q.idle_current_a == 0.24 and q.mass_g == 68
    assert q.voltage_v == 12
    items["url_y"] = {"id": "url_y", "category": "motor", "name": "Mystery", "specs": []}
    assert act.resolve("url_y") is None


def test_a_free_base_servo_that_kept_up_is_sized_on_what_it_drew():
    """DOG demo: a trotting knee servo tracked within 1.3 deg drawing 1.56 N m, but sizing took
    the free-base inverse (3.2 N m, soft contacts re-solved from differenced accelerations).
    When the servo kept up, its own torque is the demand; when it fell behind the inverse
    still says how much more it needed."""
    perf = act.resolve("servo_st3215_hs")
    j = {"joint": "knee", "actuator": "a", "peak_servo": 1.0, "peak_inverse": 3.0,
         "rms_servo": 0.3, "rms_inverse": 0.5, "peak_speed": 1.0, "points": [],
         "max_tracking_error": 0.02, "free_base": True}
    row = checks.torque_row(j, perf)
    assert row["peak_nm"] == 1.0 and row["status"] == "pass"
    lag = checks.torque_row({**j, "max_tracking_error": 0.2}, perf)
    assert lag["peak_nm"] == 3.0 and lag["status"] == "fail"
    fixed = checks.torque_row({**j, "free_base": False}, perf)
    assert fixed["peak_nm"] == 3.0


def test_a_feetech_page_with_its_own_spec_names_resolves(monkeypatch):
    """DOG demo: the imported STS3250 page names its specs peak_stall_torque, rated_torque,
    stall_current, runnig_current_at_no_load: the lead could not simulate the upgrade."""
    item = {"id": "url_f", "category": "motor",
            "name": "12V 50KG Magnetic Encoder Double Shaft TTL Servo",
            "specs": [{"key": "model", "value": "ST-3250-C001", "unit": None},
                      {"key": "weight", "value": "74.5± 1g", "unit": None},
                      {"key": "communication_speed", "value": "38400bps ~ 1 Mbps"},
                      {"key": "no_load_speed", "value": "0.133sec/60°@12V", "unit": None},
                      {"key": "runnig_current_at_no_load", "value": "280mA@12V"},
                      {"key": "peak_stall_torque", "value": "50kg.cm@12V", "unit": None},
                      {"key": "rated_torque", "value": "16kg.cm@12V", "unit": None},
                      {"key": "stall_current", "value": "4.2A@12V", "unit": None}]}
    monkeypatch.setattr(act, "_item_lookup", {"url_f": item}.get)
    p = act.resolve("url_f")
    assert p.stall_torque_nm == round(50 * 0.0980665, 4)
    assert p.rated_torque_nm == round(16 * 0.0980665, 4)
    assert round(p.no_load_rpm) == 75 and p.voltage_v == 12
    assert p.stall_current_a == 4.2 and p.idle_current_a == pytest.approx(0.28)
    assert p.mass_g == 74.5


def test_a_servo_that_kept_up_is_checked_on_its_own_torque_speed_points():
    """DOG demo: the hips 'failed the torque-speed line' on inverse points (3 N m at speed)
    while the servo itself drew 1 N m there and tracked within 1 deg."""
    perf = act.resolve("servo_st3215_hs")
    w = 0.6 * perf.no_load_rad_s
    j = {"joint": "hip", "actuator": "a", "peak_servo": 1.0, "peak_inverse": 1.0,
         "rms_servo": 0.3, "rms_inverse": 0.3, "peak_speed": w, "max_tracking_error": 0.01,
         "free_base": True, "points": [[w, 1.8]], "points_servo": [[w, 0.3]]}
    assert checks.torque_row(j, perf)["torque_speed_margin"] > 0
    assert checks.torque_row({**j, "max_tracking_error": 0.2}, perf)["torque_speed_margin"] < 0


def test_torque_rows_check_against_the_reduced_actuator():
    reduced = act.resolve({"id": "mg996r", "ratio": 2})
    peak = 1.5 * act.resolve("mg996r").stall_torque_nm * 0.6   # over 1:1, fine at 2:1
    result = {"joints": [{"joint": "knee", "actuator": "a_knee", "peak_servo": peak,
                          "peak_inverse": peak, "rms_servo": 0.1, "rms_inverse": 0.1,
                          "peak_speed": 1.0, "points": []}]}
    mapping = {"actuators": {"a_knee": {"joint_id": "knee", "actuator": reduced.id,
                                        "perf": reduced.as_dict()}}}
    row = checks.torque_rows(result, mapping)[0]
    assert row["stall_nm"] == reduced.stall_torque_nm
    assert row["status"] == "pass"
