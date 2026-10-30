"""Plan 8 G3: the layout skeleton — read-only injection, per-build refs, regeneration under C6."""
import json
from types import SimpleNamespace

import pytest

from mises import edits
from mises.cad import skeleton as sk
from mises.cad.skeleton_tools import (SkeletonGetArgs, SkeletonSetArgs, skeleton_get,
                                      skeleton_set)
from mises.cad.tools import (CreateArgs, SetSourceArgs, SourceArgs, mirror_measure,
                             mirror_part_create, mirror_part_get_source, mirror_part_set_source,
                             PartArgs)
from mises.events import EventBus
from mises.store import Store
from mises.types import MisesError

pytestmark = [pytest.mark.slow, pytest.mark.timeout(180)]  # several real sandboxed builds each

WING = '''VARS = {"chord": 30.0}

def build(v):
    return Box(skeleton("wing_span"), v["chord"], 4)
'''

PLATE = '''VARS = {"size": 20.0}

def build(v):
    return Box(v["size"], v["size"], 2)
'''

DATUMS = '''VARS = {}

def build(v):
    pl = datum("spar_plane")
    ax = datum("hinge_axis")
    pt = datum("nose_point")
    edge = curve("leading_edge")
    env = envelope()
    assert abs(pl.z_dir.Y - 1) < 1e-9 and abs(ax.direction.Z - 1) < 1e-9
    size = env.bounding_box().size
    return Box(size.X, size.Y, edge.length).moved(Location((pt.X, pt.Y, pt.Z)))
'''


@pytest.fixture
def world(tmp_path):
    store = Store(":memory:")
    proj = store.create_project("Plane")
    run = store.create_run("engineer", "fake", "m", "x", project_id=proj.id)
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id, events=EventBus(),
                          policy=None, services={}, call_id="c")
    (tmp_path / "parts").mkdir()
    return SimpleNamespace(store=store, project=proj.id, run=run, ctx=ctx, root=tmp_path / "parts")


def _user_ctx(w):
    ui = w.store.create_run("ui", "ui", "-", "ui", project_id=w.project)
    return SimpleNamespace(**{**vars(w.ctx), "run_id": ui.id})


async def _set(ctx, project, **kw):
    r = await skeleton_set.handler(ctx, SkeletonSetArgs(project_id=project, **kw))
    assert r.ok, r.summary
    return r


async def test_skeleton_change_regenerates_only_dependent_parts(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0, "fuselage_length": 80.0})
    assert (await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))).ok
    assert (await mirror_part_create.handler(w.ctx, CreateArgs(part_id="plate", script=PLATE))).ok
    wing_v = edits.read_version(w.root / "wing")
    plate_v = edits.read_version(w.root / "plate")

    # an unrelated name changes: nothing is rebuilt
    r = await _set(w.ctx, w.project, params={"fuselage_length": 90.0})
    assert r.data["regenerated"] == []
    assert edits.read_version(w.root / "wing") == wing_v

    sub = w.ctx.events.subscribe()
    r = await _set(w.ctx, w.project, params={"wing_span": 150.0})
    assert [x["part_id"] for x in r.data["regenerated"]] == ["wing"]
    m = await mirror_measure.handler(w.ctx, PartArgs(part_id="wing"))
    assert m.data["bbox"]["size"][0] == pytest.approx(150.0)
    assert edits.read_version(w.root / "wing") == wing_v + 1      # C6: a rebuild bumps it
    assert edits.read_version(w.root / "plate") == plate_v        # not a dependent: untouched
    types = []
    while not sub.queue.empty():
        types.append((await sub.queue.get()).type)
    assert "skeleton_changed" in types and types.count("part_changed") == 2  # skeleton + wing


async def test_refs_are_recorded_per_build(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))
    refs = sk.read_refs(w.root / "wing")
    assert refs["params"] == ["wing_span"] and refs["datums"] == []
    assert refs["skeleton_id"] == sk.skeleton_id(w.project) and refs["skeleton_version"] == 1
    # the next build records what IT read: a script that stops reading the skeleton is no
    # longer a dependent
    src = await mirror_part_get_source.handler(w.ctx, SourceArgs(part_id="wing"))
    r = await mirror_part_set_source.handler(w.ctx, SetSourceArgs(
        part_id="wing", source=PLATE, base_version=src.data["version"]))
    assert r.ok, r.summary
    assert sk.read_refs(w.root / "wing")["params"] == []
    r = await _set(w.ctx, w.project, params={"wing_span": 120.0})
    assert r.data["regenerated"] == []
    g = await skeleton_get.handler(w.ctx, SkeletonGetArgs(project_id=w.project))
    assert g.data["dependents"] == [] and g.data["version"] == 2


