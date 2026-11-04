"""DRONE demo: thrust-to-weight and the motor/prop excitation band were 'not verified' (no
thrust table, no tool). loads_propulsion estimates them from prop size, KV and cells with the
static propeller coefficients (T = Ct rho n^2 D^4, P = Cp rho n^3 D^5), flagged as an estimate."""
import math
from types import SimpleNamespace

import pytest

from mises.engineering.loads import RHO_AIR, propulsion
from mises.engineering.tools import PropulsionArgs, loads_propulsion


def test_a_3_inch_1404_on_3s_is_a_few_hundred_grams_per_motor():
    r = propulsion(prop_diameter_in=3.0, blades=3, motor_kv=4600, cells=3, motors=4,
                   all_up_mass_g=98.7)
    assert 200 < r["max_thrust_g_per_motor"] < 450            # 1404 4600KV 3S tri-blade class
    assert r["thrust_to_weight"] == pytest.approx(4 * r["max_thrust_g_per_motor"] / 98.7,
                                                  rel=1e-3)
    assert r["estimate"] is True and "Ct" in r["formula"]
    # hover: a quarter of the weight per motor, at the thrust law's rpm
    D = 3 * 0.0254
    n_h = r["hover_rpm"] / 60
    assert r["ct"] * RHO_AIR * n_h ** 2 * D ** 4 == pytest.approx(0.0987 * 9.80665 / 4, rel=1e-3)
    assert 0 < r["hover_throttle"] < 0.5
    lo, hi = r["rotation_band_hz"]
    assert lo == pytest.approx(r["hover_rpm"] / 60, rel=1e-3)
    assert hi == pytest.approx(r["max_rpm"] / 60, rel=1e-3)
    assert r["blade_pass_band_hz"] == pytest.approx([3 * lo, 3 * hi], rel=1e-3)


def test_a_power_limit_caps_the_rpm_and_thrust():
    free = propulsion(prop_diameter_in=5.0, blades=3, motor_kv=2600, cells=6, motors=4,
                      all_up_mass_g=650)
    capped = propulsion(prop_diameter_in=5.0, blades=3, motor_kv=2600, cells=6, motors=4,
                        all_up_mass_g=650, motor_max_power_w=150)
    assert capped["max_rpm"] < free["max_rpm"] and capped["limited_by"] == "motor power"
    assert capped["max_power_w_per_motor"] <= 150 + 1e-6
    assert free["limited_by"] == "voltage (KV x V)"


def test_a_heavier_craft_hovers_at_more_throttle():
    a = propulsion(prop_diameter_in=3, blades=3, motor_kv=4600, cells=3, motors=4,
                   all_up_mass_g=100)
    b = propulsion(prop_diameter_in=3, blades=3, motor_kv=4600, cells=3, motors=4,
                   all_up_mass_g=200)
    assert b["hover_rpm"] == pytest.approx(a["hover_rpm"] * math.sqrt(2), rel=1e-3)
    assert b["thrust_to_weight"] == pytest.approx(a["thrust_to_weight"] / 2, rel=1e-3)


async def test_the_tool_summarises_twr_and_the_band():
    out = await loads_propulsion.handler(SimpleNamespace(), PropulsionArgs(
        prop_diameter_in=3, blades=3, motor_kv=4600, cells=3, motors=4, all_up_mass_g=98.7))
    assert out.ok and "thrust-to-weight" in out.summary and "Hz" in out.summary
    assert out.data["estimate"] is True
    with pytest.raises(ValueError):
        PropulsionArgs(prop_diameter_in=3, blades=3, motor_kv=0, cells=3, motors=4,
                       all_up_mass_g=98.7)
