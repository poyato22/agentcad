"""Turn a product page URL into a catalog item. The page is untrusted data, never instructions.

Fetching: https only, port 443, the host must resolve only to public addresses (loopback,
private, link-local, NAT64/6to4/Teredo/IPv4-compatible wrappers of those are refused), and the
connection goes to the vetted address itself (the host name rides along as Host and TLS SNI, so
the certificate is still checked against it): a DNS answer that changes between the check and
the connect (rebinding) is never used. Redirects are followed by hand (at most MAX_REDIRECTS)
and every target is checked again, the body is capped at MAX_BYTES (streamed: a bigger page is
not read), the whole fetch at TIMEOUT_S, and no cookies are sent or kept. Connection failures
come back as one generic message (no probing of what is behind an address).

Extraction reads JSON-LD Product data, two-column tables, <dl> lists and "key: value" lines,
and normalizes lengths to mm, masses to g and forces to N. The page's visible text is kept as
`raw_text` (truncated) for review; agents only ever see it inside nonce delimiters
(`wrap_untrusted`).
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import secrets
import socket
from html.parser import HTMLParser
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..types import ErrorCode, MisesError

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
MAX_REDIRECTS = 5
RAW_TEXT_MAX = 20_000
SPECS_MAX = 100
USER_AGENT = "Mises-parts-directory/0.1"
_REDIRECTS = {301, 302, 303, 307, 308}
_TYPES = ("text/html", "application/xhtml+xml", "application/json", "application/ld+json",
          "text/plain")
_NUMERIC_HOST_RE = re.compile(r"(0x[0-9a-f]*|\d+)(\.(0x[0-9a-f]*|\d+))*\.?")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_V4COMPAT = ipaddress.ip_network("::/96")

Resolver = Callable[[str], Awaitable[list[str]]]


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


def _ip_blocked(ip: str) -> bool:
    """True unless `ip` is a plain public unicast address. IPv6 forms that carry an IPv4
    address (mapped, IPv4-compatible, NAT64 64:ff9b::/96, 6to4, Teredo) are judged by that
    IPv4 address; the local-use NAT64 prefix is refused outright."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if isinstance(a, ipaddress.IPv6Address):
        if a in _NAT64_LOCAL:
            return True
        inner = None
        if a.ipv4_mapped is not None:
            inner = a.ipv4_mapped
        elif a in _NAT64:
            inner = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        elif a.sixtofour is not None:
            inner = a.sixtofour
        elif a.teredo is not None:
            return True                          # tunnels to an address we cannot vet
        elif a in _V4COMPAT:
            if int(a) <= 1:                      # :: and ::1
                return True
            inner = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
        if inner is not None:
            a = inner
    return not a.is_global or a.is_multicast


