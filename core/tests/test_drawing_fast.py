"""U6: the fast B-spline export must be byte-identical to build123d's, only faster.

build123d's SVG/DXF exporters iterate OCCT ``Array1`` objects (``curve.Poles()``,
``KnotSequence()``) with a pathologically slow pybind11 ``__iter__``; the worker swaps in
versions that index the arrays instead. These tests pin the equivalence of the output.
"""
import pytest

from mises.cad import worker

pytestmark = pytest.mark.slow


def test_arr1_matches_iteration():
    """Indexed Value(k) access over an OCCT Array1 must give exactly what iterating it does —
    the whole point of the swap is that it is equivalent, only faster."""
    import build123d as b3

    circle = b3.Edge.make_circle(5.0).to_splines()
    poles = circle.geom_adaptor().Curve().Curve().Poles()
    indexed = [(p.X(), p.Y(), p.Z()) for p in worker._arr1(poles)]
    iterated = [(p.X(), p.Y(), p.Z()) for p in poles]
    assert indexed == iterated
    assert len(indexed) == poles.Upper() - poles.Lower() + 1 >= 2


def test_fast_bspline_segments_match_stock():
    """A part with holes seen edge-on yields hidden B-spline edges. The fast and the stock
    converters must produce identical SVG path data for every one of them."""
    import build123d as b3
    import build123d.exporters as ex
    from build123d import Location

    plate = b3.Box(80, 50, 10)
    for x in (-25, 0, 25):
        plate -= b3.Pos(x, 0, 0) * b3.Cylinder(4, 10)

    # a side view: the far hole rims become hidden B-spline edges
    cam = 80 * 10 + 100
    _vis, hid = plate.project_to_viewport((cam, 0, 0), (0, 0, 1))
    placed = [e.scale(0.5).moved(Location((50, 50, 0))) for e in hid]
    bsplines = [e for e in placed if str(e.geom_type) == "GeomType.BSPLINE"]
    assert bsplines, "expected hidden B-spline edges to exercise the fast path"

    worker._patch_fast_bspline_export()
    # the patch stashes build123d's own method, so this holds whatever the test order
    stock = ex.ExportSVG._mises_stock_bspline_segments
    fast = ex.ExportSVG._bspline_segments
    assert fast is not stock
    assert getattr(ex.ExportSVG, "_mises_fast_poles", False)

    svg = ex.ExportSVG()
    for edge in bsplines:
        want = ex.PT.Path(*stock(svg, edge, False)).d()
        got = ex.PT.Path(*fast(svg, edge, False)).d()
        assert got == want


def test_patch_is_idempotent():
    worker._patch_fast_bspline_export()
    import build123d.exporters as ex
    first = ex.ExportSVG._bspline_segments
    worker._patch_fast_bspline_export()
    assert ex.ExportSVG._bspline_segments is first
