"""Sketch storage: validation, photo normalisation, run attachment and the agent-facing context."""
import base64
import io
import json
import shutil
import subprocess

import pytest
from PIL import Image

from mises import sketch as sk
from mises.types import MisesError


def png_bytes(w=40, h=30, color=(255, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(w=40, h=30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(buf, "JPEG")
    return buf.getvalue()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def a_sketch(**kw) -> dict:
    d = {
        "units": "mm", "width_mm": 200, "height_mm": 150,
        "strokes": [
            {"id": "s1", "kind": "line", "points": [[10, 10], [50, 10]]},
            {"id": "s2", "kind": "rect", "points": [[10, 10], [50, 10], [50, 40], [10, 40]],
             "closed": True},
            {"id": "s3", "kind": "circle", "points": [[30, 35], [35, 30], [30, 25], [25, 30]],
             "closed": True, "center": [30, 30], "radius_mm": 5},
        ],
        "dimensions": [{"stroke_id": "s1", "text": "40", "value_mm": 40, "kind": "length"},
                       {"stroke_id": "s3", "text": "Ø10", "value_mm": 10, "kind": "diameter"}],
        "notes": "bracket, 5 mm thick",
    }
    d.update(kw)
    return d


def test_save_sketch_without_photo(tmp_path):
    out = sk.save_sketch(tmp_path, sk.SketchUpload.model_validate({"sketch": a_sketch()}))
    assert sk.SKETCH_ID_RE.match(out["sketch_id"])
    d = tmp_path / "sketches" / out["sketch_id"]
    data = json.loads((d / "sketch.json").read_text())
    assert data["strokes"][0]["points"] == [[10.0, 10.0], [50.0, 10.0]]
    assert out["photo"] is None and out["render"] is False
    assert out["strokes"] == 3 and out["dimensions"] == 2
    assert not list(d.glob("photo.*"))


def test_dimension_must_reference_a_stroke():
    bad = a_sketch(dimensions=[{"stroke_id": "nope", "text": "4", "value_mm": 4}])
    with pytest.raises(ValueError, match="unknown stroke"):
        sk.SketchUpload.model_validate({"sketch": bad})


@pytest.mark.parametrize("pts", [[[float("nan"), 1]], [[1e9, 1]], []])
def test_points_must_be_finite_and_bounded(pts):
    with pytest.raises(ValueError):
        sk.SketchUpload.model_validate({"sketch": a_sketch(strokes=[
            {"id": "s1", "kind": "freehand", "points": pts}], dimensions=[])})


def test_stroke_ids_unique_and_total_points_capped():
    with pytest.raises(ValueError, match="duplicate"):
        sk.SketchUpload.model_validate({"sketch": a_sketch(strokes=[
            {"id": "a", "kind": "line", "points": [[0, 0], [1, 1]]},
            {"id": "a", "kind": "line", "points": [[0, 0], [1, 1]]}], dimensions=[])})
    assert sk.MAX_TOTAL_POINTS == 5000
    ok = [{"id": f"s{i}", "kind": "freehand", "points": [[0, 0]] * 1000} for i in range(5)]
    sk.SketchUpload.model_validate({"sketch": a_sketch(strokes=ok, dimensions=[])})
    many = ok + [{"id": "x", "kind": "line", "points": [[0, 0]]}]
    with pytest.raises(ValueError, match="points"):
        sk.SketchUpload.model_validate({"sketch": a_sketch(strokes=many, dimensions=[])})


def test_png_photo_is_stored_as_jpeg(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(png_bytes()), "name": "a.png"}})
    out = sk.save_sketch(tmp_path, up)
    p = tmp_path / "sketches" / out["sketch_id"] / "photo.jpg"
    assert p.read_bytes()[:3] == b"\xff\xd8\xff"
    assert out["photo"] == {"source_type": "png", "media_type": "image/jpeg", "width": 40,
                            "height": 30, "bytes": p.stat().st_size}


def test_jpeg_photo_stays_jpeg(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(jpeg_bytes())}})
    out = sk.save_sketch(tmp_path, up)
    p = tmp_path / "sketches" / out["sketch_id"] / "photo.jpg"
    assert p.read_bytes()[:3] == b"\xff\xd8\xff" and out["photo"]["source_type"] == "jpeg"