def check_url(url: str, *, strip_query: bool = False) -> str:
    """The URL, normalized, if it may be fetched at all (https, port 443, a real host name).
    `strip_query` drops the query string (agent imports); the fragment is always dropped."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise _bad("url is required (at most 2048 characters)")
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise _bad("not a valid URL") from None
    if u.scheme.lower() != "https":
        raise _bad("only https links can be imported", "use the https:// address of the page")
    host = (u.hostname or "").lower().rstrip(".")
    if not host or u.username or u.password:
        raise _bad("the link needs a host and no user:password")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise _bad("local addresses cannot be imported")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
        if _NUMERIC_HOST_RE.fullmatch(host):    # 0177.0.0.1, 0x7f.1, 2130706433 ...
            raise _bad("numeric host names that are not plain IP addresses are not allowed")
    if ip is not None and _ip_blocked(str(ip)):
        raise _bad("private or local IP addresses cannot be imported")
    if port not in (None, 443):
        raise _bad("only the standard https port is allowed")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", netloc, u.path or "/", "" if strip_query else u.query, ""))


async def _vetted_ip(host: str, resolve: Resolver) -> str:
    """The address to connect to: every address the name resolves to must be public."""
    try:
        ipaddress.ip_address(host)
        return host                              # a literal, already checked by check_url
    except ValueError:
        pass
    try:
        ips = await resolve(host)
    except OSError:
        raise MisesError(ErrorCode.TRANSIENT, "could not resolve the page's host",
                         hint="check the link and the network") from None
    if not ips or any(_ip_blocked(ip) for ip in ips):
        raise _bad(f"{host} resolves to a private or local address; not fetched")
    v4 = [ip for ip in ips if ":" not in ip]
    return (v4 or ips)[0]


class Page:
    def __init__(self, url: str, final_url: str, content_type: str, text: str):
        self.url, self.final_url, self.content_type, self.text = url, final_url, content_type, text

    @property
    def domain(self) -> str:
        return urlsplit(self.final_url).hostname or ""


async def fetch(url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                resolve: Resolver = _system_resolve, strip_query: bool = False) -> Page:
    start = check_url(url, strip_query=strip_query)
    try:
        async with asyncio.timeout(TIMEOUT_S):
            return await _fetch(start, transport, resolve)
    except TimeoutError:
        raise MisesError(ErrorCode.TRANSIENT, f"the page took longer than {TIMEOUT_S:g} s",
                         hint="try again, or another page") from None
    except httpx.InvalidURL:
        raise _bad("not a valid URL") from None


def _peer_ok(resp: httpx.Response, ip: str) -> bool:
    stream = resp.extensions.get("network_stream")
    if stream is None:                           # e.g. a test transport: nothing to compare
        return True
    try:
        addr = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001
        return True
    if not addr:
        return True
    try:
        return ipaddress.ip_address(addr[0]) == ipaddress.ip_address(ip)
    except ValueError:
        return False


async def _fetch(start: str, transport, resolve: Resolver) -> Page:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.1"}
    async with httpx.AsyncClient(transport=transport, follow_redirects=False,
                                 timeout=TIMEOUT_S, trust_env=False) as http:
        url = start
        for hop in range(MAX_REDIRECTS + 1):
            logical = httpx.URL(url)
            host = logical.host
            ip = await _vetted_ip(host, resolve)
            pinned = logical.copy_with(host=ip)
            http.cookies.clear()                 # never send what a previous hop set
            try:
                async with http.stream("GET", pinned, headers={**headers, "Host": logical.netloc
                                                                .decode("ascii")},
                                       extensions={"sni_hostname": host}) as resp:
                    http.cookies.clear()
                    if not _peer_ok(resp, ip):
                        raise MisesError(ErrorCode.TRANSIENT, "could not connect to the page")
                    if resp.status_code in _REDIRECTS:
                        loc = resp.headers.get("Location")
                        if not loc:
                            raise MisesError(ErrorCode.TOOL_FAILED, "redirect without Location")
                        if hop == MAX_REDIRECTS:
                            raise MisesError(ErrorCode.TOOL_FAILED,
                                             f"more than {MAX_REDIRECTS} redirects")
                        try:
                            url = check_url(str(logical.join(loc)))
                        except (MisesError, httpx.InvalidURL) as e:
                            why = e.message if isinstance(e, MisesError) else "not a valid URL"
                            raise _bad(f"the page redirects somewhere not allowed: {why}",
                                       "only https pages on public hosts are fetched") from None
                        continue
                    if not resp.is_success:
                        raise MisesError(ErrorCode.TOOL_FAILED,
                                         f"the page answered HTTP {resp.status_code}")
                    ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    if ctype and ctype not in _TYPES:
                        raise _bad(f"not a web page ({ctype})", "link to the product page")
                    declared = resp.headers.get("Content-Length")
                    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                        raise _bad(f"page larger than {MAX_BYTES // (1024 * 1024)} MB; not read")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise _bad(f"page larger than {MAX_BYTES // (1024 * 1024)} MB;"
                                       " not read")
                    text = bytes(body).decode(resp.encoding or "utf-8", errors="replace")
                    return Page(start, url, ctype, text)
            except httpx.HTTPError:
                # one message for refused, unreachable, TLS and protocol failures alike
                raise MisesError(ErrorCode.TRANSIENT, "could not connect to the page",
                                 hint="check the link") from None
    raise MisesError(ErrorCode.TOOL_FAILED, "redirect loop")


# ---- HTML

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
          "article", "table", "ul", "ol", "dt", "dd", "header", "footer", "td", "th"}


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.rows: list[list[str]] = []
        self.dl: list[tuple[str, str]] = []
        self._skip = 0
        self._script_ld = False
        self._buf: list[str] = []
        self._in_title = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._dt: str | None = None
        self._dd: list[str] | None = None
        self._dtbuf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            self._script_ld = "ld+json" in a.get("type", "").lower()
            self._skip += 1
            self._buf = []
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"][:500])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "dt":
            self._dtbuf = []
        elif tag == "dd":
            self._dd = []
        if tag in _BLOCK:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag == "script":
            if self._script_ld:
                self.jsonld.append("".join(self._buf))
            self._script_ld = False
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_squash("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag == "dt" and self._dtbuf is not None:
            self._dt = _squash("".join(self._dtbuf))
            self._dtbuf = None
        elif tag == "dd" and self._dd is not None:
            if self._dt:
                self.dl.append((self._dt, _squash("".join(self._dd))))
            self._dd, self._dt = None, None
        if tag in _BLOCK:
            self.text.append("\n")

    def handle_data(self, data):
        if self._skip:
            if self._script_ld:
                self._buf.append(data)
            return
        if self._in_title:
            self.title += data
        for buf in (self._cell, self._dtbuf, self._dd):
            if buf is not None:
                buf.append(data)
        self.text.append(data)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _clean_text(parts: list[str]) -> str:
    lines = [_squash(line) for line in "".join(parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    return text


# ---- units

_UNITS = {  # unit -> (canonical unit, factor)
    "mm": ("mm", 1.0), "millimeter": ("mm", 1.0), "millimeters": ("mm", 1.0),
    "millimetre": ("mm", 1.0), "millimetres": ("mm", 1.0),
    "cm": ("mm", 10.0), "m": ("mm", 1000.0), "meter": ("mm", 1000.0), "metre": ("mm", 1000.0),
    "in": ("mm", 25.4), "inch": ("mm", 25.4), "inches": ("mm", 25.4), '"': ("mm", 25.4),
    "″": ("mm", 25.4), "ft": ("mm", 304.8),
    "g": ("g", 1.0), "gram": ("g", 1.0), "grams": ("g", 1.0), "gr": ("g", 1.0),
    "kg": ("g", 1000.0), "mg": ("g", 0.001), "oz": ("g", 28.349523125),
    "lb": ("g", 453.59237), "lbs": ("g", 453.59237),
    "n": ("N", 1.0), "kn": ("N", 1000.0), "kgf": ("N", 9.80665), "gf": ("N", 0.00980665),
    "lbf": ("N", 4.4482216152605),
    # UN/CEFACT codes used by schema.org QuantitativeValue.unitCode
    "mmt": ("mm", 1.0), "cmt": ("mm", 10.0), "mtr": ("mm", 1000.0), "inh": ("mm", 25.4),
    "grm": ("g", 1.0), "kgm": ("g", 1000.0), "onz": ("g", 28.349523125), "lbr": ("g", 453.59237),
    "new": ("N", 1.0),
}
_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_QTY_RE = re.compile(rf"^\s*(?:approx\.?|ca\.?|~)?\s*({_NUM})\s*([A-Za-z\"″]+)?\.?\s*$", re.I)
_DIM_SPLIT_RE = re.compile(r"[x×*](?=\s*[-+]?\d)", re.I)
_DIM_PART_RE = re.compile(rf"^\s*({_NUM})\s*([A-Za-z\"″]+)?\s*$")


def _dims(s: str) -> tuple[list[float], str | None] | None:
    """"22 x 8 x 7 mm", "16x16mm", "20mm x 40mm" -> ([numbers], unit) or None."""
    parts = _DIM_SPLIT_RE.split(s)
    if not 2 <= len(parts) <= 3:
        return None
    nums, units = [], []
    for p in parts:
        m = _DIM_PART_RE.match(p)
        if not m:
            return None
        nums.append(_num(m.group(1)))
        if m.group(2):
            units.append(m.group(2))
    if len(set(u.lower() for u in units)) > 1:
        return None
    return nums, units[-1] if units else None


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _round(v: float) -> float:
    return float(f"{v:.6g}")


def _factor(unit: str | None, key: str = "") -> tuple[str | None, float] | None:
    if unit is None:
        return None
    u = unit.strip().lower()
    hit = _UNITS.get(u)
    if hit and hit[0] == "g" and "thrust" in key.lower():
        return "N", hit[1] * 0.00980665         # "1200 g thrust" is a force
    return hit


def normalize(value, unit: str | None = None, key: str = "") -> tuple[str | float, str | None]:
    """(value, unit) with lengths in mm, masses in g and forces in N; anything else (V, mAh,
    KV, A, W, rpm, ...) is kept as it was. "22 x 8 x 7 mm" stays a string, each part in mm."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        f = _factor(unit, key)
        if f:
            return _round(value * f[1]), f[0]
        return _round(float(value)), unit
    s = _squash(str(value))[:200]
    if unit is None:
        m = _QTY_RE.match(s)
        if m:
            n, u = _num(m.group(1)), m.group(2)
            f = _factor(u, key)
            if f:
                return _round(n * f[1]), f[0]
            if u is None:
                return _round(n), None
            return _round(n), u
        d = _dims(s)
        if d:
            nums, u = d
            f = _factor(u, key)
            if f and f[0] == "mm":
                nums = [n * f[1] for n in nums]
                return " x ".join(f"{_round(n):g}" for n in nums), "mm"
            return " x ".join(f"{_round(n):g}" for n in nums), u
        return s, None
    m = _QTY_RE.match(s)
    f = _factor(unit, key)
    if m and m.group(2) is None and f:
        return _round(_num(m.group(1)) * f[1]), f[0]
    return s, unit


