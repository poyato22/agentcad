"""Layout skeletons (Plan 8 G3): one per build, the top-down reference the parts are built against.

A skeleton holds named parameters (`wing_span`), datums (planes, axes, points), an envelope box and
reference curves. It is a part-tree row of kind "skeleton" (id `skeleton_<build id>`), but it has no
build script and no part dir: its data lives in the host-only meta dir
(<home>/parts/.mises-meta/<skeleton id>/skeleton.json), next to its C6 version file, so no build
sandbox can write it.

Before every build of a part the host stages a snapshot of that part's build's skeleton in the
part's own meta dir (`skeleton_in.json`); the sandboxed worker reads it (read-only to the sandbox)
and exposes `skeleton()`, `datum()`, `curve()` and `envelope()` to the script
(cad/layout_worker.py).
The names a successful build actually read are recorded per part build (`skeleton_refs.json`), and
a skeleton change regenerates exactly the parts whose recorded names changed
(cad/skeleton_tools.py).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .. import edits
from ..naming import check_name
from ..types import MisesError
from . import safe_io

DATA_NAME = "skeleton.json"
SKELETON_IN = "skeleton_in.json"
REFS_NAME = "skeleton_refs.json"
KIND = "skeleton"
MAX_PARAMS, MAX_DATUMS, MAX_CURVES, MAX_POINTS = 200, 100, 50, 500
_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PART_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_CATS = ("params", "datums", "curves")

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]


def _finite(v: list[float] | None, what: str) -> list[float] | None:
    if v is not None and not all(math.isfinite(float(x)) for x in v):
        raise ValueError(f"{what} must be finite numbers")
    return v


def _unit(v: list[float], what: str) -> list[float]:
    n = math.sqrt(sum(float(x) ** 2 for x in v))
    if n < 1e-9:
        raise ValueError(f"{what} must not be a zero vector")
    return [float(x) / n for x in v]


class Datum(BaseModel):
    kind: Literal["plane", "axis", "point"]
    origin: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    normal: Vec3 | None = Field(default=None, description="plane: its normal (z direction)")
    x_dir: Vec3 | None = Field(default=None, description="plane: optional in-plane x direction")
    direction: Vec3 | None = Field(default=None, description="axis: its direction")

    @model_validator(mode="after")
    def _shape(self) -> "Datum":
        for name in ("origin", "normal", "x_dir", "direction"):
            _finite(getattr(self, name), f"datum {name}")
        if self.kind == "plane":
            if self.normal is None:
                raise ValueError("a plane datum needs a normal")
            n = _unit(self.normal, "a plane normal")
            if self.x_dir is not None:
                x = _unit(self.x_dir, "a plane x_dir")
                if abs(sum(a * b for a, b in zip(n, x))) > 0.999:
                    raise ValueError("a plane's x_dir must not be parallel to its normal")
        elif self.kind == "axis":
            if self.direction is None:
                raise ValueError("an axis datum needs a direction")
            _unit(self.direction, "an axis direction")
        return self


class Curve(BaseModel):
    kind: Literal["polyline", "spline"]
    points: list[Vec3] = Field(min_length=2, max_length=MAX_POINTS)
    closed: bool = False

    @field_validator("points")
    @classmethod
    def _pts(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            _finite(p, "curve points")
        return v


class Envelope(BaseModel):
    min: Vec3
    max: Vec3

    @model_validator(mode="after")
    def _order(self) -> "Envelope":
        _finite(self.min, "envelope min")
        _finite(self.max, "envelope max")
        if any(a >= b for a, b in zip(self.min, self.max)):
            raise ValueError("envelope min must be below max on every axis")
        return self


class SkeletonData(BaseModel):
    params: dict[str, float] = Field(default_factory=dict)
    datums: dict[str, Datum] = Field(default_factory=dict)
    envelope: Envelope | None = None
    curves: dict[str, Curve] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _names(self) -> "SkeletonData":
        if len(self.params) > MAX_PARAMS or len(self.datums) > MAX_DATUMS \
                or len(self.curves) > MAX_CURVES:
            raise ValueError(f"a skeleton holds at most {MAX_PARAMS} params, {MAX_DATUMS} datums "
                             f"and {MAX_CURVES} curves")
        seen: set[str] = set()
        for cat in _CATS:
            for name in getattr(self, cat):
                if not _REF_RE.fullmatch(name):
                    raise ValueError(f"skeleton name {name!r}: letters, digits and underscores")
                try:
                    check_name(name)
                except MisesError as e:
                    raise ValueError(e.message) from None
                if name in seen:
                    raise ValueError(f"skeleton name {name!r} is used twice")
                seen.add(name)
        for name, v in self.params.items():
            if not math.isfinite(float(v)):
                raise ValueError(f"skeleton param {name!r} must be a finite number")
        return self


# ---- where things live

def skeleton_id(project_id: str) -> str:
    """The skeleton's part-tree id for a build: `skeleton_<build id>` (a valid part id, so it
    can never collide with a part dir name of another kind)."""
    slug = re.sub(r"[^a-z0-9_]", "_", str(project_id).lower())[:30] or "build"
    return f"skeleton_{slug}"


def _skel_dir(parts_root: Path, project_id: str) -> Path:
    """The skeleton's (never created) part dir path: C6's meta helpers key off it."""
    return Path(parts_root) / skeleton_id(project_id)


def load(parts_root: Path, project_id: str) -> tuple[SkeletonData | None, int]:
    """(skeleton, version); (None, version) when the build has none (or it is unreadable)."""
    d = _skel_dir(parts_root, project_id)
    version = edits.read_version(d)
    try:
        raw = safe_io.read_json(edits.meta_dir(d), DATA_NAME)
    except (OSError, ValueError, safe_io.UnsafePath):
        raw = None
    if not isinstance(raw, dict):
        return None, version
    try:
        return SkeletonData.model_validate(raw), version
    except ValueError:
        return None, version


def save(parts_root: Path, project_id: str, data: SkeletonData, version: int) -> None:
    d = _skel_dir(parts_root, project_id)
    md = edits.meta_dir(d)
    safe_io.ensure_dir(md)
    safe_io.write_text(md, DATA_NAME, data.model_dump_json())
    edits.write_version(d, version)


def version_of(parts_root: Path, project_id: str) -> int:
    return edits.read_version(_skel_dir(parts_root, project_id))


def snapshot(parts_root: Path, project_id: str, data: SkeletonData, version: int) -> dict:
    return {"skeleton_id": skeleton_id(project_id), "project_id": project_id,
            "version": version, **data.model_dump(mode="json"),
            "parts_root": str(Path(parts_root).resolve())}


def stage(parts_root: Path, part_dir: Path, project_id: str | None) -> dict | None:
    """Write the snapshot the part's next build reads (host-only meta dir), or remove a stale one
    when the build has no skeleton. Returns the snapshot, or None."""
    md = edits.meta_dir(part_dir)
    data, version = load(parts_root, project_id) if project_id else (None, 0)
    if data is None:
        if safe_io.exists(md, SKELETON_IN):
            safe_io.unlink(md, SKELETON_IN)
        return None
    snap = snapshot(parts_root, project_id, data, version)
    safe_io.ensure_dir(md)
    safe_io.write_text(md, SKELETON_IN, json.dumps(snap))
    return snap


def staged_file(part_dir: Path) -> Path | None:
    """The staged snapshot for this part dir, if one exists (what run_build hands the worker)."""
    md = edits.meta_dir(safe_io.real_dir(part_dir))
    return md / SKELETON_IN if safe_io.exists(md, SKELETON_IN) else None


def staged(part_dir: Path) -> dict | None:
    try:
        v = safe_io.read_json(edits.meta_dir(part_dir), SKELETON_IN)
    except (OSError, ValueError, safe_io.UnsafePath):
        return None
    return v if isinstance(v, dict) else None


# ---- references recorded per build

def clean_refs(raw) -> dict:
    """The worker's skeleton_refs (untrusted: the part script ran in that process), reduced to
    well-formed names, capped."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for cat in _CATS:
        vals = raw.get(cat)
        names = [v for v in vals if isinstance(v, str) and _REF_RE.fullmatch(v)] \
            if isinstance(vals, list) else []
        out[cat] = sorted(set(names))[:MAX_PARAMS]
    out["envelope"] = raw.get("envelope") is True
    parts = raw.get("parts")
    out["parts"] = sorted({p for p in parts if isinstance(p, str) and _PART_RE.fullmatch(p)})[:50] \
        if isinstance(parts, list) else []
    return out


