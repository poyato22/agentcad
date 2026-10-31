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
         else volume_mm3 / 1000 * 1.24, "bbox": {"size": [10, 10, 10]}}
    (d / "metrics.json").write_text(json.dumps(m))
    store.register_part(part_id, pid)
    if row:
        store.update_part(part_id, **row)


def _rows(store, home, pid):
    return {r["id"]: r for r in projects.list_parts(store, home, pid)}


def test_density_comes_from_the_materials_table():
    assert mass.density_g_cm3("PLA") == 1.24
    assert mass.density_g_cm3("petg") == 1.27
    assert mass.density_g_cm3("TPU") == 1.21          # only in the slicer's table
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
    assert "build failed" in rows["broken"]["mass_note"]
    assert rows["frame"]["mass_g"] == pytest.approx(1.24)
    assert "broken" in rows["frame"]["mass_note"] and "1 part" in rows["frame"]["mass_note"]


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
