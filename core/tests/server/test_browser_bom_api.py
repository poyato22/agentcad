import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from browser.conftest import FakeWeb
from mises.server.app import create_app

from .conftest import AUTH, TOKEN

MCP = "mcp-token-456"
PRODUCT = "https://www.adafruit.com/product/169"


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP)) as c:
        yield c


@pytest.fixture
def web(rt):
    w = FakeWeb()

    async def resolve(host):
        return ["93.184.216.34"]
    rt.gate.services["browser_http"] = {"transport": w.transport, "resolve": resolve}
    return w


def png(w=40, h=30, fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


def agent_run(rt):
    project = rt.store.ensure_default_project().id
    run = rt.store.create_run("engineer", "fake", "m", "t")
    rt.store.update_run(run.id, project_id=project)
    from mises.types import Caps, SafetyClass as C
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
    return run, project


async def open_page(rt, run, web, n=1):
    web.fixture(PRODUCT, "product_servo.html")
    out = await rt.gate.call(run.id, "web_open", {"url": PRODUCT}, call_id=f"w{n}",
                             workdir=rt.home)
    return out.data["snapshot_id"]


def test_tools_are_registered(rt):
    names = {t.name for t in rt.registry.list()}
    assert {"web_search", "web_open", "part_show", "bom_add", "bom_list"} <= names


async def test_run_browser_and_snapshot_routes(rt, web, client):
    run, _ = agent_run(rt)
    sid = await open_page(rt, run, web)
    rows = client.get(f"/runs/{run.id}/browser", headers=AUTH).json()
    assert [r["id"] for r in rows] == [sid] and "text" not in rows[0]
    assert rows[0]["title"].startswith("Micro servo") and rows[0]["has_part"]
    full = client.get(f"/runs/{run.id}/browser?full=1", headers=AUTH).json()
    assert "Ignore all previous" in full[0]["text"] and "_images" not in full[0]
    snap = client.get(f"/browser/snapshots/{sid}", headers=AUTH).json()
    assert snap["tables"][0]["rows"][1] == ["Weight", "9 g"]
    assert snap["images"][0]["proxy_url"] == f"/browser/snapshots/{sid}/images/0"
    assert "_item" not in snap and "src" not in json.dumps(snap["images"])
    assert client.get("/browser/snapshots/snap_0000000000000000", headers=AUTH).status_code == 404
    assert client.get("/browser/snapshots/../../etc", headers=AUTH).status_code == 404
    assert client.get("/runs/nope/browser", headers=AUTH).status_code == 404
    # the MCP-only token (the agents') opens none of this
    assert client.get(f"/browser/snapshots/{sid}",
                      headers={"Authorization": f"Bearer {MCP}"}).status_code == 403


async def test_image_proxy_reencodes_caps_and_caches(rt, web, client):
    run, _ = agent_run(rt)
    sid = await open_page(rt, run, web)
    web.page("https://cdn-shop.adafruit.com/1200x900/169-00.jpg", png(fmt="JPEG"),
             ctype="image/jpeg")
    r = client.get(f"/browser/snapshots/{sid}/images/0", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["content-security-policy"] == "default-src 'none'"
    assert Image.open(io.BytesIO(r.content)).size == (40, 30)
    n = len(web.requests)
    assert client.get(f"/browser/snapshots/{sid}/images/0", headers=AUTH).content == r.content
    assert len(web.requests) == n                             # cached
    assert client.get(f"/browser/snapshots/{sid}/images/7", headers=AUTH).status_code == 404
    # image 1 is an SVG pretending to be a PNG: refused
    web.page("https://www.adafruit.com/images/169-01.jpg",
             b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>",
             ctype="image/png")
    assert client.get(f"/browser/snapshots/{sid}/images/1", headers=AUTH).status_code == 400


async def test_image_proxy_refuses_svg_type_and_huge_images(rt, web, client):
    run, _ = agent_run(rt)
    sid = await open_page(rt, run, web)
    web.page("https://cdn-shop.adafruit.com/1200x900/169-00.jpg", b"<svg/>",
             ctype="image/svg+xml")
    assert client.get(f"/browser/snapshots/{sid}/images/0", headers=AUTH).status_code == 400
    web.page("https://www.adafruit.com/images/169-01.jpg", png(9000, 9000), ctype="image/png")
    r = client.get(f"/browser/snapshots/{sid}/images/1", headers=AUTH)
    assert r.status_code == 400 and "too large" in r.json()["message"]


async def test_part_cards_routes(rt, web, client):
    run, _ = agent_run(rt)
    sid = await open_page(rt, run, web)
    out = await rt.gate.call(run.id, "part_show", {"snapshot_id": sid}, call_id="s1",
                             workdir=rt.home)
    cards = client.get(f"/runs/{run.id}/part_cards", headers=AUTH).json()
    assert [c["id"] for c in cards] == [out.data["card_id"]]
    one = client.get(f"/browser/cards/{out.data['card_id']}", headers=AUTH).json()
    assert one["price"] == 5.95 and one["catalog_backed"]
    assert client.get("/browser/cards/card_0000000000000000", headers=AUTH).status_code == 404


def test_default_sites_route_and_profile(client):
    d = client.get("/browser/sites/defaults", headers=AUTH).json()
    assert d[0]["name"] == "McMaster-Carr" and all(s["suggested"] for s in d)
    prof = client.get("/profile", headers=AUTH).json()
    assert [s["domain"] for s in prof["suggested_sites"]] == [s["domain"] for s in d]
    mine = [{"name": "My Shop", "domain": "parts.example.com",
             "search_url": "https://parts.example.com/find?q={q}", "categories": ["general"]}]
    r = client.put("/profile", headers=AUTH, json={**prof, "suggested_sites": mine})
    assert r.status_code == 200 and r.json()["suggested_sites"][0]["suggested"] is False
    bad = [{"name": "x", "domain": "parts.example.com", "search_url": "http://x/{q}"}]
    assert client.put("/profile", headers=AUTH,
                      json={**prof, "suggested_sites": bad}).status_code == 422


def test_bom_routes(rt, client, home):
    pid = rt.store.ensure_default_project().id
    d = home / "parts" / "bracket"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": 10_000.0}))
    rt.store.register_part("bracket", pid, label="Bracket")
    sub = rt.events.subscribe()
    got = client.get(f"/projects/{pid}/bom", headers=AUTH).json()
    assert got["project_id"] == pid and got["material"] == "PLA"
    assert got["items"][0]["id"] == "auto_bracket" and got["items"][0]["estimate"]
    r = client.post(f"/projects/{pid}/bom", headers=AUTH, json={
        "name": "MG996R", "qty": 4, "unit_cost": 9.5, "vendor": "Adafruit", "url": PRODUCT})
    assert r.status_code == 200
    row = r.json()
    assert row["source"] == "user" and row["line_total"] == 38.0
    r = client.patch(f"/projects/{pid}/bom/{row['id']}", headers=AUTH,
                     json={"qty": 2, "notes": "spares", "name": None})
    assert r.json()["qty"] == 2 and r.json()["name"] == "MG996R"
    r = client.patch(f"/projects/{pid}/bom/auto_bracket", headers=AUTH, json={"qty": 3})
    assert r.json()["qty"] == 3 and r.json()["estimate"]
    assert client.patch(f"/projects/{pid}/bom/bom_000000000000", headers=AUTH,
                        json={"qty": 1}).status_code == 404
    assert client.post(f"/projects/{pid}/bom", headers=AUTH,
                       json={"name": "x", "qty": 0}).status_code == 400
    assert client.post(f"/projects/{pid}/bom", headers=AUTH,
                       json={"name": "x", "part_id": "nope"}).status_code == 404
    totals = client.get(f"/projects/{pid}/bom", headers=AUTH).json()["totals"]
    assert totals["by_currency"] == {"USD": round(19.0 + 3 * 0.27, 2)}
    csv = client.get(f"/projects/{pid}/bom.csv", headers=AUTH)
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    assert csv.text.splitlines()[0].startswith("kind,name,qty,unit_cost")
    assert client.delete(f"/projects/{pid}/bom/{row['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/projects/{pid}/bom/auto_bracket", headers=AUTH).status_code == 200
    assert client.get(f"/projects/{pid}/bom", headers=AUTH).json()["items"] == []
    changed = [e for e in iter(lambda: None if sub.queue.empty() else sub.queue.get_nowait(),
                               None) if e.type == "bom_changed"]
    assert len(changed) == 5 and changed[0].data["project_id"] == pid
    assert client.get("/projects/nope/bom", headers=AUTH).status_code == 404


def test_bom_rows_go_with_the_build(rt, client):
    pid = rt.store.create_project("Temp").id
    client.post(f"/projects/{pid}/bom", headers=AUTH, json={"name": "x"})
    rt.store.purge_project(pid)
    assert rt.bom.rows(pid) == []


async def test_snapshots_and_cards_go_with_the_run(rt, web):
    run, _ = agent_run(rt)
    sid = await open_page(rt, run, web)
    await rt.gate.call(run.id, "part_show", {"snapshot_id": sid}, call_id="s1", workdir=rt.home)
    rt.store.delete_run(run.id)
    assert rt.snapshots.for_run(run.id) == [] and rt.part_cards.for_run(run.id) == []
