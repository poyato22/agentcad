"""S3 fix round 1 (review task-S3-review.md, probes from s3probe.py): implausible calibrations
are rejected and never applied, outlier tests are listed, the orthotropic stiffness must be
positive definite, fatigue keeps the infill factor of a calibrated material, labels say what
was calibrated, and the S1 (plasticity / nonlinear) interplay is guarded."""
from dataclasses import replace

import numpy as np
import pytest

from mises.sim import calibration as cal
from mises.sim import fatigue, studies
from mises.sim.aniso import orthotropic_cards
from mises.sim.materials import (BASIS_ESTIMATE, get_material, with_anisotropy, with_fdm)
from mises.sim.settings import FatigueSettings
from mises.sim.types import SimError

CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
PLA = get_material("PLA")


def _bend(E, L=64.0, b=10.0, h=4.0, P=20.0, **kw):
    d = P * L ** 3 / (48 * E * b * h ** 3 / 12)
    return cal.BendTest(span_mm=L, width_mm=b, thickness_mm=h, load_n=P, deflection_mm=d, **kw)


def _bar(strength, area=40.0, **kw):
    return cal.TensileTest(force_n=strength * area, area_mm2=area, **kw)


def _rec(f, printer="MK4"):
    return {"id": "cal_0000000001", "material": "PLA", "printer": printer, "fit": f}


# ---- C1: plausibility and outliers

def test_review_probe_typo_coupons_are_rejected_listed_and_never_applied(tmp_path):
    """s3probe.py: a 0.01 mm deflection (for 1.0) and 40 000 N on 40 mm2 (for 4000) gave
    E = 102 400 MPa and strength 1000 MPa, saved as calibrated and applied to every study."""
    inp = cal.CalibrationInput(material="PLA", printer="MK4", bend=[
        cal.BendTest(span_mm=64, width_mm=10, thickness_mm=4, load_n=10, deflection_mm=0.01)],
        tensile=[cal.TensileTest(force_n=40000, area_mm2=40)])
    f = cal.fit(inp, PLA)
    assert f["status"] == "rejected" and f["status_label"] == "rejected — implausible"
    assert {o["property"] for o in f["outliers"]} == {"e", "strength"}
    assert all("typo" in o["reason"] for o in f["outliers"])
    assert any("every E test" in r for r in f["reasons"])
    assert "e" not in f and "strength" not in f
    rec = cal.save(tmp_path, inp, f)
    assert rec["status"] == "rejected" and rec["basis"] == cal.REJECTED_LABEL and rec["reasons"]
    assert "NOT applied" in cal.summary(rec)
    assert cal.applicable(tmp_path, PLA, "MK4") is None
    s = studies.validate_settings("static", {"load_cases": [CASE],
                                             "material": {"name": "PLA", "printer": "MK4"}})
    assert studies.resolve_material(s.material, "PLA", tmp_path).basis == BASIS_ESTIMATE


def test_consistent_but_implausible_tests_reject_the_fit():
    f = cal.fit(cal.CalibrationInput(material="PLA", bend=[_bend(5500.0, P=p) for p in
                                                            (10, 20, 30)]), PLA)
    assert f["status"] == "rejected" and "e" in f
    assert any("2.39x" in r and "0.3-2x" in r for r in f["reasons"])
    assert cal.plausibility(_rec(f), PLA)


def test_one_outlier_among_good_tests_is_dropped_and_listed():
    bends = [_bend(2100.0 * k) for k in (1.0, 1.02, 0.98, 1.01, 0.55)]
    f = cal.fit(cal.CalibrationInput(material="PLA", bend=bends), PLA)
    assert f["status"] == "ok" and f["e"]["n"] == 4
    assert f["e"]["value_mpa"] == pytest.approx(2100.0, rel=0.02)
    (o,) = f["outliers"]
    assert o["index"] == 4 and o["test"] == "bend" and "median" in o["reason"]


@pytest.mark.parametrize("bend,needle", [
    (dict(span_mm=30, width_mm=10, thickness_mm=4, load_n=20, deflection_mm=0.1), "span / depth"),
    (dict(span_mm=64, width_mm=10, thickness_mm=4, load_n=20, deflection_mm=8.0),
     "deflection / span")])
def test_bend_geometry_outside_the_beam_formula_is_an_outlier(bend, needle):
    f = cal.fit(cal.CalibrationInput(material="PLA", bend=[cal.BendTest(**bend), _bend(2100.0)]),
                PLA)
    assert any(needle in o["reason"] for o in f["outliers"])
    assert f["e"]["n"] == 1


def test_tensile_bar_over_3x_the_data_sheet_is_an_outlier():
    f = cal.fit(cal.CalibrationInput(material="PLA", tensile=[_bar(45), _bar(46), _bar(160)]),
                PLA)
    assert [o["index"] for o in f["outliers"]] == [2] and f["strength"]["n"] == 2


