"""E33: the pure bbox health report (overlaps, stray, floating, extents)."""
from mises.assembly.health import health, world_aabb
from mises.assembly.model import Frame, Instance, Joint, JointRef


def _inst(name, x=0.0):
    return Instance(id=name, part_id="box", name=name,
                    transform=[[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


BOX = {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]}


def test_world_aabb_translates():
    lo, hi = world_aabb(BOX, [[1, 0, 0, 20], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    assert lo == [15, -5, -5] and hi == [25, 5, 5]


def test_overlap_detected():
    a, b = _inst("a", 0), _inst("b", 3)        # 10mm boxes 3mm apart: overlap
    joints = [Joint(kind="fixed", a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
                    b=JointRef(instance_id="b", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))]
    rep = health([a, b], {"a": a.transform, "b": b.transform}, {"box": BOX}, joints=joints)
    assert not rep["ok"] and len(rep["overlaps"]) == 1
    assert rep["overlaps"][0]["volume_mm3"] > 0


def test_no_overlap_when_far():
    a, b = _inst("a", 0), _inst("b", 40)
    joints = [Joint(kind="fixed", a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
                    b=JointRef(instance_id="b", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))]
    rep = health([a, b], {"a": a.transform, "b": b.transform}, {"box": BOX}, joints=joints)
    assert rep["overlaps"] == [] and rep["extents"]["size"][0] == 50


def test_stray_part_flagged():
    insts = [_inst("a", 0), _inst("b", 12), _inst("c", 24), _inst("far", 500)]
    tf = {i.id: i.transform for i in insts}
    joints = [Joint(kind="fixed",
                    a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
                    b=JointRef(instance_id=i.id, frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))
              for i in insts[1:]]
    rep = health(insts, tf, {"box": BOX}, joints=joints)
    assert [s["instance"] for s in rep["stray"]] == ["far"]


def test_floating_part_flagged():
    a, b = _inst("a", 0), _inst("b", 40)       # b has no joint to a
    rep = health([a, b], {"a": a.transform, "b": b.transform}, {"box": BOX}, joints=[])
    assert [f["instance"] for f in rep["floating"]] == ["b"]


def test_clean_assembly_is_ok():
    a, b = _inst("a", 0), _inst("b", 40)
    joints = [Joint(kind="fixed",
                    a=JointRef(instance_id="a", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])),
                    b=JointRef(instance_id="b", frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1])))]
    rep = health([a, b], {"a": a.transform, "b": b.transform}, {"box": BOX}, joints=joints)
    assert rep["ok"] and rep["summary"] == "assembly health: clean"
