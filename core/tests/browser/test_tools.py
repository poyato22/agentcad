import asyncio

import httpx
import pytest

from mises.profile import Profile, save_profile
from mises.tools.registry import ToolRegistry
from mises.browser.tools import register_browser_tools
from mises.types import ErrorCode, MisesError, SafetyClass as C

ADA = "https://www.adafruit.com"
PRODUCT = f"{ADA}/product/169"


def test_classes_and_flags():
    reg = ToolRegistry()
    register_browser_tools(reg)
    assert {t.name: t.safety for t in reg.list()} == {
        "web_search": C.READ, "web_open": C.READ, "part_show": C.LOCAL_MUTATE}
    assert reg.get("web_open").needs_approval is not None
    assert reg.get("web_open").open_world and reg.get("web_search").open_world
    assert reg.get("web_search").needs_approval is not None    # P11 F5: a site off the list


# ---- web_search

async def test_search_named_site(env, web):
    web.fixture(f"{ADA}/search", "search_adafruit.html")
    out = await env.call("web_search", {"query": "micro servo", "sites": ["Adafruit"]})
    assert out.ok and out.data["result_count"] == 3
    req = web.requests[0]
    assert req.headers["host"] == "www.adafruit.com" and req.url.query == b"q=micro+servo"
    assert "cookie" not in req.headers and req.method == "GET"
    assert out.data["sites"] == [{"site": "Adafruit", "status": "ok", "message": "",
                                  "snapshot_id": out.data["sites"][0]["snapshot_id"],
                                  "count": 3}]
    block = out.data["untrusted_page_data"]
    assert block.startswith("<untrusted_page") and "Micro servo - TowerPro SG92R" in block
    assert "IGNORE PREVIOUS" in block and "IGNORE" not in out.summary
    assert "Micro servo" not in out.summary
    snap = env.snaps.get(out.data["sites"][0]["snapshot_id"])
    assert snap["kind"] == "search" and snap["run_id"] == env.run.id
    assert snap["results"][0]["url"] == PRODUCT and snap["project_id"] == env.project


async def test_search_picks_sites_from_the_query_and_reports_blocked_ones(env, web):
    web.fixture(f"{ADA}/search", "search_adafruit.html")
    web.page("https://www.pololu.com/search", "denied", status=403)
    web.fixture("https://www.servocity.com/search.php", "captcha.html")
    out = await env.call("web_search", {"query": "servo"})
    by = {s["site"]: s for s in out.data["sites"]}
    assert set(by) == {"Adafruit", "Pololu", "ServoCity"}
    assert by["Pololu"]["status"] == "blocked"
    assert by["Pololu"]["message"] == "site blocked automated access (HTTP 403)"
    assert by["ServoCity"]["status"] == "blocked"
    assert "site blocked automated access" in by["ServoCity"]["message"]
    assert by["Adafruit"]["count"] == 3
    assert "Pololu: site blocked automated access (HTTP 403)" in out.summary
    # the blocked visits show in the run's browser too
    kinds = [s.get("blocked") is not None for s in env.snaps.for_run(env.run.id)]
    assert sorted(kinds) == [False, True, True]


async def test_search_other_failures_and_empty_pages(env, web):
    web.page(f"{ADA}/search", "<html><body><div id=app></div></body></html>")
    out = await env.call("web_search", {"query": "servo", "sites": ["adafruit.com", "Pololu"]})
    by = {s["site"]: s for s in out.data["sites"]}
    assert by["Adafruit"]["status"] == "ok" and "may need JavaScript" in by["Adafruit"]["message"]
    assert by["Pololu"]["status"] == "failed" and "404" in by["Pololu"]["message"]
    assert "untrusted_page_data" not in out.data


