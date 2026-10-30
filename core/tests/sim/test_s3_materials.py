"""Plan 6 S3, no solver: material calibration (fit, store, apply), the orthotropic FDM model
(deck cards, stress across the layers) and the fatigue estimate (S-N, Goodman, hand
calculation)."""
import math

import numpy as np
import pytest

from mises.sim import calibration as cal
from mises.sim import fatigue, studies
from mises.sim.aniso import layer_normal_stress, local_axes, orthotropic_cards
from mises.sim.deck import model_cards
from mises.sim.materials import (BASIS_ESTIMATE, FDM_KNOCKDOWN, get_material, with_anisotropy,
                                 with_fdm)
from mises.sim.mesh import Mesh
from mises.sim.settings import FatigueSettings
from mises.sim.types import SimError

CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
RNG = np.random.default_rng(6)


def _bend(E, L=64.0, b=10.0, h=4.0, P=20.0, noise=0.0, **kw):
    inertia = b * h ** 3 / 12
    d = P * L ** 3 / (48 * E * inertia) * (1 + noise)
    return cal.BendTest(span_mm=L, width_mm=b, thickness_mm=h, load_n=P, deflection_mm=d, **kw)


def _tensile(strength, noise=0.0, area=40.0, **kw):
    return cal.TensileTest(force_n=strength * area * (1 + noise), area_mm2=area, **kw)


# ---- calibration fit

def test_synthetic_coupon_set_recovers_known_E_and_strength_within_2pct():
    E, S = 2150.0, 46.0
    bends = [_bend(E, P=P, noise=float(n)) for P, n in
             zip((10, 15, 20, 25, 30), RNG.normal(0, 0.01, 5))]
    bars = [_tensile(S, noise=float(n)) for n in RNG.normal(0, 0.015, 6)]
    inp = cal.CalibrationInput(material="PLA", printer="MK4", tensile=bars, bend=bends)
    f = cal.fit(inp, get_material("PLA"))
    assert f["e"]["value_mpa"] == pytest.approx(E, rel=0.02)
    assert f["strength"]["value_mpa"] == pytest.approx(S, rel=0.02)
    assert f["n_tests"] == 11 and f["e"]["n"] == 5 and f["strength"]["n"] == 6
    assert 0 < f["strength"]["cv"] < 0.05 and f["e"]["sd"] > 0
    # knock-down fitted to the scatter: (mean - 2 sd) / mean
    s = f["strength"]
    assert f["knockdown"] == pytest.approx((s["mean"] - 2 * s["sd"]) / s["mean"])
    assert f["datasheet_ratio"] == pytest.approx(s["mean"] / 51.0)
    assert f["confidence"] in ("high", "medium")


def test_exact_coupons_fit_exactly_and_give_high_confidence():
    bends = [_bend(1800.0, P=P) for P in (5, 10, 15, 20, 25)]
    f = cal.fit(cal.CalibrationInput(material="PETG", bend=bends,
                                     tensile=[_tensile(40.0) for _ in range(5)]),
                get_material("PETG"))
    assert f["e"]["value_mpa"] == pytest.approx(1800.0, rel=1e-9)
    assert max(abs(r) for r in f["e"]["residuals"]) < 1e-9
    assert f["knockdown"] == pytest.approx(1.0) and f["confidence"] == "high"


def test_small_samples_get_a_conservative_knockdown_and_low_confidence():
    f = cal.fit(cal.CalibrationInput(material="PLA", tensile=[_tensile(50.0), _tensile(40.0)]),
                get_material("PLA"))
    assert f["knockdown"] == pytest.approx(0.85 * 40 / 45)
    assert f["confidence"] == "low" and "e" not in f


