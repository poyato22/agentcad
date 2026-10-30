"""The parts list (BOM) of a build (Plan 7 U4c): made parts and bought parts with quantity,
cost, vendor, specs, totals and a CSV export.

Rows:
- **Made parts come in automatically**: every part in the build's tree (`kind: "part"`) is a
  row (`id: "auto_<part_id>"`, `source: "auto"`). Its cost is an estimate: the part's volume
  times the profile's default material density gives the mass, times that material's $/kg
  (the slicer's filament prices). It is labelled `estimate: true` with the basis in
  `estimate_note`.
- Catalog parts inserted into the build (`kind: "catalog"`) come in the same way as bought
  rows, priced from their parts-directory item when the item has a price.
- Stored rows: what the user adds (POST, `source: "user"`) or an agent adds (`bom_add`,
  `source: "agent"`). Editing an automatic row stores it (same id) with the edit; a made part's
  cost stays a live estimate until someone sets `unit_cost` (null brings the estimate back).
  Deleting a row of a part that is still in the build hides it (it would come back
  otherwise).

Text in rows (names, vendors, specs, notes, URLs) may come from web pages via an agent: the
app shows it as plain text, the CSV export neutralizes spreadsheet formulas, and agents read it
back only inside an untrusted block.
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ..cad import safe_io
from ..catalog.importer import check_url
from ..store import Store
from ..types import ErrorCode, MisesError

PART_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
ITEM_RE = re.compile(r"^(?:bom_[0-9a-f]{12}|auto_[a-z][a-z0-9_]{1,40})$")
ROWS_MAX = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bom_items (
  id TEXT, project_id TEXT, data TEXT, created_at REAL, PRIMARY KEY (project_id, id));
"""

