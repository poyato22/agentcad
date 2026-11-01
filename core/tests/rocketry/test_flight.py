"""The 3-DOF flight model against OpenRocket 24.12 reference flights (Plan 11 F6)."""
import pytest

from mises.rocketry import motors
from mises.rocketry.flight import FlightSetup, chute_for, descent_rate, simulate

from . import reference as ref


@pytest.mark.parametrize("which,motor", [("A", "F52C"), ("B", "G40W")])
def test_apogee_matches_openrocket(which, motor):
    o = ref.OR_FLIGHTS[(which, motor)]
    r = simulate(FlightSetup(ref.body(which), motors.find(motor, None), ref.OR[which]["dry_mass"]))
    assert r.apogee_m == pytest.approx(o["apogee"], rel=ref.TOL_APOGEE)
    assert r.max_v_mps == pytest.approx(o["max_v"], rel=ref.TOL_VMAX)
    assert r.t_apogee_s == pytest.approx(o["t_apogee"], rel=ref.TOL_T)
    assert r.rail_exit_mps == pytest.approx(o["rail_v"], rel=0.05)
    assert r.liftoff_mass_kg == pytest.approx(o["wet_mass"], rel=1e-3)


def test_chute_opens_at_apogee_and_descends_at_the_sized_rate():
    m = motors.find("F52C", None)
    land = 0.540 + m.total_kg - m.prop_kg
    d = chute_for(land, 5.0)
    r = simulate(FlightSetup(ref.body(), m, 0.540, chute_d_m=d))
    assert r.deploy_t_s == pytest.approx(r.t_apogee_s, abs=0.05)
    assert r.deploy_v_mps < 5
    # terminal speed near the ground (air denser than at apogee -> slightly under 5)
    assert r.descent_mps == pytest.approx(5.0, rel=0.05)
    assert r.flight_time_s == pytest.approx(r.t_apogee_s + r.apogee_m / 5.0, rel=0.08)
    assert descent_rate(land, d) == pytest.approx(5.0)


def test_early_ejection_reports_the_deploy_speed_and_shock():
    m = motors.find("F52C", None)
    r = simulate(FlightSetup(ref.body(), m, 0.540, chute_d_m=0.6, delay_s=2.0))
    assert r.deploy_t_s == pytest.approx(m.burn_time_s + 2.0, abs=0.05)
    assert r.deploy_v_mps > 20 and r.deploy_shock_g > 10


def test_wind_drifts_the_rocket_downwind_on_the_chute():
    m = motors.find("F52C", None)
    calm = simulate(FlightSetup(ref.body(), m, 0.540, chute_d_m=0.6))
    windy = simulate(FlightSetup(ref.body(), m, 0.540, chute_d_m=0.6, wind_mps=5.0))
    assert abs(calm.drift_m) < 1
    assert windy.drift_m > 20
    assert windy.apogee_m < calm.apogee_m          # weathercocked into the wind


def test_heavier_rocket_flies_lower():
    m = motors.find("G40W", None)
    a = simulate(FlightSetup(ref.body(), m, 0.5)).apogee_m
    b = simulate(FlightSetup(ref.body(), m, 0.7)).apogee_m
    assert b < a


def test_cd_override_is_used():
    m = motors.find("F52C", None)
    base = simulate(FlightSetup(ref.body(), m, 0.540)).apogee_m
    draggy = simulate(FlightSetup(ref.body(), m, 0.540, cd_override=1.0)).apogee_m
    assert draggy < base * 0.8
