import pytest

from mises.assembly.model import AssemblyStore, Frame, Instance, Joint, JointRef


@pytest.fixture
def store(tmp_path):
    return AssemblyStore(tmp_path / "assembly.db")


def _ref(iid):
    return JointRef(instance_id=iid, frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1]))


def test_add_and_get_scoped_by_project(store):
    a = store.add_instance("proj1", Instance(part_id="p", name="A", fixed=True))
    store.add_instance("proj2", Instance(part_id="p", name="other"))
    asm = store.get("proj1")
    assert [i.name for i in asm.instances] == ["A"]
    assert asm.instances[0].id == a.id and asm.instances[0].fixed is True
    assert [i.name for i in store.get("proj2").instances] == ["other"]


def test_update_instance(store):
    inst = store.add_instance("p", Instance(part_id="p", name="A"))
    store.update_instance("p", inst.id, name="renamed", fixed=True)
    got = store.get_instance("p", inst.id)
    assert got.name == "renamed" and got.fixed is True


def test_update_instance_rejects_unknown_field(store):
    inst = store.add_instance("p", Instance(part_id="p", name="A"))
    with pytest.raises(ValueError, match="cannot update"):
        store.update_instance("p", inst.id, bogus=1)


def test_delete_instance_cascades_to_joints(store):
    a = store.add_instance("p", Instance(part_id="p", name="A"))
    b = store.add_instance("p", Instance(part_id="p", name="B"))
    j = store.add_joint("p", Joint(kind="revolute", a=_ref(a.id), b=_ref(b.id)))
    removed = store.delete_instance("p", a.id)
    assert removed == [j.id]
    assert store.get("p").joints == []
    assert [i.id for i in store.get("p").instances] == [b.id]


def test_add_joint_requires_known_instances(store):
    a = store.add_instance("p", Instance(part_id="p", name="A"))
    with pytest.raises(KeyError):
        store.add_joint("p", Joint(kind="fixed", a=_ref(a.id), b=_ref("nope")))


def test_update_joint_value_and_limits(store):
    a = store.add_instance("p", Instance(part_id="p", name="A"))
    b = store.add_instance("p", Instance(part_id="p", name="B"))
    j = store.add_joint("p", Joint(kind="slider", a=_ref(a.id), b=_ref(b.id), value=0.0))
    store.update_joint("p", j.id, value=12.0, limits=[0.0, 20.0])
    got = store.get_joint("p", j.id)
    assert got.value == 12.0 and got.limits == [0.0, 20.0]


def test_delete_joint_missing_raises(store):
    with pytest.raises(KeyError):
        store.delete_joint("p", "ghost")


def test_order_is_stable(store):
    ids = [store.add_instance("p", Instance(part_id="p", name=f"n{i}")).id for i in range(5)]
    assert [i.id for i in store.get("p").instances] == ids
