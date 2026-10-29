"""Sketch v2 data: new stroke kinds, construction geometry, constraints, and the closed profiles
the core derives from the strokes for the agent context (mm polylines and arcs)."""
import math

import pytest
from pydantic import ValidationError

from mises import sketch as sk


def sketch(strokes, **kw) -> sk.SketchIn:
    return sk.SketchIn.model_validate({"units": "mm", "width_mm": 200, "height_mm": 150,
                                       "strokes": strokes, **kw})


RECT = {"id": "r", "kind": "rect", "points": [[10, 10], [50, 10], [50, 30], [10, 30]], "closed": True}
HOLE = {"id": "h", "kind": "circle", "points": [[22, 20]], "closed": True, "center": [20, 20],
        "radius_mm": 2}


def test_new_kinds_and_construction_validate():
    s = sketch([
        {"id": "p", "kind": "polyline", "points": [[0, 0], [10, 0], [10, 10]]},
        {"id": "a", "kind": "arc", "points": [[0, 0], [5, 5], [10, 0]]},
        {"id": "sl", "kind": "slot", "points": [[0, 0], [20, 0]], "radius_mm": 3, "closed": True},
        {"id": "c", "kind": "line", "points": [[0, 0], [0, 10]], "construction": True},
    ])
    assert [x.kind for x in s.strokes] == ["polyline", "arc", "slot", "line"]
    assert s.strokes[3].construction is True


@pytest.mark.parametrize("bad", [
    {"id": "a", "kind": "arc", "points": [[0, 0], [10, 0]]},                    # arc needs 3 points
    {"id": "a", "kind": "arc", "points": [[0, 0], [5, 0], [10, 0]]},            # collinear
    {"id": "s", "kind": "slot", "points": [[0, 0], [20, 0]]},                   # slot needs a radius
    {"id": "p", "kind": "polyline", "points": [[0, 0], [10, 0]], "corner_r": [1]},  # length mismatch
    {"id": "p", "kind": "polyline", "points": [[0, 0], [10, 0]], "corner_r": [-1, 0]},
])
def test_bad_strokes_rejected(bad):
    with pytest.raises(ValidationError):
        sketch([bad])


def test_constraints_reference_real_geometry():
    s = sketch([RECT, HOLE], constraints=[
        {"id": "k1", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 0}], "value_mm": 40},
        {"id": "k2", "kind": "equal", "refs": [{"stroke_id": "r", "edge": 1}, {"stroke_id": "r", "edge": 2}]},
        {"id": "k3", "kind": "diameter", "refs": [{"stroke_id": "h"}], "value_mm": 4},
        {"id": "k4", "kind": "coincident", "refs": [{"stroke_id": "r", "vertex": 0}, {"stroke_id": "h", "vertex": 0}]},
    ], extrude_mm=5)
    assert len(s.constraints) == 4 and s.extrude_mm == 5
    for bad in (
        {"id": "x", "kind": "distance", "refs": [{"stroke_id": "nope", "edge": 0}], "value_mm": 4},
        {"id": "x", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 4}], "value_mm": 4},
        {"id": "x", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 0}]},          # no value
        {"id": "x", "kind": "coincident", "refs": [{"stroke_id": "r", "vertex": 9}, {"stroke_id": "h", "vertex": 0}]},
        {"id": "x", "kind": "horizontal", "refs": [{"stroke_id": "h", "edge": 0}]},          # a circle has no edges
    ):
        with pytest.raises(ValidationError):
            sketch([RECT, HOLE], constraints=[bad])


def test_rect_with_hole_is_one_profile_with_a_hole():
    [p] = sk.closed_profiles(sketch([RECT, HOLE]))
    assert p["outer"]["strokes"] == ["r"] and [h["strokes"] for h in p["holes"]] == [["h"]]
    assert p["outer"]["area_mm2"] == 800
    assert p["holes"][0]["segments"] == [{"circle": {"center": [20, 20], "r": 2}}]
    assert p["area_mm2"] == pytest.approx(800 - math.pi * 4, abs=0.05)
    # outer counter-clockwise, as one polyline
    assert p["outer"]["segments"] == [{"polyline": [[10, 10], [50, 10], [50, 30], [10, 30], [10, 10]]}]


def test_chained_lines_and_arc_close_a_profile():
    s = sketch([
        {"id": "a", "kind": "line", "points": [[0, 0], [20, 0]]},
        {"id": "b", "kind": "line", "points": [[20, 0], [20, 10]]},
        {"id": "c", "kind": "arc", "points": [[20, 10], [10, 20], [0, 10]]},
        {"id": "d", "kind": "line", "points": [[0, 0], [0, 10]]},              # reversed direction
        {"id": "e", "kind": "line", "points": [[100, 100], [120, 100]]},       # dangling: no profile
    ])
    [p] = sk.closed_profiles(s)
    assert sorted(p["outer"]["strokes"]) == ["a", "b", "c", "d"]
    arcs = [x["arc"] for x in p["outer"]["segments"] if "arc" in x]
    assert len(arcs) == 1 and arcs[0]["center"] == [10, 10] and arcs[0]["r"] == 10 and arcs[0]["ccw"]
    assert p["area_mm2"] == pytest.approx(200 + math.pi * 100 / 2, abs=0.5)


def test_construction_and_open_strokes_are_not_profiles():
    s = sketch([{**RECT, "construction": True},
                {"id": "o", "kind": "polyline", "points": [[0, 0], [10, 0], [10, 10]]}])
    assert sk.closed_profiles(s) == []


