"""A real general web search (no API key): search engines' HTML result pages through the same
hardened fetch, with the shops' own search pages as extra sources."""
import httpx
import pytest

from mises.browser import websearch as ws
from mises.browser.fallback import BROWSER_UA

from .conftest import FIXTURES

BRAVE = "https://search.brave.com/search"
YAHOO = "https://search.yahoo.com/search"
DDG = "https://html.duckduckgo.com/html/"
TMOTOR = "https://store.tmotor.com/product/mn5008-kv170-motor-antigravity-type.html"


def fx(name):
    return (FIXTURES / name).read_text()


# ---- parsing

def test_brave_results_are_the_organic_links_cleaned():
    rs = ws.parse_results(ws.ENGINES[0], fx("engine_brave.html"), BRAVE)
    assert [r["url"] for r in rs] == [
        TMOTOR, "https://unmannedrc.com/products/mn5008-antigravity-type-6-12s-uav-motor"]
    assert rs[0]["title"] == "Antigravity MN5008 KV170 UAV Motor - Ultralight"
    assert "delivers up to 4.2kg of thrust" in rs[0]["snippet"]
    assert rs[0]["site"] == "store.tmotor.com" and rs[1]["snippet"].startswith("Weight 135 g")


def test_yahoo_wrappers_are_unwrapped_and_its_own_pages_and_ads_dropped():
    rs = ws.parse_results(ws.ENGINES[1], fx("engine_yahoo.html"), YAHOO)
    assert [r["url"] for r in rs] == ["https://uav-en.tmotor.com/2020/Antigravity_0422/340.html"]
    assert rs[0]["title"] == "Antigravity MN5008 - T-MOTOR"
    assert "Weight 135 g" in rs[0]["snippet"]


def test_a_site_search_keeps_only_that_sites_pages():
    rs = ws.parse_results(ws.ENGINES[0], fx("engine_brave.html"), BRAVE, site="tmotor.com")
    assert [r["url"] for r in rs] == [TMOTOR]


def test_a_challenge_page_has_no_results():
    assert ws.parse_results(ws.ENGINES[2], fx("engine_ddg_challenge.html"), DDG) == []


@pytest.mark.parametrize("href,want", [
    ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.thrustcurve.org%2Fmotors%2F&rut=x",
     "https://www.thrustcurve.org/motors/"),
    ("https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/RE=1/RO=10/RU=https%3a%2f%2fx.example.com"
     "%2fa%2fb/RK=2/RS=c-", "https://x.example.com/a/b"),
    ("https://plain.example.com/p", "https://plain.example.com/p"),
])
def test_unwrap(href, want):
    assert ws.unwrap(href) == want


@pytest.mark.parametrize("href", ["http://x.example.com/", "https://10.0.0.1/", "https://localhost/",
                                  "https://search.brave.com/x", "https://www.bing.com/aclick?x",
                                  "https://x.example.com:8443/", "javascript:alert(1)"])
def test_unsafe_or_engine_links_are_no_results(href):
    assert ws.clean_result_url(href, BRAVE) is None


# ---- web_search

def engine_requests(web):
    return [r for r in web.requests if r.headers["host"] in {e.host for e in ws.ENGINES}]


async def test_a_general_query_searches_the_web(env, web):
    web.fixture(BRAVE, "engine_brave.html")
    out = await env.call("web_search", {"query": "MN5008 KV170 antigravity"})
    assert out.ok and out.data["result_count"] >= 2, out.summary
    req = engine_requests(web)[0]
    assert req.headers["host"] == "search.brave.com"
    assert req.url.query == b"q=MN5008+KV170+antigravity" and "cookie" not in req.headers
    web_row = out.data["sites"][0]
    assert web_row["site"] == "web" and web_row["status"] == "ok" and web_row["count"] == 2
    assert "web: 2" in out.summary
    block = out.data["untrusted_page_data"]
    assert TMOTOR in block and "Antigravity MN5008" not in out.summary
    snap = env.snaps.get(web_row["snapshot_id"])
    assert snap["kind"] == "search" and snap["results"][0]["url"] == TMOTOR
    assert env.store.pending_approvals(env.run.id) == []


async def test_a_blocked_engine_falls_through_to_the_next(env, web):
    web.page(BRAVE, "slow down", status=429)
    web.fixture(YAHOO, "engine_yahoo.html")
    out = await env.call("web_search", {"query": "MN5008 antigravity"})
    assert out.ok and out.data["sites"][0]["count"] == 1
    assert "web: 1" in out.summary
    yahoo = [r for r in web.requests if r.headers["host"] == "search.yahoo.com"]
    assert yahoo and BROWSER_UA in yahoo[0].headers["user-agent"]
    assert "cookie" not in yahoo[0].headers


async def test_every_engine_failing_says_so_and_what_was_tried(env, web):
    web.page(BRAVE, "no", status=403)
    web.page(YAHOO, "no", status=403)
    web.fixture(DDG, "engine_ddg_challenge.html")
    out = await env.call("web_search", {"query": "MN5008 antigravity"})
    row = out.data["sites"][0]
    assert row["site"] == "web" and row["status"] == "failed" and row["count"] == 0
    assert "Brave" in row["message"] and "DuckDuckGo" in row["message"]


async def test_a_named_site_without_its_own_search_page_is_searched_through_the_web(env, web):
    web.fixture(BRAVE, "engine_brave.html")
    out = await env.call("web_search", {"query": "MN5008", "sites": ["store.tmotor.com"]})
    assert out.ok and out.data["result_count"] == 1, out.summary
    req = engine_requests(web)[0]
    assert req.url.query == b"q=site%3Astore.tmotor.com+MN5008"
    assert out.data["sites"] == [{"site": "store.tmotor.com", "status": "ok", "message": "",
                                  "snapshot_id": out.data["sites"][0]["snapshot_id"],
                                  "count": 1}]


async def test_a_shop_search_that_finds_nothing_falls_back_to_a_site_web_search(env, web):
    web.page("https://www.adafruit.com/search", "<html><body><div id=app></div></body></html>")
    web.page(BRAVE, fx("engine_brave.html").replace("store.tmotor.com", "www.adafruit.com"))
    out = await env.call("web_search", {"query": "micro servo", "sites": ["Adafruit"]})
    assert out.ok and out.data["result_count"] == 1, out.summary
    row = out.data["sites"][0]
    assert row["site"] == "Adafruit" and row["status"] == "ok" and "through the web" in row["message"]
    assert engine_requests(web)[0].url.query == b"q=site%3Aadafruit.com+micro+servo"


async def test_each_engine_tried_spends_one_web_fetch(env, web, monkeypatch):
    from mises.browser import tools as bt
    monkeypatch.setattr(bt, "_budget", lambda ctx: 2)
    web.page(BRAVE, "no", status=403)
    web.page(YAHOO, "no", status=403)
    web.fixture(DDG, "engine_ddg_challenge.html")
    out = await env.call("web_search", {"query": "MN5008", "sites": ["store.tmotor.com"]})
    row = out.data["sites"][0]
    assert row["status"] == "failed" and "web fetches" in row["message"]
    assert {r.headers["host"] for r in web.requests} == {"search.brave.com", "search.yahoo.com"}
