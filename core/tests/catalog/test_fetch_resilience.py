"""The web is flaky: the hardened fetcher retries a dropped connection or a rate limit once,
reads the first part of a huge page, reads every text type, and lets a caller vet a redirect's
full URL before anything is sent there."""
import httpx
import pytest

from mises.catalog import importer
from mises.catalog.importer import import_url
from mises.types import ErrorCode, MisesError

URL = "https://shop.example.com/p/falcon"


def spec(item, key):
    return next((s for s in item["specs"] if s["key"] == key), None)


async def test_a_dropped_connection_is_retried_once(web, public_dns):
    calls = []

    def flaky(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, text="<p>ok page</p>", headers={"Content-Type": "text/html"})
    web.pages[URL] = flaky
    page = await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert "ok page" in page.text and len(calls) == 2


async def test_a_dead_connection_fails_after_one_retry(web, public_dns):
    def down(req):
        raise httpx.ConnectError("down")
    web.pages[URL] = down
    with pytest.raises(MisesError) as e:
        await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert e.value.code == ErrorCode.TRANSIENT and len(web.requests) == 2


async def test_429_backs_off_and_retries_once_honouring_retry_after(web, public_dns,
                                                                     monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)
    monkeypatch.setattr(importer, "_sleep", fake_sleep)
    monkeypatch.setattr(importer, "RETRY_WAIT_S", 1.0)
    n = []

    def limited(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, text="<p>fine</p>", headers={"Content-Type": "text/html"})
    web.pages[URL] = limited
    page = await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert "fine" in page.text and waits == [3.0]
    # a long Retry-After is capped, and a second 429 is raised with its status
    waits.clear()
    web.pages[URL] = lambda req: httpx.Response(429, headers={"Retry-After": "3600"})
    with pytest.raises(MisesError) as e:
        await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert e.value.upstream_status == 429 and waits == [importer.RETRY_WAIT_MAX_S]


async def test_404_is_not_retried(web, public_dns):
    with pytest.raises(MisesError):
        await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert len(web.requests) == 1


async def test_dns_failure_names_the_host_and_points_at_search(web):
    async def nxdomain(host):
        raise OSError("SERVFAIL")
    with pytest.raises(MisesError) as e:
        await importer.fetch(URL, transport=web.transport, resolve=nxdomain)
    assert e.value.code == ErrorCode.TRANSIENT
    assert "shop.example.com" in e.value.message and "web_search" in e.value.hint


async def test_partial_read_of_a_large_page(web, public_dns):
    web.page(URL, "<p>" + "spec " * 1000 + "</p>", headers={"Content-Length": "999999999"})
    page = await importer.fetch(URL, transport=web.transport, resolve=public_dns,
                                max_bytes=1024, partial_ok=True)
    assert page.partial and len(page.body) == 1024 and page.text.startswith("<p>spec")
    web.page(URL, "<p>small</p>")
    small = await importer.fetch(URL, transport=web.transport, resolve=public_dns,
                                 partial_ok=True)
    assert not small.partial


async def test_a_large_pdf_is_never_cut(web, public_dns):
    web.page(URL, b"%PDF-1.7" + b"0" * 4000, ctype="application/pdf")
    with pytest.raises(MisesError) as e:
        await importer.fetch(URL, transport=web.transport, resolve=public_dns,
                             types=("application/pdf",), max_bytes=1024, partial_ok=True)
    assert "larger than" in e.value.message


async def test_import_reads_the_first_part_of_a_huge_page(web, public_dns):
    web.page(URL, "<html><head><title>Big Motor</title></head><body><p>Weight: 34 g</p>"
                  + "<p>filler</p>" * 400_000 + "</body></html>")
    it = await import_url(URL, transport=web.transport, resolve=public_dns)
    assert it["name"] == "Big Motor" and spec(it, "weight")["value"] == 34


@pytest.mark.parametrize("ctype", ["text/markdown", "text/x-markdown", "text/plain",
                                   "text/csv", "application/xml", "text/xml"])
async def test_text_types_are_readable(web, public_dns, ctype):
    web.page(URL, "# Motor\n\nWeight: 34 g\n", ctype=ctype)
    page = await importer.fetch(URL, transport=web.transport, resolve=public_dns)
    assert page.content_type == ctype and "Weight" in page.text


async def test_redirect_guard_sees_the_full_url_before_anything_is_sent(web, public_dns):
    web.redirect(URL, "https://docs.example.net/guide/motor")
    web.page("https://docs.example.net/guide/motor", "<p>the docs</p>")
    seen = []

    def guard(url):
        seen.append(url)
        return None
    page = await importer.fetch(URL, transport=web.transport, resolve=public_dns,
                                redirect_guard=guard)
    assert page.final_url == "https://docs.example.net/guide/motor" and seen == [page.final_url]
    web.redirect(URL, "https://evil.example.net/x7gq9zpl2mvb8rty4wkc1ndf6hjs3qea")
    n = len(web.requests)
    with pytest.raises(MisesError) as e:
        await importer.fetch(URL, transport=web.transport, resolve=public_dns,
                             redirect_guard=lambda u: "part of the link looks like encoded data")
    assert "evil.example.net" in e.value.message and "encoded data" in e.value.message
    assert [r.headers["host"] for r in web.requests[n:]] == ["shop.example.com"]
