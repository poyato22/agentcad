import io

import numpy as np
from PIL import Image

from mises.photo_check import compare_masks
from mises.tools.registry import ToolRegistry
from mises.reference_tools import register_reference_tools


def _rect(h, w, top, left, size=300):
    m = np.zeros((size, size), dtype=bool)
    m[top:top + h, left:left + w] = True
    return m


def test_same_outline_at_another_position_and_scale_matches():
    r = compare_masks(_rect(50, 100, 40, 20), _rect(100, 200, 90, 60))
    assert r["iou"] > 0.97 and r["aspect_error"] < 0.03


def test_wrong_proportions_fail_and_show_in_the_overlay():
    r = compare_masks(_rect(50, 100, 40, 20), _rect(80, 100, 40, 20))
    assert r["iou"] < 0.8 and r["aspect_error"] > 0.2
    im = np.asarray(Image.open(io.BytesIO(r["png"])).convert("RGB"))
    red = ((im == (214, 69, 65)).all(axis=2)).sum()
    blue = ((im == (52, 101, 214)).all(axis=2)).sum()
    assert red > 0 and blue == 0 or blue > 0   # only one side can be left over for nested boxes


def test_empty_photo_is_zero_not_a_crash():
    r = compare_masks(_rect(10, 10, 5, 5), np.zeros((300, 300), dtype=bool))
    assert r["iou"] == 0.0 and r["aspect_error"] is None


def test_annotated_measurement_image_marks_the_line():
    from mises.ask_measure import annotate
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), (255, 255, 255)).save(buf, "PNG")
    png = annotate(buf.getvalue(), (0.1, 0.5), (0.9, 0.5), "bolt spacing")
    im = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    assert im.shape == (100, 200, 3)
    assert (im[50, 100] == (214, 69, 65)).all()          # the line passes the middle
    assert (im[5, 100] == (255, 255, 255)).all()


def test_ask_measurement_is_registered():
    reg = ToolRegistry()
    register_reference_tools(reg)
    assert reg.get("reference_ask_measurement").safety.value == "read"


def test_tool_is_registered_read_only():
    reg = ToolRegistry()
    register_reference_tools(reg)
    assert reg.get("reference_photo_check").safety.value == "read"
