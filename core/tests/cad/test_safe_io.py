"""C6 fix round 2: host-side part-dir I/O never follows a link a build left, and op targets are
re-found strictly (no silent retarget)."""
import os

import pytest

from mises.cad import safe_io
from mises.cad import worker


@pytest.fixture
def part(tmp_path):
    d = tmp_path / "home" / "parts" / "bracket"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def outside(tmp_path):
    f = tmp_path / "outside.txt"
    f.write_text("precious")
    return f


def test_write_replaces_a_symlink_instead_of_writing_through_it(part, outside):
    (part / "part.py").symlink_to(outside)
    safe_io.write_text(part, "part.py", "new")
    assert outside.read_text() == "precious"
    assert not (part / "part.py").is_symlink() and (part / "part.py").read_text() == "new"


def test_write_replaces_a_hardlink_instead_of_writing_through_it(part, outside):
    os.link(outside, part / "vars.json")
    safe_io.write_text(part, "vars.json", "{}")
    assert outside.read_text() == "precious"


def test_read_refuses_links(part, outside):
    (part / "part.py").symlink_to(outside)
    with pytest.raises(safe_io.UnsafePath):
        safe_io.read_text(part, "part.py")
    os.link(outside, part / "metrics.json")
    with pytest.raises(safe_io.UnsafePath):
        safe_io.read_bytes(part, "metrics.json")
    assert safe_io.read_bytes(part, "missing.json") is None


def test_part_dir_that_is_a_link_is_refused(tmp_path, outside):
    parts = tmp_path / "home" / "parts"
    parts.mkdir(parents=True)
    (parts / "evil").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(safe_io.UnsafePath):
        safe_io.write_text(parts / "evil", "outside.txt", "x")
    assert outside.read_text() == "precious"


def test_scrub_removes_links_but_not_their_targets(part, outside):
    (part / "model.stl").symlink_to(outside)
    (part / "sims").symlink_to(outside.parent, target_is_directory=True)
    (part / "tmp").mkdir()
    os.link(outside, part / "tmp" / "x.json")
    (part / "metrics.json").write_text("{}")
    os.mkfifo(part / "pipe")
    bad = safe_io.scrub(part)
    assert bad == ["model.stl", "pipe", "sims", "tmp/x.json"]
    assert outside.read_text() == "precious" and (part / "metrics.json").exists()
    assert safe_io.scrub(part) == []


# ---- strict re-match (worker helpers, run in-process)

def _sig_face(f):
    from build123d import CenterOf
    c = f.center(CenterOf.MASS)
    return {"type": worker._geom(f), "normal": worker._xyz(f.normal_at(c)),
            "center": worker._xyz(c), "area": float(f.area)}


def _sig_edge(shape, e):
    adj = worker._edge_adjacency(shape)
    return {"type": worker._geom(e), "direction": worker._canon(worker._xyz(e.tangent_at(0.5))),
            "center": worker._xyz(e.position_at(0.5)), "length": float(e.length),
            "adjacent": adj[e]}


def test_face_far_away_or_much_bigger_is_not_a_match():
    from build123d import Axis, Box, Pos
    boss = Box(40, 20, 10) + Pos(10, 0, 7.5) * Box(10, 10, 5)
    sig = _sig_face(boss.faces().sort_by(Axis.Z)[-1])              # the 10x10 boss top
    plain = Box(40, 20, 10)                                        # boss removed
    with pytest.raises(ValueError, match="mises-op 0 hole: target face not found"):
        worker._mises_face(plain, sig, "mises-op 0 hole")


def test_two_equal_candidates_are_ambiguous():
    from build123d import Box, Pos
    two = Pos(-6, 0, 0) * Box(10, 10, 10) + Pos(6, 0, 0) * Box(10, 10, 10)
    sig = {"type": "plane", "normal": [0, 0, 1], "center": [0, 0, 5], "area": 100.0}
    with pytest.raises(ValueError, match="ambiguous"):
        worker._mises_face(two, sig, "mises-op 1 shell")


def test_adjacent_normals_tell_parallel_edges_apart():
    from build123d import Box
    box = Box(40, 20, 10)
    bottom_front = next(e for e in box.edges()
                        if worker._xyz(e.position_at(0.5)) == pytest.approx([0, -10, -5]))
    sig = _sig_edge(box, bottom_front)
    taller = Box(40, 20, 30)                                    # every X edge moved 10 mm
    found = worker._mises_edge(taller, sig, "mises-op 0 fillet")
    assert worker._xyz(found.position_at(0.5)) == pytest.approx([0, -10, -15])


def test_two_signatures_resolving_to_one_edge_fail():
    from build123d import Box
    box = Box(40, 20, 10)
    sig = _sig_edge(box, box.edges()[0])
    with pytest.raises(ValueError, match="same edge"):
        worker._mises_edges(box, [sig, dict(sig)], "mises-op 0 chamfer")


def test_pick_point_must_stay_on_the_face():
    from build123d import Axis, Box
    top = Box(40, 20, 10).faces().sort_by(Axis.Z)[-1]
    worker._mises_on_face(top, (15, 5, 5), "mises-op 0 hole")
    with pytest.raises(ValueError, match="no longer on its target face"):
        worker._mises_on_face(top, (30, 0, 5), "mises-op 0 hole")
