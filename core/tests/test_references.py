"""Reference-image storage (G-R4): validation reuses the Plan 4 photo pipeline."""
import base64
import io

import pytest

from mises import references as refs
from mises.references import PhotoIn, ReferenceUpload
from mises.types import ErrorCode, MisesError


def _png_b64(color=(10, 20, 30), size=(40, 40)) -> str:
    from PIL import Image
    im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_save_and_load_reference(tmp_path):
    up = ReferenceUpload(image=PhotoIn(data_b64=_png_b64(), name="three_view.png"),
                         project_id="pA", kind="three_view", note="front/top/side")
    saved = refs.save_reference(tmp_path, up)
    rid = saved["reference_id"]
    assert rid.startswith("ref_")
    assert saved["kind"] == "three_view" and saved["project_id"] == "pA"
    media, b64 = refs.reference_image(tmp_path, rid)
    assert media in ("image/jpeg", "image/png") and len(base64.b64decode(b64)) > 0
    assert len(refs.reference_bytes(tmp_path, rid)) > 0


def test_list_references_scoped_to_build(tmp_path):
    a = refs.save_reference(tmp_path, ReferenceUpload(image=PhotoIn(data_b64=_png_b64()),
                                                      project_id="pA"))
    refs.save_reference(tmp_path, ReferenceUpload(image=PhotoIn(data_b64=_png_b64()),
                                                  project_id="pB"))
    glob = refs.save_reference(tmp_path, ReferenceUpload(image=PhotoIn(data_b64=_png_b64()),
                                                         project_id=None))
    ids = {r["reference_id"] for r in refs.list_references(tmp_path, "pA")}
    assert a["reference_id"] in ids          # the build's own
    assert glob["reference_id"] in ids       # and build-less references
    assert len(ids) == 2                     # not pB's


def test_bad_image_rejected_and_nothing_written(tmp_path):
    up = ReferenceUpload(image=PhotoIn(data_b64=base64.b64encode(b"not an image").decode()))
    with pytest.raises(MisesError) as e:
        refs.save_reference(tmp_path, up)
    assert e.value.code == ErrorCode.INVALID_INPUT
    assert not (tmp_path / "references").exists() or not any((tmp_path / "references").iterdir())


def test_unknown_reference_not_found(tmp_path):
    with pytest.raises(MisesError) as e:
        refs.reference_summary(tmp_path, "ref_000000000000")
    assert e.value.code == ErrorCode.NOT_FOUND
