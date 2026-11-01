"""The Plan 10 coupling / loop contract's units (mises.mech.contract), checked against the
library's real couplings: a rack (mm per deg) and a planet (an offset)."""
import math

import pytest

from mises.mech import contract, library


def test_rack_ratio_converts_to_metres_per_radian():
    m, p, plan = library.plan_for("rack_pinion", {"module": 1.0, "z": 20})
    rack = next(j for j in plan.joints if j.coupling)
    assert rack.coupling["ratio"] == pytest.approx(10.0 * math.pi / 180)      # mm per deg
    pc = contract.polycoef("slider", "revolute", rack.coupling["ratio"], 0.0)
    assert pc[1] == pytest.approx(0.010)                                      # r = 10 mm, in m/rad


def test_planet_offset_is_carried_in_si():
    m, p, plan = library.plan_for("planetary", {})
    planet = next(j for j in plan.joints if j.key == "planet_1_rev")
    off = planet.coupling["offset"]
    assert off != 0
    pc = contract.polycoef("revolute", "revolute", planet.coupling["ratio"], off)
    assert pc[0] == pytest.approx(math.radians(off)) and pc[1] == planet.coupling["ratio"]


def test_loop_types_map_to_mjcf():
    assert contract.LOOP_TO_MJCF == {"revolute": "connect", "ball": "connect", "fixed": "weld"}
