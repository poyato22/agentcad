"""U6: the fast B-spline export must be identical to build123d's, only faster.

build123d's SVG/DXF exporters iterate OCCT ``Array1`` objects (``curve.Poles()``,
``KnotSequence()``) with a pathologically slow pybind11 ``__iter__``; the worker swaps in
versions that index the arrays instead. These tests pin the output: per edge, and as whole
drawings (SVG tree and DXF entities) of a holed bracket and a 50+-face plate, stock vs fast.
"""
import json
import secrets
import xml.etree.ElementTree as ET

import pytest

from mises.cad import worker

pytestmark = pytest.mark.slow

BRACKET = ('def build(v):\n'
           '    p = Box(80, 50, 10)\n'
           '    for x in (-30, -10, 10, 30):\n'
           '        p -= Pos(x, 15, 0) * Cylinder(3, 10)\n'
           '        p -= Pos(x, -15, 0) * Cylinder(3, 10)\n'
           '    return p\n')
FIFTY = ('def build(v):\n'
         '    p = Box(120, 80, 12)\n'
         '    for x in range(-50, 51, 10):\n'
         '        for y in (-28, -9, 9, 28):\n'
         '            p -= Pos(x, y, 0) * Cylinder(4, 12)\n'
         '    return fillet(p.edges().filter_by(Axis.Z), 2)\n')


def _patched():
    if not worker._patch_fast_bspline_export():
        pytest.skip("fast export is pinned to build123d 0.13.x")
    import build123d.exporters as ex
    return ex


def _stock(monkeypatch, ex):
    """Put build123d's own exporter methods back for the rest of the monkeypatch context.
    `_mises_fast_poles` stays True, so worker._drawing's own patch call is a no-op."""
    for target, key, item, orig in ex.ExportSVG._mises_stock:
        if item:
            monkeypatch.setitem(target, key, orig)
        else:
            monkeypatch.setattr(target, key, orig)


def test_arr1_matches_iteration():
    """Indexed Value(k) access over an OCCT Array1 must give exactly what iterating it does."""
    import build123d as b3

    poles = b3.Edge.make_circle(5.0).to_splines().geom_adaptor().Curve().Curve().Poles()
    indexed = [(p.X(), p.Y(), p.Z()) for p in worker._arr1(poles)]
    iterated = [(p.X(), p.Y(), p.Z()) for p in poles]
    assert indexed == iterated
    assert len(indexed) == poles.Upper() - poles.Lower() + 1 >= 2


def test_patch_is_idempotent_and_reversible(monkeypatch):
    ex = _patched()
    fast = ex.ExportSVG._bspline_segments
    assert worker._patch_fast_bspline_export() and ex.ExportSVG._bspline_segments is fast
    with monkeypatch.context() as m:
        _stock(m, ex)
        assert ex.ExportSVG._bspline_segments is not fast
        assert ex.ExportDXF._CONVERTER_LOOKUP[ex.GeomType.BSPLINE] is not fast
    assert ex.ExportSVG._bspline_segments is fast


def test_patch_skips_other_build123d_versions(monkeypatch):
    import build123d
    import build123d.exporters as ex
    monkeypatch.setattr(ex.ExportSVG, "_mises_fast_poles", False, raising=False)
    monkeypatch.setattr(build123d, "__version__", "0.14.0")
    before = ex.ExportSVG._bspline_segments
    assert worker._patch_fast_bspline_export() is False
    assert ex.ExportSVG._bspline_segments is before


def _build(tmp_path, name, body):
    import build123d as b3
    ns: dict = {}
    exec("from build123d import *", ns)                        # noqa: S102 - fixed test script
    exec(body, ns)                                             # noqa: S102
    shape = ns["build"]({})
    d = tmp_path / name
    d.mkdir()
    b3.export_step(shape, str(d / "model.step"))
    metrics = worker._metrics(shape, 1.24)
    (d / "metrics.json").write_text(json.dumps(metrics))
    return d, metrics


def _draw(d):
    for f in ("drawing.svg", "drawing.dxf", "drawing.json"):
        (d / f).unlink(missing_ok=True)
    worker._drawing(d, {"label": d.name, "material": "PLA", "date": "2026-10-30"},
                    secrets.token_hex(16))
    return (d / "drawing.svg").read_text(), d / "drawing.dxf"


def _svg_tree(text):
    return [(el.tag, sorted(el.attrib.items()), (el.text or "").strip())
            for el in ET.fromstring(text).iter()]


def _dxf_entities(path):
    import ezdxf
    doc = ezdxf.readfile(str(path))
    out = []
    for e in doc.modelspace():
        attrs = {k: v for k, v in e.dxfattribs().items() if k not in ("handle", "owner")}
        if e.dxftype() == "SPLINE":
            attrs["control_points"] = [tuple(p) for p in e.control_points]
            attrs["knots"] = list(e.knots)
            attrs["weights"] = list(e.weights)
        out.append((e.dxftype(), sorted(attrs.items(), key=lambda kv: kv[0])))
    return sorted(layer.dxf.name for layer in doc.layers), out


@pytest.mark.parametrize("name,body,min_faces", [("bracket", BRACKET, 14),
                                                 ("fifty", FIFTY, 50)])
def test_whole_drawing_matches_stock(tmp_path, monkeypatch, name, body, min_faces):
    """The full sheet — views, hidden lines, dims, title block — is the same SVG tree and the
    same DXF entities (every SPLINE's control points, knots and weights included)."""
    ex = _patched()
    d, metrics = _build(tmp_path, name, body)
    assert metrics["faces"] >= min_faces
    fast_seg = ex.ExportSVG._bspline_segments
    with monkeypatch.context() as m:
        _stock(m, ex)
        assert ex.ExportSVG._SEGMENT_LOOKUP[ex.GeomType.BSPLINE] is not fast_seg   # truly stock
        stock_svg, stock_dxf_path = _draw(d)
        stock_dxf = _dxf_entities(stock_dxf_path)
    fast_svg, fast_dxf_path = _draw(d)
    fast_dxf = _dxf_entities(fast_dxf_path)

    assert _svg_tree(fast_svg) == _svg_tree(stock_svg)
    assert fast_dxf == stock_dxf
    # the sheet really exercised the B-spline path and kept its structure
    for view in ("front", "top", "right", "iso"):
        assert f'id="view-{view}"' in fast_svg
    assert 'id="annotations"' in fast_svg and "PART: " + name in fast_svg
    assert sum(1 for t, _ in fast_dxf[1] if t == "SPLINE") > 0