async def test_search_redirect_off_site_refused(env, web):
    web.redirect(f"{ADA}/search", "https://tracker.example.net/collect")
    out = await env.call("web_search", {"query": "servo", "sites": ["Adafruit"]})
    assert out.data["sites"][0]["status"] == "failed"
    assert "another site" in out.data["sites"][0]["message"]
    assert web.hosts() == ["www.adafruit.com"]


DDG = "https://html.duckduckgo.com/html/"
DDG_PAGE = """<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.thrustcurve.org%2Fmotors%2FAeroTech%2FF67W%2F&rut=x">AeroTech F67W - ThrustCurve</a>
<a class="result__snippet">F67W thrust curve, total impulse 76 Ns</a>
<a href="https://ads.example.net/click?x=1">Buy motors</a>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fevil.example.com%2Fx">Off-site result</a>
</body></html>"""


# P11 F5: any site can be searched, a suggested site without a search page too (thrustcurve.org
# was refused as "not a suggested site"): through a site-restricted web search, keeping only
# that site's pages
@pytest.mark.parametrize("named", ["thrustcurve.org", "ThrustCurve",
                                   "https://www.thrustcurve.org/"])
async def test_search_any_site_through_the_site_search(env, web, named):
    web.page(DDG, DDG_PAGE)
    out = await env.call("web_search", {"query": "F67W thrust curve", "sites": [named]})
    assert out.ok and out.data["result_count"] == 1
    req = web.requests[0]
    assert req.headers["host"] == "html.duckduckgo.com"
    assert req.url.query == b"q=site%3Athrustcurve.org+F67W+thrust+curve"
    snap = env.snaps.get(out.data["sites"][0]["snapshot_id"])
    assert [r["url"] for r in snap["results"]] == [
        "https://www.thrustcurve.org/motors/AeroTech/F67W/"]
    assert env.store.pending_approvals(env.run.id) == []


async def test_search_a_site_off_the_list_counts_and_an_odd_one_is_refused(env, web):
    web.page(DDG, DDG_PAGE.replace("thrustcurve.org", "rocketryforum.com"))
    out = await env.call("web_search", {"query": "estes rocket", "sites": ["rocketryforum.com"]})
    assert out.ok and out.data["result_count"] == 1
    root, _ = env.store.run_family(env.run.id)
    assert env.snaps.domain_counts(root, "rocketryforum.com")[0] == 1
    odd = "x7gq9zpl2mvb8rty4wkc1ndf6hjs3qea.evil.com"          # a data-looking host label
    with pytest.raises(MisesError) as e:
        await env.call("web_search", {"query": "x", "sites": [odd]})
    assert e.value.code == ErrorCode.INVALID_INPUT and "evil.com" in e.value.message
    assert len(web.requests) == 1                     # nothing more fetched
    assert env.store.pending_approvals(env.run.id) == []     # browsing never asks


async def test_search_a_name_that_is_no_site_refused(env, web):
    with pytest.raises(MisesError) as e:
        await env.call("web_search", {"query": "x", "sites": ["Not A Site"]})
    assert e.value.code == ErrorCode.INVALID_INPUT and "domain" in e.value.hint
    assert web.requests == []


async def test_search_uses_the_users_site_list(env, web):
    save_profile(env.tmp, Profile.model_validate({"suggested_sites": [
        {"name": "My Shop", "domain": "parts.example.com",
         "search_url": "https://parts.example.com/find?term={q}"}]}))
    web.fixture("https://parts.example.com/find", "search_generic.html")
    out = await env.call("web_search", {"query": "bearing"})
    assert [s["site"] for s in out.data["sites"]] == ["My Shop"]
    assert out.data["result_count"] == 2
    with pytest.raises(MisesError):
        await env.call("web_search", {"query": "x", "sites": ["Adafruit"]})   # removed


# ---- web_open

