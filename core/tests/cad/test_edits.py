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


async def read_src(ctx, part_id="bracket"):
    return (await mirror_part_get_source.handler(ctx, SourceArgs(part_id=part_id))).data


def _top(faces):
    return next(f for f in faces if f["normal"] and round(f["normal"][2], 3) == 1.0)


def _runs(tmp_path):
    """An agent run and the user's ui run editing the same part dir."""
    return (SimpleNamespace(workdir=tmp_path, run_id="agent_run"),
            SimpleNamespace(workdir=tmp_path, run_id="ui_run"))


# ---- I1: an omitted base_version means "the version this run last saw", never "no check"

async def test_agent_without_base_version_after_user_edit_conflicts(tmp_path):
    agent, user = _runs(tmp_path)
    await mk(agent)                                                  # agent saw v1
    r = await mirror_part_set_vars.handler(
        user, SetVarsArgs(part_id="bracket", vars={"length": 50}, base_version=1))
    assert r.ok and r.data["version"] == 2                           # the user's live edit
    c = await mirror_part_set_vars.handler(
        agent, SetVarsArgs(part_id="bracket", vars={"width": 30}))   # model dropped base_version
    assert not c.ok and c.data["conflict"] and c.data["current_version"] == 2
    assert c.data["vars"]["length"] == 50                            # it is shown the user's edit
    # the conflict carried the current state, so the retry lands on top of the user's edit
    r2 = await mirror_part_set_vars.handler(
        agent, SetVarsArgs(part_id="bracket", vars={"width": 30}))
    assert r2.ok and r2.data["version"] == 3 and r2.data["vars"]["length"] == 50


