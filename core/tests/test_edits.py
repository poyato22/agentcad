"""C6 unit tests for mises.edits: versions, the user-edit log, op-block generation."""
import pytest

from mises import edits
from mises.types import MisesError

METRICS = {"bbox": {"size": [40, 20, 10]},
           "face_list": [{"id": "f0", "type": "plane", "center": [0, 0, 5], "normal": [0, 0, 1]},
                         {"id": "f1", "type": "plane", "center": [0, 0, -5], "normal": [0, 0, -1]}],
           "edge_list": [{"id": "e0", "center": [20, 0, 5]}, {"id": "e1", "center": [0, 10, 5]}]}

SRC = "from build123d import *\ndef build(v):\n    return Box(40, 20, 10)\n"


def test_version_round_trip(tmp_path):
    assert edits.read_version(tmp_path) == 0
    edits.write_version(tmp_path, 3)
    assert edits.read_version(tmp_path) == 3


def test_check_base_version(tmp_path):
    edits.write_version(tmp_path, 5)
    assert edits.check_base_version(tmp_path, 5) is None
    assert edits.check_base_version(tmp_path, None) is None
    assert edits.check_base_version(tmp_path, 4) == 5


def test_user_edit_log_recent(tmp_path):
    for i in range(1, 15):
        edits.log_user_edit(tmp_path, "bracket", version=i, summary=f"edit {i}", run_id="ui")
    recent = edits.recent_user_edits(tmp_path, limit=10)
    assert len(recent) == 10
    assert recent[-1]["summary"] == "edit 14" and recent[0]["summary"] == "edit 5"
    assert all(r["by"] == "user" for r in recent)


def test_recent_user_edits_empty(tmp_path):
    assert edits.recent_user_edits(tmp_path) == []


def test_hole_block_has_concrete_geometry():
    out = edits.build_op_block(SRC, "hole", {"face_id": "f0"},
                               {"diameter": 6.0, "through": True}, METRICS)
    assert out.count(edits.OP_MARK) == 1 and "mises-op hole" in out
    assert "Cylinder(radius=3.0" in out
    assert "Plane(origin=(0, 0, 5), z_dir=(0, 0, 1))" in out
    # a second op nests on top of the first
    out2 = edits.build_op_block(out, "fillet", {"edge_ids": ["e0"]}, {"radius": 2.0}, METRICS)
    assert out2.count(edits.OP_MARK) == 2
    assert "_mises_prev_1" in out2


def test_hole_depth_vs_through():
    thru = edits.build_op_block(SRC, "hole", {"face_id": "f0"},
                                {"diameter": 4.0, "through": True}, METRICS)
    assert "Align.CENTER))" in thru
    depth = edits.build_op_block(SRC, "hole", {"face_id": "f0"},
                                 {"diameter": 4.0, "through": False, "depth": 3.0}, METRICS)
    assert "height=3.0" in depth and "Align.MAX))" in depth


def test_counterbore_requires_larger_diameter():
    with pytest.raises(MisesError):
        edits.build_op_block(SRC, "hole", {"face_id": "f0"},
                             {"diameter": 6.0, "counterbore_d": 4.0, "counterbore_depth": 2.0},
                             METRICS)


def test_shell_and_extrude_blocks():
    sh = edits.build_op_block(SRC, "shell", {"face_ids": ["f0"]}, {"thickness": 1.5}, METRICS)
    assert "offset(_p, amount=-1.5" in sh
    ex = edits.build_op_block(SRC, "extrude", {"face_id": "f0"},
                              {"profile": [[-5, -5], [5, -5], [5, 5], [-5, 5]], "distance": 4.0},
                              METRICS)
    assert "extrude(_prof, amount=4.0)" in ex and "_p + _m.extrude" in ex


def test_unknown_face_and_edge_rejected():
    with pytest.raises(MisesError):
        edits.build_op_block(SRC, "hole", {"face_id": "fX"}, {"diameter": 4.0}, METRICS)
    with pytest.raises(MisesError):
        edits.build_op_block(SRC, "fillet", {"edge_ids": ["eX"]}, {"radius": 1.0}, METRICS)


def test_bad_op_and_params_rejected():
    with pytest.raises(MisesError):
        edits.build_op_block(SRC, "bogus", {}, {}, METRICS)
    with pytest.raises(MisesError):
        edits.build_op_block(SRC, "hole", {"face_id": "f0"}, {"diameter": -1.0}, METRICS)