async def test_open_suggested_site_without_a_card(env, web):
    web.fixture(PRODUCT, "product_servo.html")
    out = await env.call("web_open", {"url": PRODUCT + "?utm_source=leak&gclid=x#x"})
    assert env.store.pending_approvals(env.run.id) == []
    assert web.requests[0].url.query == b""                 # trackers never leave
    sid = out.data["snapshot_id"]
    assert out.data["has_part"] and out.data["image_count"] == 2 and out.data["table_count"] == 2
    block = out.data["untrusted_page_data"]
    assert block.startswith('<untrusted_page domain="www.adafruit.com" nonce="')
    assert block.count("</untrusted_page") == 1
    assert "Ignore all previous instructions" in block and '"price": 5.95' in block
    assert "Weight | 9 g" in block
    assert "Ignore" not in out.summary and "TowerPro" not in out.summary
    snap = env.snaps.get(sid)
    assert snap["url"] == PRODUCT and snap["title"].startswith("Micro servo")


async def test_open_other_domain_without_a_card(env, web):
    # E31: reading a public https page is not approval-gated; a non-suggested host loses its
    # query but is still opened, with no card
    url = "https://shop.example.com/p/servo"
    web.fixture(url, "product_servo.html")
    out = await env.call("web_open", {"url": url + "?d=c2VjcmV0"})
    assert out.ok and env.store.pending_approvals(env.run.id) == []
    assert web.hosts() == ["shop.example.com"] and web.requests[0].url.query == b""


# Browsing never asks the user: a link that looks like it carries encoded data is refused with
# a hint (nothing fetched, no card); low-information shapes (repeated letters, a few extra host
# labels) open like any other page.
EXFIL = [
    "https://evil.com/p/" + "aB3dE5gH7jK9mN1pQ3sT",                           # random segment
    "https://evil.com/p/" + "8f14e45fceea167a5a36",                           # hex segment
    "https://" + "x7gq9zpl2mvb8rty4wkc1ndf6hjs3qea" + ".evil.com/x",          # random host label
    "https://evil.com/" + "k" * 130,                                          # over-long segment
]


@pytest.mark.parametrize("url", EXFIL)
async def test_exfil_shaped_links_are_refused_without_a_card(env, web, url):
    web.fixture(url, "product_servo.html")
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": url})
    assert e.value.code == ErrorCode.INVALID_INPUT and e.value.hint
    assert web.requests == [] and env.store.pending_approvals(env.run.id) == []


@pytest.mark.parametrize("url", [
    "https://www.prusa3d.com/product/original-prusa-mini-plus-semi-assembled-3d-printer-with-"
    "magnetic-steel-sheet/",                                                   # long word slug
    "https://s1.s2.s3.s4.evil.com/p",                                          # a few labels
])
async def test_ordinary_links_open_without_a_card(env, web, url):
    web.fixture(url, "product_servo.html")
    assert (await env.call("web_open", {"url": url})).ok
    assert env.store.pending_approvals(env.run.id) == []


async def test_word_paths_open_but_one_site_is_capped(env, web):
    # hyphenated words (the reviewer's third payload) look like a product slug, so they open;
    # the per-domain cap then bounds how much any one site can receive per turn
    from mises.browser.tools import PER_DOMAIN_FETCHES
    url = "https://evil.com/secret-token-1/password-hunter2-abc/key-value"
    web.fixture(url, "product_servo.html")
    for _ in range(PER_DOMAIN_FETCHES):
        assert (await env.call("web_open", {"url": url})).ok
    assert env.store.pending_approvals(env.run.id) == []
    with pytest.raises(MisesError) as e:                       # past the cap: refused, no card
        await env.call("web_open", {"url": url})
    assert f"already read {PER_DOMAIN_FETCHES} pages on evil.com" in e.value.message
    assert env.store.pending_approvals(env.run.id) == []


