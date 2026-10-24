import json
from types import SimpleNamespace

import pytest

from mises.cad.tools import (mirror_export, mirror_measure, mirror_part_create, mirror_part_list,
                             mirror_part_set_vars, register_mirror_tools, CreateArgs,
                             SetVarsArgs, PartArgs, ExportArgs, ListArgs)
from mises.tools.registry import ToolRegistry
from mises.types import ErrorCode, MisesError, StepKind

pytestmark = pytest.mark.slow

BOX = 'from build123d import *\nVARS={"length":40.0}\ndef build(v):\n    return Box(v["length"],20,10)\n'
FAILS_AT_100 = ('from build123d import *\nVARS={"length":40.0}\ndef build(v):\n'
                '    if v["length"] > 100: raise ValueError("too long")\n    return Box(v["length"],20,10)\n')


@pytest.fixture
def ctx(tmp_path):
    return SimpleNamespace(workdir=tmp_path)


async def test_set_vars_delta(ctx):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=BOX, vars={"length": 40}))
    assert r.ok, r.summary
    r = await mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 50}))
    assert r.ok, r.summary
    assert r.step_kind == StepKind.DIFF
    assert r.data["delta"]["volume_mm3"] == pytest.approx(2000)
    assert r.data["delta"]["bbox_size"][0] == pytest.approx(10)
    m = await mirror_measure.handler(ctx, PartArgs(part_id="bracket"))
    assert m.step_kind == StepKind.MEASURE and m.data["volume_mm3"] == pytest.approx(10000)


async def test_failed_set_vars_keeps_previous(ctx, tmp_path):
    await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=FAILS_AT_100, vars={"length": 40}))
    d = tmp_path / "parts" / "bracket"
    before = (d / "vars.json").read_text()
    metrics_before = (d / "metrics.json").read_text()
    r = await mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 200}))
    assert not r.ok and "too long" in r.summary
    assert (d / "vars.json").read_text() == before
    assert (d / "metrics.json").read_text() == metrics_before


async def test_failed_create_leaves_nothing(ctx, tmp_path):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="broken", script="def build(v): raise KeyError('x')", vars={}))
    assert not r.ok
    assert not (tmp_path / "parts" / "broken").exists()


async def test_export_glb_handle_and_list(ctx):
    await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=BOX, vars={"length": 40}))
    r = await mirror_export.handler(ctx, ExportArgs(part_id="bracket", format="glb"))
    from pathlib import Path
    assert r.handles[0].endswith(".glb") and Path(r.handles[0]).exists()
    lst = await mirror_part_list.handler(ctx, ListArgs())
    assert lst.data["parts"] == [{"part_id": "bracket", "vars": {"length": 40}}]


async def test_bad_names_rejected(ctx):
    for kw in ({"part_id": "bracket", "vars": {"rib2": 1}}, {"part_id": "bracket2", "vars": {}},
               {"part_id": "Bad", "vars": {}}):
        with pytest.raises(MisesError) as e:
            await mirror_part_create.handler(ctx, CreateArgs(script=BOX, **kw))
        assert e.value.code == ErrorCode.INVALID_INPUT


async def test_unknown_part_not_found(ctx):
    with pytest.raises(MisesError) as e:
        await mirror_measure.handler(ctx, PartArgs(part_id="ghost"))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_registration():
    reg = ToolRegistry()
    register_mirror_tools(reg)
    assert {t.name for t in reg.list()} == {"mirror_part_create", "mirror_part_set_vars",
                                            "mirror_measure", "mirror_export", "mirror_part_list"}