# ---- extraction

_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /()\-.#]{1,40}?)\s*[:：]\s*(\S.{0,120})$")


def _products(node) -> list[dict]:
    out = []
    if isinstance(node, list):
        for n in node:
            out += _products(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() in ("product", "individualproduct",
                                                     "productmodel") for x in types):
            out.append(node)
        for k in ("@graph", "mainEntity", "itemOffered"):
            if k in node:
                out += _products(node[k])
    return out


def _qv(v) -> tuple[object, str | None] | None:
    """A schema.org QuantitativeValue (or plain value) as (value, unit)."""
    if isinstance(v, dict):
        val = v.get("value")
        unit = v.get("unitCode") or v.get("unitText")
        if isinstance(val, (int, float, str)) and not isinstance(val, bool):
            return val, unit if isinstance(unit, str) else None
        return None
    if isinstance(v, (int, float, str)) and not isinstance(v, bool):
        return v, None
    return None


def _jsonld_specs(blobs: list[str]) -> tuple[dict, list[tuple[str, object, str | None]]]:
    info: dict = {}
    specs: list[tuple[str, object, str | None]] = []
    for blob in blobs[:20]:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for p in _products(data)[:5]:
            if isinstance(p.get("name"), str):
                info.setdefault("name", p["name"])
            brand = p.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            if isinstance(brand, str):
                info.setdefault("vendor", brand)
            for k in ("weight", "width", "height", "depth", "length"):
                q = _qv(p.get(k))
                if q:
                    specs.append((k, q[0], q[1]))
            for ap in p.get("additionalProperty") or []:
                if isinstance(ap, dict) and isinstance(ap.get("name"), str):
                    q = _qv(ap)
                    if q:
                        specs.append((ap["name"], q[0], q[1]))
    return info, specs