async def test_non_suggested_total_cap(env, web, monkeypatch):
    import mises.browser.tools as bt
    monkeypatch.setattr(bt, "NON_SUGGESTED_FETCHES", 3)
    for i in range(3):
        u = f"https://site{i}.example.com/p/servo"
        web.fixture(u, "product_servo.html")
        assert (await env.call("web_open", {"url": u})).ok
    u = "https://site9.example.com/p/servo"
    web.fixture(u, "product_servo.html")
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": u})
    assert "sites that are not suggested" in e.value.message
    assert env.store.pending_approvals(env.run.id) == []


def test_normal_product_links_are_not_suspicious():
    from mises.browser.tools import suspicious_shape
    for u in ("https://www.mcmaster.com/91290A111/", "https://shop.example.com/p/servo",
              "https://www.digikey.com/en/products/detail/texas-instruments/LM317T/33014",
              "https://www.amazon.com/dp/B07XYZ1234",
              "https://www.servocity.com/2000-series-dual-mode-servo-25-2/"):
        assert suspicious_shape(u) is None, u


async def test_open_always_allowed_domain_needs_no_card(env, web):
    save_profile(env.tmp, Profile(catalog_allowed_domains=["shop.example.com"]))
    url = "https://shop.example.com/p/servo"
    web.fixture(url, "product_servo.html")
    out = await env.call("web_open", {"url": url})
    assert out.ok and env.store.pending_approvals(env.run.id) == []


async def test_open_redirect_to_an_unapproved_site_refused(env, web):
    web.redirect(PRODUCT, "https://elsewhere.example.net/p")
    web.fixture("https://elsewhere.example.net/p", "product_servo.html")
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": PRODUCT})
    assert "another site (elsewhere.example.net)" in e.value.message
    assert web.hosts() == ["www.adafruit.com"]


async def test_open_redirect_within_the_site_and_to_suggested_sites_ok(env, web):
    web.redirect(PRODUCT, "https://cdn.adafruit.com/p/169")
    web.redirect("https://cdn.adafruit.com/p/169", "https://www.pololu.com/product/1")
    web.fixture("https://www.pololu.com/product/1", "product_servo.html")
    out = await env.call("web_open", {"url": PRODUCT})
    assert out.ok and env.snaps.get(out.data["snapshot_id"])["domain"] == "www.pololu.com"


@pytest.mark.parametrize("url", ["http://www.adafruit.com/product/1",
                                 "https://127.0.0.1/x", "https://localhost/x",
                                 "https://[::ffff:10.0.0.1]/x", "https://www.adafruit.com:8443/"])
async def test_open_refuses_unsafe_urls(env, web, url):
    with pytest.raises(MisesError):
        await env.call("web_open", {"url": url})
    assert web.requests == []


async def test_open_host_resolving_private_refused(env, web):
    async def private(host):
        return ["64:ff9b::a00:1"]                            # NAT64 of 10.0.0.1
    env.gate.services["browser_http"]["resolve"] = private
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": PRODUCT})
    assert "private or local" in e.value.message and web.requests == []


async def test_open_blocked_site(env, web):
    web.page(PRODUCT, "nope", status=429)
    out = await env.call("web_open", {"url": PRODUCT})
    assert not out.ok and out.data["blocked"]
    assert "site blocked automated access (HTTP 429)" in out.summary
    web.fixture(PRODUCT, "captcha.html")
    out = await env.call("web_open", {"url": PRODUCT})
    assert not out.ok and "site blocked automated access" in out.summary
    assert "untrusted_page_data" not in out.data


async def test_open_pdf_datasheet(env, web):
    url = "https://www.ti.com/lit/ds/symlink/drv8825.pdf"
    web.page(url, b"%PDF-1.7 ...", ctype="application/pdf")
    out = await env.call("web_open", {"url": url})
    assert out.ok and "PDF document" in out.data["untrusted_page_data"]


async def test_open_output_fits_the_gate(env, web):
    web.page(PRODUCT, "<p>" + "word " * 100_000 + "</p>")
    out = await env.call("web_open", {"url": PRODUCT})
    assert out.handles == [] and out.data["snapshot_id"]      # not spilled to a file