def test_elongation_secant_modulus_is_reported_but_never_replaces_E():
    bar = _tensile(45.0, elongation_pct=3.0)
    f = cal.fit(cal.CalibrationInput(material="PLA", tensile=[bar]), get_material("PLA"))
    assert "e" not in f and f["e_secant"]["mean"] == pytest.approx(45.0 / 0.03)
    assert "lower bound" in f["e_secant"]["method"] and f["calibrated"] == ["strength"]
    m = cal.apply(get_material("PLA"), {"id": "cal_x", "printer": "p", "fit": f})
    assert m.e_mpa == 2300.0 and m.basis == "calibrated strength (1), low confidence"
    assert m.basis_by_property["e"] == BASIS_ESTIMATE
    assert "lower bound" in m.calibration["notes"][0]
    assert "lower bound" in cal.summary({"material": "PLA", "printer": "p",
                                         "basis": cal.fit_label(f), "fit": f})
    # review probe: ductile PETG at 25% elongation would have given E = 188 MPa
    f3 = cal.fit(cal.CalibrationInput(material="PETG", tensile=[
        cal.TensileTest(force_n=1880, area_mm2=40, elongation_pct=25)]), get_material("PETG"))
    assert cal.apply(get_material("PETG"), {"id": "c", "printer": "p", "fit": f3}).e_mpa == 1500
    f2 = cal.fit(cal.CalibrationInput(material="PLA", tensile=[bar], bend=[_bend(2000.0)]),
                 get_material("PLA"))
    assert f2["e"]["value_mpa"] == pytest.approx(2000.0) and "bend" in f2["e"]["method"]


def test_z_coupons_fit_between_layer_values_and_flexural_strength_is_separate():
    f = cal.fit(cal.CalibrationInput(
        material="PLA", tensile=[_tensile(48.0), _tensile(28.0, orientation="z")],
        bend=[_bend(2200.0), _bend(1900.0, orientation="z", break_load_n=200.0)]),
        get_material("PLA"))
    assert f["e"]["value_mpa"] == pytest.approx(2200.0)
    assert f["e_z"]["value_mpa"] == pytest.approx(1900.0)
    assert f["z_strength"]["value_mpa"] == pytest.approx(28.0)
    assert f["flexural_strength"]["mean"] == pytest.approx(3 * 200 * 64 / (2 * 10 * 16))


def test_calibration_input_is_validated():
    with pytest.raises(ValueError, match="at least one"):
        cal.CalibrationInput(material="PLA")
    with pytest.raises(ValueError, match="area_mm2"):
        cal.TensileTest(force_n=100, width_mm=10)
    with pytest.raises(ValueError, match="bad name"):
        cal.CalibrationInput(material="../x", tensile=[_tensile(40)])


# ---- part load test (inverse comparison against a study)

class _Fields:
    """A cantilever's displacement field: u_z = -k x^2 (one node grid)."""
    def __init__(self, k=1e-4):
        xs = np.linspace(0, 100, 11)
        self.coords = np.array([[x, 5.0, 5.0] for x in xs])
        self.ch = {"displacement": np.array([[0.0, 0.0, -k * x * x] for x in xs])}

    def probe(self, pts):
        p = np.asarray(pts)[0]
        if not 0 <= p[0] <= 100 or abs(p[1] - 5) > 5 or abs(p[2] - 5) > 5:
            return [{"inside": False, "values": {}}]
        return [{"inside": True, "values": {"displacement": [0.0, 0.0, -1e-4 * p[0] ** 2]}}]

    def bounds(self):
        return self.coords.min(0), self.coords.max(0)


def _rec(e_eff=2300.0, stiffness=1.0, newtons=10.0):
    return {"type": "static", "status": "done", "sim_id": "sim_0123456789ab",
            "material": {"name": "PLA", "e_mpa": e_eff / stiffness, "e_eff_mpa": e_eff,
                         "stiffness_factor": stiffness},
            "settings": {"load_cases": [{**CASE, "loads": [{**CASE["loads"][0],
                                                            "newtons": newtons}]}]},
            "results": {"governing_case": "tip"}}