def noisy_jpeg(w=4000, h=3000) -> bytes:
    """A camera-like photo: per-pixel noise (worst case for compression), under 10 MB."""
    import random
    rnd = random.Random(1)
    im = Image.frombytes("RGB", (w, h), rnd.randbytes(w * h * 3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=40)
    assert len(buf.getvalue()) < sk.MAX_PHOTO_BYTES
    return buf.getvalue()


def test_noisy_photo_fits_the_per_image_api_budget(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(noisy_jpeg())}})
    sid = sk.save_sketch(tmp_path, up)["sketch_id"]
    wd = tmp_path / "wd"
    sk.attach_to_run(tmp_path, sid, wd)
    [(media, data)] = sk.sketch_images(wd)
    assert media == "image/jpeg" and len(data) < sk.IMAGE_B64_BUDGET < 5 * 1024 * 1024


def test_budget_steps_size_down(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, "IMAGE_B64_BUDGET", 150_000)
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(noisy_jpeg(2000, 1500))}})
    out = sk.save_sketch(tmp_path, up)
    p = tmp_path / "sketches" / out["sketch_id"] / "photo.jpg"
    assert len(b64(p.read_bytes())) < 150_000
    assert out["photo"]["width"] < sk.MAX_IMAGE_EDGE


def _png_header(w: int, h: int) -> bytes:
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = lambda t, d: (struct.pack(">I", len(d)) + t + d
                          + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"x") + chunk(b"IEND", b"")


def test_oversized_dimensions_rejected_before_decoding(tmp_path):
    # 8000 x 8000 px (64 MP) declared in a few bytes: refused from the header, never decoded
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(_png_header(8000, 8000))}})
    with pytest.raises(MisesError) as e:
        sk.save_sketch(tmp_path, up)
    assert e.value.code.value == "invalid_input" and "too large" in e.value.message
    assert "8000 x 8000" in e.value.message
    # far past Pillow's own bomb limit: still "too large", not "damaged"
    huge = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                           "photo": {"data_b64": b64(_png_header(30000, 30000))}})
    with pytest.raises(MisesError) as e:
        sk.save_sketch(tmp_path, huge)
    assert "too large" in e.value.message
    render = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                             "render_png_b64": b64(_png_header(9000, 9000))})
    with pytest.raises(MisesError) as e:
        sk.save_sketch(tmp_path, render)
    assert "too large" in e.value.message


def test_large_photo_is_downscaled(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(png_bytes(4000, 1000))}})
    out = sk.save_sketch(tmp_path, up)
    assert out["photo"]["width"] == sk.MAX_IMAGE_EDGE
    assert out["photo"]["height"] == round(1000 * sk.MAX_IMAGE_EDGE / 4000)


def test_photo_type_is_checked_by_content(tmp_path):
    gif = b"GIF89a" + b"\0" * 20
    for raw in (gif, b"hello, not an image", b"\x89PNG\r\n\x1a\n" + b"garbage" * 4):
        up = sk.SketchUpload.model_validate({"sketch": a_sketch(), "photo": {"data_b64": b64(raw)}})
        with pytest.raises(MisesError) as e:
            sk.save_sketch(tmp_path, up)
        assert e.value.code.value == "invalid_input"
    assert not list((tmp_path / "sketches").glob("*/sketch.json"))


def test_photo_over_10_mb_is_rejected(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n" + b"\0" * (sk.MAX_PHOTO_BYTES + 1)
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(), "photo": {"data_b64": b64(raw)}})
    with pytest.raises(MisesError) as e:
        sk.save_sketch(tmp_path, up)
    assert "10 MB" in e.value.message


def test_bad_base64_is_rejected(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(), "photo": {"data_b64": "!!!not b64"}})
    with pytest.raises(MisesError):
        sk.save_sketch(tmp_path, up)


@pytest.mark.skipif(shutil.which("sips") is None, reason="HEIC conversion uses macOS sips")
def test_heic_photo_is_converted(tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(png_bytes(64, 48, (10, 200, 10)))
    heic = tmp_path / "in.heic"
    subprocess.run(["sips", "-s", "format", "heic", str(src), "--out", str(heic)],
                   check=True, capture_output=True)
    assert sk.sniff(heic.read_bytes()) == "heic"
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                         "photo": {"data_b64": b64(heic.read_bytes())}})
    out = sk.save_sketch(tmp_path / "home", up)
    p = tmp_path / "home" / "sketches" / out["sketch_id"] / "photo.jpg"
    assert p.read_bytes()[:3] == b"\xff\xd8\xff" and out["photo"]["source_type"] == "heic"
    assert (out["photo"]["width"], out["photo"]["height"]) == (64, 48)