async def test_open_suggested_site_keeps_a_product_query(env, web):
    url = "https://us.misumi-ec.com/vona2/detail/110300465870/"
    web.fixture(url, "product_servo.html")
    out = await env.call("web_open", {"url": url + "?HissuCode=SFJ6-100&utm_medium=x&ref=a"})
    assert out.ok and env.store.pending_approvals(env.run.id) == []
    assert web.requests[0].url.query == b"HissuCode=SFJ6-100"
    assert env.snaps.get(out.data["snapshot_id"])["url"] == url + "?HissuCode=SFJ6-100"


async def test_open_allowed_domain_drops_the_query(env, web):
    save_profile(env.tmp, Profile(catalog_allowed_domains=["shop.example.com"]))
    url = "https://shop.example.com/p/servo"
    web.fixture(url, "product_servo.html")
    out = await env.call("web_open", {"url": url + "?color=red"})
    assert out.ok and web.requests[0].url.query == b""


@pytest.mark.parametrize("bad", [
    "/product/169?d=" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0NTY3ODk",     # base64 blob value
    "/product/169?x=" + "deadbeefcafebabe0123",                             # hex blob value
    "/" + "a" * 70 + "/169",                                                # blob-like segment
    "/" + "/".join(["seg"] * 90),                                           # path too long
])
async def test_odd_suggested_links_are_refused_without_a_card(env, web, bad):
    url = ADA + bad
    base = url.split("?")[0]
    web.fixture(base, "product_servo.html")
    if "?" in bad:
        # the query is dropped (it does not look like a product query) and the path is fine
        out = await env.call("web_open", {"url": url})
        assert out.ok and web.requests[0].url.query == b""
        return
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": url})
    assert web.requests == [] and "unusual shape" in e.value.message
    assert env.store.pending_approvals(env.run.id) == []


async def test_fetch_budget_per_run(env, web):
    save_profile(env.tmp, Profile(browser_fetches_per_run=3))
    web.fixture(PRODUCT, "product_servo.html")
    web.fixture(f"{ADA}/search", "search_adafruit.html")
    await env.call("web_open", {"url": PRODUCT})
    await env.call("web_search", {"query": "servo", "sites": ["Adafruit", "Pololu"]})
    n = len(web.requests)
    with pytest.raises(MisesError) as e:
        await env.call("web_open", {"url": PRODUCT})
    assert e.value.code == ErrorCode.BUDGET_EXHAUSTED and "3 web fetches" in e.value.message
    with pytest.raises(MisesError):
        await env.call("web_search", {"query": "servo", "sites": ["Adafruit"]})
    assert len(web.requests) == n                            # nothing went out


@pytest.mark.parametrize("q", ["x" * 121, "servo QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0NTY3",
                               "motor deadbeefcafebabe12"])
async def test_search_query_shape(env, web, q):
    with pytest.raises(MisesError) as e:
        await env.call("web_search", {"query": q, "sites": ["Adafruit"]})
    assert e.value.code == ErrorCode.INVALID_INPUT and web.requests == []


@pytest.mark.parametrize("q", ["MG996R servo", "M3x8 socket head", "STM32F103C8T6",
                               "SN74LVC1G125DBVR", "608-2RS bearing 8x22x7"])
async def test_real_part_queries_pass(env, web, q):
    out = await env.call("web_search", {"query": q, "sites": ["Adafruit"]})
    assert out.data["sites"][0]["site"] == "Adafruit"


# ---- part_show

async def opened(env, web):
    web.fixture(PRODUCT, "product_servo.html")
    return (await env.call("web_open", {"url": PRODUCT})).data["snapshot_id"]


