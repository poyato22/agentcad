"""H3: pages that fail to open fall back (plain -> realistic headers -> cache -> snippets)."""
import httpx
import pytest

from mises.browser.fallback import ARCHIVE, BROWSER_UA

ADA = "https://www.adafruit.com"
PRODUCT = f"{ADA}/product/169"
GOOD = ("<html><head><title>Micro servo</title></head><body><p>" + "A small hobby servo. " * 20
        + "</p></body></html>")
SHELL = "<html><head><title>App</title></head><body><div id=root></div>" \
        "<script>boot()</script><noscript>Enable JavaScript</noscript></body></html>"


def fetch_paths(env):
    return [s.get("fetch_path") for s in env.snaps.for_run(env.run.id)]


async def test_a_403_for_the_honest_agent_opens_with_browser_headers(env, web):
    def handler(req):
        if BROWSER_UA in req.headers.get("user-agent", ""):
            assert req.headers["accept-language"].startswith("en-US")
            assert "cookie" not in req.headers
            return httpx.Response(200, content=GOOD, headers={"Content-Type": "text/html"})
        return httpx.Response(403, content="denied", headers={"Content-Type": "text/html"})
    web.pages[PRODUCT] = handler
    out = await env.call("web_open", {"url": PRODUCT})
    assert out.ok and out.data["fetch_path"] == "headers"
    assert "browser-like headers" in out.summary
    assert "A small hobby servo" in out.data["untrusted_page_data"]
    assert fetch_paths(env) == ["headers"]


async def test_a_javascript_only_shell_falls_back_to_the_cached_copy(env, web):
    web.page(PRODUCT, SHELL)
    web.page(ARCHIVE + PRODUCT, GOOD)
    out = await env.call("web_open", {"url": PRODUCT})
    assert out.ok and out.data["fetch_path"] == "cache", out.summary
    snap = env.snaps.get(out.data["snapshot_id"])
    assert snap["url"] == PRODUCT and snap["domain"] == "www.adafruit.com"


async def test_everything_blocked_ends_in_snippets_and_says_so(env, web):
    web.page(PRODUCT, "no", status=403)
    snippets = (f'<html><body><div class="snippet"><a class="l1" href="{PRODUCT}"><div>'
                'Micro servo - Adafruit</div></a><div>Micro servo 9 g, 4.8 V, 1.8 kg-cm.</div>'
                '</div></body></html>')
    web.pages["https://search.brave.com/search"] = lambda req: httpx.Response(
        200, content=snippets, headers={"Content-Type": "text/html"})
    out = await env.call("web_open", {"url": PRODUCT})
    assert out.ok and out.data["fetch_path"] == "snippet"
    assert "only search-engine snippets" in out.summary
    assert "9 g, 4.8 V" in out.data["untrusted_page_data"]
    snap = env.snaps.get(out.data["snapshot_id"])
    assert snap["partial"] is True and snap["url"] == PRODUCT
    # no cookies on any request; budget charged for the extra fetches
    assert all("cookie" not in r.headers for r in web.requests)


async def test_nothing_works_reports_what_was_tried(env, web):
    web.page(PRODUCT, "no", status=403)
    out = await env.call("web_open", {"url": PRODUCT})
    assert not out.ok and out.data["blocked"] and out.data["tried"] == [
        "plain", "headers", "cache", "snippet"]


async def test_a_site_that_is_down_falls_back_after_one_retry_and_says_why(env, web):
    def boom(req):
        raise httpx.ConnectError("down")
    web.pages[PRODUCT] = boom
    out = await env.call("web_open", {"url": PRODUCT})
    assert not out.ok and "could not connect" in out.summary
    assert out.data["tried"] == ["plain", "headers", "cache", "snippet"]
    product = [r for r in web.requests if r.headers["host"] == "www.adafruit.com"]
    assert len(product) == 4          # plain and headers, each retried once


async def test_a_missing_page_does_not_fall_back(env, web):
    with pytest.raises(Exception) as e:
        await env.call("web_open", {"url": PRODUCT})
    assert "404" in str(e.value) and len(web.requests) == 1
