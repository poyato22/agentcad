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


# ---- bench fix: an image attached in the chat is checkable like a reference photo
def _png_b64():
    import base64
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (255, 255, 255)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _Store:
    def __init__(self, project_id):
        self.project_id = project_id

    def get_run(self, run_id):
        return type("Run", (), {"project_id": self.project_id})()


def _ctx(home, project_id):
    rt = type("RT", (), {"home": home})()
    return type("Ctx", (), {"services": {"runtime": rt}, "store": _Store(project_id),
                            "run_id": "r1"})()


def test_a_chat_attached_image_of_the_build_is_read_as_the_photo(tmp_path):
    from mises import chat_images
    from mises.photo_check import photo_bytes
    _blocks, refs = chat_images.store(tmp_path, "p1", [("image/png", _png_b64())])
    raw = photo_bytes(_ctx(tmp_path, "p1"), refs[0]["id"])
    assert raw[:4] == b"\x89PNG"


def test_a_chat_image_of_another_build_is_not_found(tmp_path):
    import pytest
    from mises import chat_images
    from mises.photo_check import photo_bytes
    from mises.types import MisesError
    _blocks, refs = chat_images.store(tmp_path, "p1", [("image/png", _png_b64())])
    with pytest.raises(MisesError):
        photo_bytes(_ctx(tmp_path, "p2"), refs[0]["id"])


def test_chat_message_names_its_image_ids_for_the_photo_check():
    from mises.chat_images import attached_note
    note = attached_note([{"id": "a" * 24}, {"id": "b" * 24}])
    assert "a" * 24 in note and "b" * 24 in note and "reference_photo_check" in note
    assert attached_note([]) == ""
