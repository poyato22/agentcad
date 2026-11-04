"""web_open reads real pages: cross-site redirects followed (the target vetted like a link the
agent opened), every text type, the first part of a huge page, PDF text, and pages that time
out, drop the connection or no longer resolve fall back to a cached copy."""
import httpx
import pytest

from mises.browser.fallback import ARCHIVE
from mises.types import MisesError

from .test_websearch import BRAVE

DEV = "https://developer.nvidia.com/embedded/learn/jetson-orin-nano-devkit-user-guide/hardware_spec.html"
DOCS = "https://docs.nvidia.com/jetson/orin-nano-devkit/hardware_spec.html"
GOOD = ("<html><head><title>Jetson Orin Nano carrier board</title></head><body><p>"
        + "The carrier board is 100 mm x 79 mm with four M3 mounting holes. " * 5
        + "</p></body></html>")


async def test_a_redirect_to_another_ordinary_site_is_followed_and_reported(env, web):
    web.redirect(DEV, DOCS, status=301)
    web.page(DOCS, GOOD)
    out = await env.call("web_open", {"url": DEV})
    assert out.ok, out.summary
    snap = env.snaps.get(out.data["snapshot_id"])
    assert snap["final_url"] == DOCS and snap["domain"] == "docs.nvidia.com"
    assert "redirected to docs.nvidia.com" in out.summary
    assert out.data["final_url"] == DOCS
    root, _ = env.store.run_family(env.run.id)
    assert env.snaps.domain_counts(root, "docs.nvidia.com")[0] == 1
    assert env.store.pending_approvals(env.run.id) == []


@pytest.mark.parametrize("to,why", [
    ("https://x7gq9zpl2mvb8rty4wkc1ndf6hjs3qea.evil.com/p", "encoded data"),
    ("https://evil.com/c/QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFy", "unusual shape"),
    ("https://evil.com/c?d=QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0NTY3ODk", "unusual shape"),
    ("https://127.0.0.1/admin", "not allowed"),
    ("http://docs.nvidia.com/x", "not allowed"),
])
async def test_a_redirect_that_could_carry_data_out_is_still_refused(env, web, to, why):
    web.redirect(DEV, to)
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": DEV})
    assert why in e.value.message
    assert web.hosts() == ["developer.nvidia.com"]          # nothing was sent to the target
    assert env.store.pending_approvals(env.run.id) == []    # a refusal, never a card


async def test_a_redirect_past_the_per_site_cap_is_refused(env, web, monkeypatch):
    from mises.browser import tools as bt
    monkeypatch.setattr(bt, "PER_DOMAIN_FETCHES", 1)
    root, _ = env.store.run_family(env.run.id)
    env.snaps.count_domain(root, "docs.nvidia.com")
    web.redirect(DEV, DOCS)
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": DEV})
    assert "already read 1 pages on docs.nvidia.com" in e.value.message


@pytest.mark.parametrize("ctype", ["text/markdown", "text/plain", "text/x-markdown"])
async def test_markdown_and_plain_text_are_read_as_text(env, web, ctype):
    url = "https://berkeley-humanoid-lite.gitbook.io/docs/llms.txt"
    body = "# Berkeley Humanoid Lite\n\n- [Actuator](https://x/a): 5010 motor, <15 Nm\n" * 3
    web.page(url, body, ctype=ctype)
    out = await env.call("web_open", {"url": url})
    assert out.ok, out.summary
    snap = env.snaps.get(out.data["snapshot_id"])
    assert snap["text"].startswith("# Berkeley Humanoid Lite") and "<15 Nm" in snap["text"]
    assert snap["title"] == "llms.txt"


async def test_a_huge_page_is_read_in_part_and_says_so(env, web, monkeypatch):
    from mises.browser import tools as bt
    monkeypatch.setattr(bt, "PAGE_MAX_BYTES", 4096)
    web.page(DOCS, GOOD.replace("</body>", "<p>" + "more " * 5000 + "</p></body>"))
    out = await env.call("web_open", {"url": DOCS})
    assert out.ok and out.data["partial"] is True
    assert "first 4 KB" in out.summary
    assert env.snaps.get(out.data["snapshot_id"])["partial"] is True


