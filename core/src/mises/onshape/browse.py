"""Browsing Onshape for the app and the agents: documents, their elements, thumbnails, budget.

Endpoints, checked against the live v17 OpenAPI spec (https://cad.onshape.com/api/openapi)
and https://onshape-public.github.io/docs/api-adv/documents/ :
  GET /documents?q&filter&sortColumn&sortOrder&offset&limit      getDocuments
      -> BTGlobalTreeNodeListResponse {items, next, previous, href}; limit is at most 20.
         `filter` 0 = My Documents. Each item (BTGlobalTreeNodeSummaryInfo) has id, name,
         modifiedAt, owner {name}, defaultWorkspace {id} and thumbnail {sizes: [{size, href}]}.
  GET /documents/d/{did}/{wvm}/{wvmid}/elements                   getElementsInDocument
      -> [BTDocumentElementInfo {id, name, elementType: PARTSTUDIO | ASSEMBLY | ...}]
  GET <thumbnail href>, e.g. /thumbnails/d/{did}/w/{wid}/s/{sz}  getDocumentThumbnailWithSize
      -> image bytes. Sizes differ per document, so the size is picked from `sizes`.
  GET /partstudios|assemblies/d/{did}/w/{wid}/e/{eid}/boundingboxes  (metres)

Every one of these is a real, signed, ledgered request (thumbnails too), so thumbnails are
cached under the Mises home and never fetched twice. Names, owners and descriptions are
user-authored text: they are passed through as data and truncated, never interpreted.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from ..store import Store
from ..types import ErrorCode, MisesError
from .client import OnshapeClient

API = "/api/v17"
PAGE_MAX = 20                      # Onshape's maximum page size for getDocuments
THUMB_MAX_BYTES = 512 * 1024
THUMB_SIZES = ("300x170", "300x300", "600x340", "70x40")
ELEMENT_TYPES = {"PARTSTUDIO": "partstudio", "ASSEMBLY": "assembly"}
WARN_FRACTION = 0.8
_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_DOC_TYPES = {None, "document-summary", "document", "document-summary-search"}
_NAME_MAX = 200
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
          (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def check_id(value: str, what: str) -> str:
    v = value.lower() if isinstance(value, str) else ""
    if not _ID_RE.match(v):
        raise MisesError(ErrorCode.INVALID_INPUT, f"{what} must be a 24-character Onshape id",
                         hint="copy it from the document URL")
    return v


def _text(v, limit: int = _NAME_MAX) -> str:
    return v[:limit] if isinstance(v, str) else ""


def thumbnail_href_ok(href: str, base_url: str = "https://cad.onshape.com") -> bool:
    """Only Onshape thumbnail URLs are proxied (the admin route signs them with the keys)."""
    try:
        u = urlsplit(href)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    base = (urlsplit(base_url).hostname or "").lower()
    return (u.scheme == "https" and not u.username and not u.password
            and (host == base or host == "onshape.com" or host.endswith(".onshape.com"))
            and u.path.startswith("/api/") and "/thumbnails/" in u.path
            and ".." not in u.path)


def _pick_thumbnail(thumb) -> str | None:
    sizes = thumb.get("sizes") if isinstance(thumb, dict) else None
    if not isinstance(sizes, list):
        return None
    by_size = {s.get("size"): s.get("href") for s in sizes
               if isinstance(s, dict) and isinstance(s.get("href"), str)}
    for want in THUMB_SIZES:
        if by_size.get(want):
            return by_size[want]
    return next(iter(by_size.values()), None)


def proxied_thumbnail_url(href: str | None) -> str | None:
    if not href or not thumbnail_href_ok(href):
        return None
    return "/onshape/thumbnail?href=" + quote(href, safe="")


def document_row(item: dict) -> dict | None:
    if not isinstance(item, dict) or item.get("jsonType") not in _DOC_TYPES:
        return None
    did = item.get("id")
    if not isinstance(did, str) or not _ID_RE.match(did.lower()):
        return None
    ws = item.get("defaultWorkspace")
    wid = ws.get("id") if isinstance(ws, dict) else None
    owner = item.get("owner")
    return {"did": did.lower(), "name": _text(item.get("name")),
            "modified_at": _text(item.get("modifiedAt"), 40) or None,
            "owner": _text(owner.get("name")) if isinstance(owner, dict) else "",
            "thumbnail_url": proxied_thumbnail_url(_pick_thumbnail(item.get("thumbnail"))),
            "default_wid": wid.lower() if isinstance(wid, str) and _ID_RE.match(wid.lower())
            else None}


async def list_documents(client: OnshapeClient, *, q: str = "", owner: str | None = "me",
                         offset: int = 0, limit: int = PAGE_MAX,
                         run_id: str | None = None) -> list[dict]:
    """One page of documents, newest change first. `owner="me"` is Onshape's "My Documents"
    filter; any other value searches everything the keys can see. One Onshape call."""
    if offset < 0 or not 1 <= limit <= PAGE_MAX:
        raise MisesError(ErrorCode.INVALID_INPUT, f"offset >= 0 and 1 <= limit <= {PAGE_MAX}")
    query: dict = {"sortColumn": "modifiedAt", "sortOrder": "desc", "offset": offset,
                   "limit": limit}
    if q:
        query["q"] = q[:200]
    if owner == "me":
        query["filter"] = 0
    out = await client.request("GET", API + "/documents", run_id=run_id, query=query)
    rows = [document_row(i) for i in out.get("items") or []]
    return [r for r in rows if r is not None]


async def list_elements(client: OnshapeClient, did: str, wid: str, *,
                        run_id: str | None = None) -> list[dict]:
    """Part Studios and Assemblies of a workspace (drawings, blobs etc. are left out)."""
    did, wid = check_id(did, "did"), check_id(wid, "wid")
    out = await client.request("GET", f"{API}/documents/d/{did}/w/{wid}/elements",
                               run_id=run_id)
    rows = []
    for e in out.get("items") or []:
        if not isinstance(e, dict):
            continue
        kind = ELEMENT_TYPES.get(e.get("elementType"))
        eid = e.get("id")
        if kind is None or not isinstance(eid, str) or not _ID_RE.match(eid.lower()):
            continue
        rows.append({"eid": eid.lower(), "name": _text(e.get("name")), "type": kind,
                     "microversion": _text(e.get("microversionId"), 40) or None})
    return rows


def _sniff(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def thumbnail(client: OnshapeClient, cache_dir: Path, href: str) -> tuple[bytes, str]:
    """(image bytes, media type) of an Onshape thumbnail, cached in `cache_dir`.

    A cached image costs no Onshape call. The image must be at most THUMB_MAX_BYTES and really
    be an image (magic bytes, not the server's Content-Type)."""
    if not thumbnail_href_ok(href, client.base_url):
        raise MisesError(ErrorCode.INVALID_INPUT, "not an Onshape thumbnail URL",
                         hint="pass a thumbnail_url from GET /onshape/documents")
    key = hashlib.sha256(href.encode()).hexdigest()
    path = Path(cache_dir) / f"{key}.img"
    try:
        data = path.read_bytes()
        mime = _sniff(data)
        if mime and len(data) <= THUMB_MAX_BYTES:
            return data, mime
    except OSError:
        pass
    data, _ctype = await client.request_bytes(href, run_id=None, max_bytes=THUMB_MAX_BYTES)
    mime = _sniff(data)
    if mime is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "Onshape thumbnail is not an image")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data, mime


