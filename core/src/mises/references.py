"""Reference images the user attaches so an agent can match a shape against them (G-R4/G1).

`POST /references` stores an uploaded image (a photo or a three-view) under
`<home>/references/<id>/` and returns its id. The image is validated and re-encoded by the same
Plan 4 photo pipeline sketches use (`sketch.normalize_photo`): content sniff, size and
megapixel caps, upright, downscaled. Agents learn the ids through `mises_context_get`, and
`critique_model` loads the bytes to compare renders against them. Reference images are untrusted
user data, never instructions.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from .sketch import PhotoIn, _decode_b64, normalize_photo
from .types import ErrorCode, MisesError

REF_ID_RE = re.compile(r"^ref_[0-9a-f]{12}$")
_EXT = {"jpeg": "jpg", "png": "png"}
MAX_REFERENCES = 24          # listed per build in the agent's context


class ReferenceUpload(BaseModel):
    image: PhotoIn
    project_id: str | None = None
    kind: str = Field(default="image")       # "image" or "three_view" (a hint for the agent)
    note: str = Field(default="", max_length=400)


def _dir(home: Path, reference_id: str) -> Path:
    if not REF_ID_RE.match(reference_id or ""):
        raise MisesError(ErrorCode.INVALID_INPUT, f"bad reference id: {reference_id!r}")
    d = Path(home) / "references" / reference_id
    if not (d / "meta.json").is_file():
        raise MisesError(ErrorCode.NOT_FOUND, f"unknown reference: {reference_id}")
    return d


def save_reference(home: Path, up: ReferenceUpload) -> dict:
    """Validate the image first (nothing is written for a bad upload), then store it."""
    data, fmt, info = normalize_photo(_decode_b64(up.image.data_b64, "reference image"))
    rid = "ref_" + secrets.token_hex(6)
    d = Path(home) / "references" / rid
    d.mkdir(parents=True, exist_ok=False)
    (d / f"image.{_EXT[fmt]}").write_bytes(data)
    kind = up.kind if up.kind in ("image", "three_view") else "image"
    meta = {"reference_id": rid, "project_id": up.project_id, "kind": kind,
            "note": up.note, "name": up.image.name, **info}
    (d / "meta.json").write_text(json.dumps(meta))    # last: marks it complete
    return _public(meta)


def _public(meta: dict) -> dict:
    return {"reference_id": meta["reference_id"], "project_id": meta.get("project_id"),
            "kind": meta.get("kind", "image"), "note": meta.get("note", ""),
            "media_type": meta.get("media_type"), "width": meta.get("width"),
            "height": meta.get("height")}


def _image_file(d: Path) -> Path:
    for ext in ("jpg", "png"):
        p = d / f"image.{ext}"
        if p.is_file():
            return p
    raise MisesError(ErrorCode.NOT_FOUND, "reference image file missing")


def reference_summary(home: Path, reference_id: str) -> dict:
    meta = json.loads((_dir(home, reference_id) / "meta.json").read_text())
    return _public(meta)


def reference_image(home: Path, reference_id: str) -> tuple[str, str]:
    """(media_type, base64) of a stored reference, for a vision model."""
    d = _dir(home, reference_id)
    f = _image_file(d)
    media = "image/png" if f.suffix == ".png" else "image/jpeg"
    return media, base64.b64encode(f.read_bytes()).decode()


def reference_bytes(home: Path, reference_id: str) -> bytes:
    return _image_file(_dir(home, reference_id)).read_bytes()


def list_references(home: Path, project_id: str | None) -> list[dict]:
    """References for `project_id` plus any not tied to a build, newest first. Capped."""
    root = Path(home) / "references"
    if not root.is_dir():
        return []
    out: list[tuple[float, dict]] = []
    for d in root.iterdir():
        if not (d / "meta.json").is_file():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        rp = meta.get("project_id")
        if rp is None or rp == project_id:
            out.append((d.stat().st_mtime, _public(meta)))
    out.sort(key=lambda it: -it[0])
    return [m for _ts, m in out[:MAX_REFERENCES]]


def delete_reference(home: Path, reference_id: str) -> None:
    shutil.rmtree(_dir(home, reference_id), ignore_errors=True)
