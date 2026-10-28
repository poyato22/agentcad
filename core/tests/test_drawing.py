from types import SimpleNamespace

import pytest

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.drawing import DrawingError, mirror_drawing

pytestmark = pytest.mark.slow

PLATE = ('from build123d import *\nVARS={"length":40.0}\ndef build(v):\n'
         '    return Box(v["length"],20,10) - Pos(10,0,0)*Cylinder(3,10) - Pos(-10,0,0)*Cylinder(3,10)\n')


@pytest.fixture
async def part(tmp_path):
    ctx = SimpleNamespace(workdir=tmp_path)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=PLATE, vars={}))
    assert r.ok, r.summary
    return tmp_path / "parts" / "bracket"


async def test_svg_has_four_views_dims_holes_title(part):
    res = await mirror_drawing(part, label="bracket", material="PLA", date="2026-10-28")
    svg = res.svg_path.read_text()
    for view in ("front", "top", "right", "iso"):
        assert f'id="view-{view}"' in svg
    assert 'id="annotations"' in svg
    # overall dimensions from the bbox: 40 x 20 x 10
    for dim in ("40.0", "20.0", "10.0"):
        assert f">{dim}</text>" in svg
    assert "2x Ø6 THRU" in svg
    assert "PART: bracket" in svg and "MATERIAL: PLA" in svg and "DATE: 2026-10-28" in svg
    assert ">Mises</text>" in svg
    dxf = res.dxf_path.read_text()
    assert dxf.lstrip().startswith("0")
    assert "VIEW_FRONT" in dxf
    assert res.info["views"] == ["front", "top", "right", "iso"]


async def test_cached_until_inputs_change(part):
    a = await mirror_drawing(part, label="bracket", material="PLA", date="2026-10-28")
    m1 = a.svg_path.stat().st_mtime_ns
    b = await mirror_drawing(part, label="bracket", material="PLA", date="2026-10-28")
    assert b.svg_path.stat().st_mtime_ns == m1
    c = await mirror_drawing(part, label="bracket", material="PETG", date="2026-10-28")
    assert "MATERIAL: PETG" in c.svg_path.read_text()


async def test_missing_step_is_clear_error(tmp_path):
    (tmp_path / "p").mkdir()
    with pytest.raises(DrawingError, match="not built"):
        await mirror_drawing(tmp_path / "p", label="p", material="PLA", date="2026-10-28")
