"""Grader self-tests for Mises-Physics: references pass; known-bad designs fail the intended
requirement; the physics helpers agree with hand calculations.

    cd core && uv run python -m pytest evals/bench/test_physics.py -q
"""
from __future__ import annotations

import math

import pytest

b3 = pytest.importorskip("build123d")

from build123d import Box, Cylinder, Pos  # noqa: E402

from evals.bench.functional.reference import write  # noqa: E402
from evals.bench.physics import mech  # noqa: E402
from evals.bench.physics import reference as R  # noqa: E402
from evals.bench.physics import tasks as T  # noqa: E402

FAST = ["PH01_bracket_200N", "PH05_two_link_arm", "PH06_flywheel", "PH07_pendulum",
        "PH08_robot_base", "PH13_spring_beam", "PH18_balanced_lever", "PH19_lamp_base"]


@pytest.mark.parametrize("tid", FAST)
def test_reference_passes(tid, tmp_path):
    R.build(tid, tmp_path)
    r = T.score(tid, tmp_path)
    assert r["strict"], [q for q in r["requirements"] if not q["passed"]]


def _failed(r):
    return {q["name"] for q in r["requirements"] if not q["passed"]}


def test_heavy_bracket_fails_mass_only(tmp_path):
    parts = R.ph01()
    parts["bracket"] = parts["bracket"] + Pos(1.5, 0, 0) * Box(3, 40, 40)
    write(parts, tmp_path)
    assert _failed(T.score("PH01_bracket_200N", tmp_path)) == {"mass ≤ 25 g"}


def test_unbalanced_flywheel_fails_balance(tmp_path):
    fw = R.ph06()["flywheel"] - Pos(54, 0, 0) * Cylinder(5, 30)
    write({"flywheel": fw}, tmp_path)
    assert "balanced: centre of mass on the axis (≤ 0.05 mm)" in _failed(T.score("PH06_flywheel", tmp_path))


def test_tipping_angle_matches_hand_calc():
    hull = [[-50, -50], [50, -50], [50, 50], [-50, 50]]
    import numpy as np
    ang = mech.tipping_angle_deg(np.array(hull, float), (0, 0, 100), 0.0)
    assert ang == pytest.approx(math.degrees(math.atan(0.5)), abs=1e-6)


def test_inertia_of_a_disc_matches_hand_calc():
    d = Cylinder(50, 10)
    I = mech.inertia_about(d, 7.85, (0, 0, 0), (0, 0, 1))
    m = math.pi * 50 ** 2 * 10 * 7.85e-6
    assert I == pytest.approx(0.5 * m * 0.05 ** 2, rel=1e-6)


def test_apogee_grows_with_impulse_and_falls_with_mass():
    a = mech.apogee_m("C6-5", 0.08, 25)
    assert a > mech.apogee_m("B6-4", 0.08, 25)
    assert a > mech.apogee_m("C6-5", 0.15, 25)