def test_part_load_test_updates_E_by_inverse_comparison():
    # the study (E 2300, 10 N) predicts 1.0 mm at the tip; at 20 N it would be 2.0 mm; the
    # part measured 2.5 mm: the real part is 2.0 / 2.5 as stiff -> E = 1840
    t = cal.PartTest(sim_id="sim_0123456789ab", point=(100, 5, 5), load_n=20,
                     deflection_mm=2.5, direction=(0, 0, -1))
    est = cal.part_estimate(_rec(), _Fields(), t)
    assert est["predicted_deflection_mm"] == pytest.approx(2.0)
    assert est["e_mpa"] == pytest.approx(1840.0) and est["study_load_n"] == 10.0
    f = cal.fit(cal.CalibrationInput(material="PLA", part=[t]), get_material("PLA"), [est])
    assert f["e"]["value_mpa"] == pytest.approx(1840.0) and f["n_tests"] == 1
    # infill stiffness factors are divided out: E stays the solid-material modulus
    est2 = cal.part_estimate(_rec(e_eff=1150.0, stiffness=0.5), _Fields(), t)
    assert est2["e_mpa"] == pytest.approx(1840.0)


def test_part_load_test_refuses_bad_points_and_studies():
    t = cal.PartTest(sim_id="sim_0123456789ab", point=(300, 5, 5), load_n=20, deflection_mm=1)
    with pytest.raises(SimError, match="not on the part"):
        cal.part_estimate(_rec(), _Fields(), t)
    with pytest.raises(SimError, match="finished static"):
        cal.part_estimate({**_rec(), "status": "failed"}, _Fields(), t)
    rec = _rec()
    rec["settings"]["load_cases"][0]["loads"] = [{"kind": "pressure", "face": "top", "mpa": 1}]
    with pytest.raises(SimError, match="no force load") as e:
        cal.part_estimate(rec, _Fields(), t.model_copy(update={"point": (100, 5, 5)}))
    assert "study_load_n" in e.value.hint


# ---- store and apply

def test_store_apply_and_labels(tmp_path):
    inp = cal.CalibrationInput(material="PLA", printer="MK4",
                               tensile=[_tensile(44.0), _tensile(46.0), _tensile(45.0)],
                               bend=[_bend(2000.0)])
    rec = cal.save(tmp_path, inp, cal.fit(inp, get_material("PLA")))
    assert cal.CAL_ID_RE.fullmatch(rec["id"]) and rec["basis"] == "calibrated E (1), strength (3), low confidence"
    assert cal.find(tmp_path, "pla", "mk4")["id"] == rec["id"]
    assert cal.find(tmp_path, "PLA") is None  # the default printer has no calibration
    # replacing keeps the id
    rec2 = cal.save(tmp_path, inp, cal.fit(inp, get_material("PLA")))
    assert rec2["id"] == rec["id"] and len(cal.load_all(tmp_path)) == 1
    # append mode merges the old tests in front
    more = cal.merged(rec2, cal.CalibrationInput(material="PLA", printer="MK4",
                                                 bend=[_bend(2100.0)]))
    assert len(more.tensile) == 3 and len(more.bend) == 2

    m = cal.apply(get_material("PLA"), rec)
    assert m.basis == "calibrated E (1), strength (3), low confidence" and m.e_mpa == pytest.approx(2000.0)
    assert m.yield_mpa == pytest.approx(45.0) and m.knockdown == rec["fit"]["knockdown"]
    assert m.calibration["printer"] == "MK4" and m.to_dict()["basis"] == m.basis
    assert get_material("PLA").basis == BASIS_ESTIMATE

    # studies resolve the calibration of the material + printer, unless told not to
    s = studies.validate_settings("static", {"load_cases": [CASE],
                                             "material": {"name": "PLA", "printer": "MK4"}})
    assert studies.resolve_material(s.material, "PLA", tmp_path).basis == m.basis
    off = s.material.model_copy(update={"calibrated": False})
    assert studies.resolve_material(off, "PLA", tmp_path).basis == BASIS_ESTIMATE
    other = s.material.model_copy(update={"printer": None})
    assert studies.resolve_material(other, "PLA", tmp_path).basis == BASIS_ESTIMATE
    assert "calibrated E (1), strength (3), low confidence" in " ".join(studies.s3_caveats(m, s))
    assert cal.summary(rec).startswith("PLA on MK4: calibrated E (1), strength (3), low confidence")
    assert cal.delete(tmp_path, rec["id"]) and not cal.delete(tmp_path, rec["id"])


