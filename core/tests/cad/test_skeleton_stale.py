"""Plan 8 G3 fix round 1: every planned dependent is marked stale BEFORE the cascade starts, so
only a successful rebuild clears it — cancel, timeout, the MAX_REGEN cap and regenerate=false
all leave the rest flagged (fast: a fake build, no sandbox)."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import mises.cad.skeleton_tools as skt
import mises.cad.tools as tools_mod
from mises.cad import skeleton as sk
from mises.cad.runner import BuildResult
from mises.cad.skeleton_tools import SkeletonGetArgs, SkeletonSetArgs, skeleton_get, skeleton_set
from mises.cad.tools import CreateArgs, mirror_part_create
from mises.events import EventBus
from mises.store import Store
from mises.types import MisesError

PARTS = ("rib_front", "rib_mid", "rib_rear")


class FakeBuild:
    """Echoes vars, reads the skeleton name `span`; `hang` makes the build after the first
    `ok_before_hang` regenerations wait forever (a cancel or timeout lands there)."""

    def __init__(self):
        self.hang = False
        self.ok_before_hang = 0
        self.calls = 0
        self.started = asyncio.Event()

    async def __call__(self, part_dir, vars, **_kw):
        if self.hang:
            self.calls += 1
            if self.calls > self.ok_before_hang:
                self.started.set()
                await asyncio.sleep(3600)
        m = {"volume_mm3": 1.0, "mass_g": 1.0, "valid": True, "holes": [],
             "bbox": {"min": [0, 0, 0], "max": [1, 1, 1], "size": [1, 1, 1]}}
        (part_dir / "metrics.json").write_text(json.dumps(m))
        return BuildResult(True, metrics=m, vars=dict(vars),
                           skeleton_refs={"params": ["span"]})


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeBuild()
    monkeypatch.setattr(tools_mod, "run_build", fake)
    store = Store(":memory:")
    proj = store.create_project("Wing")
    run = store.create_run("engineer", "fake", "m", "x", project_id=proj.id)
    (tmp_path / "parts").mkdir()
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id, events=EventBus(),
                          policy=None, services={}, call_id="c")
    return SimpleNamespace(ctx=ctx, project=proj.id, root=tmp_path / "parts", fake=fake)


async def _setup(w):
    r = await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project,
                                                          params={"span": 100.0}))
    assert r.ok
    for p in PARTS:
        assert (await mirror_part_create.handler(
            w.ctx, CreateArgs(part_id=p, script="VARS = {}\n"))).ok


def _stale(w) -> dict:
    return {p: (sk.read_refs(w.root / p) or {}).get("stale") for p in PARTS}


async def test_regenerate_false_leaves_every_dependent_stale_and_the_next_edit_catches_up(world):
    w = world
    await _setup(w)
    r = await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project, regenerate=False,
                                                          params={"span": 120.0}))
    assert r.ok and r.data["regenerated"] == []
    assert sorted(s["part_id"] for s in r.data["skipped"]) == sorted(PARTS)
    assert all(v and v["skeleton_version"] == 2 for v in _stale(w).values())
    g = await skeleton_get.handler(w.ctx, SkeletonGetArgs(project_id=w.project))
    assert all(d["stale"] for d in g.data["dependents"])
    # an edit of an unrelated name still regenerates the parts that missed v2
    r = await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project,
                                                          params={"other_size": 1.0}))
    assert sorted(x["part_id"] for x in r.data["regenerated"]) == sorted(PARTS)
    assert _stale(w) == {p: None for p in PARTS}


async def test_cancel_mid_cascade_leaves_the_rest_stale(world):
    w = world
    await _setup(w)
    w.fake.hang, w.fake.ok_before_hang = True, 1
    task = asyncio.create_task(skeleton_set.handler(
        w.ctx, SkeletonSetArgs(project_id=w.project, params={"span": 130.0})))
    await w.fake.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stale = _stale(w)
    assert stale[PARTS[0]] is None                       # rebuilt before the cancel: cleared
    assert stale[PARTS[1]] and stale[PARTS[2]]           # in flight and not reached: flagged


async def test_timeout_mid_cascade_leaves_the_rest_stale(world):
    w = world
    await _setup(w)
    w.fake.hang, w.fake.ok_before_hang = True, 0
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(skeleton_set.handler(
            w.ctx, SkeletonSetArgs(project_id=w.project, params={"span": 140.0})), 0.5)
    assert all(v and v["skeleton_version"] == 2 for v in _stale(w).values())


async def test_the_regeneration_cap_reports_and_flags_what_it_dropped(world, monkeypatch):
    w = world
    await _setup(w)
    monkeypatch.setattr(skt, "MAX_REGEN", 2)
    r = await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project,
                                                          params={"span": 150.0}))
    assert [x["part_id"] for x in r.data["regenerated"]] == list(PARTS[:2])
    assert [s["part_id"] for s in r.data["skipped"]] == [PARTS[2]]
    assert "cap" in r.data["skipped"][0]["reason"]
    stale = _stale(w)
    assert stale[PARTS[0]] is None and stale[PARTS[1]] is None and stale[PARTS[2]]


async def test_a_real_part_cannot_take_a_skeleton_id(world):
    w = world
    with pytest.raises(MisesError, match="reserved"):
        await mirror_part_create.handler(w.ctx, CreateArgs(
            part_id=sk.skeleton_id(w.project), script="VARS = {}\n"))


def test_refs_record_has_no_part_version(tmp_path):
    d = tmp_path / "parts" / "rib"
    d.mkdir(parents=True)
    rec = sk.record_refs(d, {"params": ["span"]})
    assert "part_version" not in rec and rec["params"] == ["span"]
