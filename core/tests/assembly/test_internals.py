"""Plan 8 G3: internals — cavity, greedy packing with clearances, per-section checks."""
from types import SimpleNamespace

import pytest

from mises.assembly.internals import (InnerVolumeArgs, PackArgs, SectionArgs, _transform,
                                      inner_volume, internals_pack, register_layout_tools,
                                      section_check)
from mises.assembly.model import AssemblyStore, Instance
from mises.cad.layout_worker import _sep, pack_boxes
from mises.cad.tools import CreateArgs, mirror_part_create
from mises.events import EventBus
from mises.store import Store
from mises.tools.registry import ToolRegistry
from mises.types import SafetyClass

CASE = '''VARS = {"wall": 2.0}

def build(v):
    w = v["wall"]
    return Box(100, 60, 40) - Box(100 - 2 * w, 60 - 2 * w, 40 - 2 * w)
'''

OPEN_CASE = '''VARS = {}

def build(v):
    return Box(100, 60, 40) - Box(96, 56, 40).moved(Location((0, 0, 2)))
'''

BLOCK = 'VARS = {}\n\ndef build(v):\n    return Box(20, 20, 20)\n'

THREE = [{"name": "battery", "size": [40, 30, 20]}, {"name": "board", "size": [30, 20, 15]},
         {"name": "fan", "size": [20, 20, 10]}]


def _overlaps(placements, clearance):
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            if not _sep(a["min"], a["size"], b["min"], b["size"], clearance - 1e-6):
                return True
    return False


# ---- the pure packer

def test_pack_boxes_fits_three_with_clearance():
    r = pack_boxes([0, 0, 0], [96, 56, 36], THREE, 2.0)
    assert r["fits"] and len(r["placements"]) == 3 and r["misfits"] == []
    for p in r["placements"]:
        assert all(p["min"][i] >= 2 - 1e-9 and p["max"][i] <= [96, 56, 36][i] - 2 + 1e-9
                   for i in range(3))
    assert not _overlaps(r["placements"], 2.0)
    assert all(p["min"][2] == pytest.approx(2.0) for p in r["placements"])  # one floor layer


def test_pack_boxes_reports_misfit_when_too_big():
    big = [{"name": "battery", "size": [95, 30, 20]}, *THREE[1:]]
    r = pack_boxes([0, 0, 0], [96, 56, 36], big, 2.0)
    assert not r["fits"]
    assert [m["name"] for m in r["misfits"]] == ["battery"]
    assert "larger than the cavity" in r["misfits"][0]["reason"]
    # each fits alone, but not all together: "no room left"
    crowd = [{"name": n, "size": [50, 50, 30]} for n in ("cell_left", "cell_mid", "cell_right")]
    r = pack_boxes([0, 0, 0], [96, 56, 36], crowd, 2.0)
    assert len(r["placements"]) == 1 and len(r["misfits"]) == 2
    assert all("no room" in m["reason"] for m in r["misfits"])


def test_pack_boxes_keep_outs_rotation_and_grid():
    keep = [{"min": [0, 0, 0], "max": [50, 56, 36]}]          # the left half is reserved
    r = pack_boxes([0, 0, 0], [96, 56, 36], [{"name": "board", "size": [20, 30, 10]}], 2.0,
                   keep_outs=keep)
    assert r["placements"][0]["min"][0] >= 52 - 1e-9
    # only fits turned 90 degrees
    r = pack_boxes([0, 0, 0], [40, 80, 20], [{"name": "rail", "size": [70, 20, 10]}], 2.0)
    assert r["fits"] and r["placements"][0]["rotated"] and r["placements"][0]["size"][1] == 70
    r = pack_boxes([0, 0, 0], [40, 80, 20], [{"name": "rail", "size": [70, 20, 10]}], 2.0,
                   allow_rotate=False)
    assert not r["fits"]
    g = pack_boxes([0, 0, 0], [150, 60, 36], THREE, 2.0, strategy="grid")
    assert g["fits"] and not _overlaps(g["placements"], 2.0)


