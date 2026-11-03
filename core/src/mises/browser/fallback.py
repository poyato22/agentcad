"""H3: pages that fail to open. Many sites need JavaScript or turn plain HTTP clients away
(Cloudflare challenges, 403s, an empty shell). The chain tried, in order, and recorded on the
snapshot as `fetch_path`:

  plain     the agent browser's own fetch (honest User-Agent)
  headers   the same fetch with a realistic browser User-Agent and headers (HTTP/2 when the
            optional `h2` package is installed)
  cache     the Internet Archive's copy of the page
  snippet   what a search engine shows for the page (partial; says so)

A real headless engine (Playwright Chromium/WebKit) is NOT used: it is not installed in core's
environment and nothing here installs one. A JavaScript-only page with no archived copy therefore
ends at `snippet`. Every request in the chain goes through the same hardened `fetch` (https
only, public hosts, pinned IP, no cookies, size/time caps, redirects re-checked), and whatever
comes back is untrusted page text exactly like a plain fetch.
"""
from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from ..catalog.importer import Page
from ..types import MisesError
from .snapshot import BLOCKED_STATUSES, blocked_reason, parse

log = logging.getLogger("mises.browser")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")
BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
}
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ARCHIVE = "https://web.archive.org/web/2/"
SEARCH = "https://html.duckduckgo.com/html/?q="
MIN_TEXT = 120          # less readable text than this is an empty shell (JavaScript-only)
SNIPPET_MAX = 1800

# get(url, realistic=False, allow_other_site=False) -> Page (raises MisesError)
Getter = Callable[..., Awaitable[Page]]


@dataclass
class Opened:
    page: Page | None
    why: str | None
    path: str | None
    tried: list[str]


def usable(page: Page) -> bool:
    """A real page: not a bot wall, and with something to read (not a JavaScript-only shell)."""
    if page.content_type == "application/pdf" or page.content_type.endswith("json"):
        return True
    p = parse(page.text)
    text = " ".join(" ".join(p.text).split())
    if blocked_reason(200, p.title or "", text):
        return False
    return len(text) >= MIN_TEXT or bool(p.tables)


def _blocked(e: MisesError) -> bool:
    return getattr(e, "upstream_status", None) in BLOCKED_STATUSES


def snippet_page(url: str, results: Page) -> Page | None:
    """A small page of search-engine text for `url`, or None when the engine had nothing."""
    p = parse(results.text)
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    lines = [ln.strip() for ln in "\n".join(p.text).splitlines() if ln.strip()]
    keep = [ln for ln in lines if (host and host in ln.lower()) or len(ln) > 40]
    body = " ".join(keep)[:SNIPPET_MAX]
    if len(body) < 40:
        return None
    doc = (f"<html><head><title>{html.escape(host)} (search-engine snippets)</title></head><body>"
           f"<p>The page itself could not be opened; this is what a search engine shows for it."
           f"</p><p>{html.escape(body)}</p></body></html>")
    return Page(url, url, "text/html", doc, doc.encode())


async def open_chain(get: Getter, url: str, *, charge: Callable[[], None] = lambda: None
                     ) -> Opened:
    """Open `url`, falling back down the chain. Non-blocking failures (the site is down, TLS,
    a refused address) are raised as they are: only a bot wall or an empty shell falls back."""
    tried: list[str] = ["plain"]
    try:
        page = await get(url)
        if usable(page):
            return Opened(page, None, "plain", tried)
        why = blocked_reason(200, parse(page.text).title or "", "") \
            or "the page has no readable text (it may need JavaScript)"
    except MisesError as e:
        if not _blocked(e):
            raise
        why = blocked_reason(getattr(e, "upstream_status", None))
    for name, target, kw in (("headers", url, {"realistic": True}),
                             ("cache", ARCHIVE + url, {"allow_other_site": True}),
                             ("snippet", SEARCH + quote(url, safe=""),
                              {"allow_other_site": True})):
        charge()
        tried.append(name)
        try:
            got = await get(target, **kw)
        except MisesError as e:
            log.info("web_open fallback %s failed for %s: %s", name, urlsplit(url).hostname,
                     e.message)
            continue
        if name == "snippet":
            got = snippet_page(url, got)
            if got is None:
                continue
        elif not usable(got):
            continue
        if name == "cache":
            got = Page(url, url, got.content_type, got.text, got.body)
        log.info("web_open %s opened via %s", urlsplit(url).hostname, name)
        return Opened(got, None, name, tried)
    return Opened(None, why, None, tried)
