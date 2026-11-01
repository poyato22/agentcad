"""Plan 9 B2 (D8): every part in the tree shows its mass. A built part weighs its volume x the
density of ITS material (default: the profile's default material); a group or parent weighs the
recursive sum of its children; a catalog part keeps its stated / estimated mass; anything else
is null with a human reason. The BOM and the assembly total use the same numbers."""
import json
from types import SimpleNamespace

import pytest

from mises import bom as bom_mod
from mises import mass, projects
from mises.events import EventBus
from mises.profile import load_profile, save_profile
from mises.store import Store
from mises.types import ErrorCode, MisesError


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def build(store):
    return store.create_project("B").id


def _built(store, home, pid, part_id, volume_mm3, *, worker_mass=None, **row):
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "part.py").write_text("x")
    (d / "vars.json").write_text("{}")
    m = {"volume_mm3": volume_mm3, "mass_g": worker_mass if worker_mass is not None
         else volume_mm3 / 1000 * 1.24, "bbox": {"size": [10, 10, 10]}, "holes": [], "valid": True}
    (d / "metrics.json").write_text(json.dumps(m))
    store.register_part(part_id, pid)
    if row:
        store.update_part(part_id, **row)


def _rows(store, home, pid):
    return {r["id"]: r for r in projects.list_parts(store, home, pid)}


def test_density_comes_from_the_materials_table():
    assert mass.density_g_cm3("PLA") == 1.24
    assert mass.density_g_cm3("petg") == 1.27
    assert mass.density_g_cm3("TPU") == 1.22          # FWC I4: TPU is the table's TPU 95A
    assert mass.density_g_cm3("PA-CF") == 1.06         # only in the sim table
    assert mass.density_g_cm3("unobtainium") is None


def test_a_custom_sim_material_has_a_density(tmp_path):
    from mises.sim.materials import custom_material, upsert_custom
    upsert_custom(tmp_path, custom_material({"name": "Alu 6061", "e_mpa": 69000, "nu": 0.33,
                                             "yield_mpa": 276, "density_g_cm3": 2.70}))
    assert mass.density_g_cm3("alu 6061", tmp_path) == 2.70
    assert mass.canonical_material("alu 6061", tmp_path) == "Alu 6061"


def test_a_built_part_weighs_volume_times_its_own_material(store, tmp_path, build):
    _built(store, tmp_path, build, "plate", 2000.0)
    r = _rows(store, tmp_path, build)["plate"]
    assert r["mass_g"] == pytest.approx(2.48) and r["mass_source"] == "built"
    assert r["material"] is None and r["mass_note"] and "PLA" in r["mass_note"]
    row = projects.patch_part(store, tmp_path, "plate", material="petg")
    assert row["material"] == "PETG"
    assert row["mass_g"] == pytest.approx(2000 / 1000 * 1.27)
    # the metrics the views hand out agree (the worker's PLA number never decides)
    assert row["metrics"]["mass_g"] == row["mass_g"]
    assert _rows(store, tmp_path, build)["plate"]["mass_g"] == pytest.approx(2.54)
    # null brings back the build default
    assert projects.patch_part(store, tmp_path, "plate", material=None)["mass_g"] == \
        pytest.approx(2.48)
    with pytest.raises(MisesError) as e:
        projects.patch_part(store, tmp_path, "plate", material="unobtainium")
    assert e.value.code == ErrorCode.INVALID_INPUT


def test_the_profile_default_material_is_the_build_default(store, tmp_path, build):
    prof = load_profile(tmp_path)
    prof.default_material = "PETG"
    save_profile(tmp_path, prof)
    _built(store, tmp_path, build, "plate", 1000.0)
    r = _rows(store, tmp_path, build)["plate"]
    assert r["mass_g"] == pytest.approx(1.27) and "PETG" in r["mass_note"]


def test_a_group_weighs_the_sum_of_its_children(store, tmp_path, build):
    store.register_part("frame", build, kind="group")
    _built(store, tmp_path, build, "a", 1000.0, parent_id="frame")
    _built(store, tmp_path, build, "b", 2000.0, parent_id="frame")
    g = _rows(store, tmp_path, build)["frame"]
    assert g["mass_g"] == pytest.approx(3.72) and g["mass_source"] == "sum"


