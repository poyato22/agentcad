"""A general web search with no API key: the HTML result pages of public search engines,
fetched through the same hardened `fetch` as every other page (https only, public hosts, the
vetted address pinned, no cookies, size/time caps, redirects re-checked).

Engines are tried in order until one gives results (each try spends one web fetch):

  Brave        search.brave.com   (answers an honest User-Agent)
  Yahoo        search.yahoo.com   (Bing's index; needs browser-like headers)
  DuckDuckGo   html.duckduckgo.com (often answers robots with a challenge page now)

Results are `[{title, url, snippet, site}]`: https links on public hosts only, the engines' own
pages, ads and tracking links dropped, redirect wrappers (`uddg=`, `RU=`) unwrapped, query
strings and fragments dropped (web_open drops them on most sites anyway), deduplicated. A
`site:` search keeps only that site's pages. Everything here is untrusted page data.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit, urlunsplit

from ..catalog.importer import Page, _squash, check_url
from ..types import MisesError
from .sites import on_domain
from .snapshot import PageParser, blocked_reason, parse

log = logging.getLogger("mises.browser")

TITLE_MAX, SNIPPET_MAX = 200, 300
RESULTS_MAX = 10
SEARCH_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class Engine:
    name: str
    host: str
    template: str                       # {q} is the URL-encoded query
    realistic: bool = False             # needs browser-like headers
    link_class: re.Pattern | None = None   # result links carry this class
    href_prefix: str | None = None      # ... or start with this (a redirect wrapper)


ENGINES: tuple[Engine, ...] = (
    Engine("Brave", "search.brave.com", "https://search.brave.com/search?q={q}",
           link_class=re.compile(r"(?:^|\s)(?:l1|heading-serpresult|result-header)(?:\s|$)")),
    Engine("Yahoo", "search.yahoo.com", "https://search.yahoo.com/search?p={q}",
           realistic=True, href_prefix="https://r.search.yahoo.com/"),
    Engine("DuckDuckGo", "html.duckduckgo.com", "https://html.duckduckgo.com/html/?q={q}",
           link_class=re.compile(r"(?:^|\s)result__a(?:\s|$)")),
)
# never a result: the engines' own pages, their image/ad/tracking hosts
ENGINE_DOMAINS = ("brave.com", "yahoo.com", "yahoo.net", "yimg.com", "duckduckgo.com",
                  "bing.com", "msn.com", "doubleclick.net", "googleadservices.com")
# a link part that is a breadcrumb or a bare address, not a title
_CRUMB = re.compile(r"(?:›|»|^https?://|^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:\s|/|$))", re.I)


def engine_url(engine: Engine, query: str) -> str:
    return engine.template.replace("{q}", quote_plus(query))


def unwrap(href: str) -> str:
    """An engine's redirect link -> the page it points at (DuckDuckGo `uddg=`, Yahoo `RU=`)."""
    u = urlsplit(href)
    host = u.hostname or ""
    if on_domain(host, "duckduckgo.com") and u.path.startswith("/l/"):
        got = parse_qs(u.query).get("uddg")
        if got:
            return got[0]
    if on_domain(host, "search.yahoo.com"):
        m = re.search(r"/RU=([^/]+)/R[KS]=", u.path + "/RK=")
        if m:
            return unquote(m.group(1))
    return href


def clean_result_url(href: str, base: str) -> str | None:
    """A result link as web_open would open it, or None when it is no result (not https, a
    private or odd host, the engine itself, an ad)."""
    try:
        url = unwrap(urljoin(base, href.strip()))
        u = urlsplit(url)
        if u.scheme != "https":
            return None
        url = check_url(urlunsplit(("https", u.netloc, u.path or "/", "", "")))
    except (MisesError, ValueError):
        return None
    host = (urlsplit(url).hostname or "").lower()
    if any(on_domain(host, d) for d in ENGINE_DOMAINS):
        return None
    return url


def _title(link: dict) -> str:
    parts = [p for p in link.get("parts") or [] if not _CRUMB.search(p)]
    best = max(parts, key=len) if parts else (link.get("text") or link.get("title") or "")
    return _squash(best)[:TITLE_MAX]


def parse_results(engine: Engine, html: str, base: str, *, site: str | None = None,
                  limit: int = RESULTS_MAX) -> list[dict]:
    """The organic results on one engine's result page (see the module docstring)."""
    p: PageParser = parse(html)
    links = p.links
    if engine.link_class is not None:
        marked = [lk for lk in links if engine.link_class.search(lk.get("cls") or "")]
        if marked:
            links = marked
    elif engine.href_prefix is not None:
        links = [lk for lk in links if lk["href"].startswith(engine.href_prefix)]
    hits: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for lk in links:
        url = clean_result_url(lk["href"], base)
        if url is None or url in seen:
            continue
        if site and not on_domain(urlsplit(url).hostname or "", site):
            continue
        title = _title(lk)
        if not title:
            continue
        seen.add(url)
        hits.append((url, lk))
    out = []
    for i, (url, lk) in enumerate(hits[:limit]):
        nxt = hits[i + 1][1]["start"] if i + 1 < len(hits) else lk["end"] + 1500
        snippet = _squash("".join(p.text[lk["end"]:nxt]))[:SNIPPET_MAX]
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        out.append({"title": _title(lk), "url": url, "snippet": snippet, "site": host})
    return out


# get(url, realistic: bool) -> Page (raises MisesError)
Getter = Callable[..., Awaitable[Page]]


@dataclass
class Found:
    results: list[dict]
    engine: str | None                  # the engine that answered, None when none did
    page: Page | None                   # its result page (for the snapshot)
    url: str                            # the last result page asked for
    tried: list[str] = field(default_factory=list)
    why: str | None = None              # why no engine answered


async def web_lookup(get: Getter, query: str, *, site: str | None = None,
                     limit: int = RESULTS_MAX,
                     charge: Callable[[], None] = lambda: None) -> Found:
    """Search the web for `query` (only `site`'s pages when given), engine by engine until one
    gives results. `charge()` is called before each engine is asked (the fetch budget); an
    exhausted budget (a MisesError from `charge`) stops the search there."""
    q = f"site:{site} {query}" if site else query
    tried: list[str] = []
    notes: list[str] = []
    url = engine_url(ENGINES[0], q)
    for eng in ENGINES:
        try:
            charge()
        except MisesError as e:             # the fetch budget is spent: stop here
            notes.append(e.message)
            break
        tried.append(eng.name)
        url = engine_url(eng, q)
        try:
            page = await get(url, realistic=eng.realistic)
        except MisesError as e:
            log.info("web search %s failed: %s", eng.name, e.message)
            notes.append(f"{eng.name}: {e.message}")
            continue
        p = parse(page.text)
        wall = blocked_reason(200, p.title or "", " ".join(p.text)[:3000])
        results = parse_results(eng, page.text, page.final_url, site=site, limit=limit)
        if results:
            return Found(results, eng.name, page, url, tried)
        notes.append(f"{eng.name}: {wall or 'no results'}")
    return Found([], None, None, url, tried, "; ".join(notes) or "no search engine answered")
