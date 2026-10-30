"""DXF entities (as the parse worker reports them) → DrawingSpec, and the upload checks."""
import math
from pathlib import Path

import pytest

from mises.imports.drawing_import import _part_base, check_upload, safe_name, sniff_drawing
from mises.imports.dxf import DEFAULT_THICKNESS_MM, preview_svg, spec_from_dxf
from mises.imports.parse_worker import _dxf
from mises.types import MisesError

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "drawings"
B90 = math.tan(math.radians(22.5))


def rect_poly(x, y, w, h, layer="0", bulge_tr=0.0):
    pts = [[x, y, 0], [x + w, y, 0], [x + w, y + h - (5 if bulge_tr else 0), bulge_tr]]
    if bulge_tr:
        pts.append([x + w - 5, y + h, 0])
    pts.append([x, y + h, 0])
    return {"t": "poly", "layer": layer, "pts": pts, "closed": True}


def circle(x, y, r, layer="0"):
    return {"t": "circle", "layer": layer, "c": [x, y], "r": r}


def lin(p2, p3, value, angle=0.0, text="<>"):
    return {"t": "dim", "layer": "DIMS", "dimtype": 0, "measurement": value, "angle": angle,
            "text": text, "pts": {"defpoint": [0, 0], "defpoint2": p2, "defpoint3": p3}}


def test_fixture_dxf_reads_exactly(tmp_path):
    (tmp_path / "input.dxf").write_bytes((FIX / "plate_holes.dxf").read_bytes())
    spec, facts = spec_from_dxf(_dxf(tmp_path))
    assert facts["units"] == "mm" and facts["thickness_source"] == "note"
    assert spec.thickness == 6
    assert sorted((round(h.x, 6), round(h.y, 6), h.diameter) for h in spec.holes) == [
        (15, 15, 6), (15, 45, 6), (50, 30, 10), (85, 15, 6), (85, 45, 6)]
    kinds = {d.kind: d for d in spec.dimensions}
    assert kinds["length"].value == 100 and kinds["width"].value == 60
    assert kinds["diameter"].value == pytest.approx(6) and kinds["diameter"].text == "4X 6"
    assert kinds["position_x"].value == pytest.approx(15)
    assert kinds["hole_spacing_x"].value == pytest.approx(70)
    assert any(seg.arc is not None for seg in spec.outline)            # the R5 bulge corner
    assert "1 other closed shape(s) left out" not in " ".join(facts["notes"])   # border layer
    assert any("ignore previous instructions" in n for n in spec.notes)   # kept as data only


def test_border_on_layer_zero_is_another_shape_not_the_part():
    raw = {"units": 4, "entities": [rect_poly(0, 0, 50, 30, "OUTLINE"), circle(10, 10, 2),
                                    rect_poly(100, 0, 20, 20, "OUTLINE")], "texts": ["T=4"]}
    spec, facts = spec_from_dxf(raw)
    assert spec.thickness == 4 and len(spec.holes) == 1
    assert "1 other closed shape(s) left out" in facts["notes"][0]


def test_inch_units_and_bulge_corner_and_auto_dims():
    raw = {"units": 1, "texts": [], "entities": [{
        "t": "poly", "layer": "0", "closed": True,
        "pts": [[0, 0, 0], [4, 0, 0], [4, 1.8, B90], [3.8, 2, 0], [0, 2, 0]]}]}
    spec, facts = spec_from_dxf(raw)
    assert facts["units"] == "inch" and facts["thickness_source"] == "assumed"
    assert spec.thickness == DEFAULT_THICKNESS_MM
    dims = {d.kind: d for d in spec.dimensions}
    assert dims["length"].value == pytest.approx(101.6) and dims["length"].text == "(geometry)"
    arc = next(s.arc for s in spec.outline if s.arc is not None)
    assert arc.r == pytest.approx(0.2 * 25.4)


def test_dimension_text_override_and_tolerance():
    ents = [rect_poly(0, 0, 100, 60), circle(20, 30, 4),
            lin([0, 0], [100, 0], 100, text="100 ±0.2"),
            lin([0, 0], [20, 30], 20, text="2X 20"),
            {"t": "dim", "layer": "D", "dimtype": 2, "measurement": 90.0, "text": "", "pts": {}}]
    spec, _ = spec_from_dxf({"units": 4, "entities": ents, "texts": []}, thickness_mm=2)
    by = {d.kind: d for d in spec.dimensions}
    assert by["length"].value == 100 and by["length"].tol_plus == pytest.approx(0.2)
    assert by["position_x"].value == 20 and by["position_x"].hole == 0
    assert by["angle"].value == 90


def test_no_closed_outline_and_untrusted_numbers():
    with pytest.raises(MisesError, match="no closed outline"):
        spec_from_dxf({"units": 4, "entities": [{"t": "line", "layer": "0", "a": [0, 0],
                                                  "b": [10, 0]}]})
    with pytest.raises(MisesError, match="no lines"):
        spec_from_dxf({"units": 4, "entities": [{"t": "line", "a": ["nan", 0], "b": [1, "x"]},
                                                 "junk", {"t": "circle", "c": [0, 0], "r": -1}]})
    with pytest.raises(MisesError, match="100 m"):
        spec_from_dxf({"units": 6, "entities": [rect_poly(0, 0, 500, 1)]})


def test_preview_svg_escapes_text():
    raw = {"units": 4, "entities": [rect_poly(0, 0, 10, 10), circle(5, 5, 1),
                                    {"t": "arc", "layer": "0", "c": [0, 0], "r": 2, "a0": 0, "a1": 90},
                                    lin([0, 0], [10, 0], 10)]}
    svg = preview_svg(raw)
    assert svg.startswith("<svg") and "<script" not in svg and "<circle" in svg and ">10<" in svg


@pytest.mark.parametrize("data,kind", [
    (b"%PDF-1.7\n...", "pdf"), (b"\x89PNG\r\n\x1a\n....", "png"), (b"\xff\xd8\xff\xe0..", "jpeg"),
    (b"  0\nSECTION\n  2\nHEADER\n", "dxf"), (b"999\nmade by x\n0\nSECTION\n", "dxf"),
    (b"\xef\xbb\xbf0\r\nSECTION\r\n", "dxf"), (b"AutoCAD Binary DXF\r\n\x1a\x00rest", "dxf"),
    (b"GIF89a", None), (b"0\nLINE\n", None), (b"<svg></svg>", None)])
def test_sniff(data, kind):
    assert sniff_drawing(data) == kind


def test_check_upload_and_names():
    with pytest.raises(MisesError) as e:
        check_upload(b"AC1032....")
    assert "DWG" in e.value.hint
    assert safe_name("../../etc/pa\x00ss.dxf") == "pass.dxf"
    assert _part_base("Bracket-V2 (rev 3).pdf") == "bracket_v2_rev"
    assert _part_base("1234.png") == "drawing_part"
    assert _part_base("ä.dxf") == "drawing_part"
