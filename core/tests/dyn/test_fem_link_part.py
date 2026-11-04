"""DOG demo: "check part stresses" studied the two knee SERVOS (SF 314 / 336), because a link's
part was the heaviest instance of its rigid body, and the 75 g servo outweighs the 36 g thigh
it is bolted into. The structural member is the instance that carries the link's moving
joints; the heaviest of those is studied."""
from types import SimpleNamespace as NS

from mises.dyn import tools as dt


def _asm():
    j = lambda jid, kind, a, b: NS(id=jid, kind=kind, a=NS(instance_id=a),  # noqa: E731
                                   b=NS(instance_id=b))
    return NS(joints=[j("hip", "revolute", "bracket", "thigh"),
                      j("knee", "revolute", "thigh", "shank"),
                      j("mount", "fixed", "thigh", "servo"),
                      j("cover", "fixed", "thigh", "cover")])


def test_the_link_part_is_the_member_carrying_its_moving_joints():
    mass = {"thigh": 36.0, "servo": 75.0, "cover": 9.0}
    assert dt._link_member(["thigh", "servo", "cover"], _asm(), mass.get) == "thigh"


def test_a_body_without_moving_joints_on_any_member_falls_back_to_the_heaviest():
    mass = {"a": 5.0, "b": 9.0}
    assert dt._link_member(["a", "b"], NS(joints=[]), mass.get) == "b"