async def test_skeleton_is_read_only_to_the_sandboxed_script(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    staged = edits.meta_dir(w.root / "evil") / sk.SKELETON_IN
    script = f'''VARS = {{}}

def build(v):
    span = skeleton("wing_span")
    try:
        open({str(staged)!r}, "w").write('{{"params": {{"wing_span": 999}}}}')
        wrote = 1.0
    except OSError:
        wrote = 0.0
    return Box(span, 10, 10 + 50 * wrote)
'''
    r = await mirror_part_create.handler(w.ctx, CreateArgs(part_id="evil", script=script))
    assert r.ok, r.summary
    assert r.data["bbox"]["size"][2] == pytest.approx(10.0)       # the write was refused
    assert json.loads(staged.read_text())["params"] == {"wing_span": 100.0}
    data, version = sk.load(w.root, w.project)
    assert data.params == {"wing_span": 100.0} and version == 1


async def test_datums_curves_and_envelope_reach_the_script(world):
    w = world
    await _set(w.ctx, w.project,
               datums={"spar_plane": {"kind": "plane", "origin": [0, 40, 0], "normal": [0, 1, 0]},
                       "hinge_axis": {"kind": "axis", "origin": [0, 0, 0], "direction": [0, 0, 1]},
                       "nose_point": {"kind": "point", "origin": [5, 0, 0]}},
               curves={"leading_edge": {"kind": "polyline", "points": [[0, 0, 0], [0, 12, 0]]}},
               envelope={"min": [0, 0, 0], "max": [40, 20, 10]})
    r = await mirror_part_create.handler(w.ctx, CreateArgs(part_id="probe", script=DATUMS))
    assert r.ok, r.summary
    assert r.data["bbox"]["size"] == pytest.approx([40, 20, 12])
    refs = sk.read_refs(w.root / "probe")
    assert refs["datums"] == ["hinge_axis", "nose_point", "spar_plane"]
    assert refs["curves"] == ["leading_edge"] and refs["envelope"] is True
    # moving the envelope regenerates it
    r = await _set(w.ctx, w.project, envelope={"min": [0, 0, 0], "max": [50, 20, 10]})
    assert [x["part_id"] for x in r.data["regenerated"]] == ["probe"]


async def test_no_skeleton_message_and_default(world):
    w = world
    ok = 'VARS = {}\ndef build(v):\n    return Box(skeleton("span", default=12.0), 5, 5)\n'
    r = await mirror_part_create.handler(w.ctx, CreateArgs(part_id="lone", script=ok))
    assert r.ok and r.data["bbox"]["size"][0] == pytest.approx(12.0)
    bad = 'VARS = {}\ndef build(v):\n    return Box(skeleton("span"), 5, 5)\n'
    r = await mirror_part_create.handler(w.ctx, CreateArgs(part_id="lonely", script=bad))
    assert not r.ok and "no layout skeleton" in r.summary
    # the part that used a default becomes a dependent once the name exists
    r = await _set(w.ctx, w.project, params={"span": 30.0})
    assert [x["part_id"] for x in r.data["regenerated"]] == ["lone"]


async def test_skeleton_edits_follow_c6(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    other = w.store.create_run("designer", "fake", "m", "y", project_id=w.project)
    octx = SimpleNamespace(**{**vars(w.ctx), "run_id": other.id})
    r = await skeleton_set.handler(octx, SkeletonSetArgs(project_id=w.project,
                                                         params={"wing_span": 1.0}))
    assert not r.ok and r.data["conflict"] and "not read" in r.summary
    # the conflict carried the current state: the retry goes through
    r = await skeleton_set.handler(octx, SkeletonSetArgs(project_id=w.project,
                                                         params={"wing_span": 110.0}))
    assert r.ok, r.summary
    # the first run's version is now stale
    r = await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project,
                                                          params={"wing_span": 120.0}))
    assert not r.ok and r.data["current_version"] == 2
    # the user must name the version they saw
    user = _user_ctx(w)
    r = await skeleton_set.handler(user, SkeletonSetArgs(project_id=w.project,
                                                         params={"wing_span": 130.0}))
    assert not r.ok
    r = await skeleton_set.handler(user, SkeletonSetArgs(project_id=w.project, base_version=2,
                                                         params={"wing_span": 130.0}))
    assert r.ok and r.data["version"] == 3
    row = w.store.get_part(sk.skeleton_id(w.project))
    assert row.kind == "skeleton" and row.project_id == w.project and row.version == 3


async def test_regeneration_never_clobbers_a_users_edit(world):
    """The user has the wing's source open at version N while the skeleton changes. The
    regeneration rebuilds from the CURRENT script (never rewrites it) and bumps the version, so
    the user's save at N is a conflict they resolve (Keep mine), not a silent overwrite."""
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))
    user = _user_ctx(w)
    src = await mirror_part_get_source.handler(user, SourceArgs(part_id="wing"))
    n = src.data["version"]
    before = (w.root / "wing" / "part.py").read_text()
    await _set(w.ctx, w.project, params={"wing_span": 140.0})
    assert (w.root / "wing" / "part.py").read_text() == before
    assert edits.read_version(w.root / "wing") == n + 1
    mine = WING.replace('v["chord"]', 'v["chord"] * 2')
    r = await mirror_part_set_source.handler(user, SetSourceArgs(part_id="wing", source=mine,
                                                                 base_version=n))
    assert not r.ok and r.data["conflict"]
    r = await mirror_part_set_source.handler(user, SetSourceArgs(part_id="wing", source=mine,
                                                                 base_version=n + 1))
    assert r.ok, r.summary
    assert r.data["bbox"]["size"][:2] == pytest.approx([140.0, 60.0])


