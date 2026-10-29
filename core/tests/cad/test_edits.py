"""C6: live editing alongside the agent — versions, conflicts, feature ops."""
import asyncio
import math
from types import SimpleNamespace

import pytest

from mises import edits
from mises.cad.tools import (CreateArgs, OpArgs, PartArgs, SetSourceArgs, SetVarsArgs, SourceArgs,
                             mirror_edges, mirror_measure, mirror_part_create, mirror_part_get_source,
                             mirror_part_op, mirror_part_set_source, mirror_part_set_vars)
from mises.cad.tools import EdgesArgs
from mises.types import StepKind

pytestmark = pytest.mark.slow

BOX = ('from build123d import *\nVARS={"length":40.0,"width":20.0,"height":10.0}\n'
       'def build(v):\n    return Box(v["length"], v["width"], v["height"])\n')


@pytest.fixture
def ctx(tmp_path):
    return SimpleNamespace(workdir=tmp_path)


async def mk(ctx, part_id="bracket", script=BOX):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id=part_id, script=script, vars={}))
    assert r.ok, r.summary
    return r


async def test_create_sets_version_one(ctx):
    r = await mk(ctx)
    assert r.data["version"] == 1
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    assert m.data["version"] == 1


async def test_set_vars_bumps_version_and_guards(ctx):
    await mk(ctx)
    r = await mirror_part_set_vars.handler(
        ctx, SetVarsArgs(part_id="bracket", vars={"length": 50}, base_version=1))
    assert r.ok and r.data["version"] == 2
    # a stale base_version is refused with a structured conflict, not an overwrite
    c = await mirror_part_set_vars.handler(
        ctx, SetVarsArgs(part_id="bracket", vars={"length": 60}, base_version=1))
    assert not c.ok and c.data["conflict"] and c.data["current_version"] == 2
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    assert m.data["version"] == 2  # unchanged by the refused edit


async def test_simultaneous_user_and_agent_edit_one_conflict(ctx):
    """Both read version 1 then both try to write it; exactly one lands, the other conflicts."""
    await mk(ctx)
    r1, r2 = await asyncio.gather(
        mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 41},
                                                      base_version=1)),
        mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 42},
                                                      base_version=1)))
    oks = [r for r in (r1, r2) if r.ok]
    conflicts = [r for r in (r1, r2) if not r.ok]
    assert len(oks) == 1 and len(conflicts) == 1
    assert conflicts[0].data["conflict"] and conflicts[0].data["current_version"] == 2


async def test_get_and_set_source(ctx):
    await mk(ctx)
    g = await mirror_part_get_source.handler(ctx, SourceArgs(part_id="bracket"))
    assert "def build" in g.data["source"] and g.data["version"] == 1
    assert {s["name"] for s in g.data["vars"]} == {"length", "width", "height"}
    new = BOX.replace("Box(v[\"length\"], v[\"width\"], v[\"height\"])",
                      "Box(v[\"length\"], v[\"width\"], v[\"height\"]*2)")
    r = await mirror_part_set_source.handler(
        ctx, SetSourceArgs(part_id="bracket", source=new, base_version=1))
    assert r.ok and r.data["version"] == 2
    assert r.data["bbox"]["size"][2] == pytest.approx(20)
    # stale source edit is refused
    c = await mirror_part_set_source.handler(
        ctx, SetSourceArgs(part_id="bracket", source=new, base_version=1))
    assert not c.ok and c.data["conflict"]


async def test_failed_source_edit_rolls_back_part_py(ctx):
    await mk(ctx)
    d = ctx.workdir / "parts" / "bracket"
    before = (d / "part.py").read_text()
    r = await mirror_part_set_source.handler(
        ctx, SetSourceArgs(part_id="bracket", source="def build(v):\n    raise ValueError('x')\n",
                           base_version=1))
    assert not r.ok
    assert (d / "part.py").read_text() == before  # previous source kept
    g = await mirror_part_get_source.handler(ctx, SourceArgs(part_id="bracket"))
    assert g.data["version"] == 1  # version not bumped by the failed edit


async def test_hole_op_appends_block_and_removes_cylinder(ctx):
    await mk(ctx)
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    vol0 = m.data["volume_mm3"]
    # top face: the one whose normal points +Z
    faces = (await _all_faces(ctx, "bracket"))
    top = next(f for f in faces if f["normal"] and round(f["normal"][2], 3) == 1.0)
    d_mm = 6.0
    r = await mirror_part_op.handler(ctx, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": d_mm, "through": True}, base_version=1))
    assert r.ok, r.summary
    assert r.data["version"] == 2
    removed = vol0 - r.data["volume_mm3"]
    expected = math.pi * (d_mm / 2) ** 2 * 10.0  # height of the box
    assert removed == pytest.approx(expected, rel=0.02)
    src = (ctx.workdir / "parts" / "bracket" / "part.py").read_text()
    assert src.count(edits.OP_MARK) == 1 and "mises-op hole" in src


async def test_fillet_op_reduces_volume(ctx):
    await mk(ctx)
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    vol0 = m.data["volume_mm3"]
    edges = (await mirror_edges.handler(ctx, EdgesArgs(part_id="bracket"))).data["edges"]
    r = await mirror_part_op.handler(ctx, OpArgs(
        part_id="bracket", op="fillet", target={"edge_ids": [edges[0]["id"]]},
        params={"radius": 2.0}, base_version=1))
    assert r.ok, r.summary
    assert r.data["volume_mm3"] < vol0


async def test_shell_op_hollows(ctx):
    await mk(ctx)
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    vol0 = m.data["volume_mm3"]
    faces = await _all_faces(ctx, "bracket")
    top = next(f for f in faces if f["normal"] and round(f["normal"][2], 3) == 1.0)
    r = await mirror_part_op.handler(ctx, OpArgs(
        part_id="bracket", op="shell", target={"face_ids": [top["id"]]},
        params={"thickness": 1.5}, base_version=1))
    assert r.ok, r.summary
    assert r.data["volume_mm3"] < vol0


async def test_failed_op_drops_block_and_keeps_state(ctx):
    await mk(ctx)
    before = (ctx.workdir / "parts" / "bracket" / "part.py").read_text()
    faces = await _all_faces(ctx, "bracket")
    top = next(f for f in faces if f["normal"] and round(f["normal"][2], 3) == 1.0)
    # a hole larger than the part cannot build cleanly through; op must roll back
    r = await mirror_part_op.handler(ctx, OpArgs(
        part_id="bracket", op="fillet", target={"edge_ids": ["e0"]},
        params={"radius": 999.0}, base_version=1))
    assert not r.ok
    assert (ctx.workdir / "parts" / "bracket" / "part.py").read_text() == before


async def _all_faces(ctx, part_id):
    from mises.cad.tools import FacesArgs, mirror_faces
    return (await mirror_faces.handler(ctx, FacesArgs(part_id=part_id))).data["faces"]