async def bounding_box_mm(client: OnshapeClient, did: str, wid: str, eid: str, kind: str, *,
                          run_id: str | None = None) -> dict:
    """{"min": [x,y,z], "max": [x,y,z]} in mm of a Part Studio or Assembly (Onshape: metres)."""
    did, wid, eid = check_id(did, "did"), check_id(wid, "wid"), check_id(eid, "eid")
    seg = {"partstudio": "partstudios", "assembly": "assemblies"}.get(kind)
    if seg is None:
        raise MisesError(ErrorCode.INVALID_INPUT, "kind must be partstudio or assembly")
    bb = await client.request("GET", f"{API}/{seg}/d/{did}/w/{wid}/e/{eid}/boundingboxes",
                              run_id=run_id)
    try:
        lo = [float(bb[k]) * 1000 for k in ("lowX", "lowY", "lowZ")]
        hi = [float(bb[k]) * 1000 for k in ("highX", "highY", "highZ")]
    except (KeyError, TypeError, ValueError):
        raise MisesError(ErrorCode.TOOL_FAILED, "Onshape returned no bounding box",
                         hint="the element may be empty") from None
    return {"min": lo, "max": hi}


# ---- budget

def year_start(now: datetime | None = None) -> float:
    now = now or datetime.now()
    return datetime(now.year, 1, 1).timestamp()


