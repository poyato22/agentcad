"""Plan 6 integration (int-678): S1's nonlinear deck and settings meet S3's orthotropic material,
calibration and caveats in the merged core. These check the seams each branch could only assume."""
import pytest

from mises.sim import calibration as cal
from mises.sim import nonlinear as nlm
from mises.sim import studies
from mises.sim.deck import model_cards
from mises.sim.materials import MATERIALS, with_anisotropy
from mises.sim.mesh import Mesh
from mises.sim.settings import NonlinearSettings, StaticSettings
from mises.sim.types import SimError

PLA = MATERIALS["PLA"]
CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}
MESH = Mesh(nodes={1: (0, 0, 0)}, tets=[], surfaces=[])


def test_s1_plastic_card_and_s3_orthotropic_elastic_share_one_material_block():
    m = with_anisotropy(PLA, (1, 0, 0))
    deck = model_cards(MESH, m, nlm.plastic_cards(m))
    i = deck.index("*MATERIAL, NAME=MAT")
    assert deck[i - 2].startswith("*ORIENTATION")            # S3: the print's frame first
    assert deck[i + 1] == "*ELASTIC, TYPE=ENGINEERING CONSTANTS"
    assert deck[i + 4] == "*PLASTIC"                          # S1: bilinear, after *ELASTIC
    assert any(x.startswith("*SOLID SECTION") and "ORIENTATION=" in x for x in deck)
    # the isotropic E, nu line is gone; S1's two plastic points stay
    assert f"{m.e_eff_mpa:.9g},{m.nu:.9g}" not in deck
    assert deck[i + 5:i + 7] == nlm.plastic_cards(m)[1:]


def test_s3_refuses_more_than_bilinear_plasticity_with_orthotropy_in_the_real_deck():
    m = with_anisotropy(PLA, (0, 0, 1))
    three = nlm.plastic_cards(m) + ["60,2"]
    with pytest.raises(SimError, match="bilinear"):
        model_cards(MESH, m, three)


def test_s3_sees_s1_plasticity_by_its_real_key():
    s = StaticSettings.model_validate({"load_cases": [CASE],
                                       "nonlinear": {"plasticity": True}})
    assert isinstance(s.nonlinear, NonlinearSettings) and "plasticity" in type(s.nonlinear).model_fields
    m = with_anisotropy(PLA, (0, 0, 1))
    assert studies._plastic(s.nonlinear)
    assert studies.CAVEAT_ORTHO_PLASTIC in studies.s3_caveats(m, s)
    ld = StaticSettings.model_validate({"load_cases": [CASE],
                                        "nonlinear": {"large_deflection": True}})
    assert not studies._plastic(ld.nonlinear)
    assert studies.CAVEAT_ORTHO_PLASTIC not in studies.s3_caveats(m, ld)


def test_one_orthotropic_caveat_when_s1_swaps_and_s3_adds():
    m = with_anisotropy(PLA, (0, 0, 1))
    s = StaticSettings.model_validate({"load_cases": [CASE], "nonlinear": {"plasticity": True}})
    cav = studies._dedupe(studies.material_caveats(studies.CAVEATS_NONLINEAR, m)
                          + studies.s3_caveats(m, s))
    assert cav.count(studies.CAVEAT_ORTHOTROPIC) == 1
    assert not any(c.startswith("Isotropic material model") for c in cav)


def test_part_load_test_refuses_a_real_s1_record():
    rec = {"type": "static", "status": "done", "fidelity_class": "fem_nonlinear",
           "settings": StaticSettings.model_validate(
               {"load_cases": [CASE], "nonlinear": {"rigid_supports": [{"face": "bottom"}]}}
           ).model_dump(mode="json")}
    assert cal._nonlinear(rec)
    # a linear study's dumped settings (nonlinear null) is not
    lin = {**rec, "fidelity_class": "fem", "settings": StaticSettings.model_validate(
        {"load_cases": [CASE]}).model_dump(mode="json")}
    assert not cal._nonlinear(lin)


def test_nonlinear_card_keeps_s3_metrics_and_s1_metrics():
    rec = {"type": "static", "status": "done", "fidelity_class": "fem_nonlinear",
           "warnings": [], "results": {"max_von_mises_mpa": 10, "safety_factor": 2,
                                       "max_displacement_mm": 1, "error_band_pct": 3,
                                       "load_fraction": 1.0, "max_plastic_strain": 0.01,
                                       "max_contact_pressure_mpa": None,
                                       "layer_safety_factor": 1.5, "fatigue_safety_factor": 2.0,
                                       "model_error_band": 0.1,
                                       "nonlinear": {"description": "plasticity"}}}
    keys = [m["key"] for m in studies.card(rec)["metrics"]]
    assert {"load_fraction", "max_plastic_strain", "layer_safety_factor",
            "fatigue_safety_factor"} <= set(keys)
    assert "max_contact_pressure_mpa" not in keys
