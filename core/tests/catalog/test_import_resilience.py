"""catalog_import_url reads real shops: a rate limit is retried and then falls back like
web_open, a page this build already read is imported from its snapshot, and an ordinary
cross-site redirect is followed (one that could carry data out is still refused)."""
import httpx
import pytest

from mises.browser.fallback import ARCHIVE, BROWSER_UA
from mises.browser.snapshot import SnapshotStore, build
from mises.catalog.importer import Page
from mises.types import MisesError

from .conftest import FIXTURES
from .test_tools import URL, Env


@pytest.fixture
def env(tmp_path, web, public_dns):
    return Env(tmp_path, web, public_dns)

MOTOR = (FIXTURES / "table_motor.html").read_text()


def html(body):
    return lambda req: httpx.Response(200, content=body, headers={"Content-Type": "text/html"})


async def test_a_rate_limited_shop_is_retried_then_read_with_browser_headers(env, web):
    def shop(req):
        if BROWSER_UA in req.headers["user-agent"]:
            return html(MOTOR)(req)
        return httpx.Response(429, headers={"Retry-After": "1"})
    web.pages[URL] = shop
    out = await env.call("catalog_import_url", {"url": URL})
    assert out.ok and out.data["existing"] is False
    uas = [r.headers["user-agent"] for r in web.requests]
    assert len(uas) == 3 and BROWSER_UA in uas[-1]          # plain, its retry, browser headers
    assert all("cookie" not in r.headers for r in web.requests)


async def test_a_blocked_shop_falls_back_to_the_archived_copy(env, web):
    web.pages[URL] = lambda req: httpx.Response(403, text="denied")
    web.pages[ARCHIVE + URL] = html(MOTOR)
    out = await env.call("catalog_import_url", {"url": URL})
    assert out.ok and out.data["item"]["url"] == URL


async def test_a_shop_blocked_everywhere_says_so(env, web):
    web.pages[URL] = lambda req: httpx.Response(403, text="denied")
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": URL})
    assert "blocked automated access" in e.value.message


async def test_a_page_this_build_already_read_is_imported_without_fetching(env, web):
    snaps = SnapshotStore(env.store)
    env.gate.services["browser"] = snaps
    rec = build(Page(URL, URL, "text/html", MOTOR), run_id=env.run.id, project_id=None)
    snaps.add(rec)
    out = await env.call("catalog_import_url", {"url": URL})
    assert out.ok and web.requests == []
    assert '"key": "kv"' in out.data["untrusted_page_data"]
    assert env.catalog.get(out.data["item"]["id"])["url"] == URL


async def test_an_ordinary_cross_site_redirect_is_followed(env, web):
    web.redirect(URL, "https://store.example.net/products/falcon-2207")
    web.fixture("https://store.example.net/products/falcon-2207", "table_motor.html")
    out = await env.call("catalog_import_url", {"url": URL})
    assert out.ok
    assert [r.headers["host"] for r in web.requests] == ["shop.example.com", "store.example.net"]


async def test_a_redirect_that_could_carry_data_out_is_refused(env, web):
    web.redirect(URL, "https://x7gq9zpl2mvb8rty4wkc1ndf6hjs3qea.evil.example.net/p")
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": URL})
    assert "encoded data" in e.value.message
    assert {r.headers["host"] for r in web.requests} == {"shop.example.com"}
    assert env.store.pending_approvals(env.run.id) == []
