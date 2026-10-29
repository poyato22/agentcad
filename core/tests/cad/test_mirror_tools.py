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
                                            "mirror_measure", "mirror_export", "mirror_part_list",
                                            "mirror_faces"}


async def test_forged_metrics_then_raise_keeps_state(ctx, tmp_path):
    script = ('from build123d import *\nVARS={"length":40.0}\ndef build(v):\n'
              '    if v["length"] > 100:\n'
              '        open("metrics.json","w").write("{\\"forged\\": 1}")\n'
              '        raise ValueError("x")\n    return Box(v["length"],20,10)\n')
    await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=script, vars={}))
    d = tmp_path / "parts" / "bracket"
    vj, mj = (d / "vars.json").read_text(), (d / "metrics.json").read_text()
    r = await mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 200}))
    assert not r.ok
    assert (d / "vars.json").read_text() == vj and (d / "metrics.json").read_text() == mj


async def test_script_var_defaults_recorded(ctx):
    await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=BOX, vars={}))
    lst = await mirror_part_list.handler(ctx, ListArgs())
    assert lst.data["parts"] == [{"part_id": "bracket", "vars": {"length": 40.0}}]
    r = await mirror_part_set_vars.handler(ctx, SetVarsArgs(part_id="bracket", vars={"length": 50}))
    assert r.ok, r.summary


async def test_part_lock_shared_across_runs(tmp_path, monkeypatch):
    # I4: two runs see the shared parts dir through their own symlink; builds must serialize
    import asyncio
    import mises.cad.tools as tools_mod
    from mises.cad.runner import BuildResult
    shared = tmp_path / "parts"
    shared.mkdir()
    ctxs = []
    for name in ("r1", "r2"):
        wd = tmp_path / "runs" / name
        wd.mkdir(parents=True)
        (wd / "parts").symlink_to(shared, target_is_directory=True)
        ctxs.append(SimpleNamespace(workdir=wd))
    active, peak = 0, 0

    async def fake_build(d, vars, **kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        metrics = {"volume_mm3": 1.0, "mass_g": 1.0, "bbox": {"size": [1, 1, 1]}, "holes": [],
                   "valid": True}
        (d / "metrics.json").write_text(json.dumps(metrics))
        return BuildResult(True, metrics=metrics, vars=dict(vars))
    monkeypatch.setattr(tools_mod, "run_build", fake_build)
    await asyncio.gather(*(mirror_part_create.handler(c, CreateArgs(part_id="shared_box", script="x"))
                           for c in ctxs))
    assert peak == 1


async def test_create_registers_the_tree_row_in_the_runs_build(tmp_path):
    """Plan 5 C1: a part's tree row is made when it is first built, in the creating run's build;
    a rebuild from another build keeps it where it is."""
    from mises.store import Store
    store = Store(":memory:")
    drone, rover = store.create_project("Drone"), store.create_project("Rover")
    run = store.create_run("designer", "fake", "m", "x", project_id=drone.id)
    other = store.create_run("designer", "fake", "m", "y", project_id=rover.id)
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id)
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id="bracket", script=BOX))
    assert r.ok, r.summary
    assert store.get_part("bracket").project_id == drone.id
    ctx2 = SimpleNamespace(workdir=tmp_path, store=store, run_id=other.id)
    assert (await mirror_part_create.handler(ctx2, CreateArgs(part_id="bracket", script=BOX))).ok
    assert store.get_part("bracket").project_id == drone.id
