"""NACA TN-4197 flutter and the RASP motor library (Plan 11 F6)."""
import json
import math

import pytest

from mises.rocketry import flutter, motors
from mises.types import MisesError


def test_flutter_constant_is_tn4197_eq16_17():
    """39.3 / 14.7 / 2 of eq. 18 is (24 eps / pi) gamma / 2 with eps 0.25, gamma 1.4."""
    assert flutter.FLUTTER_K == pytest.approx(24 * 0.25 / math.pi * 1.4 / 2)
    assert 39.3 / 14.7 / 2 == pytest.approx(flutter.FLUTTER_K, rel=2e-3)


def test_flutter_speed_hand_calculation():
    """A 3.2 mm PLA fin, root 120, tip 50, span 65 at sea level, by hand from eq. 18."""
    g = 2300e6 / (2 * 1.35)
    root, tip, span, t = 0.12, 0.05, 0.065, 0.0032
    area = (root + tip) / 2 * span
    ar = span ** 2 / area
    lam = tip / root
    eq18 = g / (39.3 * ar ** 3 / ((t / root) ** 3 * (ar + 2)) * (lam + 1) / 2 * (101325 / 101325))
    by_hand = 340.3 * math.sqrt(eq18 / 6894.76)        # eq. 18 has G in psi, p0 = 14.7 psi
    vf = flutter.flutter_speed(g, root=root, tip=tip, span=span, thickness=t, a=340.3,
                               p=101325)
    assert vf == pytest.approx(by_hand, rel=3e-3)
    assert 150 < vf < 300


def test_flutter_is_unit_free_and_monotonic():
    kw = dict(root=120, tip=50, span=65, a=340.0)
    v1 = flutter.flutter_speed(800e6, thickness=3.2, p=101325, **kw)
    v2 = flutter.flutter_speed(800e6 / 6894.76, thickness=3.2, p=101325 / 6894.76, **kw)
    assert v1 == pytest.approx(v2)
    assert flutter.flutter_speed(800e6, thickness=4.0, p=101325, **kw) > v1     # thicker
    assert flutter.flutter_speed(800e6, thickness=3.2, p=80000, **kw) > v1      # thinner air


def test_shear_modulus_from_the_materials_table():
    g, note = flutter.shear_modulus_pa("PLA")
    assert g == pytest.approx(2300e6 / 2.7)
    assert "PLA" in note
    g_petg, _ = flutter.shear_modulus_pa("PETG")
    assert g_petg < g


# ---- motors

# ThrustCurve's listed total impulse per bundled simfile (Ns)
LISTED = {"F44W": 41.5, "F52C": 66.2, "F67C": 77.46, "G25W-10A": 111.7,
          "G40W": 97.14}


def test_bundled_curves_parse_and_match_listed_impulse():
    lib = {m.designation: m for m in motors.bundled()}
    assert set(lib) == set(LISTED)
    for name, imp in LISTED.items():
        m = lib[name]
        assert m.total_impulse_ns == pytest.approx(imp, rel=0.03), name
        assert m.license == "PD" and m.url and m.url.startswith("https://www.thrustcurve.org/")
        assert m.prop_kg < m.total_kg and m.burn_time_s > 0


def test_bundled_files_are_all_public_domain_and_sourced():
    src = json.loads((motors.BUNDLED_DIR / "sources.json").read_text())["files"]
    files = {p.name for p in motors.BUNDLED_DIR.glob("*.eng")}
    assert files == set(src)
    assert all(v["license"] == "PD" for v in src.values())


def test_find_by_common_name_and_designation():
    assert motors.find("F52C", None).designation == "F52C"
    assert motors.find("AeroTech F52", None).designation == "F52C"
    assert motors.find("f52-8c", None).designation == "F52C"
    assert motors.find("G40", None).designation == "G40W"
    assert motors.find("G25", None).designation == "G25W-10A"
    assert motors.find("F39", None) is None
    assert motors.parse_common("F39-6T") == "F39"


def test_mass_burns_with_impulse():
    m = motors.find("G40W", None)
    assert m.mass_kg(0) == pytest.approx(m.total_kg)
    assert m.mass_kg(m.burn_time_s + 1) == pytest.approx(m.total_kg - m.prop_kg)
    half = m.mass_kg(m.burn_time_s / 2)
    assert m.total_kg - m.prop_kg < half < m.total_kg


ENG = """; a test motor
X10T 29 100 4-8 0.020 0.060 Test
   0.1 10
   0.5 12
   1.0 10
   1.1 0
"""


def test_parse_save_and_find_cached(tmp_path):
    m = motors.parse_eng(ENG, source="test")
    assert m.delays == (4.0, 8.0) and m.diameter_mm == 29
    assert m.total_impulse_ns == pytest.approx(0.5 + 4.4 + 5.5 + 0.5, rel=1e-6)
    motors.save(tmp_path, m, source="test")
    got = motors.find("X10", tmp_path)
    assert got is not None and got.total_impulse_ns == pytest.approx(m.total_impulse_ns)
    assert motors.resolve("X10T", None, tmp_path).designation == "X10T"


@pytest.mark.parametrize("bad", ["", "nothing here", "X 29 100 P 0.1 0.05 T\n 0.1 5\n 1 0",
                                 "X 29 100 P a b T\n 0.1 5\n 1 0",
                                 "X 29 100 P 0.01 0.05 T\n 0.5 5\n 0.2 3\n 1 0"])
def test_bad_eng_is_refused(bad):
    with pytest.raises(MisesError):
        motors.parse_eng(bad)


def test_unknown_motor_names_how_to_get_it(tmp_path):
    with pytest.raises(MisesError) as e:
        motors.resolve("F39T", None, tmp_path)
    assert "rocket_motor" in e.value.hint and "thrustcurve" in e.value.hint
