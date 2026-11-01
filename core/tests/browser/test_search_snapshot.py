from pathlib import Path

import pytest

from mises.browser import search, sites
from mises.browser.snapshot import BLOCKED, blocked_reason, build, public, summary
from mises.catalog.importer import Page
from mises.profile import Profile, SuggestedSite

FX = Path(__file__).parent / "fixtures"


def site(domain, name=None):
    return next((s for s in sites.DEFAULT_SITES if s["domain"] == domain), None) or {
        "name": name or domain, "domain": domain, "search_url": None, "categories": []}


def results(fixture, base, domain, **kw):
    return search.extract_results((FX / fixture).read_text(), base, site(domain), **kw)


# ---- sites

def test_defaults_are_marked_suggested_and_valid():
    p = Profile()
    names = [s.name for s in p.suggested_sites]
    for want in ("McMaster-Carr", "Misumi", "Digi-Key", "Mouser", "Adafruit", "SparkFun",
                 "Pololu", "ServoCity", "GetFPV", "RobotShop", "AliExpress", "Amazon"):
        assert want in names
    assert all(s.suggested for s in p.suggested_sites)
    assert any(s.search_url is None and "datasheets" in s.categories for s in p.suggested_sites)


@pytest.mark.parametrize("bad", [
    {"name": "x", "domain": "shop.example.com", "search_url": "http://shop.example.com/s?q={q}"},
    {"name": "x", "domain": "shop.example.com", "search_url": "https://shop.example.com/s"},
    {"name": "x", "domain": "shop.example.com", "search_url": "https://other.example.com/?q={q}"},
    {"name": "x", "domain": "shop.example.com", "search_url": "https://a:b@shop.example.com/{q}"},
    {"name": "x", "domain": "not a domain"},
])
def test_bad_site_entries_refused(bad):
    with pytest.raises(ValueError):
        SuggestedSite.model_validate(bad)


def test_duplicate_site_domains_refused():
    s = {"name": "x", "domain": "shop.example.com"}
    with pytest.raises(ValueError):
        Profile.model_validate({"suggested_sites": [s, {**s, "name": "y"}]})


def test_user_site_on_subdomain_ok():
    s = SuggestedSite.model_validate({"name": "Shop", "domain": "Example.com.",
                                      "search_url": "https://shop.example.com/find?q={q}"})
    assert s.domain == "example.com" and not s.suggested


def test_search_url_encoding():
    dk = site("digikey.com")
    assert sites.search_url(dk, "m3 screw & nut") == (
        "https://www.digikey.com/en/products/result?keywords=m3+screw+%26+nut")
    mc = site("mcmaster.com")
    assert sites.search_url(mc, "m3 screw/x") == "https://www.mcmaster.com/m3-screw%2Fx/"


def test_on_domain():
    assert sites.on_domain("www.digikey.com", "digikey.com")
    assert sites.on_domain("digikey.com", "digikey.com")
    assert not sites.on_domain("evildigikey.com", "digikey.com")
    assert not sites.on_domain("digikey.com.evil.net", "digikey.com")


def test_pick_sites_by_query_words_and_by_name():
    ds = sites.DEFAULT_SITES
    picked = [s["name"] for s in sites.pick_sites(ds, "mg996r servo")]
    assert picked[0] in ("Pololu", "ServoCity", "Adafruit", "RobotShop") and len(picked) == 3
    assert [s["name"] for s in sites.pick_sites(ds, "608 bearing")][:2] == ["McMaster-Carr",
                                                                           "Misumi"]
    assert [s["name"] for s in sites.pick_sites(ds, "xyzzy")] == ["McMaster-Carr", "Misumi",
                                                                 "Digi-Key"]
    named = sites.pick_sites(ds, "x", ["pololu", "www.adafruit.com"])
    assert [s["name"] for s in named] == ["Pololu", "Adafruit"]
    # P11 F5: any domain is searchable (off the list: marked so the exfil guard sees it), and a
    # listed site without a search page goes through the site-restricted web search
    [off] = sites.pick_sites(ds, "x", ["https://www.thrustcurve.org/motors"])
    assert off["domain"] == "thrustcurve.org" and off["via"] == sites.SITE_SEARCH_HOST
    assert off.get("listed") is None and off["name"] == "ThrustCurve"   # now a suggested site
    [evil] = sites.pick_sites(ds, "x", ["evil.example.com"])
    assert evil["listed"] is False and "site%3Aevil.example.com" in evil["search_url"]
    [ti] = sites.pick_sites(ds, "x", ["ti.com"])                # no search page of its own
    assert ti["name"] == "Texas Instruments" and ti["via"] == sites.SITE_SEARCH_HOST
    with pytest.raises(KeyError):
        sites.pick_sites(ds, "x", ["Not A Site"])
    rocket = [s["domain"] for s in sites.pick_sites(ds, "estes rocket parachute")]
    assert "thrustcurve.org" in rocket and "apogeerockets.com" in rocket


# ---- search adapters on fixture pages

def test_adafruit_results():
    r = results("search_adafruit.html", "https://www.adafruit.com/search?q=servo",
                "adafruit.com")
    assert [x["url"] for x in r] == ["https://www.adafruit.com/product/169",
                                     "https://www.adafruit.com/product/155",
                                     "https://www.adafruit.com/product/1143"]
    assert r[0]["title"] == "Micro servo - TowerPro SG92R" and r[0]["site"] == "Adafruit"
    assert "180 degrees" in r[0]["snippet"] and "$5.95" in r[0]["snippet"]
    assert r[1]["title"] == "Standard servo - TowerPro SG-5010"    # longest title kept
    assert "IGNORE PREVIOUS" in r[2]["snippet"]                     # data, not dropped
    assert all("9999" not in x["url"] for x in r)                   # script text ignored