Short = Annotated[str, StringConstraints(max_length=200)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class SpecRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    value: Short | float
    unit: Annotated[str, StringConstraints(max_length=20)] | None = None


def _url(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    return check_url(v)


class BomItemIn(BaseModel):
    """A new row (POST /projects/{id}/bom)."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["made", "bought"] = "bought"
    part_id: Annotated[str, StringConstraints(pattern=PART_RE.pattern)] | None = None
    catalog_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    qty: int = Field(1, ge=1, le=100_000)
    unit_cost: float | None = Field(None, ge=0, lt=1e7)
    currency: Currency = "USD"
    vendor: Short | None = None
    url: str | None = Field(None, max_length=2048)
    specs: list[SpecRow] = Field(default_factory=list, max_length=40)
    notes: Annotated[str, StringConstraints(max_length=2000)] = ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _url(v)


class BomPatch(BaseModel):
    """An edit (PATCH /projects/{id}/bom/{item_id}): only the fields sent change."""
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    qty: int | None = Field(None, ge=1, le=100_000)
    unit_cost: float | None = Field(None, ge=0, lt=1e7)
    currency: Currency | None = None
    vendor: Short | None = None
    url: str | None = Field(None, max_length=2048)
    specs: list[SpecRow] | None = Field(None, max_length=40)
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _url(v)


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def validate(model: type[BaseModel], body) -> BaseModel:
    if not isinstance(body, dict):
        raise _bad("body must be a JSON object")
    try:
        return model.model_validate(body)
    except MisesError:
        raise
    except ValueError as e:
        msgs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()] \
            if hasattr(e, "errors") else [str(e)]
        raise _bad("invalid parts list row: " + "; ".join(msgs)) from None


class BomStore:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store._db.executescript(_SCHEMA)

    def rows(self, project_id: str) -> list[dict]:
        rows = self.store._all("SELECT data FROM bom_items WHERE project_id=? ORDER BY"
                               " created_at, rowid", (project_id,))
        return [json.loads(r[0]) for r in rows]

    def get(self, project_id: str, item_id: str) -> dict | None:
        row = self.store._one("SELECT data FROM bom_items WHERE project_id=? AND id=?",
                              (project_id, item_id))
        return json.loads(row[0]) if row else None

    def find(self, project_id: str, *, part_id: str | None = None,
             catalog_id: str | None = None, url: str | None = None) -> dict | None:
        """The visible stored row for the same part, catalog item or product URL."""
        for r in self.rows(project_id):
            if r.get("hidden"):
                continue
            if (part_id and r.get("part_id") == part_id) or \
                    (catalog_id and r.get("catalog_id") == catalog_id) or \
                    (url and r.get("url") == url):
                return r
        return None

    def put(self, project_id: str, row: dict) -> dict:
        with self.store._tx() as db:
            n = db.execute("SELECT COUNT(*) FROM bom_items WHERE project_id=?",
                           (project_id,)).fetchone()[0]
            old = db.execute("SELECT created_at FROM bom_items WHERE project_id=? AND id=?",
                             (project_id, row["id"])).fetchone()
            if old is None and n >= ROWS_MAX:
                raise _bad(f"a parts list holds at most {ROWS_MAX} rows")
            created = old[0] if old else row.get("created_at") or time.time()
            row = {**row, "project_id": project_id, "created_at": created,
                   "updated_at": time.time()}
            db.execute("INSERT OR REPLACE INTO bom_items VALUES (?,?,?,?)",
                       (row["id"], project_id, json.dumps(row), created))
        return row

    def remove(self, project_id: str, item_id: str) -> None:
        with self.store._tx() as db:
            db.execute("DELETE FROM bom_items WHERE project_id=? AND id=?",
                       (project_id, item_id))

    def delete_project(self, project_id: str) -> None:
        with self.store._tx() as db:
            db.execute("DELETE FROM bom_items WHERE project_id=?", (project_id,))


def new_id() -> str:
    return "bom_" + secrets.token_hex(6)


# ---- made-part cost estimates

def material_price(material: str) -> tuple[float, float] | None:
    """(density g/cm3, $/kg) of a filament, from the slicer's table; None when unknown."""
    from ..print.slicer import MATERIALS
    m = MATERIALS.get((material or "").upper())
    return (m.density_g_cm3, m.usd_per_kg) if m else None


def _metrics(home: Path, part_id: str) -> dict | None:
    try:  # safe_io: never follows a link a build left in the part dir
        m = safe_io.read_json(Path(home) / "parts" / part_id, "metrics.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        return None
    return m if isinstance(m, dict) else None


def made_estimate(home: Path, part_id: str, material: str) -> dict:
    """{unit_cost, mass_g, note} for a made part: volume x density = mass, x $/kg."""
    m = _metrics(home, part_id)
    vol = (m or {}).get("volume_mm3")
    if not isinstance(vol, (int, float)) or isinstance(vol, bool) or vol <= 0:
        return {"unit_cost": None, "mass_g": None,
                "note": "no cost estimate: the part has not been built"}
    price = material_price(material)
    if price is None:
        return {"unit_cost": None, "mass_g": None,
                "note": f"no cost estimate: no $/kg for material {material}"}
    density, usd_kg = price
    mass_g = vol / 1000.0 * density
    cost = round(mass_g / 1000.0 * usd_kg, 2)
    return {"unit_cost": cost, "mass_g": round(mass_g, 2), "bbox": (m.get("bbox") or {}).get("size"),
            "note": f"estimate: {mass_g:.1f} g of {material.upper()} x ${usd_kg:g}/kg"
                    " (material only)"}


# ---- the view

def _catalog_item(catalog, item_id: str | None) -> dict | None:
    if catalog is None or not item_id:
        return None
    try:
        return catalog.get(item_id)
    except MisesError:
        return None


def _catalog_part_item(home: Path, part_id: str, catalog) -> dict | None:
    try:
        meta = safe_io.read_json(Path(home) / "parts" / part_id, "part.json")
    except (OSError, ValueError, safe_io.UnsafePath):
        meta = None
    item_id = meta.get("catalog_item_id") if isinstance(meta, dict) else None
    return _catalog_item(catalog, item_id)


def _short_specs(specs: list | None, n: int = 12) -> list[dict]:
    out = []
    for s in (specs or [])[:n]:
        if isinstance(s, dict) and isinstance(s.get("value"), (str, int, float)):
            out.append({"key": str(s.get("key"))[:80], "value": s["value"],
                        "unit": s.get("unit")})
    return out


def _auto_row(part, home: Path, catalog, material: str) -> dict:
    row = {"id": f"auto_{part.id}", "part_id": part.id, "catalog_id": None, "name": part.label,
           "qty": 1, "unit_cost": None, "currency": "USD", "vendor": None, "url": None,
           "specs": [], "notes": "", "source": "auto"}
    if part.kind == "catalog":
        it = _catalog_part_item(home, part.id, catalog) or {}
        row.update(kind="bought", catalog_id=it.get("id"), vendor=it.get("vendor"),
                   url=it.get("url"), specs=_short_specs(it.get("specs")),
                   unit_cost=it.get("price"), currency=it.get("currency") or "USD")
    else:
        row["kind"] = "made"
    return row


def _finish(row: dict, home: Path, material: str) -> dict:
    """A row as routes return it: made rows without a set cost get the live estimate."""
    out = {k: row.get(k) for k in ("id", "kind", "part_id", "catalog_id", "name", "qty",
                                   "unit_cost", "currency", "vendor", "url", "specs", "notes",
                                   "source")}
    out["specs"] = list(out["specs"] or [])
    out["notes"] = out["notes"] or ""
    out["estimate"], out["estimate_note"] = False, None
    if out["kind"] == "made" and out["unit_cost"] is None and out["part_id"]:
        est = made_estimate(home, out["part_id"], material)
        out["unit_cost"], out["estimate_note"] = est["unit_cost"], est["note"]
        out["estimate"] = est["unit_cost"] is not None
        out["currency"] = "USD"
        keys = {s["key"] for s in out["specs"]}
        if est["mass_g"] is not None and "mass" not in keys:
            out["specs"].append({"key": "mass", "value": est["mass_g"], "unit": "g"})
        if "material" not in keys:
            out["specs"].append({"key": "material", "value": material.upper(), "unit": None})
        if est.get("bbox") and "size" not in keys:
            out["specs"].append({"key": "size", "value": " x ".join(f"{v:.1f}" for v in
                                                                     est["bbox"]),
                                 "unit": "mm"})
    q, c = out["qty"], out["unit_cost"]
    out["line_total"] = round(q * c, 2) if c is not None else None
    return out


def _add(d: dict, cur: str, v: float) -> None:
    d[cur] = round(d.get(cur, 0.0) + v, 2)


def totals(items: list[dict]) -> dict:
    by_currency: dict = {}
    made: dict = {}
    bought: dict = {}
    estimated: dict = {}
    vendors: dict[tuple, dict] = {}
    unpriced = 0
    for it in items:
        lt = it["line_total"]
        if lt is None:
            unpriced += 1
            continue
        cur = it["currency"] or "USD"
        _add(by_currency, cur, lt)
        _add(made if it["kind"] == "made" else bought, cur, lt)
        if it["estimate"]:
            _add(estimated, cur, lt)
        key = (it["vendor"] or None, cur) if it["kind"] == "bought" else (None, cur)
        v = vendors.setdefault(key, {"vendor": key[0], "kind": it["kind"] if key[0] is None
                                     else "bought", "currency": cur, "total": 0.0, "count": 0})
        v["total"] = round(v["total"] + lt, 2)
        v["count"] += 1
    return {"by_currency": by_currency, "made": made, "bought": bought,
            "estimated": estimated, "has_estimates": bool(estimated),
            "by_vendor": sorted(vendors.values(), key=lambda v: (-v["total"],
                                                                  v["vendor"] or "")),
            "unpriced": unpriced, "rows": len(items), "qty": sum(i["qty"] for i in items)}


def view(store: Store, bom: BomStore, home: Path, project_id: str, *, catalog=None,
         material: str = "PLA") -> dict:
    """GET /projects/{id}/bom: {project_id, material, items, totals}. Made rows first."""
    stored = bom.rows(project_id)
    covered = {r["part_id"] for r in stored if r.get("part_id")}
    tree = {p.id: p for p in store.list_part_rows(project_id)}
    rows = []
    for p in tree.values():
        if p.kind in ("part", "catalog") and p.id not in covered:
            rows.append(_auto_row(p, home, catalog, material))
    for r in stored:
        if r.get("hidden"):
            continue
        rows.append(r)
    items = [_finish(r, home, material) for r in rows]
    items.sort(key=lambda i: 0 if i["kind"] == "made" else 1)
    return {"project_id": project_id, "material": material.upper(), "items": items,
            "totals": totals(items)}


def auto_rows(store: Store, home: Path, project_id: str, catalog=None,
              material: str = "PLA") -> dict[str, dict]:
    return {f"auto_{p.id}": _auto_row(p, home, catalog, material)
            for p in store.list_part_rows(project_id) if p.kind in ("part", "catalog")}


# ---- edits shared by the routes and bom_add

def add_row(bom: BomStore, project_id: str, data: dict, source: str) -> dict:
    row = {**data, "id": new_id(), "source": source}
    return bom.put(project_id, row)


def patch_row(store: Store, bom: BomStore, home: Path, project_id: str, item_id: str,
              patch: dict, *, catalog=None, material: str = "PLA") -> dict:
    if not ITEM_RE.match(item_id or ""):
        raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")
    row = bom.get(project_id, item_id)
    if row is None or row.get("hidden"):
        auto = auto_rows(store, home, project_id, catalog, material).get(item_id)
        if auto is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")
        row = {**auto, "source": "user"}
    row = {**row, **patch}
    row.pop("hidden", None)
    return bom.put(project_id, row)


def delete_row(store: Store, bom: BomStore, project_id: str, item_id: str) -> None:
    if not ITEM_RE.match(item_id or ""):
        raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")
    row = bom.get(project_id, item_id)
    part_id = (row or {}).get("part_id")
    if row is None:
        if not item_id.startswith("auto_"):
            raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")
        part_id = item_id[len("auto_"):]
    part = store.get_part(part_id) if part_id else None
    if part is not None and part.project_id == project_id and part.kind in ("part", "catalog"):
        # the part is still in the build: hide its row, or it would come back automatically
        bom.put(project_id, {"id": item_id if row is None else row["id"], "part_id": part_id,
                             "hidden": True})
        return
    if row is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no parts list row {item_id!r}")
    bom.remove(project_id, item_id)


# ---- CSV

CSV_COLUMNS = ["kind", "name", "qty", "unit_cost", "currency", "line_total", "estimate",
               "vendor", "url", "part_id", "catalog_id", "specs", "notes", "source"]
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else ""
    if isinstance(v, (int, float)):
        return f"{v:g}" if isinstance(v, float) else str(v)
    s = str(v)
    # a spreadsheet must never run a formula a web page put in a name or note
    return "'" + s if s.startswith(_FORMULA) else s


def _specs_text(specs: list[dict]) -> str:
    return "; ".join(f"{s['key']}: {s['value']}{(' ' + s['unit']) if s.get('unit') else ''}"
                     for s in specs or [])


def to_csv(v: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for it in v["items"]:
        notes = " | ".join(x for x in (it["notes"], it.get("estimate_note")) if x)
        row = {**it, "specs": _specs_text(it["specs"]), "notes": notes}
        w.writerow([_cell(row.get(c)) for c in CSV_COLUMNS])
    for cur, total in v["totals"]["by_currency"].items():
        w.writerow([_cell(x) for x in ("total", "", "", "", cur, total,
                                       "yes" if v["totals"]["estimated"].get(cur) else "",
                                       "", "", "", "", "", "", "")])
    return buf.getvalue()
