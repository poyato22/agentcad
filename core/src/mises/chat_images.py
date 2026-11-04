"""Images the user attaches to a chat message (P11 F7): pasted, dropped or picked in the composer.

Each image is checked by its content (PNG, JPEG, WebP, GIF, or HEIC converted with macOS's
sips), refused past MAX_PIXELS from its header, turned upright, shrunk to at most MAX_EDGE px
and re-encoded under MAX_BYTES (PNG when that fits and the source was lossless, else JPEG,
stepping the size then the quality down). At most MAX_IMAGES per message. The files are kept
under the build (home/chat_images/<build>/<id>.<ext>, never inline in the DB): the user's
message step lists them as refs ({id, media_type, width, height, bytes}) and the app shows
them from GET /projects/{id}/chat-images/{image_id}. The agent gets them as real image blocks.
Text inside an image is data, never instructions (the harness rules say so).
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import io
from pathlib import Path

from .sketch import (MAX_PIXELS, SIPS, _heic_size, _heic_to_png, _too_large, sniff)
from .types import ErrorCode, MisesError

MAX_IMAGES = 5
MAX_EDGE = 2048
MAX_BYTES = 1_000_000              # each, after re-encoding
MAX_UPLOAD_BYTES = 6 * 1000 * 1000      # = server ImageIn.data_b64 max (8 M chars)
MIN_EDGE = 512
DIR = "chat_images"
_EXT = {"png": "png", "jpeg": "jpg"}
_ID_LEN = 24


def _kind(raw: bytes) -> str | None:
    k = sniff(raw)
    if k:
        return k
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def _encode(im, lossless: bool) -> tuple[bytes, str]:
    from PIL import Image
    if lossless:
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        if buf.tell() < MAX_BYTES:
            return buf.getvalue(), "png"
    rgb = im
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, (255, 255, 255))
        rgb.paste(rgba, mask=rgba.split()[-1])
    elif im.mode != "RGB":
        rgb = im.convert("RGB")
    quality = 85
    while True:
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() < MAX_BYTES:
            return buf.getvalue(), "jpeg"
        if max(rgb.size) > MIN_EDGE:
            rgb = rgb.resize((max(1, int(rgb.width * 0.8)), max(1, int(rgb.height * 0.8))),
                             Image.Resampling.LANCZOS)
        elif quality > 40:
            quality -= 15
        else:
            raise MisesError(ErrorCode.INVALID_INPUT, "this image cannot be made small enough",
                             hint="attach a smaller or simpler image")


def normalize(raw: bytes) -> tuple[bytes, str, tuple[int, int]]:
    """(bytes, "png" | "jpeg", (w, h)) of one attached image, ready for the model."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MisesError(ErrorCode.INVALID_INPUT, "an attached image is larger than 6 MB",
                         hint="attach a smaller image")
    kind = _kind(raw)
    if kind is None:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         "attached images must be PNG, JPEG, WebP, GIF or HEIC")
    if kind == "heic":
        if not Path(SIPS).exists():
            raise MisesError(ErrorCode.INVALID_INPUT, "HEIC images need macOS to convert",
                             hint="export it as JPEG or PNG and attach that")
        size = _heic_size(raw)
        if size and size[0] * size[1] > MAX_PIXELS:
            raise _too_large(*size, "image")
        raw, kind = _heic_to_png(raw), "png"
    try:
        im = Image.open(io.BytesIO(raw), formats=[kind.upper()])
        if im.width * im.height > MAX_PIXELS:
            raise _too_large(im.width, im.height, "image")
        with im:
            if im.format == "JPEG":
                im.draft("RGB", (MAX_EDGE, MAX_EDGE))
            im.seek(0)
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            im = ImageOps.exif_transpose(im)
            data, fmt = _encode(im, lossless=kind in ("png", "gif"))
        with Image.open(io.BytesIO(data)) as out:
            return data, fmt, out.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise MisesError(ErrorCode.INVALID_INPUT, "could not read an attached image",
                         hint="the file may be damaged; try exporting it again") from None


def build_dir(home: Path, project_id: str) -> Path:
    return Path(home) / DIR / project_id


def store(home: Path, project_id: str, images: list[tuple[str, str]]
          ) -> tuple[list[tuple[str, str]], list[dict]]:
    """The images of one message, normalized and saved under the build: ((media_type, base64)
    blocks for the agent, refs for the message step). `images` are (media_type, base64) as the
    app sent them; the media type is only a label, the content decides."""
    if len(images) > MAX_IMAGES:
        raise MisesError(ErrorCode.INVALID_INPUT,
                         f"at most {MAX_IMAGES} images per message ({len(images)} attached)",
                         hint="send the rest in another message")
    blocks, refs = [], []
    d = build_dir(home, project_id)
    for _mt, b64 in images:
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise MisesError(ErrorCode.INVALID_INPUT, "an attached image is not valid base64"
                             ) from None
        data, fmt, (w, h) = normalize(raw)
        iid = hashlib.sha256(data).hexdigest()[:_ID_LEN]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{iid}.{_EXT[fmt]}"
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        mt = f"image/{fmt}"
        blocks.append((mt, base64.b64encode(data).decode()))
        refs.append({"id": iid, "media_type": mt, "width": w, "height": h, "bytes": len(data)})
    return blocks, refs


def attached_note(refs: list[dict]) -> str:
    """The line appended to a message that carries images, naming their ids so the agent can
    check a model against one (reference_photo_check accepts them). Empty without images."""
    if not refs:
        return ""
    ids = ", ".join(r["id"] for r in refs)
    return (f"\n\n[Attached image ids: {ids}. When an image shows the object to model, verify "
            "your model against it with reference_photo_check (reference_image_id = the id, "
            "view = the viewpoint it shows) before you finish.]")


def forget_build(home: Path, project_id: str) -> int:
    """Remove a purged build's chat images (fix round 1). Only files this module writes, in
    the build's own directory; returns how many went."""
    d = build_dir(home, project_id)
    if d.is_symlink() or not d.is_dir() or d.parent.resolve() != (Path(home) / DIR).resolve():
        return 0
    n = 0
    for p in d.iterdir():
        if p.is_file() and not p.is_symlink() and p.suffix in (".png", ".jpg", ".tmp"):
            p.unlink(missing_ok=True)
            n += 1
    with contextlib.suppress(OSError):
        d.rmdir()
    return n


def path_of(home: Path, project_id: str, image_id: str) -> Path | None:
    """The stored file of `image_id` in this build, or None (ids are hex, so no traversal)."""
    if len(image_id) != _ID_LEN or any(c not in "0123456789abcdef" for c in image_id):
        return None
    d = build_dir(home, project_id)
    for ext in _EXT.values():
        p = d / f"{image_id}.{ext}"
        if p.is_file():
            return p
    return None
