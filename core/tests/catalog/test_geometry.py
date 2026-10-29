import ast
import json

import pytest

from mises.catalog import geometry
from mises.catalog.builtin import builtin_by_id, builtin_items
from mises.naming import check_name
from mises.types import MisesError


def test_every_builtin_item_has_a_valid_script():
    for it in builtin_items():
        src, vars_ = geometry.script_for(it)
        ast.parse(src)
        assert "def build(v: dict):" in src
        for name in vars_:
            check_name(name)                 # editable later through mirror_part_set_vars
        pid = geometry.base_part_id(it)
        check_name(pid)


def test_item_text_never_reaches_the_script():
    it = {"id": "url_x", "source": "url", "category": "other",
          "name": "evil\"\nimport os; os.system('x')#",
          "dims": {"length_mm": 10, "width_mm": 5, "height_mm": 2}}
    src, vars_ = geometry.script_for(it)
    assert "import os" not in src and "os.system" not in src
    assert vars_ == {"l": 10.0, "w": 5.0, "h": 2.0}


def test_url_item_without_size_refused():
    with pytest.raises(MisesError):
        geometry.script_for({"id": "url_x", "source": "url", "dims": {"mass_g": 3}})


def test_url_item_cylinder_from_diameter():
    shape = geometry.shape_of({"dims": {"od_mm": 22, "width_mm": 7, "bore_mm": 8}})
    assert shape == {"kind": "cylinder", "d": 22.0, "h": 7.0, "bore": 8.0}


def test_part_id_reuse_and_project_suffix(tmp_path):
    item = builtin_by_id()["motor_2207_class"]
    owner_a = {"kind": "catalog", "catalog_item_id": item["id"], "project_id": "a"}
    pid, existing = geometry.choose_part_id(tmp_path, "motor_2207_class", owner_a)
    assert (pid, existing) == ("motor_2207_class", False)
    (tmp_path / pid).mkdir()
    (tmp_path / pid / "part.json").write_text(json.dumps(owner_a))
    assert geometry.choose_part_id(tmp_path, pid, owner_a) == (pid, True)
    owner_b = {**owner_a, "project_id": "b"}
    other, existing = geometry.choose_part_id(tmp_path, pid, owner_b)
    assert other.startswith("motor_2207_class_") and not existing
    check_name(other)


@pytest.mark.slow
async def test_insert_motor_envelope_bbox_and_catalog_mass(tmp_path):
    item = builtin_by_id()["motor_2207_class"]
    out = await geometry.insert_item(tmp_path, item, "prj")
    m, meta = out["metrics"], out["meta"]
    size = m["bbox"]["size"]
    assert size[0] == pytest.approx(27.5, abs=0.01) and size[1] == pytest.approx(27.5, abs=0.01)
    assert size[2] == pytest.approx(32 + 10, abs=0.01)           # bell + shaft
    assert m["mass_g"] == 33 and m["mass_source"] == "catalog"
    assert meta["kind"] == "catalog" and meta["project_id"] == "prj"
    assert meta["geometry"] == "envelope" and meta["catalog_item_id"] == "motor_2207_class"
    holes = [h for h in m["holes"] if abs(h["diameter_mm"] - 3.0) < 1e-6]
    assert len(holes) == 4
    d = tmp_path / out["part_id"]
    assert json.loads((d / "metrics.json").read_text())["mass_g"] == 33
    assert (d / "model.glb").is_file()
    again = await geometry.insert_item(tmp_path, item, "prj")
    assert again["existing"] and again["part_id"] == out["part_id"]


@pytest.mark.slow
async def test_insert_screw_uses_bd_warehouse(tmp_path):
    out = await geometry.insert_item(tmp_path, builtin_by_id()["m3_socket_head_screw"], None)
    assert out["meta"]["geometry"] == "builtin"
    size = out["metrics"]["bbox"]["size"]
    assert size[2] == pytest.approx(3 + 10, abs=0.05)           # head + length
    assert out["metrics"]["volume_mm3"] > 0


@pytest.mark.slow
async def test_insert_bearing_builtin_geometry_and_skf_mass(tmp_path):
    out = await geometry.insert_item(tmp_path, builtin_by_id()["bearing_608_deep_groove"], None)
    assert out["meta"]["geometry"] == "builtin"
    assert out["metrics"]["bbox"]["size"] == [pytest.approx(22, abs=0.05),
                                              pytest.approx(22, abs=0.05),
                                              pytest.approx(7, abs=0.05)]
    assert out["metrics"]["mass_g"] == 12


@pytest.mark.slow
async def test_insert_board_holes(tmp_path):
    out = await geometry.insert_item(tmp_path, builtin_by_id()["raspberry_pi_4_model_b"], "p")
    size = out["metrics"]["bbox"]["size"]
    assert size[:2] == [pytest.approx(85), pytest.approx(56)]
    assert sum(1 for h in out["metrics"]["holes"] if abs(h["diameter_mm"] - 2.7) < 1e-6) == 4


@pytest.mark.slow
async def test_failed_build_leaves_nothing(tmp_path):
    with pytest.raises(MisesError):
        await geometry.build_part(tmp_path, "broken_part", "def build(v):\n    raise ValueError\n",
                                  {}, {"kind": "catalog"})
    assert not (tmp_path / "broken_part").exists()
