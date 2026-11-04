"""dyn_to_fem's face picking on a gimbal ring (the TVC demo build): a ring's own walls are
centred on every axis through its centre, so the pivot bores must be told apart by their
axis, and a pin through two bores is held / loaded on both."""
import numpy as np

from mises.dyn.fem_bridge import load_case, nearest_face

# the outer gimbal ring's cylinders as its metrics.json lists them (tvc_gimbal, 62 mm bore)
RING = {"face_list": [
    {"id": "f0", "type": "cylinder", "center": [0.0, 0.0, 0.0], "normal": [0.3662, 0.9305, 0.0],
     "radius": 21.49},
    {"id": "f1", "type": "cylinder", "center": [19.956, 0.0, 0.0], "normal": None,
     "radius": 1.65},
    {"id": "f4", "type": "cylinder", "center": [-19.956, 0.0, 0.0],
     "normal": [0.0, 0.2647, 0.9643], "radius": 1.65},
    {"id": "f5", "type": "cylinder", "center": [0.0, -19.956, 0.0],
     "normal": [-0.4496, 0.0, -0.8932], "radius": 1.65},
    {"id": "f6", "type": "cylinder", "center": [0.0, 19.956, 0.0],
     "normal": [0.4496, 0.0, -0.8932], "radius": 1.65},
    {"id": "f7", "type": "cylinder", "center": [0.0, 0.0, 0.0], "normal": [-0.4743, -0.8804, 0.0],
     "radius": 18.49},
    {"id": "f17", "type": "cylinder", "center": [-10.0, 19.99, 11.0], "normal": None,
     "radius": 1.15}]}


class _Identity:
    def point(self, p_m):
        return np.array(p_m, dtype=float) * 1000.0

    def vec(self, v):
        return np.array(v, dtype=float)


def test_the_pitch_pivot_is_its_two_pin_bores_not_the_ring_wall():
    got = nearest_face(RING, [0, 0, 0], [1, 0, 0])
    assert got["kind"] == "bore" and {got["face"], got["pair"]} == {"f1", "f4"}
    got = nearest_face(RING, [0, 0, 0], [0, 1, 0], avoid={"f1", "f4"})
    assert {got["face"], got["pair"]} == {"f5", "f6"}


def test_a_ring_held_at_pitch_and_loaded_at_yaw_uses_four_distinct_bores():
    link = {"anchor_m": [0, 0, 0], "axis": [1, 0, 0], "children": [
        {"joint": "yaw", "anchor_m": [0, 0, 0], "axis": [0, 1, 0], "force_n": [0, 0, 122.6],
         "moment_nm": [0, 0, 0]}]}
    out = load_case(link, _Identity(), RING, label="outer ring @ peak thrust")
    case = out["case"]
    assert sorted(s["face"] for s in case["supports"]) == ["f1", "f4"]
    loaded = {ld["face"]: ld["newtons"] for ld in case["loads"]}
    assert set(loaded) == {"f5", "f6"}
    assert sum(loaded.values()) == 122.6
