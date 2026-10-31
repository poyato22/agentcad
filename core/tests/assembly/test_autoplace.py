"""Plan 9 D10: a newly built part is placed into its build's assembly."""
import json

import pytest

from mises import edits
from mises.assembly.autoplace import auto_place
from mises.assembly.model import AssemblyStore, Instance
from mises.cad import skeleton


def _part(root, pid, lo, hi):
    d = root / pid
    d.mkdir(parents=True)
    size = [b - a for a, b in zip(lo, hi)]
    (d / "metrics.json").write_text(json.dumps({"bbox": {"min": lo, "max": hi, "size": size}}))


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "parts"
    root.mkdir()
    return AssemblyStore(tmp_path / "asm.db"), root


def _box(store, root, pid):
    """World box of every instance (pure translations here)."""
    out = {}
    for i in store.get(pid).instances:
        m = json.loads((root / i.part_id / "metrics.json").read_text())["bbox"]
        t = [i.transform[r][3] for r in range(3)]
        out[i.name] = ([a + d for a, d in zip(m["min"], t)], [b + d for b, d in zip(m["max"], t)])
    return out


def test_first_part_is_grounded_at_its_own_origin(env):
    store, root = env
    _part(root, "base", [-10, -10, 0], [10, 10, 5])
    inst = auto_place(store, root, "p", "base")
    assert inst is not None and inst.fixed is True
    assert [inst.transform[r][3] for r in range(3)] == [0, 0, 0]


def test_next_part_goes_beside_the_assembly_without_overlap(env):
    store, root = env
    _part(root, "base", [-10, -10, 0], [10, 10, 5])
    _part(root, "arm", [-30, -2, -1], [30, 2, 1])
    auto_place(store, root, "p", "base")
    inst = auto_place(store, root, "p", "arm")
    assert inst is not None and inst.fixed is False
    boxes = _box(store, root, "p")
    (blo, bhi), (alo, ahi) = boxes["base"], boxes["arm"]
    assert alo[0] >= bhi[0] + 5 - 1e-9                       # past +X with a gap
    assert (alo[1] + ahi[1]) / 2 == pytest.approx((blo[1] + bhi[1]) / 2)
    assert alo[2] == pytest.approx(blo[2])                   # on the same floor
    # a third one clears both
    _part(root, "pin", [0, 0, 0], [4, 4, 4])
    auto_place(store, root, "p", "pin")
    boxes = _box(store, root, "p")
    assert boxes["pin"][0][0] > boxes["arm"][1][0]


def test_a_placed_part_is_left_alone(env):
    store, root = env
    _part(root, "base", [0, 0, 0], [1, 1, 1])
    store.add_instance("p", Instance(part_id="base", name="mine", fixed=True))
    assert auto_place(store, root, "p", "base") is None
    assert len(store.get("p").instances) == 1


def test_names_never_collide(env):
    store, root = env
    _part(root, "base", [0, 0, 0], [1, 1, 1])
    _part(root, "arm", [0, 0, 0], [1, 1, 1])
    store.add_instance("p", Instance(part_id="base", name="Arm", fixed=True))
    assert auto_place(store, root, "p", "arm", name="Arm").name == "Arm 2"


def test_a_skeleton_slot_named_after_the_part_places_it(env):
    store, root = env
    _part(root, "base", [0, 0, 0], [10, 10, 10])
    _part(root, "motor", [0, 0, 0], [2, 2, 2])
    auto_place(store, root, "p", "base")
    skeleton.save(root, "p", skeleton.SkeletonData(datums={
        "slot_motor": {"kind": "point", "origin": [3, 4, 5]}}), 1)
    inst = auto_place(store, root, "p", "motor")
    assert [inst.transform[r][3] for r in range(3)] == [3, 4, 5]


def test_a_part_built_against_the_skeleton_stays_in_the_layout_frame(env):
    store, root = env
    _part(root, "base", [0, 0, 0], [10, 10, 10])
    _part(root, "wing", [50, 0, 0], [80, 5, 1])
    auto_place(store, root, "p", "base")
    md = edits.meta_dir(root / "wing")
    md.mkdir(parents=True)
    (md / skeleton.REFS_NAME).write_text(json.dumps({"params": ["wing_span"]}))
    inst = auto_place(store, root, "p", "wing")
    assert [inst.transform[r][3] for r in range(3)] == [0, 0, 0]