def test_fillets_and_slots_export_arcs():
    s = sketch([
        {**RECT, "corner_r": [0, 5, 0, 0]},
        {"id": "sl", "kind": "slot", "points": [[100, 50], [130, 50]], "radius_mm": 4, "closed": True},
    ])
    profiles = sk.closed_profiles(s)
    assert len(profiles) == 2
    rect = next(p for p in profiles if p["outer"]["strokes"] == ["r"])
    [arc] = [x["arc"] for x in rect["outer"]["segments"] if "arc" in x]
    assert arc["center"] == [45, 15] and arc["r"] == 5 and arc["start"] == [45, 10] and arc["end"] == [50, 15]
    assert rect["area_mm2"] == pytest.approx(800 - 25 + math.pi * 25 / 4, abs=0.05)
    slot = next(p for p in profiles if p["outer"]["strokes"] == ["sl"])
    assert sum("arc" in x for x in slot["outer"]["segments"]) == 2
    assert slot["area_mm2"] == pytest.approx(30 * 8 + math.pi * 16, abs=0.1)


def test_context_carries_profiles_constraints_and_extrude(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": {
        "units": "mm", "width_mm": 200, "height_mm": 150, "strokes": [RECT, HOLE, {
            "id": "cl", "kind": "line", "points": [[0, 0], [0, 50]], "construction": True}],
        "constraints": [{"id": "k1", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 0}], "value_mm": 40}],
        "extrude_mm": 6}})
    sid = sk.save_sketch(tmp_path, up)["sketch_id"]
    wd = tmp_path / "wd"
    sk.attach_to_run(tmp_path, sid, wd)
    ctx = sk.sketch_context(wd, vision=False)
    assert len(ctx["profiles"]) == 1 and ctx["extrude_mm"] == 6
    assert ctx["constraints"] == [{"id": "k1", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 0}], "value_mm": 40}]
    assert ctx["strokes"][2]["construction"] is True
    assert "construction" not in ctx["strokes"][0]          # only flagged when true (compact)


def test_old_sketches_still_give_the_same_stroke_items(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": {"units": "mm", "width_mm": 200, "height_mm": 150, "strokes": [RECT]}})
    sid = sk.save_sketch(tmp_path, up)["sketch_id"]
    wd = tmp_path / "wd"
    sk.attach_to_run(tmp_path, sid, wd)
    ctx = sk.sketch_context(wd, vision=False)
    assert set(ctx["strokes"][0]) == {"id", "kind", "points", "closed", "label", "measured"}
    assert "constraints" not in ctx and "extrude_mm" not in ctx


def test_profile_cap():
    many = [{"id": f"c{i}", "kind": "circle", "points": [[0, 0]], "closed": True,
             "center": [5 + (i % 20) * 9, 5 + (i // 20) * 9], "radius_mm": 1} for i in range(sk.MAX_PROFILES + 5)]
    assert len(sk.closed_profiles(sketch(many))) == sk.MAX_PROFILES


LINE = {"id": "l", "kind": "line", "points": [[0, 0], [10, 0]]}
OPEN = {"id": "p", "kind": "polyline", "points": [[0, 0], [10, 0], [10, 10]]}


@pytest.mark.parametrize("bad", [
    {"id": "x", "kind": "coincident", "refs": [{"stroke_id": "r", "vertex": 0}, {"stroke_id": "r", "vertex": 2}]},  # collapses
    {"id": "x", "kind": "coincident", "refs": [{"stroke_id": "l", "vertex": 0}, {"stroke_id": "l", "vertex": 1}]},
    {"id": "x", "kind": "distance", "refs": [{"stroke_id": "l", "vertex": 0}, {"stroke_id": "l", "vertex": 0}], "value_mm": 5},
    {"id": "x", "kind": "horizontal", "refs": [{"stroke_id": "l", "vertex": 1}, {"stroke_id": "l", "vertex": 1}]},
    {"id": "x", "kind": "diameter", "refs": [{"stroke_id": "l"}], "value_mm": 4},                     # a line has no diameter
    {"id": "x", "kind": "equal", "refs": [{"stroke_id": "l", "edge": 0}, {"stroke_id": "h"}]},        # a line and a circle
    {"id": "x", "kind": "distance_x", "refs": [{"stroke_id": "l", "vertex": 0}], "value_mm": 4},
    {"id": "x", "kind": "point_on_circle", "refs": [{"stroke_id": "h", "vertex": 0}, {"stroke_id": "h"}]},
])
def test_degenerate_constraints_rejected(bad):
    with pytest.raises(ValidationError):
        sketch([RECT, HOLE, LINE, OPEN], constraints=[bad])


def test_offsets_on_circle_and_closing_a_chain_are_fine():
    s = sketch([RECT, HOLE, LINE, OPEN], constraints=[
        {"id": "a", "kind": "distance_x", "refs": [{"stroke_id": "r", "vertex": 0}, {"stroke_id": "h", "vertex": 0}], "value_mm": 10},
        {"id": "b", "kind": "distance_y", "refs": [{"stroke_id": "r", "vertex": 0}, {"stroke_id": "h", "vertex": 0}], "value_mm": 10},
        {"id": "c", "kind": "point_on_circle", "refs": [{"stroke_id": "l", "vertex": 0}, {"stroke_id": "h"}]},
        {"id": "d", "kind": "coincident", "refs": [{"stroke_id": "p", "vertex": 0}, {"stroke_id": "p", "vertex": 2}]},
    ])
    assert len(s.constraints) == 4


def test_a_rect_always_has_four_edges():
    s = sketch([{**RECT, "closed": False}], constraints=[
        {"id": "e", "kind": "distance", "refs": [{"stroke_id": "r", "edge": 3}], "value_mm": 20}])
    assert s.constraints[0].refs[0].edge == 3