def test_stiffer_across_layers_than_in_plane_is_rejected():
    f = cal.fit(cal.CalibrationInput(material="PLA", bend=[_bend(2000.0),
                                                           _bend(2600.0, orientation="z")]), PLA)
    assert f["status"] == "rejected" and any("E_z / E" in r for r in f["reasons"])


def test_fibre_filled_class_has_its_own_bounds():
    assert cal.material_class(get_material("PA-CF")) == "fibre-filled"
    assert cal.material_class(PLA) == "unfilled"
    f = cal.fit(cal.CalibrationInput(material="PA-CF", bend=[_bend(3860 * 0.25, P=p)
                                                             for p in (10, 20, 30)]),
                get_material("PA-CF"))
    assert f["status"] == "ok"  # 0.25x is plausible for fibre-filled, not for unfilled
    f2 = cal.fit(cal.CalibrationInput(material="PLA", bend=[_bend(2300 * 0.25, P=p)
                                                            for p in (10, 20, 30)]), PLA)
    assert f2["status"] == "rejected"


def test_scatter_gives_low_confidence_on_the_label_and_the_clamp_warns():
    bars = [_bar(s) for s in (30, 45, 60, 32, 58)]
    f = cal.fit(cal.CalibrationInput(material="PLA", tensile=bars), PLA)
    assert f["strength"]["cv"] > 0.25 and f["confidence"] == "low"
    assert any("CV" in w for w in f["warnings"])
    assert any("floor" in w for w in f["warnings"])
    assert cal.fit_label(f).endswith("low confidence")


def test_an_old_record_without_a_status_is_rechecked_before_it_applies(tmp_path):
    bad = {"n_tests": 1, "confidence": "low",
           "strength": {"value_mpa": 900.0, "n": 1, "knockdown": 0.85}}
    assert cal.plausibility(_rec(bad), PLA)
    good = {"n_tests": 3, "confidence": "medium",
            "strength": {"value_mpa": 45.0, "n": 3, "knockdown": 0.9}}
    assert cal.plausibility(_rec(good), PLA) == []


def test_residuals_are_the_relative_deflection_residuals():
    f = cal.fit_modulus([2000.0, 2200.0])
    e = f["value_mpa"]
    assert f["residuals"] == pytest.approx([2000 / e - 1, 2200 / e - 1])


def test_append_past_the_test_limit_is_a_clean_error(tmp_path):
    rec = {"tests": {"tensile": [_bar(45).model_dump()] * cal.MAX_TESTS, "bend": [], "part": []}}
    with pytest.raises(SimError, match="at most 100 tests"):
        cal.merged(rec, cal.CalibrationInput(material="PLA", tensile=[_bar(45)]))


# ---- I3: positive definiteness

def test_orthotropic_constants_must_be_positive_definite():
    with pytest.raises(SimError, match="positive definite"):
        with_anisotropy(replace(PLA, nu=0.49), (0, 0, 1), e_z_ratio=1.2)
    with pytest.raises(SimError, match="at most 1.2x"):
        with_anisotropy(replace(PLA, e_z_mpa=3 * PLA.e_mpa), (0, 0, 1))
    with pytest.raises(SimError, match="e_z_ratio"):
        studies.validate_settings("static", {"load_cases": [CASE], "material": {
            "anisotropy": {"print_direction": "+z", "e_z_ratio": 1.5}}})
    assert with_anisotropy(PLA, (0, 0, 1), e_z_ratio=1.0).orthotropic


# ---- I4: fatigue keeps the infill factor of a calibrated material

def test_calibrated_infill_part_keeps_its_fatigue_infill_factor():
    """s3probe.py: PLA 20% infill, 2 walls (factor 0.44); with a calibrated knock-down of
    0.95 the fatigue UTS / yield was 0.597."""
    for base in (PLA, replace(PLA, knockdown=0.95)):
        m = with_fdm(base, 20, 2, "xy")
        assert fatigue.fatigue_uts(m) / m.yield_mpa == pytest.approx(0.44)
    st = FatigueSettings(cycles=1e5, cycle="fully_reversed")
    a = fatigue.evaluate([1], np.array([[5.0, 0, 0, 0, 0, 0]]), {1: (0, 0, 0)},
                         with_fdm(PLA, 20, 2, "xy"), st)
    b = fatigue.evaluate([1], np.array([[5.0, 0, 0, 0, 0, 0]]), {1: (0, 0, 0)},
                         with_fdm(replace(PLA, knockdown=0.95), 20, 2, "xy"), st)
    assert a["safety_factor"] == pytest.approx(b["safety_factor"])


# ---- I5 / M7 / M8: labels and which calibration applies

