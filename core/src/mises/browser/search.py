"""Search-page result extraction: one adapter per suggested site (which links are product
pages, and how to write them canonically) plus a generic fallback for sites the user added.

Results are `[{title, url, snippet, site}]`, links on the site's own domain only (ads and
off-site links are dropped), https, deduplicated by canonical URL, query strings dropped
(web_open drops them too). All of it is untrusted page data.

Sources tried in order: schema.org ItemList JSON-LD (many shops embed one), then the page's
links matched by the site's product-path pattern. The title is the link's text (or its title
attribute or image alt), the snippet the page text that follows it up to the next result.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..catalog.importer import _squash
from .sites import on_domain
from .snapshot import PageParser, parse

TITLE_MAX, SNIPPET_MAX = 200, 240
RESULTS_MAX = 10


@dataclass(frozen=True)
class Adapter:
    product: re.Pattern            # matches the path of a product page
    canonical: re.Pattern | None = None   # group(0) of a match is the canonical path


_A = Adapter
ADAPTERS: dict[str, Adapter] = {
    "mcmaster.com": _A(re.compile(r"^/(\d{4,5}[A-Z]\d{1,4})/?$")),
    "misumi-ec.com": _A(re.compile(r"^/vona2/detail/[A-Za-z0-9]+/?")),
    "digikey.com": _A(re.compile(r"^/[a-z]{2}/products/detail/[^/]+/[^/]+/\d+")),
    "mouser.com": _A(re.compile(r"^/ProductDetail/[^?#]+")),
    "adafruit.com": _A(re.compile(r"^/product/\d+"), re.compile(r"^/product/\d+")),
    "sparkfun.com": _A(re.compile(r"^/[a-z0-9][a-z0-9-]{3,}\.html$")),
    "pololu.com": _A(re.compile(r"^/product/\d+"), re.compile(r"^/product/\d+")),
    "servocity.com": _A(re.compile(r"^/[a-z0-9]+(?:-[a-z0-9]+){2,}/?$")),
    "getfpv.com": _A(re.compile(r"^/[a-z0-9][a-z0-9-]{3,}\.html$")),
    "robotshop.com": _A(re.compile(r"^/(?:[a-z]{2}/)?products/[a-z0-9-]+"),
                        re.compile(r"^/(?:[a-z]{2}/)?products/[a-z0-9-]+")),
    "aliexpress.com": _A(re.compile(r"^/item/\d+\.html"), re.compile(r"^/item/\d+\.html")),
    "amazon.com": _A(re.compile(r"/dp/[A-Z0-9]{10}"), re.compile(r"/dp/[A-Z0-9]{10}")),
}
# paths that are never products on any site
_NAV = re.compile(r"^/(?:$|search|cart|checkout|login|account|customer|help|contact|about|"
                  r"wishlist|compare|blog|news|support|policies|pages|collections/?$|"
                  r"catalogsearch|s$|gp/|ap/|b/|sitemap|careers)", re.I)
_GENERIC = re.compile(r"(?:/products?/|/item/|/p/|/dp/|/detail/|\.html$|"
                      r"^/[a-z0-9]+(?:-[a-z0-9]+){3,}/?$)", re.I)


def adapter_for(domain: str) -> Adapter | None:
    return next((a for d, a in ADAPTERS.items() if on_domain(domain, d)), None)


def _canonical(url: str, adapter: Adapter | None) -> str:
    u = urlsplit(url)
    path = u.path
    if adapter is not None and adapter.canonical is not None:
        m = adapter.canonical.search(path)
        if m:
            path = m.group(0)
    return urlunsplit(("https", u.netloc.lower(), path, "", ""))


def _is_product(path: str, adapter: Adapter | None) -> bool:
    if adapter is not None:
        return bool(adapter.product.search(path))
    return not _NAV.match(path) and bool(_GENERIC.search(path))


def _jsonld_items(p: PageParser) -> list[tuple[str, str]]:
    out = []
    for blob in p.jsonld[:20]:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        stack = [data]
        while stack and len(out) < 100:
            n = stack.pop(0)
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                if n.get("@type") == "ItemList" or "itemListElement" in n:
                    for el in n.get("itemListElement") or []:
                        if not isinstance(el, dict):
                            continue
                        it = el.get("item") if isinstance(el.get("item"), dict) else el
                        url, name = it.get("url") or el.get("url"), it.get("name") or el.get("name")
                        if isinstance(url, str) and isinstance(name, str):
                            out.append((url, name))
                for k in ("@graph", "mainEntity"):
                    if k in n:
                        stack.append(n[k])
    return out


def _ok_url(href: str, base: str, domain: str) -> str | None:
    try:
        url = urljoin(base, href.strip())
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme != "https" or not on_domain(u.hostname or "", domain) or u.port not in (None, 443):
        return None
    return url


def extract_results(html: str, base_url: str, site: dict,
                    limit: int = RESULTS_MAX) -> list[dict]:
    """The product results on a site's search page (see the module docstring)."""
    p = parse(html)
    return results_from(p, base_url, site, limit)


def results_from(p: PageParser, base_url: str, site: dict, limit: int = RESULTS_MAX) -> list[dict]:
    domain, name = site["domain"], site["name"]
    adapter = adapter_for(domain)
    found: dict[str, dict] = {}
    order: list[str] = []

    def add(url: str, title: str, snippet: str = "") -> None:
        key = _canonical(url, adapter)
        title = _squash(title)[:TITLE_MAX]
        hit = found.get(key)
        if hit is None:
            found[key] = {"title": title, "url": key, "snippet": snippet, "site": name}
            order.append(key)
            return
        if len(title) > len(hit["title"]):
            hit["title"] = title
        if not hit["snippet"] and snippet:
            hit["snippet"] = snippet

    for url, title in _jsonld_items(p):
        ok = _ok_url(url, base_url, domain)
        if ok and _is_product(urlsplit(ok).path, adapter):
            add(ok, title)
    links = []
    for link in p.links:
        ok = _ok_url(link["href"], base_url, domain)
        if ok and _is_product(urlsplit(ok).path, adapter):
            links.append((ok, link))
    for i, (ok, link) in enumerate(links):
        nxt = links[i + 1][1]["start"] if i + 1 < len(links) else link["end"] + 2000
        snippet = _squash("".join(p.text[link["end"]:nxt]))[:SNIPPET_MAX]
        title = link["text"] or link.get("title") or link.get("alt") or ""
        add(ok, title, snippet)
    out = [found[k] for k in order if found[k]["title"]]
    return out[:limit]
