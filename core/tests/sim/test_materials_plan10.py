"""Plan 10 E32: engineering materials beyond PLA, with their process and knock-down, one table
for mass, studies and the agent's per-part pick."""
import pytest

from mises import mass
from mises.sim.materials import (FDM_KNOCKDOWN, MATERIALS, PROCESS_KNOCKDOWN, Material,
                                 get_material, rank_materials, with_anisotropy, with_fdm)
from mises.sim.types import SimError

PLAN10 = ["PLA", "PETG", "ABS", "ASA", "PC", "PA12", "PA6-CF", "PA-CF", "TPU 95A", "POM",
          "Al 6061-T6", "Al 7075-T6", "Steel 1018", "Steel 4140", "Steel 4140 QT",
          "304 stainless", "Brass C360", "CF plate"]


def test_at_least_fifteen_materials_including_metals():
    assert set(PLAN10) <= set(MATERIALS)
    assert len(MATERIALS) >= 15
    assert sum(m.process == "machined" for m in MATERIALS.values()) >= 6


@pytest.mark.parametrize("name,e,yield_,rho", [
    ("Al 6061-T6", 68900, 276, 2.70), ("Al 7075-T6", 71700, 503, 2.81),
    ("Steel 1018", 205000, 370, 7.87), ("304 stainless", 193000, 215, 8.00),
    ("Brass C360", 97000, 310, 8.50), ("POM", 3100, 72, 1.42), ("PA12", 1800, 48, 1.01)])
def test_property_table_values(name, e, yield_, rho):
    m = MATERIALS[name]
    assert (m.e_mpa, m.yield_mpa, m.density_g_cm3) == (e, yield_, rho)
    assert m.uts_mpa is not None and m.uts_mpa >= m.yield_mpa
    assert m.source


def test_knockdown_follows_the_process():
    assert MATERIALS["PLA"].knockdown == FDM_KNOCKDOWN
    assert MATERIALS["PA12"].knockdown == PROCESS_KNOCKDOWN["sls_mjf"] == 0.85
    for n in ("Al 6061-T6", "Steel 4140 QT", "CF plate", "POM"):
        assert MATERIALS[n].knockdown == 1.0
        assert MATERIALS[n].allowable_mpa == MATERIALS[n].yield_mpa
    assert MATERIALS["Al 6061-T6"].min_safety_factor == 1.5
    assert MATERIALS["PETG"].min_safety_factor == 2.0
    cf = MATERIALS["CF plate"]
    assert cf.failure == "brittle" and cf.min_safety_factor == 3.0
    assert "brittle" in cf.to_dict()["model"]


def test_fdm_settings_refuse_stock_materials():
    with pytest.raises(SimError, match="FDM"):
        with_fdm(MATERIALS["Al 6061-T6"], 40, 3, "xy")
    with pytest.raises(SimError, match="FDM"):
        with_anisotropy(MATERIALS["Steel 1018"], (0, 0, 1))
    assert with_fdm(MATERIALS["PETG"], 40, 3, "xy").knockdown < FDM_KNOCKDOWN


def test_pa6_cf_is_orthotropic_with_data_sheet_z():
    m = with_anisotropy(MATERIALS["PA6-CF"], (0, 0, 1))
    assert m.e_z_mpa == 2170.0 and m.z_strength_mpa == 48.0


def test_tpu_carries_a_hyperelastic_model():
    m = MATERIALS["TPU 95A"]
    assert m.hyperelastic["model"] == "neo_hooke" and m.elongation_pct == 580


def test_bad_process_is_refused():
    with pytest.raises(SimError):
        Material("X", 1, 0.3, 1, 1, 1, "s", process="forged")


def test_mass_reads_the_same_table():
    assert mass.density_g_cm3("Al 6061-T6") == 2.70
    assert mass.canonical_material("steel 1018") == "Steel 1018"
    assert "Brass C360" in mass.known_materials()
    assert get_material("cf plate").name == "CF plate"


def test_rank_materials_for_a_hot_stiff_printed_bracket():
    rows = rank_materials({"priority": "stiffness", "printable": True, "max_temp_c": 90})
    ok = [r for r in rows if not r["rejected"]]
    assert ok and ok[0]["name"] in ("PA6-CF", "PA-CF")
    assert all(r["printable"] for r in ok)


def test_rank_materials_for_a_shaft_and_for_an_rf_window():
    rows = rank_materials({"priority": "strength", "printable": False, "min_yield_mpa": 300,
                           "form": "shaft"},
                          limit=20)
    ok = [r["name"] for r in rows if not r["rejected"]]
    assert ok[0] in ("Al 7075-T6", "Steel 4140 QT")
    assert "PLA" not in ok
    rf = rank_materials({"conductive": False, "printable": True}, limit=30)
    bad = {r["name"] for r in rf if r["rejected"]}
    assert {"Al 6061-T6", "CF plate"} <= bad