async def test_run_that_never_read_the_part_must_read_first(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    other = SimpleNamespace(workdir=tmp_path, run_id="other_run")
    c = await mirror_part_set_vars.handler(other, SetVarsArgs(part_id="bracket", vars={"width": 9}))
    assert not c.ok and c.data["conflict"] and "has not read" in c.summary
    other2 = SimpleNamespace(workdir=tmp_path, run_id="other_run_2")
    await mirror_measure.handler(other2, PartArgs(part_id="bracket"))   # read first
    r = await mirror_part_set_vars.handler(other2, SetVarsArgs(part_id="bracket", vars={"width": 9}))
    assert r.ok


async def test_create_over_user_edited_part_conflicts_and_keeps_the_hole(tmp_path):
    agent, user = _runs(tmp_path)
    await mk(agent)
    top = _top(await _all_faces(user, "bracket"))
    r = await mirror_part_op.handler(user, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": True}, base_version=1))
    assert r.ok
    c = await mirror_part_create.handler(agent, CreateArgs(part_id="bracket", script=BOX, vars={}))
    assert not c.ok and c.data["conflict"]
    assert "mises-op hole" in (tmp_path / "parts" / "bracket" / "part.py").read_text()


async def test_create_over_existing_part_keeps_its_op_blocks(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    top = _top(await _all_faces(agent, "bracket"))
    assert (await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": True}))).ok
    taller = BOX.replace('"height":10.0', '"height":12.0')
    await read_src(agent)                    # whole-script replacement: read the source first
    r = await mirror_part_create.handler(agent, CreateArgs(part_id="bracket", script=taller, vars={}))
    assert r.ok and "kept its feature ops" in r.summary
    assert len(r.data["holes"]) == 1
    assert r.data["volume_mm3"] == pytest.approx(40 * 20 * 12 - math.pi * 9 * 12, rel=0.02)


# ---- I2: a cancelled build puts everything back

async def test_cancel_mid_build_restores_part_py(tmp_path, monkeypatch):
    import mises.cad.tools as tools_mod
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    before, version = (d / "part.py").read_text(), edits.read_version(d)
    started = asyncio.Event()

    async def hang(part_dir, vars, **kw):
        started.set()
        await asyncio.sleep(30)
    monkeypatch.setattr(tools_mod, "run_build", hang)
    task = asyncio.create_task(mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="fillet", target={"edge_ids": ["e0"]}, params={"radius": 1.0})))
    await started.wait()
    assert "mises-op" in (d / "part.py").read_text()                # mid-build: block written
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (d / "part.py").read_text() == before and edits.read_version(d) == version


# ---- I3: ops follow their target when the part regenerates

async def test_blind_hole_follows_the_top_face_when_height_changes(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    top = _top(await _all_faces(agent, "bracket"))
    r = await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": False, "depth": 5.0}))
    assert r.ok, r.summary
    r30 = await mirror_part_set_vars.handler(
        agent, SetVarsArgs(part_id="bracket", vars={"height": 30}))
    assert r30.ok, r30.summary
    assert r30.data["volume_mm3"] == pytest.approx(40 * 20 * 30 - math.pi * 9 * 5, rel=0.02)
    (hole,) = r30.data["holes"]
    assert hole["center"][2] == pytest.approx(15 - 2.5, abs=0.05)   # opens at the new top face
    assert hole["depth_mm"] == pytest.approx(5, abs=0.05)
    r4 = await mirror_part_set_vars.handler(agent, SetVarsArgs(part_id="bracket", vars={"height": 4}))
    assert r4.ok, r4.summary
    # 5 mm deep from the top of a 4 mm part: all the way through, not a 2 mm stub
    assert r4.data["volume_mm3"] == pytest.approx(40 * 20 * 4 - math.pi * 9 * 4, rel=0.02)


async def test_through_hole_stays_through(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    top = _top(await _all_faces(agent, "bracket"))
    assert (await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": True}))).ok
    r = await mirror_part_set_vars.handler(agent, SetVarsArgs(part_id="bracket", vars={"height": 30}))
    assert r.ok
    assert r.data["volume_mm3"] == pytest.approx(40 * 20 * 30 - math.pi * 9 * 30, rel=0.02)


BOX_WITH_HOLE = ('from build123d import *\nVARS={"length":40.0,"width":20.0,"height":10.0}\n'
                 'def build(v):\n'
                 '    return Box(v["length"], v["width"], v["height"]) - Cylinder(3, 40)\n')


async def test_fillet_stays_on_its_edge_after_the_base_script_gains_a_hole(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    edges = (await mirror_edges.handler(agent, EdgesArgs(part_id="bracket"))).data["edges"]
    # the bottom front edge, along X at y=-10, z=-5
    target = next(e for e in edges if e["center"] == pytest.approx([0, -10, -5], abs=1e-3))
    r = await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="fillet", target={"edge_ids": [target["id"]]},
        params={"radius": 2.0}))
    assert r.ok, r.summary
    loss = 40 * 20 * 10 - r.data["volume_mm3"]
    src = (tmp_path / "parts" / "bracket" / "part.py").read_text()
    new_src = BOX_WITH_HOLE + src[src.index(edits.OP_MARK) - 2:]      # same op block, new base
    await read_src(agent)
    s = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=new_src))
    assert s.ok, s.summary
    assert s.data["volume_mm3"] == pytest.approx(40 * 20 * 10 - math.pi * 9 * 10 - loss, rel=0.01)
    faces = await _all_faces(agent, "bracket")
    rounds = [f for f in faces if f["type"] == "cylinder" and f["center"][1] < -8
              and f["center"][2] < -3]
    assert len(rounds) == 1                                           # still the same edge


async def test_lost_target_fails_loudly_naming_the_op(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    top = _top(await _all_faces(agent, "bracket"))
    assert (await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": True}))).ok
    d = tmp_path / "parts" / "bracket"
    before = (d / "part.py").read_text()
    src = before.replace('return Box(v["length"], v["width"], v["height"])', "return Sphere(10)")
    await read_src(agent)
    s = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=src))
    assert not s.ok
    assert "mises-op 0 hole" in s.summary and "not found" in s.summary
    assert (d / "part.py").read_text() == before


# ---- I4: a source edit runs in the sandbox

async def test_source_edit_cannot_reach_the_network(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    before, version = (d / "part.py").read_text(), edits.read_version(d)
    src = ("import socket\nfrom build123d import *\nVARS={}\ndef build(v):\n"
           "    socket.create_connection(('1.1.1.1', 80), 3)\n    return Box(1, 1, 1)\n")
    await read_src(agent)                    # so the refusal comes from the sandbox, not the check
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=src))
    assert "conflict" not in r.data
    assert not r.ok
    assert (d / "part.py").read_text() == before and edits.read_version(d) == version


async def test_source_edit_cannot_write_outside_the_part_dir(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    escape = tmp_path / "escape.txt"
    before, version = (d / "part.py").read_text(), edits.read_version(d)
    src = ("from build123d import *\nVARS={}\ndef build(v):\n"
           f"    open({str(escape)!r}, 'w').write('x')\n    return Box(1, 1, 1)\n")
    await read_src(agent)
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=src))
    assert "conflict" not in r.data
    assert not r.ok and not escape.exists()
    assert (d / "part.py").read_text() == before and edits.read_version(d) == version


