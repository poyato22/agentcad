"""Plan 5 final fix I4: Empty Trash refuses while a trashed build has live work, and otherwise
purges everything the build owns (rows, assembly, catalog items, part files and meta, study
scratch), keeping part files another build's assembly still places."""
import json
import os
import time

import pytest

from mises import edits, projects
from mises.assembly.model import AssemblyStore, Instance
from mises.catalog import Catalog
from mises.store import Approval, Store


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def asm(tmp_path):
    return AssemblyStore(tmp_path / "assembly.db")


def _part(store, home, project_id, part_id, status="done"):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps({"length": 40.0}))
    run = store.create_run("designer", "fake", "m", "build", project_id=project_id)
    projects.register_built_part(store, run.id, part_id)
    edits.write_version(d, 3)
    store.update_run(run.id, status=status)
    return run


def _trashed(store, name="Drone"):
    p = store.create_project(name)
    store.update_project(p.id, trashed_at=time.time())
    return p.id


def test_purge_removes_concept_topopt_and_job_files(store, tmp_path):
    # FW-core M-5: Delete forever must also take the concept rows (the user's marks), the concept
    # images, the generative-design records and the build's background-job files.
    from mises.concept import ConceptStore
    concepts = ConceptStore(store)
    pid = _trashed(store)
    run = store.create_run("sim", "fake", "m", "x", project_id=pid)
    store.update_run(run.id, status="done")
    sk = concepts.add_sketch(pid, {"sketch_id": "sk_1", "version": 1})
    concepts.add_pitch(pid, {"pitch_id": "pt_1"})
    (tmp_path / "concepts" / "sk_1").mkdir(parents=True)
    (tmp_path / "concepts" / "sk_1" / "iso.png").write_text("png")
    (tmp_path / "concepts" / "pt_1" / "o0").mkdir(parents=True)
    (tmp_path / "topopt" / "to_abc123def456").mkdir(parents=True)
    (tmp_path / "topopt" / "to_abc123def456" / "record.json").write_text(
        json.dumps({"topopt_id": "to_abc123def456", "project_id": pid, "run_id": run.id}))
    (tmp_path / "jobs").mkdir(parents=True)
    (tmp_path / "jobs" / "sim_1.json").write_text(json.dumps({"id": "sim_1", "run_id": run.id}))

    out = projects.empty_trash(store, tmp_path, only=pid, concepts=concepts)
    assert out["purged"] == [pid]
    assert concepts.sketches(pid) == [] and concepts.pitches(pid) == []
    assert not (tmp_path / "concepts" / "sk_1").exists()
    assert not (tmp_path / "concepts" / "pt_1").exists()
    assert not (tmp_path / "topopt" / "to_abc123def456").exists()
    assert not (tmp_path / "jobs" / "sim_1.json").exists()
    assert sk["sketch_id"] == "sk_1"


@pytest.mark.parametrize("status", ["running", "queued"])
def test_refuses_while_a_run_is_live(store, tmp_path, status):
    pid = _trashed(store)
    run = _part(store, tmp_path, pid, "frame_plate", status=status)
    with pytest.raises(projects.TrashBusy) as e:
        projects.empty_trash(store, tmp_path)
    assert e.value.reason == "running" and e.value.run_ids == [run.id]
    assert e.value.body()["run_ids"] == [run.id]
    assert store.get_project(pid) is not None                     # nothing purged
    assert (tmp_path / "parts" / "frame_plate" / "vars.json").exists()


def test_refuses_while_an_approval_is_pending(store, tmp_path):
    pid = _trashed(store)
    run = _part(store, tmp_path, pid, "frame_plate")
    store.insert_approval(Approval(id="ap", run_id=run.id, action="x", payload={},
                                   payload_hash="h", status="pending", expires_at=1e12))
    with pytest.raises(projects.TrashBusy) as e:
        projects.empty_trash(store, tmp_path)
    assert e.value.reason == "pending_approval" and e.value.run_ids == [run.id]


def test_busy_hook_blocks_and_idle_ui_run_does_not(store, tmp_path):
    pid = _trashed(store)
    ui = store.create_run("you", "ui", "ui", "app", external=True, project_id=pid)  # "running"
    with pytest.raises(projects.TrashBusy) as e:   # a UI call is in flight on it
        projects.empty_trash(store, tmp_path, busy=lambda rid: rid == ui.id)
    assert e.value.run_ids == [ui.id]
    assert projects.empty_trash(store, tmp_path, busy=lambda rid: False)["purged"] == [pid]


def test_live_work_in_a_build_not_in_the_trash_does_not_block(store, tmp_path):
    live = store.create_project("Live")
    _part(store, tmp_path, live.id, "other_part", status="running")
    pid = _trashed(store)
    assert projects.empty_trash(store, tmp_path)["purged"] == [pid]


