import json

import pytest

from mises.catalog import Catalog
from mises.catalog.builtin import DATA, builtin_items, categories
from mises.store import Store
from mises.types import ErrorCode, MisesError


@pytest.fixture
def cat():
    return Catalog(Store(":memory:"))


def url_item(**kw):
    return {"id": "url_0123456789abcdef", "name": "Falcon motor", "category": "motor",
            "source": "url", "url": "https://shop.example.com/p", "vendor": None,
            "specs": [{"key": "kv", "value": 1950.0, "unit": None}], "dims": {"mass_g": 34.0},
            "mount": None, "geometry": None, "raw_text": "page text", **kw}


def test_every_data_file_cites_sources():
    for f in DATA.glob("*.json"):
        doc = json.loads(f.read_text())
        assert doc["sources"], f.name
        assert all(s.get("title") for s in doc["sources"]), f.name
        assert any(s.get("url", "").startswith("https://") for s in doc["sources"]
                   if s.get("url")), f.name


def test_builtin_coverage():
    ids = {i["id"] for i in builtin_items()}
    for size in ("m2", "m2_5", "m3", "m4", "m5", "m6", "m8"):
        assert {f"{size}_socket_head_screw", f"{size}_hex_nut", f"{size}_plain_washer"} <= ids
    for b in ("623", "608", "6000", "6204"):
        assert f"bearing_{b}_deep_groove" in ids
    for m in ("1404", "2207", "2306", "2806_5"):
        assert f"motor_{m}_class" in ids
    patterns = {i["mount"]["pattern"] for i in builtin_items() if i["category"] == "motor"}
    assert {"9x9", "12x12", "16x16", "19x19"} <= patterns
    assert {"prop_3in_class", "prop_5in_class", "prop_7in_class", "lipo_4s_1500mah",
            "lipo_6s_1300mah", "fc_stack_20x20_mini", "fc_aio_25_5_whoop", "fc_stack_30_5_full",
            "servo_sg90_micro", "servo_mg996r_standard", "nema17_stepper",
            "extrusion_2020_profile", "extrusion_2040_profile", "raspberry_pi_4_model_b",
            "arduino_uno_rev_three"} <= ids
    assert set(categories()) >= {"fastener", "bearing", "motor", "prop", "battery",
                                 "flight_controller", "servo", "stepper", "extrusion", "board"}


def test_typical_values_are_labeled():
    motors = [i for i in builtin_items() if i["category"] == "motor"]
    for m in motors:
        thrust = next(s for s in m["specs"] if s["key"] == "max_thrust")
        assert "typical" in thrust["note"] and thrust["unit"] == "N"
        assert m["geometry"] == "envelope"


def test_ids_are_not_numbered_names():
    # part ids derive from item ids, and names must not end in a number
    for i in builtin_items():
        assert not i["id"][-1].isdigit(), i["id"]


def test_search_words_and_category(cat):
    hits = cat.search("m3 screw")
    assert hits[0]["id"] == "m3_socket_head_screw"
    assert all(h["category"] == "bearing" for h in cat.search("", "bearing"))
    assert {h["id"] for h in cat.search("16x16")} >= {"motor_2207_class", "motor_2306_class"}
    assert cat.search("no such part zzz") == []
    assert len(cat.search("", limit=5)) == 5


def test_get_and_not_found(cat):
    assert cat.get("bearing_608_deep_groove")["dims"]["od_mm"] == 22
    with pytest.raises(MisesError) as e:
        cat.get("nope")
    assert e.value.code == ErrorCode.NOT_FOUND


def test_get_returns_copies(cat):
    cat.get("m3_socket_head_screw")["name"] = "changed"
    assert cat.get("m3_socket_head_screw")["name"] != "changed"


def test_url_items_saved_scoped_and_deleted(cat):
    cat.save(url_item(), project_id="prj_a")
    assert cat.get("url_0123456789abcdef")["project_id"] == "prj_a"
    assert [h["id"] for h in cat.search("falcon")] == ["url_0123456789abcdef"]
    assert cat.search("falcon", project_id="prj_b") == []
    assert "raw_text" not in cat.search("falcon")[0]
    cat.delete("url_0123456789abcdef")
    with pytest.raises(MisesError):
        cat.get("url_0123456789abcdef")


def test_builtin_cannot_be_deleted_or_edited(cat):
    with pytest.raises(MisesError):
        cat.delete("m3_socket_head_screw")
    with pytest.raises(MisesError):
        cat.update("m3_socket_head_screw", {"name": "x"})


def test_review_edits(cat):
    cat.save(url_item())
    it = cat.update("url_0123456789abcdef", {
        "name": "Falcon 2207", "specs": [{"key": "kv", "value": 1900, "unit": None}],
        "mount": {"pattern": "16x16", "hole_d": 3, "spacing_mm": [16, 16], "thread": "M3"}})
    assert it["name"] == "Falcon 2207" and it["specs"][0]["value"] == 1900
    assert it["raw_text"] == "page text"
    for bad in ({"raw_text": "x"}, {"url": "https://evil.example.com"}, {"source": "builtin"},
                {"dims": {"Bad Key": 1}}, {"specs": [{"key": "", "value": 1}]}):
        with pytest.raises(MisesError) as e:
            cat.update("url_0123456789abcdef", bad)
        assert e.value.code == ErrorCode.INVALID_INPUT