# ---- fix round 2 regressions (from the re-review probes)

async def test_symlinks_left_by_a_hostile_build_are_never_written_through(tmp_path):
    """probe_symlink.py: the build links part.py to an outside file and model.stl to the version
    file, then fails. The host's restore must replace the links, never write through them."""
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    vp = edits.version_path(d)
    target = tmp_path / "outside_target.txt"
    before, version, vbytes = (d / "part.py").read_text(), edits.read_version(d), vp.read_bytes()
    hostile = ("import os\nfrom build123d import *\nVARS={}\ndef build(v):\n"
               f"    os.unlink({str(d / 'part.py')!r})\n"
               f"    os.symlink({str(target)!r}, {str(d / 'part.py')!r})\n"
               f"    os.unlink({str(d / 'model.stl')!r})\n"
               f"    os.symlink({str(vp)!r}, {str(d / 'model.stl')!r})\n"
               "    raise RuntimeError('fail on purpose')\n")
    await read_src(agent)
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=hostile))
    assert not r.ok and "conflict" not in r.data
    # the links really were made in the sandbox, and were caught and removed
    assert "links or special files" in r.summary and "part.py" in r.summary
    assert not target.exists()
    assert vp.read_bytes() == vbytes and edits.read_version(d) == version
    for n in ("part.py", "model.stl"):
        assert not (d / n).is_symlink()
    assert (d / "part.py").read_text() == before


async def test_a_successful_build_that_leaves_a_link_is_refused(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    version = edits.read_version(d)
    src = ("import os\nfrom build123d import *\nVARS={}\ndef build(v):\n"
           f"    os.symlink({str(tmp_path)!r}, {str(d / 'sims')!r})\n"
           "    return Box(5, 5, 5)\n")
    await read_src(agent)
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=src))
    assert not r.ok and "links or special files" in r.summary and "sims" in r.summary
    assert not (d / "sims").exists() and edits.read_version(d) == version


async def test_the_build_sandbox_cannot_write_the_meta_dir(tmp_path):
    agent, _ = _runs(tmp_path)
    await mk(agent)
    d = tmp_path / "parts" / "bracket"
    vp = edits.version_path(d)
    vbytes = vp.read_bytes()
    src = ("from build123d import *\nVARS={}\ndef build(v):\n"
           f"    open({str(vp)!r}, 'w').write('{{\"version\": 0}}')\n    return Box(5, 5, 5)\n")
    await read_src(agent)
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=src))
    assert not r.ok and vp.read_bytes() == vbytes


async def _user_hole(user, part_id="bracket"):
    top = _top(await _all_faces(user, part_id))
    v = (await read_src(user, part_id))["version"]
    r = await mirror_part_op.handler(user, OpArgs(
        part_id=part_id, op="hole", target={"face_id": top["id"]},
        params={"diameter": 6.0, "through": True}, base_version=v))
    assert r.ok, r.summary