def test_sums_are_recursive_and_a_parent_part_adds_its_own_mass(store, tmp_path, build):
    store.register_part("robot", build, kind="group")
    store.register_part("leg", build, kind="group")
    store.update_part("leg", parent_id="robot")
    _built(store, tmp_path, build, "body", 1000.0, parent_id="robot")
    _built(store, tmp_path, build, "lid", 1000.0, parent_id="body")
    _built(store, tmp_path, build, "shin", 2000.0, parent_id="leg")
    rows = _rows(store, tmp_path, build)
    assert rows["body"]["mass_g"] == pytest.approx(2.48) and rows["body"]["mass_source"] == "sum"
    assert rows["leg"]["mass_g"] == pytest.approx(2.48)
    assert rows["robot"]["mass_g"] == pytest.approx(4.96)


def test_unknown_children_are_named_in_the_sum_note(store, tmp_path, build):
    store.register_part("frame", build, kind="group")
    _built(store, tmp_path, build, "a", 1000.0, parent_id="frame")
    d = tmp_path / "parts" / "broken"
    d.mkdir(parents=True)
    (d / "part.py").write_text("x")
    store.register_part("broken", build)
    store.update_part("broken", parent_id="frame")
    rows = _rows(store, tmp_path, build)
    assert rows["broken"]["mass_g"] is None and rows["broken"]["mass_source"] == "unknown"
    # no build ever recorded as failed: it is simply not built yet (FW-core coordinator fix)
    assert rows["broken"]["mass_note"] == "not built yet"
    # a recorded failed build (host-only marker) says so
    meta = tmp_path / "parts" / ".mises-meta" / "broken"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "build_error.json").write_text('{"error": "NameError: x"}')
    rows = _rows(store, tmp_path, build)
    assert rows["broken"]["mass_note"] == "build failed: NameError: x"
    assert "broken" in rows["frame"]["mass_note"]
    assert rows["frame"]["mass_g"] == pytest.approx(1.24)
    assert "broken" in rows["frame"]["mass_note"] and "1 part" in rows["frame"]["mass_note"]


def test_a_failed_build_is_marked_and_a_good_one_clears_it(store, tmp_path, build):
    from mises.cad.tools import _mark_build
    d = tmp_path / "parts" / "bracket"
    d.mkdir(parents=True)
    (d / "part.py").write_text("x")
    store.register_part("bracket", build)
    _mark_build(d, "SyntaxError: bad")                  # failed, no earlier good build
    assert _rows(store, tmp_path, build)["bracket"]["mass_note"] == "build failed: SyntaxError: bad"
    _mark_build(d, None)                                # a later build succeeded
    assert _rows(store, tmp_path, build)["bracket"]["mass_note"] == "not built yet"


def test_an_empty_group_and_unbuilt_parts_have_reasons(store, tmp_path, build):
    store.register_part("empty", build, kind="group")
    store.register_part("ghost", build)                 # a part row with no build dir
    rows = _rows(store, tmp_path, build)
    assert rows["empty"]["mass_g"] is None and rows["empty"]["mass_source"] == "unknown"
    assert rows["empty"]["mass_note"]
    view = projects.part_view(store, tmp_path, store.get_part("ghost"))
    assert view["mass_g"] is None and "not built yet" in view["mass_note"]


def test_a_catalog_part_keeps_its_stated_mass(store, tmp_path, build):
    d = tmp_path / "parts" / "motor"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": 9000, "mass_g": 31.0,
                                                "mass_source": "catalog"}))
    store.register_part("motor", build, kind="catalog")
    store.register_part("nomass", build, kind="catalog")
    rows = _rows(store, tmp_path, build)
    assert rows["motor"]["mass_g"] == 31.0 and rows["motor"]["mass_source"] == "catalog"
    assert rows["nomass"]["mass_g"] is None and rows["nomass"]["mass_note"]


