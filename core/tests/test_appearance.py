"""Plan 7 U8 (R22): per-part appearance presets (PBR metalness / roughness / opacity), stored on
the part row, patched through projects.patch_part, set by agents with part_set_appearance, and
resolved for renderers (U1's thumbnails) by appearance.render_params."""
import json
from types import SimpleNamespace

import pytest

from mises import appearance, looks, projects
from mises.events import EventBus
from mises.store import Store
from mises.types import ErrorCode, MisesError


@pytest.fixture
def store():
    return Store(":memory:")


def _part(store, home, part_id="arm"):
    p = store.create_project("B")
    d = home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "vars.json").write_text(json.dumps({"length": 40.0}))
    run = store.create_run("designer", "fake", "m", "build", project_id=p.id)
    projects.register_built_part(store, run.id, part_id)
    return run


def test_every_preset_is_physically_plausible():
    assert set(appearance.PRESETS) == {"matte_plastic", "glossy_plastic", "brushed_aluminum",
                                       "polished_metal", "anodized", "rubber", "clear"}
    for pid, p in appearance.PRESETS.items():
        assert 0 <= p["metalness"] <= 1 and 0 <= p["roughness"] <= 1, pid
        assert 0.05 <= p["opacity"] <= 1, pid
    assert appearance.PRESETS["polished_metal"]["metalness"] == 1
    assert appearance.PRESETS["polished_metal"]["roughness"] < appearance.PRESETS["brushed_aluminum"]["roughness"]
    assert appearance.PRESETS["clear"]["opacity"] < 1


def test_normalize_fills_from_the_preset_and_validates():
    a = appearance.normalize({"preset": "polished_metal"})
    assert a == {"preset": "polished_metal", "color": appearance.PRESETS["polished_metal"]["color"],
                 "metalness": 1.0, "roughness": appearance.PRESETS["polished_metal"]["roughness"],
                 "opacity": 1.0}
    # an explicit value wins over the preset's; the color is normalized
    b = appearance.normalize({"preset": "glossy_plastic", "color": "#FF0000", "roughness": 0.5})
    assert b["color"] == "#ff0000" and b["roughness"] == 0.5 and b["metalness"] == 0.0
    # no preset: a custom material, defaults are matte
    c = appearance.normalize({"color": "#00ff00", "metalness": 0.3})
    assert c["preset"] is None and c["metalness"] == 0.3 and c["opacity"] == 1.0
    for bad in ({"preset": "gold"}, {"color": "red"}, {"metalness": 2}, {"roughness": -1},
                {"opacity": 0}, {"opacity": "x"}, {"shine": 1}, "matte"):
        with pytest.raises(MisesError) as e:
            appearance.normalize(bad)
        assert e.value.code == ErrorCode.INVALID_INPUT


def test_patch_appearance_round_trips_and_keeps_color_in_step(store, tmp_path):
    _part(store, tmp_path)
    row = projects.patch_part(store, tmp_path, "arm", appearance={"preset": "anodized", "color": "#3366cc"})
    assert row["appearance"]["preset"] == "anodized" and row["color"] == "#3366cc"
    assert store.get_part("arm").appearance["metalness"] == appearance.PRESETS["anodized"]["metalness"]
    # a plain color change recolors the material too
    row = projects.patch_part(store, tmp_path, "arm", color="#112233")
    assert row["appearance"]["color"] == "#112233" and row["appearance"]["preset"] == "anodized"
    # listed with the tree
    listed = projects.list_parts(store, tmp_path, store.get_part("arm").project_id)
    assert listed[0]["appearance"]["color"] == "#112233"
    # null clears the material (the color stays as it was)
    row = projects.patch_part(store, tmp_path, "arm", appearance=None)
    assert row["appearance"] is None and row["color"] == "#112233"
    with pytest.raises(MisesError):
        projects.patch_part(store, tmp_path, "arm", appearance={"preset": "nope"})


def test_appearance_column_is_added_to_an_old_database(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE parts (id TEXT PRIMARY KEY, project_id TEXT, label TEXT,"
                      " parent_id TEXT, ord INTEGER, color TEXT, kind TEXT, version INTEGER);"
                      "INSERT INTO parts VALUES ('arm', 'p', 'arm', NULL, 0, '#aabbcc', 'part', 1);")
    con.commit()
    con.close()
    s = Store(db)
    assert s.get_part("arm").appearance is None
    s.update_part("arm", appearance={"preset": "rubber"})
    assert s.get_part("arm").appearance == {"preset": "rubber"}


def test_render_params_for_thumbnails(store, tmp_path):
    _part(store, tmp_path)
    plain = appearance.render_params(store.get_part("arm"))
    # an unstyled part renders in its stable palette color (matte plastic), not plain grey
    assert plain["preset"] == "matte_plastic" and plain["color"] in looks.PART_PALETTE
    projects.patch_part(store, tmp_path, "arm", color="#abcdef")
    assert appearance.render_params(store.get_part("arm"))["color"] == "#abcdef"
    projects.patch_part(store, tmp_path, "arm", appearance={"preset": "polished_metal"})
    p = appearance.render_params(store.get_part("arm"))
    assert p["metalness"] == 1.0 and p["preset"] == "polished_metal"


async def test_agent_tool_sets_appearance_and_announces_it(store, tmp_path):
    run = _part(store, tmp_path)
    ctx = SimpleNamespace(run_id=run.id, store=store, events=EventBus(), workdir=tmp_path)
    sub = ctx.events.subscribe()
    tool = appearance.part_set_appearance
    out = await tool.handler(ctx, tool.input_model(part_id="arm", preset="brushed_aluminum"))
    assert out.data["appearance"]["preset"] == "brushed_aluminum"
    assert store.get_part("arm").appearance["metalness"] == 1.0
    assert tool.safety.value == "local_mutate" and tool.scope_arg == "part_id"
    ev = await sub.queue.get()
    # the app refetches the part list on part_changed; the version is unchanged (no rebuild)
    assert ev.type == "part_changed" and ev.data["part_id"] == "arm" and ev.data["by"] == "agent"
    assert ev.data["version"] == 1 and "appearance" in ev.data["summary"]
    with pytest.raises(MisesError):
        await tool.handler(ctx, tool.input_model(part_id="nope", preset="rubber"))
    with pytest.raises(MisesError):
        await tool.handler(ctx, tool.input_model(part_id="arm", preset="gold"))


def test_an_untinted_preset_keeps_the_parts_color(store, tmp_path):
    _part(store, tmp_path)
    projects.patch_part(store, tmp_path, "arm", color="#cc3322")
    row = projects.patch_part(store, tmp_path, "arm", appearance={"preset": "glossy_plastic"})
    assert row["appearance"]["color"] == "#cc3322" and row["color"] == "#cc3322"
    # a tinted preset brings its own tint
    row = projects.patch_part(store, tmp_path, "arm", appearance={"preset": "brushed_aluminum"})
    assert row["color"] == appearance.PRESETS["brushed_aluminum"]["color"]
