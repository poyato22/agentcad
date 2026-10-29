"""The parts directory: built-in items (./data) plus items imported from a product page URL.

An item is {id, name, category, source: "builtin"|"url", url, vendor, specs: [{key, value,
unit, note?}], dims: {...}, mount: {pattern, hole_d, spacing_mm, thread?, layout?, holes_mm?}
|null, geometry: "builtin"|"envelope"|null} plus `sources` (built-ins: where the numbers come
from), `project_id` (URL items may belong to one build; null = every build) and, for URL items,
`raw_text`: the page's text, untrusted data shown to people as plain text and to agents only
inside nonce delimiters (see importer.wrap_untrusted).

`mount.hole_d` is the diameter of the hole in the part: the nominal thread diameter for a tapped
hole (a motor's M3 holes: 3.0), the drilled diameter otherwise (a Pi's 2.7 mm holes).
"""
from __future__ import annotations

import copy
import json
import re
import time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..store import Store
from ..types import ErrorCode, MisesError
from .builtin import builtin_by_id, builtin_items

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_items (
  id TEXT PRIMARY KEY, project_id TEXT, url TEXT, data TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS catalog_items_project ON catalog_items(project_id);
"""
SEARCH_MAX = 200
CATEGORIES = ("fastener", "bearing", "motor", "prop", "battery", "flight_controller", "servo",
              "stepper", "extrusion", "board", "other")

Short = Annotated[str, StringConstraints(max_length=200)]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    value: Short | float
    unit: Annotated[str, StringConstraints(max_length=20)] | None = None
    note: Short | None = None


class Mount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: Annotated[str, StringConstraints(max_length=40)] | None = None
    hole_d: float | None = Field(None, gt=0, le=100)
    spacing_mm: list[Annotated[float, Field(gt=0, le=1000)]] | None = Field(None, max_length=4)
    thread: Annotated[str, StringConstraints(max_length=12)] | None = None
    layout: Annotated[str, StringConstraints(max_length=12)] | None = None
    holes_mm: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        None, max_length=32)


DimKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class ItemEdit(BaseModel):
    """The user's review edits of an imported item."""
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    category: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")] | None = None
    vendor: Short | None = None
    specs: list[Spec] | None = Field(None, max_length=200)
    dims: dict[DimKey, float] | None = Field(None, max_length=40)
    mount: Mount | None = None


def _haystack(it: dict) -> str:
    parts = [it.get("id", ""), it.get("name", ""), it.get("category", ""),
             it.get("vendor") or ""]
    for s in it.get("specs") or []:
        parts += [str(s.get("key", "")), str(s.get("value", "")), str(s.get("unit") or "")]
    mount = it.get("mount") or {}
    parts.append(str(mount.get("pattern") or ""))
    return " ".join(parts).lower().replace("_", " ")


def summary(it: dict) -> dict:
    """The item without its page text (search results, lists)."""
    return {k: v for k, v in it.items() if k != "raw_text"}


class Catalog:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store._db.executescript(_SCHEMA)

    # ---- reads
    def _url_items(self, project_id: str | None = None, *, any_project: bool = True) -> list[dict]:
        if any_project:
            rows = self.store._all("SELECT data FROM catalog_items ORDER BY created_at")
        else:
            rows = self.store._all("SELECT data FROM catalog_items WHERE project_id IS NULL"
                                   " OR project_id=? ORDER BY created_at", (project_id,))
        return [json.loads(r[0]) for r in rows]

    def search(self, q: str = "", category: str | None = None, *,
               project_id: str | None = None, limit: int = 50) -> list[dict]:
        """Items whose id, name, category, vendor or specs contain every word of `q`
        (case-insensitive); name matches rank first. With `project_id`, URL items of other
        builds are left out."""
        words = [w for w in re.split(r"[\s_]+", (q or "").lower()) if w][:12]
        items = [*builtin_items(), *self._url_items(project_id,
                                                     any_project=project_id is None)]
        out = []
        for it in items:
            if category and it.get("category") != category:
                continue
            hay = _haystack(it)
            if not all(w in hay for w in words):
                continue
            name = it.get("name", "").lower()
            out.append((-sum(w in name for w in words), it.get("category", ""), name, it))
        out.sort(key=lambda t: t[:3])
        return [summary(copy.deepcopy(t[3])) for t in out[:max(1, min(limit, SEARCH_MAX))]]

    def get(self, item_id: str) -> dict:
        it = builtin_by_id().get(item_id)
        if it is not None:
            return copy.deepcopy(it)
        row = self.store._one("SELECT data FROM catalog_items WHERE id=?", (item_id,))
        if row is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no catalog item {item_id!r}",
                             hint="search the directory with catalog_search")
        return json.loads(row[0])

    # ---- URL items
    def save(self, item: dict, project_id: str | None = None) -> dict:
        """Store (or replace) an imported item; re-importing the same URL keeps its id."""
        if item.get("source") != "url" or not ID_RE.match(item.get("id", "")):
            raise MisesError(ErrorCode.INVALID_INPUT, "only URL items with a valid id are saved")
        if item["id"] in builtin_by_id():
            raise MisesError(ErrorCode.INVALID_INPUT, "id collides with a built-in item")
        item = {**item, "project_id": project_id}
        with self.store._tx() as db:
            db.execute("INSERT OR REPLACE INTO catalog_items VALUES (?,?,?,?,?)",
                       (item["id"], project_id, item.get("url"), json.dumps(item), time.time()))
        return item

    def update(self, item_id: str, patch: dict) -> dict:
        """The user's review edits of an imported item (name, category, vendor, specs, dims,
        mount). Raw page text and the source URL cannot be edited."""
        it = self.get(item_id)
        if it.get("source") != "url":
            raise MisesError(ErrorCode.INVALID_INPUT, "built-in items cannot be edited")
        try:
            edit = ItemEdit.model_validate(patch).model_dump(exclude_unset=True)
        except ValueError as e:
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad item edit: {e}",
                             hint="editable: name, category, vendor, specs, dims, mount") from None
        return self.save({**it, **edit}, it.get("project_id"))

    def delete(self, item_id: str) -> None:
        if item_id in builtin_by_id():
            raise MisesError(ErrorCode.INVALID_INPUT, "built-in items cannot be deleted",
                             hint="only items imported from a link can be deleted")
        with self.store._tx() as db:
            cur = db.execute("DELETE FROM catalog_items WHERE id=?", (item_id,))
        if cur.rowcount == 0:
            raise MisesError(ErrorCode.NOT_FOUND, f"no catalog item {item_id!r}")
