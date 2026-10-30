"""Reference silhouette scoring: only matching views, best of 8 orientations, sheets by
region, and an untagged reference scored against its single best view."""
import numpy as np
import pytest

from mises.cad import silhouette


def _l_shape(h=60, w=60) -> np.ndarray:
    """An asymmetric L (so rotations and mirrors really differ)."""
    m = np.zeros((h, w), dtype=bool)
    m[5:55, 5:20] = True
    m[40:55, 5:50] = True
    return m


def _bar(h=60, w=60) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[25:35, 5:55] = True
    return m


RENDERS = {"front": _bar(), "top": _l_shape(), "side": _bar().T.copy(),
           "iso": np.pad(np.ones((30, 30), bool), 15)}


def _ref(view="unknown", regions=None, rid="ref_a"):
    return {"reference_id": rid, "view": view, "regions": regions or {}}


def test_top_only_reference_scores_only_top():
    per_view, scores = silhouette.score([_ref("top")], {"ref_a": _l_shape()}, RENDERS)
    assert set(per_view) == {"top"}                  # no failing front/side/iso numbers
    assert per_view["top"] == pytest.approx(1.0, abs=0.02)
    assert scores[0]["tagged"] is True and scores[0]["rotation"] == 0


@pytest.mark.parametrize("k,mirror", [(1, False), (2, False), (3, False), (0, True), (1, True)])
def test_rotated_or_mirrored_reference_still_matches(k, mirror):
    ref = np.rot90(np.fliplr(_l_shape()) if mirror else _l_shape(), k)
    per_view, scores = silhouette.score([_ref("top")], {"ref_a": ref}, RENDERS)
    assert per_view["top"] == pytest.approx(1.0, abs=0.02)
    assert scores[0]["mirrored"] == mirror or scores[0]["rotation"] != 0


def test_wrong_shape_scores_low_in_its_view():
    per_view, _ = silhouette.score([_ref("top")], {"ref_a": _bar()}, RENDERS)
    assert per_view["top"] < 0.6


def test_untagged_reference_reports_only_its_best_view():
    per_view, scores = silhouette.score([_ref("unknown")], {"ref_a": _l_shape()}, RENDERS)
    assert list(per_view) == ["top"]
    assert scores[0]["note"] == silhouette.UNTAGGED and scores[0]["tagged"] is False


def test_sheet_regions_score_each_panel_against_its_view():
    sheet = np.zeros((60, 120), dtype=bool)
    sheet[:, :60] = _bar()                             # left panel: front
    sheet[:, 60:] = _l_shape()                         # right panel: top
    regions = {"front": {"x0": 0, "y0": 0, "x1": 0.5, "y1": 1},
               "top": {"x0": 0.5, "y0": 0, "x1": 1, "y1": 1}}
    per_view, scores = silhouette.score([_ref("sheet", regions)], {"ref_a": sheet}, RENDERS)
    assert set(per_view) == {"front", "top"}
    assert min(per_view.values()) == pytest.approx(1.0, abs=0.03)
    assert all(s["tagged"] for s in scores)


def test_every_reference_is_scored_not_only_the_first():
    refs = [_ref("top", rid="ref_a"), _ref("front", rid="ref_b")]
    per_view, scores = silhouette.score(refs, {"ref_a": _l_shape(), "ref_b": _bar()}, RENDERS)
    assert {s["reference_id"] for s in scores} == {"ref_a", "ref_b"}
    assert set(per_view) == {"top", "front"}


def test_blank_reference_is_noted_not_scored():
    per_view, scores = silhouette.score([_ref("top")], {"ref_a": np.zeros((10, 10), bool)},
                                        RENDERS)
    assert per_view == {} and scores[0]["iou"] is None


def test_needed_views():
    assert silhouette.needed_views([_ref("top")]) == ["top"]
    assert silhouette.needed_views([_ref("sheet", {"side": {}})]) == ["side"]
    assert silhouette.needed_views([_ref("unknown")]) == list(silhouette.VIEWS)
