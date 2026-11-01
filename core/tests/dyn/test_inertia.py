"""B-rep mass properties from the build worker (Plan 10 C3 shared contract: part metrics gain
inertia {mass_kg, com_mm, inertia_kgm2}) and the host-side helpers that combine them."""
import numpy as np
import pytest

from mises.cad.runner import run_build
from mises.dyn import inertia

pytestmark = pytest.mark.slow

# a 40 x 20 x 10 box off the origin (centre at 20, 10, 5)
BOX = ('from build123d import *\nVARS={}\ndef build(v):\n'
       '    return Box(40,20,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')


@pytest.mark.timeout(180)
async def test_box_inertia_matches_analytic(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(BOX)
    r = await run_build(d, {})
    assert r.ok, r.error
    i = r.metrics["inertia"]
    m = 8000 * 1.24e-6          # PLA, kg
    assert i["mass_kg"] == pytest.approx(m, rel=1e-9)
    assert i["com_mm"] == pytest.approx([20, 10, 5], abs=1e-6)
    a, b, c = 0.040, 0.020, 0.010
    want = np.diag([m * (b * b + c * c) / 12, m * (a * a + c * c) / 12, m * (a * a + b * b) / 12])
    assert np.allclose(i["inertia_kgm2"], want, rtol=1e-6, atol=1e-15)


def test_scaling_to_the_part_material_and_box_fallback():
    i = {"mass_kg": 0.01, "com_mm": [1, 2, 3], "inertia_kgm2": np.eye(3).tolist()}
    s = inertia.of_part({"inertia": i}, mass_kg=0.02)
    assert s.mass == pytest.approx(0.02) and s.inertia[0][0] == pytest.approx(2.0)
    assert s.com == pytest.approx([0.001, 0.002, 0.003])
    assert s.source == "brep"
    box = inertia.of_part({"bbox": {"min": [0, 0, 0], "max": [40, 20, 10],
                                    "size": [40, 20, 10]}}, mass_kg=0.1)
    assert box.source == "box" and box.com == pytest.approx([0.02, 0.01, 0.005])
    assert box.inertia[2][2] == pytest.approx(0.1 * (0.04 ** 2 + 0.02 ** 2) / 12)


def test_combine_two_point_like_bodies_uses_the_parallel_axis_theorem():
    small = np.zeros((3, 3)).tolist()
    a = inertia.MassProps(1.0, [0.0, 0.0, 0.0], small, "brep")
    b = inertia.MassProps(1.0, [0.2, 0.0, 0.0], small, "brep")
    c = inertia.combine([a, b])
    assert c.mass == 2.0 and c.com == pytest.approx([0.1, 0, 0])
    assert c.inertia[1][1] == pytest.approx(2 * 1.0 * 0.1 ** 2)
    assert c.inertia[0][0] == pytest.approx(0.0)


def test_transform_rotates_the_tensor():
    i = inertia.MassProps(1.0, [0.01, 0, 0], np.diag([1.0, 2.0, 3.0]).tolist(), "brep")
    rz90 = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    w = inertia.to_world(i, rz90)
    assert w.com == pytest.approx([0, 0.01, 0])
    assert np.allclose(w.inertia, np.diag([2.0, 1.0, 3.0]))
