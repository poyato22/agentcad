"""Checker self-tests: references score full marks; known-bad answers lose the right requirements.

    cd core && uv run python -m pytest evals/bench/test_functional.py -q
"""
from __future__ import annotations

import pytest

b3 = pytest.importorskip("build123d")

from build123d import Box, Cylinder, Pos  # noqa: E402

from evals.bench.functional import reference as R  # noqa: E402
from evals.bench.functional import tasks as T  # noqa: E402

FAST = [t for t in [*R.SINGLE, *R.EDITS] if t not in ("K2_spur_gear_pair", "K3_rack_and_pinion")]


@pytest.mark.parametrize("tid", FAST)
def test_reference_scores_full_marks(tid, tmp_path):
    R.build(tid, tmp_path)
    r = T.score(tid, tmp_path)
    assert r["strict"], [q for q in r["requirements"] if not q["passed"]]


def _failed(r):
    return {q["name"] for q in r["requirements"] if not q["passed"]}


def test_pin_at_nominal_fails_the_fit_only(tmp_path):
    parts = R.f1()
    parts["pin"] = Cylinder(5.0, 30)                   # Ø10.000: not g6, and clashes with H7 min
    R.write(parts, tmp_path)
    assert _failed(T.score("F1_pin_hole_fit", tmp_path)) == {"pin g6 (9.986..9.995)"}


def test_overlapping_flanges_fail_contact_checks(tmp_path):
    parts = R.f2()
    parts["flange_b"] = Pos(0, 0, 1) * parts["flange_b"]   # 1 mm interpenetration
    R.write(parts, tmp_path)
    assert "no interference" in _failed(T.score("F2_bolted_flanges", tmp_path))


def test_edit_that_changes_more_fails_regression(tmp_path):
    before, after = R.e1()
    after = {"plate": after["plate"] - Pos(0, 30, 0) * Cylinder(3, 8)}  # an extra hole
    R.write(before, tmp_path / "stage_0")
    R.write(after, tmp_path)
    f = _failed(T.score("E1_edit_hole_pattern", tmp_path))
    assert "nothing else changed (symmetric difference)" in f


def test_missing_parts_and_missing_export(tmp_path):
    R.write({"thing": Box(10, 10, 10)}, tmp_path)
    r = T.score("F1_pin_hole_fit", tmp_path)
    assert r["passed"] == 0 and r["n_req"] == 1
    r = T.score("S1_cantilever_arm", tmp_path / "nothing")
    assert r["score"] == 0 and not r["strict"]


def test_undercut_detected(tmp_path):
    parts = R.p1()
    parts["plate"] = parts["plate"] - Pos(0, 0, 3) * Box(60, 10, 2)    # a hidden internal slot
    R.write(parts, tmp_path)
    assert "no undercuts from +Z (3-axis, one setup)" in _failed(T.score("P1_cnc_plate", tmp_path))