async def test_part_show_inserts_into_build_and_lists_it(env, web):
    # E15: a shown catalog-backed part is inserted into the build and listed in the BOM, with no
    # user "insert" / "add to list" buttons
    from mises.bom import view
    sid = await opened(env, web)
    out = await env.call("part_show", {"snapshot_id": sid})
    assert out.data["part_id"]                                   # inserted into the build
    items = view(env.store, env.bom, env.tmp, env.project, catalog=env.catalog)["items"]
    assert any((i.get("catalog_id") or "").startswith("url_") for i in items)


async def test_part_show_from_a_snapshot(env, web):
    sid = await opened(env, web)
    env.drained()
    out = await env.call("part_show", {"snapshot_id": sid, "specs": [
        {"key": "stall torque", "value": 2.5, "unit": "kg-cm"}]})
    card = env.cards.get(out.data["card_id"])
    assert card["name"] == "Micro servo - TowerPro SG92R" and card["price"] == 5.95
    assert card["currency"] == "USD" and card["vendor"] == "TowerPro"
    assert card["url"] == PRODUCT and card["image"] == f"/browser/snapshots/{sid}/images/0"
    assert card["specs"] == [{"key": "stall torque", "value": 2.5, "unit": "kg-cm"}]
    assert card["catalog_backed"] and card["catalog_id"].startswith("url_")
    item = env.catalog.get(card["catalog_id"])
    assert item["project_id"] == env.project and item["dims"]["mass_g"] == 9.0
    evs = env.drained()
    shown = [e for e in evs if e.type == "part_shown"]
    assert shown[0].data["id"] == card["id"]
    # the announcement is the Gate mapper's (U3c): data["card"] carries ids only
    # merged with U3c: exactly one, from the Gate's mapper, and no page text in its title
    ann = [e.data for e in evs if e.type == "agent_announce"]
    assert [a["kind"] for a in ann] == ["part_shown"]
    assert "TowerPro" not in ann[0]["title"] and ann[0]["ref"]["card_id"] == card["id"]
    assert out.data["card"] == {"card_id": card["id"], "catalog_id": card["catalog_id"],
                                "snapshot_id": sid}
    assert "5.95 USD" in out.summary
    assert "TowerPro" not in out.summary and "untrusted_page_data" in out.data
    assert env.cards.for_run(env.run.id)[0]["id"] == card["id"]


async def test_part_show_refuses_foreign_urls_and_images(env, web):
    sid = await opened(env, web)
    with pytest.raises(MisesError) as e:
        await env.call("part_show", {"snapshot_id": sid, "url": "https://phish.example.com/"})
    assert "snapshot's site" in e.value.message
    with pytest.raises(MisesError):
        await env.call("part_show", {"snapshot_id": sid, "image": "https://x.example.com/a.png"})
    with pytest.raises(MisesError):
        await env.call("part_show", {})                      # neither source
    with pytest.raises(MisesError):
        await env.call("part_show", {"snapshot_id": sid, "catalog_id": "x"})


async def test_part_show_of_another_builds_snapshot_refused(env, web):
    sid = await opened(env, web)
    other = env.store.create_run("engineer", "fake", "m", "t")
    env.store.update_run(other.id, project_id=env.store.create_project("Other").id)
    from mises.types import Caps
    env.store.create_policy(other.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    with pytest.raises(MisesError) as e:
        await env.gate.call(other.id, "part_show", {"snapshot_id": sid}, call_id="o1",
                            workdir=env.tmp)
    assert e.value.code == ErrorCode.OUT_OF_SCOPE


async def test_part_show_from_the_catalog(env, web):
    out = await env.call("part_show", {"catalog_id": "servo_mg996r_standard", "price": 9.5})
    card = env.cards.get(out.data["card_id"])
    assert card["catalog_backed"] and card["catalog_id"] == "servo_mg996r_standard"
    assert card["price"] == 9.5 and card["image"] is None
    assert card["currency"] is None                          # never assumed
    assert "(currency unknown)" in out.summary
    assert web.requests == []
