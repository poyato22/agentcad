"""Readable page snapshots: what the user sees of a page an agent read (title, URL, text,
tables, proxied images; never live JS) and the per-run store that keeps them.

Everything in a snapshot came from the web and is untrusted: the app shows it as plain text,
the model only sees it inside a nonce-delimited <untrusted_page> block. Image sources are kept
in the stored record but never returned; the app reads each image through the core's proxy
(`proxy_url`), which fetches it with the same hardened fetcher and re-encodes it.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from urllib.parse import urljoin, urlsplit

from ..catalog.importer import Page, _clean_text, _Collector, _squash, check_url, extract
from ..store import Store
from ..types import ErrorCode, MisesError

TEXT_MAX = 50_000
TABLES_MAX, ROWS_MAX, CELLS_MAX, CELL_CHARS = 20, 100, 12, 300
IMAGES_MAX = 12
LINKS_MAX = 2000
SNAPSHOTS_PER_RUN = 300
SID_RE = re.compile(r"^snap_[0-9a-f]{16}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_snapshots (
  id TEXT PRIMARY KEY, run_id TEXT, project_id TEXT, data TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS browser_snapshots_run ON browser_snapshots(run_id, created_at);
CREATE TABLE IF NOT EXISTS browser_budget (run_id TEXT PRIMARY KEY, used INTEGER);
CREATE TABLE IF NOT EXISTS browser_domain_fetches (
  run_id TEXT, domain TEXT, n INTEGER, PRIMARY KEY (run_id, domain));
"""

# what a page that refuses robots says instead of its content
_BLOCK_MARKERS = ("captcha", "robot check", "are you a human", "are you a robot",
                  "access denied", "request unsuccessful", "pardon our interruption",
                  "unusual traffic", "verify you are human", "attention required",
                  "enable javascript and cookies", "bot detection", "just a moment...")
BLOCKED_STATUSES = {401, 403, 429, 503, 999}
BLOCKED = "site blocked automated access"


def blocked_reason(status: int | None = None, title: str = "", text: str = "") -> str | None:
    """BLOCKED (with why) when a response is a bot wall, else None."""
    if status in BLOCKED_STATUSES:
        return f"{BLOCKED} (HTTP {status})"
    hay = (title + "\n" + text[:3000]).lower()
    for m in _BLOCK_MARKERS:
        if m in hay:
            return f"{BLOCKED} (the page asks for a {'captcha' if 'captcha' in m else 'human'})"
    return None


class PageParser(_Collector):
    """The importer's collector plus tables (grouped), images and links (for search pages)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.images: list[dict] = []
        self.links: list[dict] = []
        self._tables: list[int] = []
        self._link: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "table":
            self._tables.append(len(self.rows))
        elif tag == "img" and not self._skip:
            src = a.get("src") or a.get("data-src") or ""
            if not src and a.get("srcset"):
                src = a["srcset"].split(",")[0].strip().split(" ")[0]
            if src and len(self.images) < 200:
                self.images.append({"src": src, "alt": _squash(a.get("alt", ""))[:200]})
            if self._link is not None and a.get("alt"):
                self._link["alt"] = self._link.get("alt") or _squash(a["alt"])
        elif tag == "a" and not self._skip and a.get("href"):
            self._finish_link()
            self._link = {"href": a["href"], "text": [], "start": len(self.text),
                          "title": _squash(a.get("title") or a.get("aria-label") or ""),
                          "cls": a.get("class", "")[:200]}
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == "table" and self._tables:
            start = self._tables.pop()
            if not self._tables:                   # outermost table: its rows, once
                rows = self.rows[start:]
                if rows:
                    self.tables.append(rows)
        elif tag == "a":
            self._finish_link()

    def handle_data(self, data):
        super().handle_data(data)
        if self._link is not None and not self._skip:
            self._link["text"].append(data)

    def _finish_link(self) -> None:
        if self._link is None:
            return
        link, self._link = self._link, None
        if len(self.links) < LINKS_MAX:
            link["text"] = _squash("".join(link["text"]))
            link["end"] = len(self.text)
            self.links.append(link)

    def close(self):
        super().close()
        self._finish_link()


def parse(html: str) -> PageParser:
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML still gives what was parsed
        pass
    return p


def _tables(raw: list[list[list[str]]]) -> list[dict]:
    out = []
    for rows in raw[:TABLES_MAX]:
        out.append({"rows": [[c[:CELL_CHARS] for c in r[:CELLS_MAX]] for r in rows[:ROWS_MAX]]})
    return out


def _image_sources(p: PageParser, base: str) -> list[dict]:
    """Up to IMAGES_MAX https images (the page's og:image first), as absolute URLs."""
    seen, out = set(), []
    cands = []
    if p.meta.get("og:image"):
        cands.append({"src": p.meta["og:image"], "alt": "page image"})
    cands += p.images
    for c in cands:
        if c["src"].startswith("data:"):
            continue
        try:
            url = check_url(urljoin(base, c["src"]))
        except MisesError:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"src": url, "alt": c["alt"]})
        if len(out) >= IMAGES_MAX:
            break
    return out


def new_id() -> str:
    return "snap_" + secrets.token_hex(8)