async def test_pdf_text_is_read(env, web):
    url = "https://arxiv.org/pdf/2504.17249"
    web.page(url, b"%PDF-1.7 fake", ctype="application/pdf")
    seen = []

    async def fake_pdf_text(body):
        seen.append(body)
        return "Berkeley Humanoid Lite\nThe cycloidal actuator weighs 312 g."
    env.gate.services["pdf_text"] = fake_pdf_text
    out = await env.call("web_open", {"url": url})
    assert out.ok and seen == [b"%PDF-1.7 fake"]
    assert "weighs 312 g" in out.data["untrusted_page_data"]
    assert env.snaps.get(out.data["snapshot_id"])["title"] == "2504.17249"


async def test_a_pdf_without_readable_text_says_so(env, web):
    url = "https://www.ti.com/lit/ds/symlink/drv8825.pdf"
    web.page(url, b"%PDF-1.7 ...", ctype="application/pdf")

    async def nothing(body):
        return None
    env.gate.services["pdf_text"] = nothing
    out = await env.call("web_open", {"url": url})
    assert out.ok and "PDF document" in out.data["untrusted_page_data"]


async def test_a_page_that_times_out_falls_back_to_the_cached_copy(env, web, monkeypatch):
    import asyncio

    from mises.catalog import importer
    monkeypatch.setattr(importer, "TIMEOUT_S", 0.05)

    async def slow(req):
        await asyncio.sleep(1)
        return httpx.Response(200, text="late")
    web.pages[DOCS] = slow
    web.page(ARCHIVE + DOCS, GOOD)
    out = await env.call("web_open", {"url": DOCS})
    assert out.ok and out.data["fetch_path"] == "cache", out.summary
    assert "cached copy" in out.summary
    assert out.data["tried"] == ["plain", "cache"]       # a second slow try would only wait


async def test_a_dropped_connection_falls_back_after_its_retry(env, web):
    def down(req):
        raise httpx.ConnectError("reset")
    web.pages[DOCS] = down
    web.page(ARCHIVE + DOCS, GOOD)
    out = await env.call("web_open", {"url": DOCS})
    assert out.ok and out.data["fetch_path"] == "cache", out.summary


async def test_a_host_that_no_longer_resolves_falls_back_to_the_cached_copy(env, web):
    url = "https://www.intelrealsense.com/depth-camera-d435i/"
    real = env.gate.services["browser_http"]["resolve"]

    async def resolve(host):
        if host == "www.intelrealsense.com":
            raise OSError("SERVFAIL")
        return await real(host)
    env.gate.services["browser_http"]["resolve"] = resolve
    web.page(ARCHIVE + url, GOOD.replace("Jetson Orin Nano carrier board", "D435i"))
    out = await env.call("web_open", {"url": url})
    assert out.ok and out.data["fetch_path"] == "cache", out.summary
    assert out.data["tried"] == ["plain", "cache"]       # browser headers cannot help DNS


async def test_a_dead_host_with_no_copy_anywhere_reports_why(env, web):
    url = "https://gone.example.com/p"

    async def resolve(host):
        if host == "gone.example.com":
            raise OSError("SERVFAIL")
        return ["93.184.216.34"]
    env.gate.services["browser_http"]["resolve"] = resolve
    out = await env.call("web_open", {"url": url})
    assert not out.ok and "could not resolve gone.example.com" in out.summary
    assert "web_search" in out.summary


async def test_the_snippet_fallback_uses_the_web_search(env, web):
    url = "https://www.robotshop.com/products/dynamixel-xl430-w250-t-servo"
    web.page(url, "no", status=403)
    web.page(BRAVE, f"""<html><body><div class="snippet"><a class="l1" href="{url}">
      <div class="title">DYNAMIXEL XL430-W250-T Servo - RobotShop</div></a>
      <div class="content">Stall torque 1.5 Nm at 12 V, weight 57.2 g, 28.5 x 46.5 x 34 mm.</div>
      </div></body></html>""")
    out = await env.call("web_open", {"url": url})
    assert out.ok and out.data["fetch_path"] == "snippet", out.summary
    assert "weight 57.2 g" in out.data["untrusted_page_data"]