def test_digikey_results():
    r = results("search_digikey.html", "https://www.digikey.com/en/products/result?keywords=s",
                "digikey.com")
    assert [x["url"].rsplit("/", 1)[1] for x in r] == ["7597224", "6827164"]
    assert r[0]["title"] == "SER0006" and "SERVO MOTOR" in r[0]["snippet"]


def test_amazon_results_are_canonical_and_skip_ads():
    r = results("search_amazon.html", "https://www.amazon.com/s?k=nema+17", "amazon.com")
    assert [x["url"] for x in r] == ["https://www.amazon.com/dp/B00PNEQKC0",
                                     "https://www.amazon.com/dp/B07LCJ2QC4"]
    assert r[0]["title"].startswith("STEPPERONLINE Nema 17 Stepper Motor Bipolar 2A 59Ncm (84")
    assert "4 pack" in r[1]["snippet"]


def test_itemlist_jsonld_on_a_user_site():
    r = results("search_itemlist.html", "https://shop.example.org/search?q=bearing",
                "example.org")
    assert [(x["title"], x["url"]) for x in r] == [
        ("608-2RS Ball Bearing 8x22x7", "https://shop.example.org/products/608-2rs-bearing"),
        ("625ZZ Bearing 5x16x5", "https://shop.example.org/products/625zz")]


def test_generic_adapter_for_unknown_site():
    r = results("search_generic.html", "https://parts.example.com/search?q=bearing",
                "parts.example.com")
    assert [x["title"] for x in r] == ["LM8UU linear bearing", "LM8LUU flanged long bearing"]
    assert r[0]["snippet"].startswith("8 mm bore")


def test_magento_result_links_only_and_query_ranking():
    base = "https://www.sparkfun.com/catalogsearch/result/?q=micro+servo"
    r = results("search_magento.html", base, "sparkfun.com")
    # nav and "new products" links are .html too; only the marked result links count
    assert [x["title"] for x in r] == ["Stepper Motor - Small", "Servo - Generic (Sub-Micro Size)"]
    assert "$14.95" in r[0]["snippet"]
    ranked = results("search_magento.html", base, "sparkfun.com", query="micro servo")
    assert ranked[0]["title"] == "Servo - Generic (Sub-Micro Size)"


def test_query_ranking_puts_matches_before_a_new_products_strip():
    html = ('<a href="https://www.pololu.com/product/5000">New: USB cable</a>'
            '<a href="/product/2818">FEETECH FS90 Micro Servo</a> 9 g')
    r = search.extract_results(html, "https://www.pololu.com/search?query=micro+servo",
                               site("pololu.com"), query="micro servo")
    assert [x["title"] for x in r] == ["FEETECH FS90 Micro Servo", "New: USB cable"]


def test_limit_and_garbage():
    r = results("search_adafruit.html", "https://www.adafruit.com/search", "adafruit.com",
                limit=1)
    assert len(r) == 1
    assert search.extract_results("<a href='/product/1'", "https://www.adafruit.com/",
                                  site("adafruit.com")) == []
    assert search.extract_results("", "https://www.adafruit.com/", site("adafruit.com")) == []


# ---- bot walls

def test_blocked_detection():
    assert blocked_reason(403).startswith(BLOCKED)
    assert blocked_reason(429).startswith(BLOCKED)
    assert blocked_reason(404) is None
    html = (FX / "captcha.html").read_text()
    assert blocked_reason(200, "Robot Check", html).startswith(BLOCKED)
    assert blocked_reason(200, "Servo", "A servo page") is None


# ---- snapshots

def page(fixture, url):
    text = (FX / fixture).read_text()
    return Page(url, url, "text/html", text, text.encode())


def test_snapshot_of_a_product_page():
    url = "https://www.adafruit.com/product/169"
    rec = build(page("product_servo.html", url), run_id="r1", project_id="p1")
    assert rec["id"].startswith("snap_") and rec["domain"] == "www.adafruit.com"
    assert rec["title"] == "Micro servo - TowerPro SG92R : ID 169 : Adafruit"
    assert "Ignore all previous instructions" in rec["text"]          # kept as data
    assert "hidden" not in rec["text"]                                # scripts never
    assert rec["tables"][0]["rows"][1] == ["Weight", "9 g"]
    assert len(rec["tables"]) == 2
    # og:image first, relative made absolute; data: and http images dropped
    assert [i["src"] for i in rec["_images"]] == [
        "https://cdn-shop.adafruit.com/1200x900/169-00.jpg",
        "https://www.adafruit.com/images/169-01.jpg"]
    assert rec["images"][1] == {"proxy_url": f"/browser/snapshots/{rec['id']}/images/1",
                                "alt": "Servo with horns"}
    part = rec["part"]
    assert part["price"] == 5.95 and part["currency"] == "USD" and part["vendor"] == "TowerPro"
    assert part["dims"]["mass_g"] == 9.0 and part["category"] == "servo"
    assert rec["_item"]["source"] == "url"
    pub = public(rec)
    assert "_images" not in pub and "_item" not in pub and pub["images"] == rec["images"]
    row = summary(rec)
    assert "text" not in row and row["image_count"] == 2 and row["has_part"]


def test_snapshot_of_a_pdf():
    url = "https://www.ti.com/lit/ds/symlink/drv8825.pdf"
    rec = build(Page(url, url, "application/pdf", "", b"%PDF-1.7 ..."), run_id="r",
                project_id=None)
    assert rec["title"] == "drv8825.pdf" and "PDF document" in rec["text"]
    assert rec["part"] is None and rec["images"] == []
