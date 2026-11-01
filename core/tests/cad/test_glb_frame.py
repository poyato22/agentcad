"""P11 F4: a part whose final shape carries a location (the rocket's coupler, `Pos(0, 0, z0) *
tube`) was written to model.glb with its translation in the wrong frame, so the app drew it
z0 mm off the rocket's axis (the user's click at (31.5, -362.7, 59.2) on a coupler the core had
at z 360-480). model.glb now draws where the core has the part, and an old file is fixed when
served."""
from pathlib import Path

import pytest

from mises.cad.glb_frame import GLB_FRAME, cad_bbox, fix_stale_glb
from mises.cad.runner import run_build

pytestmark = pytest.mark.slow

COUPLER = '''from build123d import *
VARS = {}
def build(v):
    A = (Align.CENTER, Align.CENTER, Align.MIN)
    return Pos(0, 0, 360) * (Cylinder(31.6, 120, align=A) - Cylinder(30.0, 120, align=A))
'''
TILTED = '''from build123d import *
VARS = {}
def build(v):
    return Location((10, -20, 50), (0, 1, 0), 30) * Box(40, 20, 10)
'''


def _close(box, metrics_bbox, tol=0.05):
    lo, hi = box
    assert lo == pytest.approx(metrics_bbox["min"], abs=tol)
    assert hi == pytest.approx(metrics_bbox["max"], abs=tol)


@pytest.mark.parametrize("script", [COUPLER, TILTED])
async def test_model_glb_draws_where_the_core_has_the_part(tmp_path, script):
    d = tmp_path / "p"
    d.mkdir()
    (d / "part.py").write_text(script)
    r = await run_build(d, {})
    assert r.ok, r.error
    assert r.metrics["glb_frame"] == GLB_FRAME
    _close(cad_bbox((d / "model.glb").read_bytes()), r.metrics["bbox"], tol=0.5)


def test_an_old_glb_is_fixed_to_the_cad_frame(tmp_path):
    import build123d as b3
    from build123d import Align, Cylinder, Pos
    A = (Align.CENTER, Align.CENTER, Align.MIN)
    shape = Pos(0, 0, 360) * (Cylinder(31.6, 120, align=A) - Cylinder(30.0, 120, align=A))
    p = Path(tmp_path) / "old.glb"
    b3.export_gltf(shape, str(p), binary=True)          # what the worker wrote before P11
    old = p.read_bytes()
    lo, hi = cad_bbox(old)
    # the bug, as the user saw it: drawn about the -Y axis, 360 mm off the rocket's axis
    assert hi[1] == pytest.approx(-360 + 31.6, abs=0.5) and hi[2] == pytest.approx(120, abs=0.5)
    fixed = fix_stale_glb(old)
    want = shape.bounding_box()
    _close(cad_bbox(fixed), {"min": list(want.min), "max": list(want.max)}, tol=0.5)
    # a shape without a location is unchanged by the fix
    q = Path(tmp_path) / "plain.glb"
    b3.export_gltf(Cylinder(5, 10), str(q), binary=True)
    assert cad_bbox(fix_stale_glb(q.read_bytes())) == pytest.approx(cad_bbox(q.read_bytes()))