def test_purges_everything_the_build_owns(store, tmp_path, asm):
    pid = _trashed(store)
    keep = store.create_project("Keep")
    _part(store, tmp_path, pid, "frame_plate")
    _part(store, tmp_path, keep.id, "keep_plate")
    store.put_spec(pid, {"goal": "fly"})
    store.put_spec(keep.id, {"goal": "stay"})
    store.create_study("study_abc123", pid, {"id": "study_abc123"}, "done")
    (tmp_path / "studies" / "study_abc123" / "e1").mkdir(parents=True)
    cat = Catalog(store)
    cat.save({"id": "mine_motor", "name": "M", "category": "motor", "source": "url",
              "url": "https://x.example/m"}, pid)
    cat.save({"id": "everyone_motor", "name": "E", "category": "motor", "source": "url",
              "url": "https://x.example/e"}, None)
    asm.add_instance(pid, Instance(part_id="frame_plate", name="frame"))
    asm.add_instance(keep.id, Instance(part_id="keep_plate", name="keep"))
    legacy = tmp_path / edits.VERSIONS_DIR
    legacy.mkdir()
    (legacy / "frame_plate.json").write_text('{"version": 2}')

    assert projects.empty_trash(store, tmp_path, assembly=asm)["purged"] == [pid]

    assert store.get_part("frame_plate") is None and store.get_spec(pid) is None
    assert store.list_studies(pid) == [] and store.get_study("study_abc123") is None
    assert not (tmp_path / "studies" / "study_abc123").exists()
    ids = {i["id"] for i in cat.search("", limit=200) if i.get("source") == "url"}
    assert "mine_motor" not in ids and "everyone_motor" in ids
    assert asm.get(pid).instances == []
    assert not (tmp_path / "parts" / "frame_plate").exists()
    assert not edits.meta_dir(tmp_path / "parts" / "frame_plate").exists()
    assert not (legacy / "frame_plate.json").exists()
    # the other build is untouched
    assert store.get_part("keep_plate") is not None and store.get_spec(keep.id) == {"goal": "stay"}
    assert (tmp_path / "parts" / "keep_plate" / "vars.json").exists()
    assert edits.read_version(tmp_path / "parts" / "keep_plate") == 3
    assert len(asm.get(keep.id).instances) == 1


def test_purges_the_builds_references_and_drawing_imports(store, tmp_path):
    """Final review M6: reference images and imported drawings (the user's own files) of a
    purged build go with it; another build's, and unbuilt ones, stay."""
    pid = _trashed(store)
    keep = store.create_project("Keep")
    for sub, rec, name, owner in (("references", "meta.json", "ref_aa", pid),
                                  ("references", "meta.json", "ref_bb", keep.id),
                                  ("references", "meta.json", "ref_cc", None),
                                  ("imports", "import.json", "imp_aa", pid),
                                  ("imports", "import.json", "imp_bb", keep.id)):
        d = tmp_path / sub / name
        d.mkdir(parents=True)
        (d / rec).write_text(json.dumps({"project_id": owner}))
    projects.empty_trash(store, tmp_path)
    left = {p.name for sub in ("references", "imports") for p in (tmp_path / sub).iterdir()}
    assert left == {"ref_bb", "ref_cc", "imp_bb"}


async def test_deleting_a_run_removes_its_renders(tmp_path, monkeypatch):
    """Final review M6: GET /renders/{id} stops serving a deleted run's renders."""
    import mises.runtime as runtime_mod
    from mises.runtime import Runtime
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home")
    run = rt.store.create_run("designer", "fake", "m", "t")
    rt.store.update_run(run.id, status="done")
    other = "zz" + run.id[2:]
    d = rt.home / "renders"
    d.mkdir(parents=True)
    mine = [f"crit_{run.id[:8]}_0123456789_iso.png", f"rv_{run.id[:8]}_abcdef0123_front.png"]
    for n in (*mine, f"pv_{other[:8]}_0123456789_iso.png"):
        (d / n).write_bytes(b"png")
    assert await rt.delete_run(run.id) is None
    assert sorted(p.name for p in d.iterdir()) == [f"pv_{other[:8]}_0123456789_iso.png"]


def test_part_placed_by_another_build_keeps_its_files(store, tmp_path, asm):
    pid = _trashed(store)
    keep = store.create_project("Keep")
    _part(store, tmp_path, pid, "shared_plate")
    asm.add_instance(keep.id, Instance(part_id="shared_plate", name="borrowed"))
    projects.empty_trash(store, tmp_path, assembly=asm)
    assert store.get_part("shared_plate") is None
    assert (tmp_path / "parts" / "shared_plate" / "vars.json").exists()
    assert edits.read_version(tmp_path / "parts" / "shared_plate") == 3


def test_a_linked_part_dir_is_unlinked_not_followed(store, tmp_path):
    pid = _trashed(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    run = store.create_run("designer", "fake", "m", "build", project_id=pid)
    store.update_run(run.id, status="done")
    (tmp_path / "parts").mkdir()
    os.symlink(outside, tmp_path / "parts" / "evil_part")
    store.register_part("evil_part", pid)
    projects.empty_trash(store, tmp_path)
    assert not os.path.lexists(tmp_path / "parts" / "evil_part")
    assert (outside / "precious.txt").read_text() == "keep me"