async def test_failed_regeneration_keeps_the_part_and_marks_it_stale(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))
    v = edits.read_version(w.root / "wing")
    r = await _set(w.ctx, w.project, params={"wing_span": 0.0})
    assert [f["part_id"] for f in r.data["failed"]] == ["wing"]
    assert edits.read_version(w.root / "wing") == v
    m = await mirror_measure.handler(w.ctx, PartArgs(part_id="wing"))
    assert m.data["bbox"]["size"][0] == pytest.approx(100.0)
    g = await skeleton_get.handler(w.ctx, SkeletonGetArgs(project_id=w.project))
    assert g.data["dependents"][0]["stale"]["skeleton_version"] == 2


async def test_regeneration_respects_the_edit_scope(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))
    narrow = SimpleNamespace(**{**vars(w.ctx),
                                "policy": SimpleNamespace(edit_scope=["other_part"])})
    r = await skeleton_set.handler(narrow, SkeletonSetArgs(project_id=w.project,
                                                           params={"wing_span": 120.0}))
    assert r.ok and r.data["regenerated"] == []
    assert r.data["skipped"][0]["part_id"] == "wing"


def test_skeleton_data_validation():
    with pytest.raises(ValueError):
        sk.SkeletonData(params={"span2": 1.0})            # names never end in a number
    with pytest.raises(ValueError):
        sk.SkeletonData(params={"a_b": 1.0}, datums={"a_b": {"kind": "point"}})  # used twice
    with pytest.raises(ValueError):
        sk.Datum(kind="plane", origin=[0, 0, 0])          # no normal
    with pytest.raises(ValueError):
        sk.Envelope(min=[0, 0, 0], max=[1, 0, 1])
    assert sk.skeleton_id("Ab-12") == "skeleton_ab_12"


def test_clean_refs_drops_junk():
    raw = {"params": ["ok_name", "../x", 3], "datums": "nope", "envelope": "yes",
           "parts": ["case", "BAD", "../../etc"]}
    assert sk.clean_refs(raw) == {"params": ["ok_name"], "datums": [], "curves": [],
                                  "envelope": False, "parts": ["case"]}


async def test_unknown_remove_is_invalid(world):
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 100.0})
    with pytest.raises(MisesError, match="no skeleton names"):
        await skeleton_set.handler(w.ctx, SkeletonSetArgs(project_id=w.project, remove=["nope"]))


CASE = '''VARS = {"wall": 2.0}

def build(v):
    L, w = skeleton("case_length"), v["wall"]
    return Box(L, 40, 30) - Box(L - 2 * w, 40 - 2 * w, 30 - 2 * w)
'''

INSERT = '''VARS = {}

def build(v):
    size = inner_volume("case").bounding_box().size
    return Box(size.X - 4, size.Y - 4, 5)
'''


async def test_parts_reading_a_regenerated_part_follow_it(world):
    """inner_volume("case") is a recorded reference too: when the skeleton regenerates the case,
    the insert sized from its cavity is regenerated after it."""
    w = world
    await _set(w.ctx, w.project, params={"case_length": 80.0})
    assert (await mirror_part_create.handler(w.ctx, CreateArgs(part_id="case", script=CASE))).ok
    r = await mirror_part_create.handler(w.ctx, CreateArgs(part_id="insert", script=INSERT))
    assert r.ok, r.summary
    assert r.data["bbox"]["size"][0] == pytest.approx(72.0)
    assert sk.read_refs(w.root / "insert")["parts"] == ["case"]
    r = await _set(w.ctx, w.project, params={"case_length": 100.0})
    assert [x["part_id"] for x in r.data["regenerated"]] == ["case", "insert"]
    m = await mirror_measure.handler(w.ctx, PartArgs(part_id="insert"))
    assert m.data["bbox"]["size"][0] == pytest.approx(92.0)


async def test_an_explicit_skeleton_file_reaches_a_scratch_build(world, tmp_path):
    """Study variants build a scratch copy elsewhere: run_build(skeleton_file=) hands them the
    live part's staged skeleton (engineering/tools.py does this)."""
    from mises.cad.runner import run_build
    w = world
    await _set(w.ctx, w.project, params={"wing_span": 64.0})
    await mirror_part_create.handler(w.ctx, CreateArgs(part_id="wing", script=WING))
    scratch = tmp_path / "scratch" / "parts" / "wing"
    scratch.mkdir(parents=True)
    (scratch / "part.py").write_text(WING)
    res = await run_build(scratch, {"chord": 10.0},
                          skeleton_file=sk.staged_file(w.root / "wing"))
    assert res.ok, res.error
    assert res.metrics["bbox"]["size"][:2] == pytest.approx([64.0, 10.0])
    assert res.skeleton_refs["params"] == ["wing_span"]