def _key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", k.lower()).strip("_")[:60]


_DIM_KEYS = [  # (regex on the spec key, dims key, unit)
    (r"weight|mass", "mass_g", "g"), (r"outer_diam|^od$|outside_diam", "od_mm", "mm"),
    (r"bore|inner_diam|^id$", "bore_mm", "mm"), (r"shaft", "shaft_d_mm", "mm"),
    (r"length", "length_mm", "mm"), (r"width", "width_mm", "mm"),
    (r"height", "height_mm", "mm"), (r"thick", "thickness_mm", "mm"),
    (r"diameter", "diameter_mm", "mm"), (r"depth", "height_mm", "mm"),
]
_PATTERN_RE = re.compile(rf"({_NUM})\s*(?:mm)?\s*[x×*]\s*({_NUM})", re.I)
_THREAD_RE = re.compile(r"\bM(\d+(?:\.\d+)?)\b")
_CATEGORY_WORDS = [
    ("stepper", r"\bnema\s?\d+|stepper"), ("motor", r"\bkv\b|brushless|\bstator|\bmotor"),
    ("prop", r"propell?er|\bprops?\b"), ("battery", r"\blipo\b|\bmah\b|battery|\bli-?ion"),
    ("servo", r"\bservo"), ("bearing", r"bearing"),
    ("fastener", r"\bscrew|\bbolt|\bnut\b|washer"), ("extrusion", r"extrusion|t-slot|v-slot"),
    ("flight_controller", r"flight controller|\besc\b|\bfc\b|stack"),
    ("board", r"raspberry|arduino|\bpcb\b|dev(elopment)? board"),
]