def test_the_bom_mass_is_the_tree_mass(store, tmp_path, build):
    _built(store, tmp_path, build, "plate", 2000.0, material="PETG")
    _built(store, tmp_path, build, "arm", 1234.5)
    b = bom_mod.BomStore(store)
    v = bom_mod.view(store, b, tmp_path, build, material="PLA")
    tree = _rows(store, tmp_path, build)
    for it in v["items"]:
        spec = {s["key"]: s["value"] for s in it["specs"]}
        assert spec["mass"] == tree[it["part_id"]]["mass_g"]
    plate = next(i for i in v["items"] if i["part_id"] == "plate")
    assert {s["key"]: s["value"] for s in plate["specs"]}["material"] == "PETG"


def test_assembly_total_is_instances_times_part_mass(store, tmp_path, build):
    _built(store, tmp_path, build, "wheel", 1000.0)
    _built(store, tmp_path, build, "deck", 2000.0)
    d = tmp_path / "parts" / "bad"
    d.mkdir(parents=True)
    (d / "part.py").write_text("x")
    store.register_part("bad", build)
    insts = [SimpleNamespace(part_id=p) for p in ("wheel", "wheel", "wheel", "wheel", "deck", "bad")]
    t = mass.assembly_total(store, tmp_path, build, insts)
    assert t["total_mass_g"] == pytest.approx(4 * 1.24 + 2.48)
    assert t["mass_unknown"] == [{"part_id": "bad", "count": 1,
                                  "reason": _rows(store, tmp_path, build)["bad"]["mass_note"]}]
    assert mass.assembly_total(store, tmp_path, build, [])["total_mass_g"] == 0


async def test_agent_tool_sets_the_material(store, tmp_path, build):
    _built(store, tmp_path, build, "plate", 1000.0)
    run = store.create_run("designer", "fake", "m", "build", project_id=build)
    ctx = SimpleNamespace(run_id=run.id, store=store, events=EventBus(), workdir=tmp_path)
    sub = ctx.events.subscribe()
    tool = mass.part_set_material
    assert tool.safety.value == "local_mutate" and tool.scope_arg == "part_id"
    out = await tool.handler(ctx, tool.input_model(part_id="plate", material="abs"))
    assert out.data["material"] == "ABS" and out.data["mass_g"] == pytest.approx(1.04)
    assert store.get_part("plate").material == "ABS"
    ev = await sub.queue.get()
    assert ev.type == "part_changed" and ev.data["part_id"] == "plate"
    with pytest.raises(MisesError):
        await tool.handler(ctx, tool.input_model(part_id="plate", material="gold leaf"))