def test_label_names_the_calibrated_properties(tmp_path):
    f = cal.fit(cal.CalibrationInput(material="PLA", bend=[_bend(2100.0, P=p)
                                                           for p in (10, 15, 20, 25, 30)]), PLA)
    m = cal.apply(PLA, _rec(f))
    assert m.basis == "calibrated E (5)" and m.yield_mpa == PLA.yield_mpa
    assert m.basis_by_property == {"e": "calibrated (5 tests)", "e_z": BASIS_ESTIMATE,
                                   "strength": BASIS_ESTIMATE, "z_strength": BASIS_ESTIMATE}
    assert "strength" in " ".join(studies.s3_caveats(m, None))


def test_inline_custom_material_is_not_calibrated_by_a_builtin_name(tmp_path):
    inp = cal.CalibrationInput(material="PLA", tensile=[_bar(45), _bar(46), _bar(44)])
    cal.save(tmp_path, inp, cal.fit(inp, PLA))
    s = studies.validate_settings("static", {"load_cases": [CASE], "material": {"custom": {
        "name": "PLA", "e_mpa": 3000, "nu": 0.3, "yield_mpa": 60, "density_g_cm3": 1.2}}})
    assert studies.resolve_material(s.material, "PLA", tmp_path).basis == BASIS_ESTIMATE
    s2 = studies.validate_settings("static", {"load_cases": [CASE], "material": {"name": "PLA"}})
    assert studies.resolve_material(s2.material, "PLA", tmp_path).basis.startswith("calibrated")


def test_layer_orientation_z_uses_a_calibrated_z_strength():
    m = replace(PLA, z_strength_mpa=20.0)
    z = with_fdm(m, 100, 3, "z")
    assert z.allowable_mpa == pytest.approx(PLA.yield_mpa * 0.7 * 20 / 51)
    assert with_fdm(PLA, 100, 3, "z").allowable_mpa == pytest.approx(51 * 0.7 * 0.6)


# ---- M1: S1 interplay

def test_orthotropic_plasticity_must_be_bilinear():
    m = with_anisotropy(PLA, (0, 0, 1))
    ok = orthotropic_cards(["*MATERIAL, NAME=MAT", "*ELASTIC", "2300,0.35", "*PLASTIC",
                            "40,0", "50,0.05", "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"], m)
    assert "50,0.05" in ok
    with pytest.raises(SimError, match="bilinear"):
        orthotropic_cards(["*MATERIAL, NAME=MAT", "*ELASTIC", "2300,0.35", "*PLASTIC",
                           "40,0", "50,0.05", "55,0.1", "*SOLID SECTION, ELSET=EALL"], m)


def test_plasticity_with_anisotropy_gets_the_in_plane_yield_caveat():
    m = with_anisotropy(PLA, (0, 0, 1))
    cav = studies.s3_caveats(m, type("S", (), {"nonlinear": {"plasticity": True}})())
    assert any("yield uses the in-plane strength" in c and "elastic only" in c for c in cav)
    assert not any("in-plane strength (isotropic" in c for c in studies.s3_caveats(m, None))


def test_part_load_test_refuses_a_nonlinear_study():
    class F:
        ch = {"displacement": None}
    rec = {"type": "static", "status": "done", "material": {"e_eff_mpa": 2300},
           "settings": {"load_cases": [CASE], "nonlinear": {"large_deflection": True}}}
    t = cal.PartTest(sim_id="sim_0123456789ab", point=(100, 5, 5), load_n=20, deflection_mm=1)
    with pytest.raises(SimError, match="nonlinear"):
        cal.part_estimate(rec, F(), t)


# ---- M4: fatigue labels

def test_fatigue_extrapolation_and_user_curves_are_labelled():
    S = np.array([[5.0, 0, 0, 0, 0, 0]])
    hi = fatigue.evaluate([1], S, {1: (0, 0, 0)}, PLA, FatigueSettings(cycles=1e9))
    assert "extrapolated beyond 2e+06" in hi["sn_curve"]["source"]
    lo = fatigue.evaluate([1], S, {1: (0, 0, 0)}, PLA, FatigueSettings(cycles=100))
    assert "low-cycle" in lo["sn_curve"]["source"]
    mid = fatigue.evaluate([1], S, {1: (0, 0, 0)}, PLA, FatigueSettings(cycles=1e5))
    assert "extrapolated" not in mid["sn_curve"]["source"].replace("(extrapolated)", "")
    user = fatigue.evaluate([1], S, {1: (0, 0, 0)}, PLA, FatigueSettings(
        cycles=1e5, sn={"a_mpa": 80, "b": -0.1}))
    assert user["sn_curve"]["basis"] == "user-supplied"
    card = studies.card({"type": "static", "status": "done", "material": {},
                         "results": {"fatigue": hi, "fatigue_safety_factor": 1.0}})
    assert "extrapolated" in card["fatigue_note"]
