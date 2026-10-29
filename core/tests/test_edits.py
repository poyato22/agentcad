"""C6 unit tests for mises.edits: versions, the user-edit log, op-block generation."""
import pytest

from mises import edits
from mises.types import MisesError

METRICS = {"bbox": {"size": [40, 20, 10]},
           "face_list": [{"id": "f0", "type": "plane", "center": [0, 0, 5], "normal": [0, 0, 1]},
                         {"id": "f1", "type": "plane", "center": [0, 0, -5], "normal": [0, 0, -1]}],
           "edge_list": [{"id": "e0", "type": "line", "center": [20, 0, 5], "direction": [0, 1, 0],
                          "length": 20.0},
                         {"id": "e1", "type": "line", "center": [0, 10, 5], "direction": [1, 0, 0],
                          "length": 40.0}]}

SRC = "from build123d import *\ndef build(v):\n    return Box(40, 20, 10)\n"


@pytest.fixture
def part(tmp_path):
    d = tmp_path / "home" / "parts" / "bracket"
    d.mkdir(parents=True)
    return d


def test_version_round_trip(part):
    assert edits.read_version(part) == 0
    edits.write_version(part, 3)
    assert edits.read_version(part) == 3


def test_version_lives_outside_the_sandbox_write_dir(part):
    edits.write_version(part, 2)
    vp = edits.version_path(part)
    assert not vp.is_relative_to(part.resolve())         # a build cannot rewrite it
    assert vp == part.resolve().parent / ".mises-meta" / "bracket" / "version.json"
    assert not [p for p in vp.parent.iterdir() if p.name.startswith(".mises-tmp")]


def test_old_version_files_are_migrated_once(part):
    old = part.resolve().parent.parent / "part_versions"
    old.mkdir()
    (old / "bracket.json").write_text('{"version": 7}')
    assert edits.read_version(part) == 7
    assert not (old / "bracket.json").exists() and edits.version_path(part).is_file()
    other = part.parent / "arm"
    other.mkdir()
    (other / "version.json").write_text('{"version": 4}')        # layout before that
    assert edits.read_version(other) == 4 and not (other / "version.json").exists()


def test_version_file_link_is_not_followed(part, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    md = edits.meta_dir(part)
    md.mkdir(parents=True)
    (md / "version.json").symlink_to(outside)
    assert edits.read_version(part) == 0                  # a link is never read through
    edits.write_version(part, 3)
    assert outside.read_text() == "keep me" and edits.read_version(part) == 3


def test_check_base_version(part):
    edits.write_version(part, 5)
    assert edits.check_base_version(part, 5) is None
    assert edits.check_base_version(part, None) is None
    assert edits.check_base_version(part, 4) == 5


def test_user_edit_log_recent(tmp_path):
    for i in range(1, 15):
        edits.log_user_edit(tmp_path, "bracket", version=i, summary=f"edit {i}", run_id="ui")
    recent = edits.recent_user_edits(tmp_path, limit=10)
    assert len(recent) == 10
    assert recent[-1]["summary"] == "edit 14" and recent[0]["summary"] == "edit 5"
    assert all(r["by"] == "user" for r in recent)


def test_recent_user_edits_empty(tmp_path):
    assert edits.recent_user_edits(tmp_path) == []


def test_recent_user_edits_filtered_by_part_and_build(tmp_path):
    edits.log_user_edit(tmp_path, "bracket", version=1, summary="a", run_id="ui", project_id="p1")
    edits.log_user_edit(tmp_path, "arm", version=1, summary="b", run_id="ui", project_id="p1")
    edits.log_user_edit(tmp_path, "bracket", version=2, summary="c", run_id="ui", project_id="p2")
    edits.log_user_edit(tmp_path, "bracket", version=3, summary="d", run_id="ui")  # pre-builds
    assert [r["summary"] for r in edits.recent_user_edits(tmp_path, part_id="bracket")] == \
        ["a", "c", "d"]
    assert [r["summary"] for r in edits.recent_user_edits(tmp_path, project_id="p1")] == \
        ["a", "b", "d"]


def test_hole_block_resolves_its_face_at_regeneration():
    out = edits.build_op_block(SRC, "hole", {"face_id": "f0"},
                               {"diameter": 6.0, "through": True}, METRICS)
    assert out.count(edits.OP_MARK) == 1 and "mises-op hole" in out
    assert "Cylinder(radius=3.0" in out
    # no baked coordinates: the face is re-found by its signature, labelled with the op
    assert "_mises_face(_p, {" in out and "'mises-op 0 hole'" in out
    assert "'normal': [0, 0, 1]" in out
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