def test_fdm_factors_build_on_a_calibrated_knockdown():
    m = get_material("PLA")
    assert with_fdm(m, 40, 2, "xy").knockdown == pytest.approx(
        FDM_KNOCKDOWN * with_fdm(m, 40, 2, "xy").stiffness_factor)
    from dataclasses import replace
    mc = replace(m, knockdown=0.9)
    assert with_fdm(mc, 40, 2, "xy").knockdown == pytest.approx(
        with_fdm(m, 40, 2, "xy").knockdown * 0.9 / FDM_KNOCKDOWN)


# ---- anisotropy

def test_orthotropic_material_uses_typical_ratios_marked_estimate():
    m = with_anisotropy(get_material("PETG"), (0, 0, 1))
    assert m.orthotropic and m.e_z_mpa == pytest.approx(1500 * 0.9)
    assert m.z_strength_mpa == pytest.approx(47 * 0.38)  # TDS interlayer adhesion 18 / 47
    assert "estimate" in m.source and m.allowable_z_mpa == pytest.approx(47 * 0.38 * 0.7)
    pa = with_anisotropy(get_material("PA-CF"), (0, 0, 1))  # TDS V2.0: E_z 2180 / 3860
    assert pa.e_z_mpa == pytest.approx(3860 * 0.565) and pa.z_strength_mpa == pytest.approx(
        64 * 0.73)
    d = m.to_dict()
    assert d["build_dir"] == [0.0, 0.0, 1.0] and "orthotropic" in d["model"]
    # explicit ratios win; a material's own Z data wins over the typical ones
    m2 = with_anisotropy(get_material("PLA"), (1, 0, 0), e_z_ratio=0.5, strength_z_ratio=0.4)
    assert m2.e_z_mpa == pytest.approx(1150) and m2.z_strength_mpa == pytest.approx(20.4)
    from dataclasses import replace
    m3 = with_anisotropy(replace(get_material("PLA"), e_z_mpa=1000.0, z_strength_mpa=20.0),
                         (0, 1, 0))
    assert (m3.e_z_mpa, m3.z_strength_mpa) == (1000.0, 20.0) and "typical" not in m3.source


def test_settings_with_anisotropy_resolve_and_drop_the_z_layer_factor(tmp_path):
    s = studies.validate_settings("static", {"load_cases": [CASE], "material": {
        "name": "PLA", "anisotropy": {"print_direction": "+x"},
        "fdm": {"infill_pct": 100, "walls": 3, "layer_orientation": "z"}}})
    m = studies.resolve_material(s.material, "PLA", None)
    assert m.build_dir == (1.0, 0.0, 0.0)
    assert m.knockdown == pytest.approx(FDM_KNOCKDOWN)  # no extra 0.6: the Z check covers it
    with pytest.raises(SimError, match="print_direction"):
        studies.validate_settings("static", {"load_cases": [CASE], "material": {
            "anisotropy": {"print_direction": "sideways"}}})


@pytest.mark.parametrize("d", [(0, 0, 1), (1, 0, 0), (0, 1, 0), (1, 1, 1), (0, 0, -1)])
def test_local_axes_are_right_handed_with_3_along_the_build_direction(d):
    e1, e2, e3 = local_axes(d)
    assert np.allclose(np.cross(e1, e2), e3) and np.allclose(e3, np.array(d) / np.linalg.norm(d))
    assert abs(np.dot(e1, e2)) < 1e-12


def _mesh():
    return Mesh(nodes={1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (0, 0, 1)},
                tets=[[1, 2, 3, 4]], surfaces={}, order=1)


