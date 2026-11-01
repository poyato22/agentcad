"""E26 (Plan 10 C3): a concept sketch whose massing volumes clip through each other is refused
with the clashes; separate (or fused) volumes render as before."""
import pytest

from mises.concept import sketch as sk

from .test_concept import _gate_rt, home  # noqa: F401 - the fixture

pytestmark = pytest.mark.slow

CLASHING = '''VARS = {}
def build(v):
    torso = Box(300, 200, 600)
    arm = Pos(160, 0, 100) * Box(80, 80, 500)          # sunk 30 mm into the torso
    return Compound([torso, arm])
'''

APART = '''VARS = {}
def build(v):
    torso = Box(300, 200, 600)
    arm = Pos(200, 0, 100) * Box(80, 80, 500)          # 10 mm clear of the torso
    return Compound([torso, arm])
'''


@pytest.mark.timeout(240)
async def test_clashing_massing_is_refused_with_the_clashes(home):  # noqa: F811
    rt, project, run = _gate_rt(home, "design")
    out = await rt.gate.call(run.id, "concept_sketch", {"massing_script": CLASHING},
                             call_id="s1", workdir=rt.home)
    assert out.ok is False and out.data["error"] == "massing_clash"
    c = out.data["clashes"][0]
    # common volume: 30 x 80 x 450 mm (34% of the arm)
    assert c["overlap_mm3"] == pytest.approx(30 * 80 * 450, rel=1e-3)
    assert c["overlap_pct_of_smaller"] == pytest.approx(33.75, abs=0.1)
    assert "clash" in out.summary
    assert rt.concepts.latest_sketch(project) is None            # nothing was shown
    ok = await rt.gate.call(run.id, "concept_sketch", {"massing_script": APART},
                            call_id="s2", workdir=rt.home)
    assert ok.ok, ok.summary
    assert ok.data["clash_check"]["checked"] is True
    assert "Look at the returned sketch images" in ok.data["review"]


@pytest.mark.timeout(240)
async def test_tolerance_lets_a_small_overlap_through(tmp_path):
    from mises.concept.sketch import build_massing
    d = tmp_path / "m"
    await build_massing(d, CLASHING)
    strict = await sk.massing_clashes(d, 2.0)
    loose = await sk.massing_clashes(d, 25.0)
    assert strict["clashes"] and strict["checked"]
    # 34% of the arm overlaps: a clash at 25%, through at 40%
    assert loose["clashes"]
    assert (await sk.massing_clashes(d, 40.0))["clashes"] == []