def record_refs(part_dir: Path, raw) -> dict:
    """After a successful build: what it read, with the skeleton version it was built against
    (from the snapshot staged for it). Written to the part's host-only meta dir."""
    refs = clean_refs(raw)
    snap = staged(part_dir) or {}
    sv = snap.get("version")
    rec = {**refs, "skeleton_id": snap.get("skeleton_id"),
           "skeleton_version": sv if isinstance(sv, int) else None}
    md = edits.meta_dir(part_dir)
    safe_io.ensure_dir(md)
    safe_io.write_text(md, REFS_NAME, json.dumps(rec))
    return rec


def mark_stale(part_dir: Path, skeleton_version: int, reason: str) -> None:
    """A dependent that was not regenerated for skeleton `skeleton_version` (its rebuild failed
    or was out of scope): flagged until its next successful build rewrites the record."""
    rec = read_refs(part_dir)
    if rec is None:
        return
    rec["stale"] = {"skeleton_version": int(skeleton_version), "reason": reason[:200]}
    try:
        safe_io.write_text(edits.meta_dir(part_dir), REFS_NAME, json.dumps(rec))
    except (OSError, safe_io.UnsafePath):
        pass


def read_refs(part_dir: Path) -> dict | None:
    try:
        v = safe_io.read_json(edits.meta_dir(part_dir), REFS_NAME)
    except (OSError, ValueError, safe_io.UnsafePath):
        return None
    return v if isinstance(v, dict) else None


def uses_skeleton(refs: dict | None) -> bool:
    return bool(refs) and (any(refs.get(c) for c in _CATS) or bool(refs.get("envelope")))


def changed_names(old: SkeletonData | None, new: SkeletonData) -> dict:
    """What a skeleton edit changed: {params|datums|curves: {names}, envelope: bool}."""
    o = (old or SkeletonData()).model_dump(mode="json")
    n = new.model_dump(mode="json")
    out: dict = {}
    for cat in _CATS:
        a, b = o[cat], n[cat]
        out[cat] = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    out["envelope"] = o["envelope"] != n["envelope"]
    return out


def depends(refs: dict | None, changed: dict, skel_id: str) -> bool:
    """Whether a part built with `refs` must be regenerated after `changed`."""
    if not refs or refs.get("skeleton_id") not in (None, skel_id):
        return False
    if refs.get("stale"):                 # missed an earlier regeneration: catch it up now
        return True
    if changed.get("envelope") and refs.get("envelope"):
        return True
    return any(set(refs.get(c) or ()) & changed.get(c, set()) for c in _CATS)