def test_deck_writes_engineering_constants_and_an_orientation():
    m = with_anisotropy(get_material("PLA"), (1, 0, 0), e_z_ratio=0.5)
    cards = model_cards(_mesh(), m)
    i = cards.index("*ELASTIC, TYPE=ENGINEERING CONSTANTS")
    e1, e2, e3, n12, n13, n23, g12, g13 = map(float, cards[i + 1].split(","))
    assert (e1, e2, e3) == pytest.approx((2300, 2300, 1150))
    assert (n12, n13, n23) == pytest.approx((0.35, 0.35, 0.35))
    assert g12 == pytest.approx(2300 / 2.7) and g13 == pytest.approx(1150 / 2.7)
    assert float(cards[i + 2]) == pytest.approx(1150 / 2.7)  # G23 on the second line
    o = cards.index("*ORIENTATION, NAME=ORFDM, SYSTEM=RECTANGULAR")
    a_b = list(map(float, cards[o + 1].split(",")))
    assert np.allclose(np.cross(a_b[:3], a_b[3:]), [1, 0, 0])  # local 3 = the build direction
    assert cards[o + 2] == "*MATERIAL, NAME=MAT" and o < i
    assert cards[-1] == "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT, ORIENTATION=ORFDM"
    assert "*DENSITY" in cards and "*EXPANSION" in cards
    # isotropic decks are unchanged
    iso = model_cards(_mesh(), get_material("PLA"))
    assert "*ELASTIC" in iso and iso[-1] == "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"


def test_orthotropic_cards_keep_other_material_cards():
    m = with_anisotropy(get_material("PLA"), (0, 0, 1))
    out = orthotropic_cards(["*MATERIAL, NAME=MAT", "*ELASTIC", "2300,0.35", "*PLASTIC",
                             "40,0", "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT"], m)
    assert "2300,0.35" not in out and out[out.index("*PLASTIC") + 1] == "40,0"


def test_layer_normal_stress_is_d_sigma_d():
    S = np.array([[10.0, 20.0, 30.0, 1.0, 2.0, 3.0]])
    assert layer_normal_stress(S, (0, 0, 1))[0] == pytest.approx(30.0)
    assert layer_normal_stress(S, (1, 0, 0))[0] == pytest.approx(10.0)
    d = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)
    sig = np.array([[10, 1, 3], [1, 20, 2], [3, 2, 30]])
    assert layer_normal_stress(S, (1, 1, 0))[0] == pytest.approx(d @ sig @ d)


# ---- fatigue

def test_fatigue_hand_calculation_pla_zero_to_max():
    """PLA, S_u 51 MPa, uniaxial 20 MPa peak, 0 -> F, 1e5 cycles required.
    sigma_a = sigma_m = 10; Goodman sigma_ar = 10 / (1 - 10/51) = 12.439 MPa;
    curve: sigma_a = 0.1 S_u (N / 2e6)^(-1/5.5) -> S(1e5) = 5.1 x 20^(1/5.5) = 8.794 MPa;
    fatigue SF = 8.794 / 12.439 = 0.707; N_f = 2e6 (12.439 / 5.1)^(-5.5) = 1.48e4."""
    m = get_material("PLA")
    st = FatigueSettings(cycles=1e5, cycle="zero_to_max")
    f = fatigue.evaluate([1], np.array([[20.0, 0, 0, 0, 0, 0]]), {1: (1, 2, 3)}, m, st)
    sar = 10 / (1 - 10 / 51)
    assert f["equivalent_amplitude_mpa"] == pytest.approx(sar)
    assert f["equivalent_amplitude_mpa"] == pytest.approx(12.439, abs=1e-3)
    assert f["allowable_amplitude_mpa"] == pytest.approx(5.1 * 20 ** (1 / 5.5))
    assert f["safety_factor"] == pytest.approx(5.1 * 20 ** (1 / 5.5) / sar)
    assert f["safety_factor"] == pytest.approx(0.707, abs=1e-3)
    assert f["cycles_to_failure"] == pytest.approx(2e6 * (sar / 5.1) ** -5.5, rel=1e-9)
    assert f["cycles_to_failure"] == pytest.approx(1.48e4, rel=0.01)
    assert f["label"] == "fatigue estimate" and f["sn_curve"]["basis"] == "estimate"
    assert f["hotspot_at"] == [1, 2, 3] and fatigue.warnings_for(f)


