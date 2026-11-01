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


def _clear(boxes, gap=5.0 - 1e-6):
    items = list(boxes.items())
    for i, (na, (alo, ahi)) in enumerate(items):
        for nb, (blo, bhi) in items[i + 1:]:
            assert not all(alo[k] < bhi[k] + gap and ahi[k] > blo[k] - gap for k in range(3)), \
                (na, nb)


def test_next_part_goes_next_to_the_assembly_without_overlap(env):
    store, root = env
    _part(root, "base", [-10, -10, 0], [10, 10, 5])
    _part(root, "arm", [-30, -2, -1], [30, 2, 1])
    auto_place(store, root, "p", "base")
    inst = auto_place(store, root, "p", "arm")
    assert inst is not None and inst.fixed is False
    boxes = _box(store, root, "p")
    _clear(boxes)
    (blo, bhi), (alo, ahi) = boxes["base"], boxes["arm"]
    assert alo[2] == pytest.approx(blo[2])                   # on the same floor
    # right next to the base: one gap away on some side, not a box-share further out
    gap = max(alo[0] - bhi[0], blo[0] - ahi[0], alo[1] - bhi[1], blo[1] - ahi[1])
    assert 5 - 1e-6 <= gap <= 10
    _part(root, "pin", [0, 0, 0], [4, 4, 4])
    auto_place(store, root, "p", "pin")
    _clear(_box(store, root, "p"))


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


def test_a_write_landing_during_the_plan_is_seen_and_the_plan_redone(env, monkeypatch):
    """Re-review N3: the plan runs off the lock; a change meanwhile sends it round again."""
    import mises.assembly.autoplace as ap
    store, root = env
    _part(root, "base", [0, 0, 0], [10, 10, 10])
    _part(root, "arm", [0, 0, 0], [5, 5, 5])
    auto_place(store, root, "p", "base")
    real = ap._plan
    calls = []

    def racing(*a):
        calls.append(1)
        if len(calls) == 1:   # another writer adds a part while the first plan is worked out
            store.add_instance("p", Instance(part_id="base", name="spare", transform=[[1, 0, 0, 40], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
        return real(*a)
    monkeypatch.setattr(ap, "_plan", racing)
    inst = auto_place(store, root, "p", "arm")
    assert len(calls) == 2
    _clear(_box(store, root, "p"))                              # planned against the new state


# ---- FWC I3 (E33): near related parts or the origin cluster, never an ever-growing +X stack
def _bboxes(root, asm):
    out = {}
    for i in asm.instances:
        bb = json.loads((root / i.part_id / "metrics.json").read_text())["bbox"]
        out[i.part_id] = {"min": bb["min"], "max": bb["max"]}
    return out


def test_many_parts_stay_in_a_compact_cluster_round_the_origin(env):
    from mises.assembly.health import health
    store, root = env
    _part(root, "base", [-20, -20, 0], [20, 20, 10])
    auto_place(store, root, "p", "base")
    for k in range(12):
        _part(root, f"p{k}", [0, 0, 0], [10, 10, 10])
        auto_place(store, root, "p", f"p{k}")
    boxes = _box(store, root, "p")
    _clear(boxes)
    # 12 parts of 10 mm in a +X row would reach past x = 200; the cluster stays round the base
    assert max(max(b[1][0], -b[0][0], b[1][1], -b[0][1]) for b in boxes.values()) < 70
    asm = store.get("p")
    rep = health(asm.instances, {i.id: i.transform for i in asm.instances}, _bboxes(root, asm))
    assert rep["stray"] == [] and rep["overlaps"] == []


def test_a_child_lands_by_its_parent_not_by_the_cluster(env):
    store, root = env
    _part(root, "base", [-10, -10, 0], [10, 10, 10])
    _part(root, "leg", [-5, -5, 0], [5, 5, 40])
    _part(root, "foot", [0, 0, 0], [8, 8, 4])
    auto_place(store, root, "p", "base")
    far = [[1, 0, 0, 300], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    store.add_instance("p", Instance(part_id="leg", name="leg", transform=far))
    auto_place(store, root, "p", "foot", related=("leg",))
    boxes = _box(store, root, "p")
    _clear(boxes)
    lc = [(boxes["leg"][0][a] + boxes["leg"][1][a]) / 2 for a in range(2)]
    fc = [(boxes["foot"][0][a] + boxes["foot"][1][a]) / 2 for a in range(2)]
    assert ((lc[0] - fc[0]) ** 2 + (lc[1] - fc[1]) ** 2) ** 0.5 < 20   # beside the leg
    assert boxes["foot"][0][2] == pytest.approx(boxes["leg"][0][2])     # on the leg's floor


def test_related_parts_are_parent_siblings_and_children():
    from types import SimpleNamespace as N

    from mises.assembly.autoplace import related_parts
    rows = [N(id="body", parent_id=None, project_id="p"),
            N(id="leg", parent_id="body", project_id="p"),
            N(id="arm", parent_id="body", project_id="p"),
            N(id="foot", parent_id="leg", project_id="p"),
            N(id="other", parent_id=None, project_id="p")]
    store = N(get_part=lambda pid: next((r for r in rows if r.id == pid), None),
              list_part_rows=lambda project_id=None: rows)
    assert set(related_parts(store, "leg")) == {"body", "arm", "foot"}
    assert related_parts(store, "other") == ()
    assert related_parts(store, "missing") == ()
