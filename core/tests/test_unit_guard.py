from mises.unit_guard import note, unit_slip

REQ = ("Create a rectangular 3D object with dimensions 0.75 units in length, 0.5 units in "
       "width, and 0.375 units in height ... (1 unit = 1 mm).")


def test_a_thousandfold_build_is_flagged():
    assert unit_slip(REQ, [750, 500, 375]) == 1000
    assert "1000 times the numbers in the request" in note(REQ, [750, 500, 375])


def test_numbers_as_given_are_not_flagged():
    assert unit_slip(REQ, [0.75, 0.5, 0.375]) is None
    assert note(REQ, [0.75, 0.5, 0.375]) == ""


def test_inch_conversion_is_flagged_and_unrelated_sizes_are_not():
    req = "a plate 4 x 2 inches with a 0.25 hole"
    assert unit_slip(req, [101.6, 50.8, 3]) == 25.4
    assert unit_slip("a 40 x 20 x 10 block with a fillet", [43, 21.5, 11]) is None


def test_part_create_summary_reads_the_runs_request():
    from mises.cad.tools import _unit_note

    class Store:
        def get_run(self, rid):
            return type("R", (), {"task": REQ})()
    ctx = type("C", (), {"store": Store(), "run_id": "r"})()
    assert "1000 times" in _unit_note(ctx, {"bbox": {"size": [750, 500, 375]}})
    assert _unit_note(object(), {"bbox": {"size": [1, 2, 3]}}) == ""


def test_one_matching_size_clears_the_warning():
    # a derived size may be a coincidence; one size equal to a stated number means no slip
    assert unit_slip("box 60 x 40, wall 2, lid 0.06 thick", [60, 40, 60]) is None
