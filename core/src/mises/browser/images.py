"""The snapshot image proxy: the app never loads a web image itself. The core fetches it with
the hardened fetcher (https only, public addresses pinned, redirects re-checked, no cookies),
caps it at IMAGE_MAX_BYTES, decodes it with Pillow (PNG, JPEG, GIF or WebP only; SVG and
anything else refused; at most MAX_PIXELS pixels, so no decompression bombs), shrinks it to
MAX_SIDE and re-encodes it as PNG, which also drops metadata. Results are cached under
<home>/browser/images.

The image hosts are whatever the page named (often a CDN on another domain): they are not
asked an approval card, because the user's own view loads them, not an agent, and nothing an
agent wrote goes into those URLs.
"""
from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

from ..catalog.importer import fetch
from ..types import ErrorCode, MisesError

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
IMAGE_MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 1600
_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "MPO"}
_NAME_RE = re.compile(r"^snap_[0-9a-f]{16}_\d{1,2}\.png$")


def _reencode(data: bytes) -> bytes:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "GIF", "WEBP"]) as im:
            if im.format not in _FORMATS:
                raise MisesError(ErrorCode.INVALID_INPUT, "not a supported image")
            w, h = im.size
            if w * h > MAX_PIXELS:
                raise MisesError(ErrorCode.INVALID_INPUT, "image too large")
            im.seek(0)
            im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, "PNG", optimize=True)
            return out.getvalue()
    except MisesError:
        raise
    except Exception:  # noqa: BLE001 - any decoder failure is "not an image"
        raise MisesError(ErrorCode.INVALID_INPUT, "not a supported image") from None


def cache_dir(home: Path) -> Path:
    return Path(home) / "browser" / "images"


def forget(home: Path, snapshot_ids: list[str]) -> None:
    """Remove the cached images of these snapshots (their run is being deleted)."""
    d = cache_dir(home)
    if not d.is_dir():
        return
    for sid in snapshot_ids:
        if not re.fullmatch(r"snap_[0-9a-f]{16}", sid):
            continue
        for p in d.glob(f"{sid}_*.png"):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            except OSError:
                pass


async def proxied(home: Path, rec: dict, n: int, http: dict | None = None) -> bytes:
    """Image `n` of snapshot `rec` as PNG bytes (cached)."""
    images = rec.get("_images") or []
    if not 0 <= n < len(images):
        raise MisesError(ErrorCode.NOT_FOUND, f"snapshot {rec['id']} has no image {n}")
    name = f"{rec['id']}_{n}.png"
    if not _NAME_RE.match(name):
        raise MisesError(ErrorCode.NOT_FOUND, "no such image")
    d = cache_dir(home)
    path = d / name
    if path.is_file() and not path.is_symlink():
        return path.read_bytes()
    page = await fetch(images[n]["src"], types=IMAGE_TYPES, max_bytes=IMAGE_MAX_BYTES,
                       accept=", ".join(IMAGE_TYPES), **(http or {}))
    png = await asyncio.to_thread(_reencode, page.body or b"")
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_bytes(png)
    tmp.replace(path)
    return png