def build(page: Page, *, run_id: str, project_id: str | None, kind: str = "page",
          sid: str | None = None) -> dict:
    """A stored snapshot of a fetched page (the full record; `public()` is what routes return).
    HTML pages also get the importer's product extraction (`part`: name, vendor, price, specs,
    dims, mount) and the catalog item it would make (`item`)."""
    sid = sid or new_id()
    host = urlsplit(page.final_url).hostname or ""
    rec = {"id": sid, "run_id": run_id, "project_id": project_id, "kind": kind,
           "url": page.url, "final_url": page.final_url, "domain": host,
           "content_type": page.content_type, "fetched_at": time.time(),
           "title": "", "text": "", "tables": [], "images": [], "part": None, "_images": [],
           "_item": None}
    if page.content_type == "application/pdf":
        rec["title"] = urlsplit(page.final_url).path.rsplit("/", 1)[-1][:200] or host
        rec["text"] = (f"PDF document ({len(page.body or b'')} bytes). Mises does not read PDF"
                       " text yet: open it in the browser to read it.")
        return rec
    p = parse(page.text) if page.content_type not in ("application/json",
                                                       "application/ld+json") else None
    if p is not None:
        rec["title"] = _squash(p.title or p.meta.get("og:title") or host)[:300]
        rec["text"] = _clean_text(p.text)[:TEXT_MAX]
        rec["tables"] = _tables(p.tables)
        imgs = _image_sources(p, page.final_url)
        rec["_images"] = imgs
        rec["images"] = [{"proxy_url": f"/browser/snapshots/{sid}/images/{i}",
                          "alt": im["alt"]} for i, im in enumerate(imgs)]
    else:
        rec["title"] = host
        rec["text"] = page.text[:TEXT_MAX]
    item = extract(page)
    rec["_item"] = {**item, "raw_text": item.get("raw_text", "")[:20_000]}
    if item.get("specs") or item.get("price") is not None or item.get("dims"):
        rec["part"] = {k: item.get(k) for k in ("name", "vendor", "price", "currency", "specs",
                                                "dims", "mount", "category")}
    return rec


def public(rec: dict) -> dict:
    """A snapshot as routes return it: no stored image sources, no catalog item draft."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def summary(rec: dict) -> dict:
    """A list row: no page text, tables or results."""
    out = {k: rec.get(k) for k in ("id", "run_id", "kind", "url", "final_url", "domain",
                                   "title", "fetched_at", "site", "blocked")}
    out["image_count"] = len(rec.get("images") or [])
    out["result_count"] = len(rec.get("results") or [])
    out["has_part"] = rec.get("part") is not None
    return out


class SnapshotStore:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store._db.executescript(_SCHEMA)

    def add(self, rec: dict) -> dict:
        with self.store._tx() as db:
            db.execute("INSERT INTO browser_snapshots VALUES (?,?,?,?,?)",
                       (rec["id"], rec["run_id"], rec.get("project_id"), json.dumps(rec),
                        rec.get("fetched_at") or time.time()))
            n = db.execute("SELECT COUNT(*) FROM browser_snapshots WHERE run_id=?",
                           (rec["run_id"],)).fetchone()[0]
            if n > SNAPSHOTS_PER_RUN:                # keep the newest per run
                db.execute("DELETE FROM browser_snapshots WHERE id IN (SELECT id FROM"
                           " browser_snapshots WHERE run_id=? ORDER BY created_at, rowid"
                           " LIMIT ?)", (rec["run_id"], n - SNAPSHOTS_PER_RUN))
        return rec

    def get(self, sid: str) -> dict:
        row = self.store._one("SELECT data FROM browser_snapshots WHERE id=?", (sid,)) \
            if SID_RE.match(sid or "") else None
        if row is None:
            raise MisesError(ErrorCode.NOT_FOUND, f"no browser snapshot {sid!r}",
                             hint="snapshot ids come from web_open / web_search")
        return json.loads(row[0])

    def for_run(self, run_id: str) -> list[dict]:
        rows = self.store._all("SELECT data FROM browser_snapshots WHERE run_id=?"
                               " ORDER BY created_at, rowid", (run_id,))
        return [json.loads(r[0]) for r in rows]

    def charge(self, run_id: str, n: int, cap: int) -> int | None:
        """Spend `n` web fetches of run `run_id`'s budget `cap`: the new total, or None (and
        nothing spent) when it would go over."""
        with self.store._tx() as db:
            row = db.execute("SELECT used FROM browser_budget WHERE run_id=?",
                             (run_id,)).fetchone()
            used = (row[0] if row else 0) + n
            if used > cap:
                return None
            db.execute("INSERT OR REPLACE INTO browser_budget VALUES (?,?)", (run_id, used))
        return used

    def domain_counts(self, run_id: str, domain: str) -> tuple[int, int]:
        """E31: (pages fetched from `domain`, pages fetched from any non-suggested host) by run
        `run_id`'s family this turn. Cleared each user turn with the fetch budget."""
        row = self.store._one("SELECT n FROM browser_domain_fetches WHERE run_id=? AND domain=?",
                              (run_id, domain))
        total = self.store._one("SELECT COALESCE(SUM(n), 0) FROM browser_domain_fetches"
                                " WHERE run_id=?", (run_id,))
        return (int(row[0]) if row else 0), int(total[0] if total else 0)

    def count_domain(self, run_id: str, domain: str) -> None:
        """Count one fetch from a non-suggested host (after the fetch budget accepted it)."""
        with self.store._tx() as db:
            db.execute("INSERT INTO browser_domain_fetches VALUES (?,?,1) ON CONFLICT(run_id,"
                       " domain) DO UPDATE SET n = n + 1", (run_id, domain))

    def ids_for_run(self, run_id: str) -> list[str]:
        return [r[0] for r in self.store._all("SELECT id FROM browser_snapshots WHERE run_id=?",
                                              (run_id,))]

    def delete_run(self, run_id: str) -> None:
        with self.store._tx() as db:
            db.execute("DELETE FROM browser_snapshots WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM browser_budget WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM browser_domain_fetches WHERE run_id=?", (run_id,))