def test_material_column_is_added_to_an_old_database(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE parts (id TEXT PRIMARY KEY, project_id TEXT, label TEXT,"
                      " parent_id TEXT, ord INTEGER, color TEXT, kind TEXT, version INTEGER);"
                      "INSERT INTO parts VALUES ('arm', 'p', 'arm', NULL, 0, NULL, 'part', 1);")
    con.commit()
    con.close()
    s = Store(db)
    assert s.get_part("arm").material is None
    assert s.update_part("arm", material="PETG").material == "PETG"


# ---- fix round 1: one mass truth for every reader

def test_a_parent_parts_bom_line_weighs_the_part_itself(store, tmp_path, build):
    _built(store, tmp_path, build, "body", 10000.0)
    _built(store, tmp_path, build, "arm", 2000.0, parent_id="body")
    tree = _rows(store, tmp_path, build)
    assert tree["body"]["mass_source"] == "sum" and tree["body"]["mass_g"] == pytest.approx(14.88)
    assert tree["body"]["metrics"]["mass_g"] == pytest.approx(12.4)     # its own mass
    assert "own_mass_g" not in tree["body"]                              # internal to the BOM
    v = bom_mod.view(store, bom_mod.BomStore(store), tmp_path, build, material="PLA")
    lines = {i["part_id"]: i for i in v["items"]}
    body = lines["body"]
    assert body["estimate"] and body["unit_cost"] == round(12.4 / 1000 * 22.0, 2)
    assert {s["key"]: s["value"] for s in body["specs"]}["mass"] == pytest.approx(12.4)
    # leaves: BOM mass == tree mass
    assert {s["key"]: s["value"] for s in lines["arm"]["specs"]}["mass"] == tree["arm"]["mass_g"]


def test_an_unknown_stored_default_is_weighed_as_pla_everywhere(store, tmp_path, build):
    prof = load_profile(tmp_path)
    prof.default_material = "PLA+"          # an old free-text value, stored before validation
    save_profile(tmp_path, prof)
    _built(store, tmp_path, build, "arm", 1000.0)
    r = _rows(store, tmp_path, build)["arm"]
    assert r["mass_g"] == pytest.approx(1.24) and r["mass_source"] == "built"
    assert r["mass_note"].startswith("unknown material PLA+ — weighed as PLA")
    assert mass.build_density(store, tmp_path, "arm") == 1.24
    assert mass.part_mass(store, tmp_path, "arm")["mass_g"] == r["mass_g"]


def test_the_unknown_material_error_is_capped(store, tmp_path, build):
    _built(store, tmp_path, build, "arm", 1000.0)
    with pytest.raises(MisesError) as e:
        projects.patch_part(store, tmp_path, "arm", material="x" * 500)
    assert len(e.value.message) < 120 and "PLA" in (e.value.hint or "")


async def test_every_reader_agrees_on_a_parts_mass(store, tmp_path, build):
    """The UI tree, the BOM line, mirror_measure, the spec check and the study default all
    weigh a PETG part the same, although its metrics.json still holds the PLA build mass."""
    from mises.cad.tools import PartArgs, mirror_measure
    from mises.engineering.spec import Spec
    from mises.engineering.tools import spec_report
    from mises.sim import studies
    _built(store, tmp_path, build, "plate", 2000.0)
    projects.patch_part(store, tmp_path, "plate", material="PETG")
    tree = _rows(store, tmp_path, build)["plate"]["mass_g"]
    assert tree == pytest.approx(2.54)
    ctx = SimpleNamespace(workdir=tmp_path, store=store, run_id=None)
    measured = await mirror_measure.handler(ctx, PartArgs(part_id="plate"))
    assert measured.data["mass_g"] == tree and "2.54 g" in measured.summary
    spec = Spec.model_validate({"requirements": [{"id": "m", "kind": "max_mass_g", "target": 3}]})
    rep = spec_report(tmp_path, spec, "plate", store)
    assert rep["requirements"][0]["measured"] == tree
    v = bom_mod.view(store, bom_mod.BomStore(store), tmp_path, build, material="PLA")
    assert {s["key"]: s["value"] for s in v["items"][0]["specs"]}["mass"] == tree
    assert studies.default_material(ctx, "plate") == "PETG"
    assert mass.part_mass(store, tmp_path, "plate")["mass_g"] == tree


def test_a_group_of_hidden_rows_is_an_empty_group(store, tmp_path, build):
    store.register_part("frame", build, kind="group")
    store.register_part("gone", build)            # its build dir is gone: hidden
    store.update_part("gone", parent_id="frame")
    g = _rows(store, tmp_path, build)["frame"]
    assert g["mass_g"] is None and g["mass_note"] == "empty group"


def test_part_view_reads_only_the_part_and_its_subtree(store, tmp_path, build, monkeypatch):
    for i in range(30):
        _built(store, tmp_path, build, f"p{i:02d}", 1000.0)
    store.register_part("frame", build, kind="group")
    _built(store, tmp_path, build, "kid", 1000.0, parent_id="frame")
    calls = []
    real = projects._part_disk
    monkeypatch.setattr(projects, "_part_disk", lambda h, pid: (calls.append(pid), real(h, pid))[1])
    v = projects.part_view(store, tmp_path, store.get_part("p05"))
    assert calls == ["p05"] and v["mass_g"] == pytest.approx(1.24)
    calls.clear()
    g = projects.part_view(store, tmp_path, store.get_part("frame"))
    assert sorted(calls) == ["frame", "kid"] and g["mass_g"] == pytest.approx(1.24)


# ---- fix round 2

def _filament_without_sim_data(monkeypatch, name="PVA"):
    """A slicer filament the sim table does not have (FWC I4 made TPU an alias of TPU 95A, so
    no built-in filament lacks sim data any more)."""
    import dataclasses

    from mises.print import slicer
    fil = dataclasses.replace(slicer.MATERIALS["TPU"])
    monkeypatch.setitem(slicer.MATERIALS, name, fil)
    return name


def test_a_tpu_part_is_studied_as_tpu_95a(store, tmp_path, build):
    from mises.sim import studies
    _built(store, tmp_path, build, "boot", 10000.0, material="TPU")
    ctx = SimpleNamespace(workdir=tmp_path, store=store, services={})
    assert studies.resolve_material(None, studies.default_material(ctx, "boot"),
                                    tmp_path).name == "TPU 95A"


def test_a_study_of_a_part_material_without_sim_data_is_refused(store, tmp_path, build,
                                                               monkeypatch):
    from mises.sim import studies
    from mises.sim.settings import MaterialSettings
    from mises.sim.types import SimError
    pva = _filament_without_sim_data(monkeypatch)
    _built(store, tmp_path, build, "boot", 10000.0, material=pva)
    ctx = SimpleNamespace(workdir=tmp_path, store=store, services={})
    assert studies.default_material(ctx, "boot") == pva             # never swapped for PLA
    for ms in (None, MaterialSettings()):
        with pytest.raises(SimError) as e:
            studies.resolve_material(ms, studies.default_material(ctx, "boot"), tmp_path)
        assert e.value.message == (f"{pva} has no simulation data; set a sim material for this "
                                   "study or calibrate one")
        assert "PETG" in (e.value.hint or "")
    # an explicit material in the call is studied as asked
    m = studies.resolve_material(MaterialSettings(name="PETG"), pva, tmp_path)
    assert m.name == "PETG"
    # a name nobody knows keeps the plain unknown-material error
    with pytest.raises(SimError) as e:
        studies.resolve_material(None, "unobtainium", tmp_path)
    assert "unknown material" in e.value.message


def test_design_review_weighs_max_mass_with_the_spec_check_mass(store, tmp_path, build):
    from mises.engineering.spec import Spec
    from mises.engineering.tools import spec_report
    from mises.sim.tools import _metric_rows
    _built(store, tmp_path, build, "boot", 10000.0, material="TPU")
    spec = {"requirements": [{"id": "m", "kind": "max_mass_g", "target": 12.2}]}
    metrics = json.loads((tmp_path / "parts" / "boot" / "metrics.json").read_text())
    row = _metric_rows(spec, metrics, mass.part_mass(store, tmp_path, "boot", metrics))[0]
    checked = spec_report(tmp_path, Spec.model_validate(spec), "boot", store)["requirements"][0]
    assert row["measured"] == checked["measured"] == pytest.approx(12.2)    # TPU 95A 1.22
    # the verdict bands stay each tool's own (design_review warns on a near-miss below target,
    # like its stress rows); the mass they judge is the one number
    assert row["note"].startswith("the part's mass: ") and "TPU" in row["note"]


def test_drawings_and_prints_default_to_the_parts_material(store, tmp_path, build):
    _built(store, tmp_path, build, "plate", 1000.0, material="PETG")
    _built(store, tmp_path, build, "arm", 1000.0)
    assert mass.part_material(store, tmp_path, "plate") == "PETG"
    assert mass.part_material(store, tmp_path, "arm") == "PLA"           # the build default
    assert mass.part_material(None, tmp_path, None) == "PLA"


def test_concurrent_reparents_never_form_a_cycle(store, tmp_path, build):
    import threading
    _built(store, tmp_path, build, "a", 1000.0)
    _built(store, tmp_path, build, "b", 1000.0)
    errors = []

    def move(child, parent):
        for _ in range(40):
            try:
                projects.patch_part(store, tmp_path, child, parent_id=parent)
            except MisesError:
                pass                                      # the second move is refused
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            try:
                projects.patch_part(store, tmp_path, child, parent_id=None)
            except MisesError:
                pass

    ts = [threading.Thread(target=move, args=("a", "b")), threading.Thread(target=move, args=("b", "a"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    a, b = store.get_part("a"), store.get_part("b")
    assert not (a.parent_id == "b" and b.parent_id == "a")
