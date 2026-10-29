"""p5-int wave C: host-side reads of part dirs (sims, assembly, design-study variants) go
through C6's safe_io, so a link a hostile build leaves is never followed."""
import json
import os

import pytest

from mises.assembly.geometry import resolve_face
from mises.sim import studies
from mises.types import MisesError


def test_sim_record_behind_a_link_is_not_read(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"part_id": "p", "leak": True}))
    sim_dir = tmp_path / "parts" / "p" / "sims" / "sim_aaaaaaaaaaaa"
    sim_dir.mkdir(parents=True)
    os.symlink(secret, sim_dir / "result.json")
    with pytest.raises(MisesError):
        studies.read_record(sim_dir, tmp_path / "parts")


def test_write_record_replaces_a_planted_link_instead_of_writing_through(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    sim_dir = tmp_path / "parts" / "p" / "sims" / "sim_bbbbbbbbbbbb"
    sim_dir.mkdir(parents=True)
    os.symlink(outside, sim_dir / "result.json")
    studies.write_record(sim_dir, {"sim_id": "sim_bbbbbbbbbbbb"})
    assert outside.read_text() == "keep"
    assert not (sim_dir / "result.json").is_symlink()


def test_write_record_refuses_a_linked_sims_dir(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    part = tmp_path / "parts" / "p"
    part.mkdir(parents=True)
    os.symlink(elsewhere, part / "sims")
    with pytest.raises(MisesError):
        studies.write_record(part / "sims" / "sim_cccccccccccc", {"sim_id": "x"})
    assert list(elsewhere.iterdir()) == []


def test_assembly_face_frame_never_follows_a_metrics_link(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"face_list": [{"id": "f0", "center": [0, 0, 0],
                                                 "normal": [0, 0, 1]}]}))
    part = tmp_path / "parts" / "bracket"
    part.mkdir(parents=True)
    os.symlink(secret, part / "metrics.json")
    with pytest.raises(MisesError):
        resolve_face(tmp_path / "parts", "bracket", "f0")
