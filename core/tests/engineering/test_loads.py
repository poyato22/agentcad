import pytest

from mises.engineering.loads import DEFAULT_IMPACT_K, GRAVITY, impact_equivalent


def test_impact_formula_and_estimate_flag():
    r = impact_equivalent(0.5, 1.5, 8.0)
    assert r["estimate"] is True
    assert "m·g·k" in r["formula"]
    assert r["newtons"] == pytest.approx(0.5 * GRAVITY * 8.0, abs=1e-3)
    assert r["k"] == 8.0 and r["drop_m"] == 1.5 and r["mass_kg"] == 0.5
    assert "estimate" in r["note"].lower()


def test_impact_default_k():
    r = impact_equivalent(1.0, 2.0)
    assert r["k"] == DEFAULT_IMPACT_K


def test_impact_rejects_bad_inputs():
    with pytest.raises(ValueError):
        impact_equivalent(0, 1, 1)
    with pytest.raises(ValueError):
        impact_equivalent(1, -1, 1)
    with pytest.raises(ValueError):
        impact_equivalent(1, 1, 0)
