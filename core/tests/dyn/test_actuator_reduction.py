"""DOG demo: a joint driven through an external reduction (a 2:1 belt after the servo) must be
checked against the reduced actuator. dyn_torque's actuator map keeps the reduced perf's id
("<id>@2"); resolving that id again dropped the reduction, so a fixed drivetrain still failed."""
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