def _dims_and_mount(specs: list[dict]) -> tuple[dict, dict | None]:
    dims: dict = {}
    mount = None
    for s in specs:
        k, v, unit = s["key"], s["value"], s["unit"]
        if mount is None and re.search(r"mount|hole_(spacing|pattern|distance)|pattern", k):
            m = _PATTERN_RE.search(str(v))
            if m:
                a, b = _num(m.group(1)), _num(m.group(2))
                t = _THREAD_RE.search(str(v))
                mount = {"pattern": f"{a:g}x{b:g}", "spacing_mm": [a, b],
                         "hole_d": float(t.group(1)) if t else None,
                         "thread": f"M{t.group(1)}" if t else None, "layout": None}
        if isinstance(v, str) and unit == "mm" and re.search(r"dimension|size|^l_?x", k):
            nums = [float(n) for n in v.split(" x ")]
            for name, n in zip(("length_mm", "width_mm", "height_mm"), nums):
                dims.setdefault(name, n)
            continue
        if not isinstance(v, float):
            continue
        for pat, name, want in _DIM_KEYS:
            if re.search(pat, k) and unit == want:
                dims.setdefault(name, v)
                break
    return dims, mount


def extract(page: Page) -> dict:
    """A catalog item (source "url") from a fetched page. Nothing in it is trusted."""
    c = _Collector()
    if page.content_type in ("application/json", "application/ld+json"):
        c.jsonld.append(page.text)
    else:
        try:
            c.feed(page.text)
            c.close()
        except Exception:  # noqa: BLE001 - malformed HTML still gives what was parsed
            pass
    raw = _clean_text(c.text)
    info, ld = _jsonld_specs(c.jsonld)
    pairs: list[tuple[str, object, str | None]] = list(ld)
    pairs += [(r[0], r[1], None) for r in c.rows if len(r) == 2 and r[0] and r[1]]
    pairs += [(k, v, None) for k, v in c.dl]
    for line in raw.splitlines():
        m = _LINE_RE.match(line)
        if m:
            pairs.append((m.group(1), m.group(2), None))
    specs: list[dict] = []
    seen: set[str] = set()
    for k, v, unit in pairs:
        key = _key(str(k))
        if not key or key in seen or len(specs) >= SPECS_MAX:
            continue
        seen.add(key)
        value, u = normalize(v, unit, key)
        specs.append({"key": key, "value": value, "unit": u})
    dims, mount = _dims_and_mount(specs)
    name = _squash(info.get("name") or c.meta.get("og:title") or c.title or page.domain)[:200]
    vendor = info.get("vendor") or c.meta.get("og:site_name") or None
    hay = " ".join([name, *(s["key"] for s in specs)]).lower()
    category = next((cat for cat, pat in _CATEGORY_WORDS if re.search(pat, hay)), "other")
    has_box = all(k in dims for k in ("length_mm", "width_mm", "height_mm"))
    has_round = "diameter_mm" in dims or "od_mm" in dims
    return {"id": item_id_for(page.url), "name": name,
            "category": category, "source": "url", "url": page.url,
            "vendor": _squash(vendor)[:200] if isinstance(vendor, str) else None,
            "specs": specs, "dims": dims, "mount": mount,
            "geometry": "envelope" if has_box or has_round else None,
            "mass_source": "catalog" if "mass_g" in dims else "estimate",
            "sources": [{"title": f"Imported from {page.domain}", "url": page.url}],
            "raw_text": raw[:RAW_TEXT_MAX]}


def item_id_for(url: str) -> str:
    return "url_" + hashlib.sha256(url.encode()).hexdigest()[:16]


async def import_url(url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                     resolve: Resolver = _system_resolve, strip_query: bool = False) -> dict:
    return extract(await fetch(url, transport=transport, resolve=resolve,
                               strip_query=strip_query))


# ---- untrusted data for agents

_CLOSE_RE = re.compile(r"</\s*untrusted_page", re.I)


def wrap_untrusted(text: str, domain: str, nonce: str | None = None) -> str:
    """Page-derived text as a nonce-delimited data block; the text cannot forge the end."""
    nonce = nonce or secrets.token_hex(8)
    body = _CLOSE_RE.sub("<\\/untrusted_page", text)
    dom = re.sub(r"[^a-z0-9.\-]", "", domain.lower())
    return (f'<untrusted_page domain="{dom}" nonce="{nonce}">\n{body}\n'
            f'</untrusted_page nonce="{nonce}">')
