"""Barrowman CP and the drag build-up against published and OpenRocket answers (Plan 11 F6)."""
import math

import pytest

from mises.rocketry import aero
from mises.rocketry.aero import Body, Fins, Nose, Section

from . import reference as ref


def test_barrowman_matches_culps_miniemagg_worked_example():
    """Culp's published Barrowman spreadsheet for the Mini Mag (inches), to 1e-9."""
    e, a = ref.MINIEMAGG, ref.MINIEMAGG_ANSWER
    body = Body(Nose("ogive", e["ln"], e["d"]), [Section(e["ln"], 30.0, e["d"], e["d"])],
                [Fins(e["n"], e["cr"], e["ct"], e["s"], e["xr"], 0.1, e["xb"], 2 * e["r"],
                      midchord=e["lf"])], e["d"])
    cp, cn, terms = aero.barrowman(body)
    nose, fins = terms
    assert nose.cn == pytest.approx(a["cn_nose"]) and nose.x == pytest.approx(a["x_nose"])
    assert fins.cn == pytest.approx(a["cn_fins"], rel=1e-9)
    assert fins.x == pytest.approx(a["x_fins"], rel=1e-9)
    assert cn == pytest.approx(a["cn"], rel=1e-9)
    assert cp == pytest.approx(a["x"], rel=1e-9)


def test_midchord_line_is_computed_from_the_planform():
    f = Fins(3, 10.0, 0.0, 5.25, 9.0, 0.1, 27.0, 5.54)
    # mid-chord line from root mid (5) to tip mid (9): sqrt(5.25^2 + 4^2)
    assert f.lf == pytest.approx(math.hypot(5.25, 4.0))


@pytest.mark.parametrize("which", ["A", "B"])
def test_cp_matches_openrocket(which):
    cp, cn, _ = aero.barrowman(ref.body(which))
    o = ref.OR[which]
    assert cp == pytest.approx(o["cp"], rel=ref.TOL_CP)
    assert cn == pytest.approx(o["cn_alpha"], rel=0.02)


def test_conical_transition_terms():
    """Barrowman's conical transition: CN = 2[(d2/d)^2 - (d1/d)^2], negative for a boattail,
    its CP inside the transition."""
    _cp, _cn, terms = aero.barrowman(ref.body("B"))
    bt = next(t for t in terms if t.name == "boattail")
    assert bt.cn == pytest.approx(2 * ((0.040 / 0.066) ** 2 - 1))
    r = 0.066 / 0.040
    assert bt.x == pytest.approx(0.9 + 0.034 / 3 * (1 + (1 - r) / (1 - r * r)))
    assert 0.9 < bt.x < 0.934


@pytest.mark.parametrize("which", ["A", "B"])
@pytest.mark.parametrize("mach", [0.1, 0.2, 0.3, 0.5])
def test_drag_buildup_matches_openrocket(which, mach):
    air = aero.isa(0)
    d = aero.drag(ref.body(which), mach * air.a, air, roughness_m=60e-6)
    assert d.cd == pytest.approx(ref.OR[which]["cd"][mach], rel=ref.TOL_CD)
    assert d.friction > 0 and d.base > 0 and d.pressure > 0


def test_isa_sea_level():
    a = aero.isa(0)
    assert a.rho == pytest.approx(1.225, rel=1e-3)
    assert a.a == pytest.approx(340.3, rel=1e-3)
    assert aero.isa(1000).p == pytest.approx(89875, rel=2e-3)


def test_rough_finish_adds_drag():
    air = aero.isa(0)
    smooth = aero.drag(ref.body(), 60, air, roughness_m=20e-6).cd
    rough = aero.drag(ref.body(), 60, air, roughness_m=250e-6).cd
    assert rough > smooth