def test_transform_moves_the_part_bbox_onto_the_placement():
    def apply(m, p):
        return [sum(m[r][k] * (list(p) + [1])[k] for k in range(4)) for r in range(3)]
    p = {"min": [10, 20, 5], "bbox_min": [-5, -2, 0], "bbox_max": [5, 2, 3], "rotated": False}
    assert apply(_transform(p), [-5, -2, 0]) == pytest.approx([10, 20, 5])
    p["rotated"] = True
    corners = [apply(_transform(p), [x, y, z]) for x in (-5, 5) for y in (-2, 2) for z in (0, 3)]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    assert lo == pytest.approx([10, 20, 5]) and hi == pytest.approx([14, 30, 8])


def test_layout_tools_register_with_the_right_safety():
    reg = ToolRegistry()
    register_layout_tools(reg)
    names = {n: reg.get(n).safety for n in ("skeleton_get", "skeleton_set", "inner_volume",
                                             "internals_pack", "section_check")}
    assert names["skeleton_set"] == SafetyClass.LOCAL_MUTATE
    assert all(names[n] == SafetyClass.READ for n in ("skeleton_get", "inner_volume",
                                                       "internals_pack", "section_check"))


# ---- through the sandboxed worker

@pytest.fixture
def world(tmp_path):
    store = Store(":memory:")
    proj = store.create_project("Pi case")
    run = store.create_run("engineer", "fake", "m", "x", project_id=proj.id)
    asm = AssemblyStore(tmp_path / "assembly.db")
    (tmp_path / "parts").mkdir()
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=run.id, events=EventBus(),
                          policy=None, services={"assembly": asm}, call_id="c")
    return SimpleNamespace(ctx=ctx, project=proj.id, asm=asm)


async def _build(ctx, part_id, script):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id=part_id, script=script, place=False))
    assert r.ok, r.summary


@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_inner_volume_of_closed_and_open_cases(world):
    await _build(world.ctx, "case", CASE)
    r = await inner_volume.handler(world.ctx, InnerVolumeArgs(project_id=world.project,
                                                              part_id="case"))
    assert r.data["volume_mm3"] == pytest.approx(96 * 56 * 36, rel=1e-6)
    assert r.data["open_sides"] == [] and r.data["bbox"]["size"] == pytest.approx([96, 56, 36])
    await _build(world.ctx, "tray", OPEN_CASE)
    r = await inner_volume.handler(world.ctx, InnerVolumeArgs(project_id=world.project,
                                                              part_id="tray"))
    assert r.data["open_sides"] == ["z-max"]
    assert r.data["volume_mm3"] == pytest.approx(96 * 56 * 38, rel=1e-6)


POCKET_CASE = '''VARS = {}

def build(v):
    shell = Box(100, 60, 40) - Box(96, 56, 36)
    return shell - Box(20, 20, 1).moved(Location((30, 0, 19.5)))   # a label recess on top
'''

# a closed case whose inside is L-shaped: a solid block fills the -x/-y quarter, so the cavity's
# bounding box is the full 96 x 56 x 36 but its -x/-y corner is not free
L_CASE = '''VARS = {}

def build(v):
    shell = Box(100, 60, 40) - Box(96, 56, 36)
    return shell + Box(48, 28, 36).moved(Location((-24, -14, 0)))
'''


@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_cavity_is_the_largest_void_and_other_voids_are_listed(world):
    await _build(world.ctx, "label_case", POCKET_CASE)
    r = await inner_volume.handler(world.ctx, InnerVolumeArgs(project_id=world.project,
                                                              part_id="label_case"))
    assert r.data["volume_mm3"] == pytest.approx(96 * 56 * 36, rel=1e-6)
    assert r.data["open_sides"] == []
    assert [v["volume_mm3"] for v in r.data["other_voids"]] == [pytest.approx(400.0)]
    assert r.data["other_voids"][0]["open_sides"] == ["z-max"]
    r = await internals_pack.handler(world.ctx, PackArgs(
        project_id=world.project, case_part_id="label_case",
        components=[{"name": "board", "size": [30, 20, 10]}]))
    assert r.data["fits"] and r.data["placements"][0]["max"][2] <= 18 - 2 + 1e-6  # inside