def next_reset(now: datetime | None = None) -> float:
    now = now or datetime.now()
    return datetime(now.year + 1, 1, 1).timestamp()


def budget(store: Store, user: str, limit_year: int, *, run_id: str | None = None,
           session_since: float = 0.0) -> dict:
    """The live budget meter: calls used this calendar year (Onshape's allowance is annual,
    https://onshape-public.github.io/docs/auth/limits/) and by one run. Without a run,
    `used_run` is what requests without a run (the app's own browsing) used since
    `session_since` (the core's start)."""
    used_year = store.ledger_used(user, since=year_start())
    if run_id is not None:
        used_run = store.ledger_used(user, since=0, run_id=run_id)
    else:
        used_run = _ledger_no_run(store, user, session_since)
    return {"used_year": used_year, "limit_year": limit_year, "used_run": used_run,
            "warn_at": int(limit_year * WARN_FRACTION), "reset_at": next_reset(),
            "run_id": run_id}


def _ledger_no_run(store: Store, user: str, since: float) -> int:
    row = store._one(
        "SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN estimate ELSE 1 END),0) FROM ledger"
        " WHERE user=? AND ts>=? AND run_id IS NULL"
        " AND (state='reserved' OR (state='settled' AND counted=1))", (user, since))
    return int(row[0])


# ---- import into a build

async def import_element(client: OnshapeClient, parts_root: Path, did: str, wid: str, eid: str,
                         project_id: str | None, *, run_id: str | None = None) -> dict:
    """Mirror a Part Studio or Assembly into a build as a local part (2 Onshape calls: the
    element list, for its name and type, and its bounding box).

    The mirror is the element's bounding-box envelope, linked to the Onshape element; Onshape
    is only read, never changed. Importing the same element into the same build again
    returns the existing part (`existing: true`)."""
    from ..catalog.geometry import (_existing_part, base_part_id, build_part, choose_lock,
                                    choose_part_id, script_for)

    did, wid, eid = check_id(did, "did"), check_id(wid, "wid"), check_id(eid, "eid")
    parts_root = Path(parts_root)
    owner = {"kind": "part", "source": "onshape", "onshape_ref": f"{did}/{wid}/{eid}",
             "project_id": project_id}
    elements = await list_elements(client, did, wid, run_id=run_id)
    el = next((e for e in elements if e["eid"] == eid), None)
    if el is None:
        raise MisesError(ErrorCode.NOT_FOUND, "no Part Studio or Assembly with that eid",
                         hint="pick an element from GET /onshape/documents/{did}/elements")
    base = base_part_id({"source": "onshape", "name": el["name"], "category": "onshape_part"})
    async with choose_lock(parts_root, base):
        pid, existing = choose_part_id(parts_root, base, owner)
        if existing:
            return _existing_part(parts_root / pid)
        bb = await bounding_box_mm(client, did, wid, eid, el["type"], run_id=run_id)
        size = [hi - lo for lo, hi in zip(bb["min"], bb["max"])]
        if min(size) <= 0:
            raise MisesError(ErrorCode.INVALID_INPUT, "the element has no solid extent to"
                             " mirror", hint="import a Part Studio with at least one part")
        script, vars_ = script_for({"id": "onshape_import", "source": "onshape", "dims": {
            "length_mm": size[0], "width_mm": size[1], "height_mm": size[2]}})
        meta = {**owner, "label": el["name"],
                "onshape": {"did": did, "wid": wid, "eid": eid, "type": el["type"],
                            "url": f"https://cad.onshape.com/documents/{did}/w/{wid}/e/{eid}"},
                "bbox_mm": bb, "envelope": True}
        out = await build_part(parts_root, pid, script, vars_, meta, mass_note=(
            "estimate: bounding-box envelope of the Onshape element, not its real mass;"
            " read the mass from Onshape"))
    return {**out, "existing": False}