async def test_measure_does_not_license_a_stale_source_replacement(tmp_path):
    """probe_c6.py B: the agent read the source at v1, the user adds a hole, the agent measures
    (routine) and replaces the script with its v1 copy: refused, the hole survives."""
    agent, user = _runs(tmp_path)
    await mk(agent)
    src_v1 = (await read_src(agent))["source"]
    await _user_hole(user)
    await mirror_measure.handler(agent, PartArgs(part_id="bracket"))
    r = await mirror_part_set_source.handler(
        agent, SetSourceArgs(part_id="bracket", source=src_v1.replace("40.0", "45.0")))
    assert not r.ok and r.data["conflict"] and "mirror_part_get_source" in r.summary
    assert len(r.data["ops"]) == 1 and "mises-op hole" in r.data["ops"][0]
    assert "mises-op hole" in (tmp_path / "parts" / "bracket" / "part.py").read_text()


async def test_blind_retry_after_a_source_conflict_is_refused_again(tmp_path):
    """probe_c6.py C: the same set_source retried unchanged after the conflict stays refused."""
    agent, user = _runs(tmp_path)
    await mk(agent)
    s2 = (await read_src(agent))["source"].replace("40.0", "45.0")
    await _user_hole(user)
    r1 = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=s2))
    r2 = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=s2))
    assert not r1.ok and not r2.ok and r2.data["conflict"]
    assert "source" not in r2.data                       # no state handed over to retry on
    assert "mises-op hole" in (tmp_path / "parts" / "bracket" / "part.py").read_text()


async def test_source_replacement_after_a_fresh_read_keeps_the_users_ops(tmp_path):
    agent, user = _runs(tmp_path)
    await mk(agent)
    await _user_hole(user)
    base = BOX.replace("40.0", "45.0")                  # an agent script without the op block
    await read_src(agent)
    r = await mirror_part_set_source.handler(agent, SetSourceArgs(part_id="bracket", source=base))
    assert r.ok and "kept its feature ops" in r.summary and len(r.data["holes"]) == 1
    await read_src(agent)
    r = await mirror_part_set_source.handler(
        agent, SetSourceArgs(part_id="bracket", source=base, drop_ops=True))
    assert r.ok and len(r.data["holes"]) == 0           # only when asked


STEP = ('from build123d import *\nVARS={"boss_h":5.0}\n'
        'def build(v):\n    b = Box(40, 20, 10)\n'
        '    if v["boss_h"] > 0:\n'
        '        b = b + Pos(10, 0, 5 + v["boss_h"]/2) * Box(10, 10, v["boss_h"])\n'
        '    return b\n')


async def test_hole_on_a_removed_boss_fails_loudly(tmp_path):
    """probe_c6.py A: a hole on a boss top; the boss is removed. The op must not move to the
    base top face: the rebuild fails, naming the op, and nothing changes."""
    agent, _ = _runs(tmp_path)
    await mk(agent, script=STEP)
    faces = await _all_faces(agent, "bracket")
    boss_top = next(f for f in faces if f["normal"] and round(f["normal"][2], 3) == 1
                    and f["center"][2] > 9)
    assert (await mirror_part_op.handler(agent, OpArgs(
        part_id="bracket", op="hole", target={"face_id": boss_top["id"]},
        params={"diameter": 4.0, "through": False, "depth": 3.0}))).ok
    d = tmp_path / "parts" / "bracket"
    version = edits.read_version(d)
    r = await mirror_part_set_vars.handler(agent, SetVarsArgs(part_id="bracket", vars={"boss_h": 0}))
    assert not r.ok and "mises-op 0 hole" in r.summary and "not found" in r.summary
    assert edits.read_version(d) == version
    # a modest change keeps it on the boss
    r = await mirror_part_set_vars.handler(agent, SetVarsArgs(part_id="bracket", vars={"boss_h": 7}))
    assert r.ok, r.summary
    (hole,) = r.data["holes"]
    assert hole["center"][0] == pytest.approx(10, abs=0.05)
