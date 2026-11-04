"""Bench fix: a part that came out as several disconnected solids is flagged in the build
summary the agent reads (CADTestBench 00996962: a groove cut split a plate in two and the
build was accepted silently)."""
from mises.cad.tools import _summary


def _m(solids):
    return {"bbox": {"size": [10, 20, 30]}, "volume_mm3": 6000.0, "mass_g": 7.0, "holes": [],
            "valid": True, "solids": solids}


def test_one_solid_has_no_warning():
    assert "solid" not in _summary("p", _m(1)).lower()


def test_several_solids_are_flagged():
    s = _summary("p", _m(2))
    assert "2 separate solids" in s and "one connected solid" in s


def test_no_solid_at_all_is_flagged():
    # CADBench F-23374: the delivered part was a shell with 0 solids and passed silently
    s = _summary("p", _m(0))
    assert "no closed solid" in s


def test_old_metrics_without_a_solid_count_still_summarise():
    m = _m(1)
    del m["solids"]
    assert _summary("p", m).startswith("p: volume")
