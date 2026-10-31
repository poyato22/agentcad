"""FW-core I-1: deleting a part, a run, a build or emptying the Trash must see B3 background
jobs. After B3 an agent's turn ends while its study keeps writing the part's files, so a delete
that ignores the job recreates a ghost part dir and Undo silently loses the geometry."""
import json

import pytest

from mises import jobs, projects
from mises.store import Store
from mises.types import MisesError


@pytest.fixture(autouse=True)
def _clean_jobs():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def _mk_part(home, store, part_id, project_id):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mass_g": 1.0}))
    (d / "model.stl").write_text("solid x\nendsolid x\n")
    (d / "part.py").write_text("VARS = {}\n")
    store.register_part(part_id, project_id)


def _running_job(run_id, *, build_id=None, part_id=None, kind="sim"):
    return jobs.register(jobs.Job(id=f"{kind}_x", kind=kind, run_id=run_id,
                                  build_id=build_id, part_id=part_id))


def test_delete_part_is_refused_while_a_background_study_runs(tmp_path):
    store = Store(tmp_path / "db")
    project = store.ensure_default_project().id
    _mk_part(tmp_path, store, "beam", project)
    _running_job("lead1", build_id=project, part_id="beam")

    with pytest.raises(projects.PartBusy) as e:
        projects.delete_part(store, tmp_path, "beam")
    assert "lead1" in e.value.run_ids
    # the files are untouched (not moved to the trash)
    assert (tmp_path / "parts" / "beam" / "model.stl").exists()

    # once the job ends the delete goes through
    jobs.reset_for_tests()
    out = projects.delete_part(store, tmp_path, "beam")
    assert out["deleted"] == "beam"


def test_restore_part_refuses_when_a_ghost_dir_is_in_the_way(tmp_path):
    store = Store(tmp_path / "db")
    project = store.ensure_default_project().id
    _mk_part(tmp_path, store, "beam", project)
    out = projects.delete_part(store, tmp_path, "beam")       # no job: clean delete
    assert not (tmp_path / "parts" / "beam").exists()
    # a study that was still running recreates the part dir after the delete
    (tmp_path / "parts" / "beam").mkdir(parents=True)
    (tmp_path / "parts" / "beam" / "result.json").write_text("{}")
    with pytest.raises(MisesError, match="already there"):
        projects.restore_part(store, tmp_path, "beam", out["undo"])


def test_empty_trash_is_refused_while_a_job_writes_a_trashed_build(tmp_path):
    store = Store(tmp_path / "db")
    project = store.create_project("Scrap").id
    _mk_part(tmp_path, store, "bracket", project)
    projects.trash_project(store, project)
    _running_job("lead2", build_id=project, part_id="bracket", kind="topopt")

    with pytest.raises(projects.TrashBusy) as e:
        projects.empty_trash(store, tmp_path, only=project)
    assert "lead2" in e.value.run_ids
    assert (tmp_path / "parts" / "bracket").exists()          # not purged under it

    jobs.reset_for_tests()
    assert projects.empty_trash(store, tmp_path, only=project)["purged"] == [project]


def test_delete_run_sees_its_own_background_job(tmp_path):
    store = Store(tmp_path / "db")
    project = store.ensure_default_project().id
    run = store.create_run("sim", "fake", "m", "study", project_id=project)
    store.update_run(run.id, status="done")
    run = store.get_run(run.id)
    assert projects.delete_reason(store, run) is None         # finished run, no job: deletable
    _running_job(run.id, build_id=project)
    reason = projects.delete_reason(store, run)
    assert reason is not None and reason[0] == "running"