def test_render_png_must_be_png(tmp_path):
    ok = sk.SketchUpload.model_validate({"sketch": a_sketch(), "render_png_b64": b64(png_bytes())})
    assert sk.save_sketch(tmp_path, ok)["render"] is True
    bad = sk.SketchUpload.model_validate({"sketch": a_sketch(),
                                          "render_png_b64": b64(jpeg_bytes())})
    with pytest.raises(MisesError):
        sk.save_sketch(tmp_path, bad)


def test_attach_context_and_images(tmp_path):
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(), "photo": {"data_b64": b64(png_bytes())},
                                         "render_png_b64": b64(png_bytes(20, 20))})
    sid = sk.save_sketch(tmp_path, up)["sketch_id"]
    wd = tmp_path / "runs" / "r1"
    wd.mkdir(parents=True)
    sk.attach_to_run(tmp_path, sid, wd)
    assert (wd / "sketch" / "sketch.json").exists() and (wd / "sketch" / "photo.jpg").exists()

    ctx = sk.sketch_context(wd, vision=True)
    assert ctx["sketch_id"] == sid and ctx["units"] == "mm"
    assert "+y up" in ctx["frame"]
    s1 = ctx["strokes"][0]
    assert s1["measured"]["length_mm"] == 40.0 and s1["measured"]["bbox_mm"] == [10, 10, 50, 10]
    assert ctx["strokes"][1]["measured"]["length_mm"] == 140.0          # closed rect perimeter
    assert ctx["strokes"][2]["radius_mm"] == 5 and ctx["strokes"][2]["center"] == [30, 30]
    assert "points" not in ctx["strokes"][2]          # a circle is its centre + radius
    assert ctx["dimensions"][1]["value_mm"] == 10
    assert ctx["notes"] == "bracket, 5 mm thick"
    assert ctx["images"] == [{"kind": "photo", "path": "sketch/photo.jpg", "attached": True},
                             {"kind": "render", "path": "sketch/render.png", "attached": True}]
    assert "image_note" not in ctx
    imgs = sk.sketch_images(wd)
    assert [m for m, _ in imgs] == ["image/jpeg", "image/png"]     # line-art render stays PNG
    assert base64.b64decode(imgs[0][1])[:3] == b"\xff\xd8\xff"
    assert base64.b64decode(imgs[1][1])[:4] == b"\x89PNG"

    blind = sk.sketch_context(wd, vision=False)
    assert all(not i["attached"] for i in blind["images"])
    assert "cannot see images" in blind["image_note"]


def test_attach_unknown_or_bad_id(tmp_path):
    for sid in ("sk_000000000000", "../../etc", "sk_ZZ"):
        with pytest.raises(MisesError) as e:
            sk.attach_to_run(tmp_path, sid, tmp_path / "wd")
        assert e.value.code.value in ("not_found", "invalid_input")


def test_no_sketch_no_context(tmp_path):
    assert sk.sketch_context(tmp_path, vision=True) is None
    assert sk.sketch_images(tmp_path) == []


def test_procedure_is_staged_with_three_questions_max():
    p = sk.SKETCH_PROCEDURE
    for word in ("strokes", "dimensions", "constraints", "at most 3", "ui_ask_user"):
        assert word in p
    assert p.index("strokes") < p.index("dimensions") < p.index("constraints")
    assert sk.VISION_PROVIDERS == {"anthropic", "claude-code", "openai", "gemini"}


def test_render_over_budget_becomes_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, "IMAGE_B64_BUDGET", 150_000)
    import random
    im = Image.frombytes("RGB", (600, 450), random.Random(2).randbytes(600 * 450 * 3))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    up = sk.SketchUpload.model_validate({"sketch": a_sketch(), "render_png_b64": b64(buf.getvalue())})
    sid = sk.save_sketch(tmp_path, up)["sketch_id"]
    wd = tmp_path / "wd"
    sk.attach_to_run(tmp_path, sid, wd)
    [(media, data)] = sk.sketch_images(wd)
    assert media == "image/jpeg" and len(data) < 150_000


def test_context_block_keeps_points_compact():
    from mises.claude_code.prompts import context_block
    up = sk.SketchUpload.model_validate({"sketch": a_sketch()})
    ctx = {"sketch": {"strokes": [{"points": [[10, 10], [50.5, 10]], "bbox_mm": [1, 2, 3, 4]}]},
           "selected_part_id": None}
    block = context_block(ctx)
    assert "[[10,10],[50.5,10]]" in block and "[1,2,3,4]" in block
    body = block.split("\n", 2)[2].rsplit("</mises-context>", 1)[0]
    assert json.loads(body) == ctx
    assert up.sketch.strokes