@pytest.mark.slow
@pytest.mark.timeout(240)
async def test_pack_rejects_bbox_placements_the_real_cavity_does_not_allow(world):
    await _build(world.ctx, "l_case", L_CASE)
    comps = [{"name": "battery", "size": [40, 20, 20]}]
    boxed = pack_boxes([-48, -28, -18], [48, 28, 18], comps, 2.0)
    p = boxed["placements"][0]
    assert p["min"][0] < 0 and p["min"][1] < 0               # by bbox alone: into the block
    r = await internals_pack.handler(world.ctx, PackArgs(
        project_id=world.project, case_part_id="l_case", components=comps, clearance_mm=2.0))
    assert r.data["fits"], r.summary
    p = r.data["placements"][0]
    # clear of the block (x < 0 and y < 0) by the clearance
    assert p["min"][0] >= 2 - 1e-6 or p["min"][1] >= 2 - 1e-6


@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_pack_three_boxes_into_a_case_with_clearance(world):
    await _build(world.ctx, "case", CASE)
    r = await internals_pack.handler(world.ctx, PackArgs(
        project_id=world.project, case_part_id="case", components=THREE, clearance_mm=2.0))
    assert r.data["fits"], r.summary
    assert len(r.data["placements"]) == 3
    lo, hi = [-48, -28, -18], [48, 28, 18]                     # the cavity, centred on the origin
    for p in r.data["placements"]:
        assert all(p["min"][i] >= lo[i] + 2 - 1e-6 and p["max"][i] <= hi[i] - 2 + 1e-6
                   for i in range(3))
    assert not _overlaps(r.data["placements"], 2.0)
    too_big = [{"name": "battery", "size": [95, 30, 20]}, *THREE[1:]]
    r = await internals_pack.handler(world.ctx, PackArgs(
        project_id=world.project, case_part_id="case", components=too_big, clearance_mm=2.0))
    assert not r.data["fits"] and [m["name"] for m in r.data["misfits"]] == ["battery"]
    assert "misfit" in r.summary


@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_pack_built_parts_returns_transforms(world):
    await _build(world.ctx, "case", CASE)
    await _build(world.ctx, "block", BLOCK)
    r = await internals_pack.handler(world.ctx, PackArgs(
        project_id=world.project, case_part_id="case",
        components=[{"name": "block_left", "part_id": "block"},
                    {"name": "block_right", "part_id": "block"}]))
    assert r.data["fits"]
    for p in r.data["placements"]:
        t = p["transform"]
        # the block's bbox min (-10, -10, -10) lands on the placement's min corner
        moved = [t[i][3] - 10 * (t[i][0] + t[i][1] + t[i][2]) for i in range(3)]
        assert moved == pytest.approx(p["min"])


@pytest.mark.slow
@pytest.mark.timeout(180)
async def test_section_check_finds_a_deliberate_collision(world):
    await _build(world.ctx, "block", BLOCK)
    pid = world.project

    def at(x):
        return [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    world.asm.add_instance(pid, Instance(part_id="block", name="base", fixed=True))
    world.asm.add_instance(pid, Instance(part_id="block", name="intruder", fixed=True,
                                         transform=at(15)))      # overlaps base by 5 mm
    world.asm.add_instance(pid, Instance(part_id="block", name="neighbour", fixed=True,
                                         transform=at(42)))      # 7 mm from the intruder
    r = await section_check.handler(world.ctx, SectionArgs(
        project_id=pid, planes=[{"axis": "z", "offset": 0.0}], min_clearance_mm=10.0))
    plane = r.data["planes"][0]
    assert plane["interferences"] == [{"a": "base", "b": "intruder",
                                       "overlap_mm2": pytest.approx(100.0)}]
    assert plane["clearances"] == [{"a": "intruder", "b": "neighbour",
                                    "clearance_mm": pytest.approx(7.0)}]
    assert {s["instance"]: s["area_mm2"] for s in plane["sections"]} == {
        "base": pytest.approx(400), "intruder": pytest.approx(400),
        "neighbour": pytest.approx(400)}
    # a plane above everything sees nothing; the defaults are the three mid-planes
    r = await section_check.handler(world.ctx, SectionArgs(
        project_id=pid, planes=[{"axis": "z", "offset": 50.0}]))
    assert r.data["planes"][0]["interferences"] == []
    r = await section_check.handler(world.ctx, SectionArgs(project_id=pid))
    assert len(r.data["planes"]) == 3
    assert sum(len(p["interferences"]) for p in r.data["planes"]) >= 2