def test_fully_reversed_has_no_mean_and_compression_gets_no_goodman_credit():
    m = get_material("PLA")
    fr = FatigueSettings(cycles=2e6, cycle="fully_reversed")
    f = fatigue.evaluate([1], np.array([[5.1, 0, 0, 0, 0, 0]]), {1: (0, 0, 0)}, m, fr)
    assert f["mean_stress_mpa"] == 0 and f["safety_factor"] == pytest.approx(1.0)
    zm = FatigueSettings(cycles=2e6, cycle="zero_to_max")
    c = fatigue.evaluate([1], np.array([[-10.2, 0, 0, 0, 0, 0]]), {1: (0, 0, 0)}, m, zm)
    assert c["mean_stress_mpa"] == pytest.approx(-5.1)
    assert c["equivalent_amplitude_mpa"] == pytest.approx(5.1)  # no credit for compression
    mm = FatigueSettings(cycles=1e3, cycle="min_max", min_pct=50)
    assert mm.load_ratio() == 0.5


def test_datasheet_curve_and_hotspot_skip_the_supports():
    m = get_material("PLA")
    st = FatigueSettings(cycles=1e6, cycle="fully_reversed", sn={"a_mpa": 100, "b": -0.1,
                                                                 "source": "TDS"})
    S = np.array([[30.0, 0, 0, 0, 0, 0], [10.0, 0, 0, 0, 0, 0]])
    f = fatigue.evaluate([1, 2], S, {1: (0, 0, 0), 2: (5, 0, 0)}, m, st, exclude={1})
    assert f["hotspot_at"] == [5, 0, 0] and f["sn_curve"]["basis"] == "datasheet"
    assert f["safety_factor"] == pytest.approx(100 * 1e6 ** -0.1 / 10)
    assert f["cycles_to_failure"] == pytest.approx((10 / 100) ** (1 / -0.1))


def test_orthotropic_fatigue_checks_tension_across_layers():
    m = with_anisotropy(get_material("PLA"), (0, 0, 1), strength_z_ratio=0.5)
    st = FatigueSettings(cycles=1e5, cycle="fully_reversed")
    S = np.array([[0, 0, 8.0, 0, 0, 0]])
    f = fatigue.evaluate([1], S, {1: (0, 0, 0)}, m, st, layer_normal=np.array([8.0]))
    assert f["governing"] == "across layers (Z)"
    iso = fatigue.evaluate([1], S, {1: (0, 0, 0)}, get_material("PLA"), st)
    assert f["safety_factor"] == pytest.approx(0.5 * iso["safety_factor"])


def test_fatigue_settings_validate_and_card_shows_the_estimate():
    s = studies.validate_settings("static", {"load_cases": [CASE], "fatigue": {
        "cycles": 1e6, "cycle": "min_max", "min_pct": -50}})
    assert s.fatigue.load_ratio() == -0.5
    with pytest.raises(SimError, match="cycles"):
        studies.validate_settings("static", {"load_cases": [CASE], "fatigue": {"cycles": 0}})
    rec = {"type": "static", "status": "done", "sim_id": "sim_0123456789ab",
           "material": {"name": "PLA", "basis": BASIS_ESTIMATE},
           "results": {"safety_factor": 3.0, "fatigue_safety_factor": 0.8,
                       "cycles_to_failure": 1e4,
                       "fatigue": {"sn_curve": {"basis": "estimate"}}}}
    c = studies.card(rec)
    keys = [x["key"] for x in c["metrics"]]
    assert "fatigue_safety_factor" in keys and "layer_safety_factor" not in keys
    assert c["material_basis"] == BASIS_ESTIMATE and "estimate" in c["fatigue_note"]
    chk = studies.check_requirements(rec, [{"id": "f", "kind": "min_fatigue_safety_factor",
                                            "target": 0.5}])
    assert chk[0]["status"] == "warn" and "estimate" in chk[0]["note"]
    chk = studies.check_requirements(rec, [{"id": "f", "kind": "min_fatigue_safety_factor",
                                            "target": 1.0}])
    assert chk[0]["status"] == "fail"
    assert "fatigue" in " ".join(studies.s3_caveats(None, s)).lower()
