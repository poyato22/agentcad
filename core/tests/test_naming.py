import pytest
from mises.naming import check_name, display_name
from mises.types import MisesError, ErrorCode


@pytest.mark.parametrize("n", ["Gusset1", "Extrude 1", "var2", ""])
def test_check_name_rejects(n):
    with pytest.raises(MisesError) as e:
        check_name(n)
    assert e.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("n", ["gusset_upright", "plate_thickness", "M4_hole"])
def test_check_name(n):
    check_name(n)


def test_display_name():
    d = display_name("Extrude", "Extrude 1", {"depth": "5 mm"})
    assert d.startswith("Extrude · ") and "Extrude 1" not in d
    assert display_name("Extrude", "Extrude 1") == "Extrude · unnamed"
    assert display_name("Gusset", "Gusset · upright") == "Gusset · upright"
