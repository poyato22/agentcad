"""FWC I4 (E32): BOM cost reads the materials table's cost_per_kg for every material (metals and
engineering plastics too), and "TPU" names the table's TPU 95A everywhere."""
import json

import pytest

from mises import mass
from mises.bom import made_estimate, material_price
from mises.sim.materials import MATERIALS, get_material


def _part(home, pid, volume_mm3=10_000.0):
    d = home / "parts" / pid
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": volume_mm3}))


def test_every_table_material_has_a_price():
    missing = [k for k, m in MATERIALS.items() if m.cost_per_kg is None]
    assert missing == []


@pytest.mark.parametrize("name", ["Al 6061-T6", "Steel 1018", "Brass C360", "PC", "POM",
                                  "CF plate", "PA12", "PLA"])
def test_made_estimate_prices_any_table_material(tmp_path, name):
    _part(tmp_path, "p")
    out = made_estimate(tmp_path, "p", name)
    m = get_material(name)
    assert out["unit_cost"] == pytest.approx(round(10.0 * m.density_g_cm3 / 1000 * m.cost_per_kg,
                                                   2))
    assert "no cost estimate" not in out["note"]


def test_the_table_price_wins_over_the_slicer_for_shared_names():
    m = get_material("PETG")
    assert material_price("PETG") == (m.density_g_cm3, m.cost_per_kg)


def test_unknown_material_still_has_no_price():
    assert material_price("unobtainium") is None


def test_tpu_is_an_alias_of_tpu_95a():
    assert get_material("TPU").name == "TPU 95A"
    assert get_material("tpu").name == "TPU 95A"
    assert mass.canonical_material("TPU") == "TPU 95A"
    assert mass.density_g_cm3("TPU") == pytest.approx(1.22)
    assert material_price("TPU") == (1.22, 35.0)
