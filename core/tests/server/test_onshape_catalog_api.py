import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from mises.onshape.adapter import OnshapeAdapter
from mises.onshape.client import OnshapeClient
from mises.server.app import create_app
from onshape.fake_onshape import PNG, FakeOnshape
from catalog.conftest import FakeWeb

from .conftest import AUTH, TOKEN

MCP = "mcp-token-456"
MCP_AUTH = {"Authorization": f"Bearer {MCP}"}


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP)) as c:
        yield c


@pytest.fixture
def onshape(rt):
    fake = FakeOnshape("AK", "SK")
    c = OnshapeClient("AK", "SK", store=rt.store, user="local", transport=fake.transport)
    c.on_request = rt.publish_onshape_budget        # what Runtime._onshape_adapter wires
    rt.gate.services["onshape"] = OnshapeAdapter(c, rt.store)
    return fake


@pytest.fixture
def web(rt):
    w = FakeWeb()

    async def resolve(host):
        return ["93.184.216.34"]
    rt.gate.services["catalog_http"] = {"transport": w.transport, "resolve": resolve}
    return w


def drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def test_not_connected(client):
    r = client.get("/onshape/documents", headers=AUTH)
    assert r.status_code == 403 and "not connected" in r.json()["message"]


def test_mcp_token_refused(client, onshape):
    assert client.get("/onshape/documents", headers=MCP_AUTH).status_code == 403
    assert client.get("/catalog/search", headers=MCP_AUTH).status_code == 403
    assert client.post("/catalog/import", headers=MCP_AUTH,
                       json={"url": "https://x.example.com"}).status_code == 403
    assert onshape.requests == []


def test_documents_paging_and_budget_event_per_request(client, onshape, rt):
    onshape.add_docs(23)
    sub = rt.events.subscribe()
    p1 = client.get("/onshape/documents", headers=AUTH, params={"limit": 20}).json()
    p2 = client.get("/onshape/documents", headers=AUTH, params={"offset": 20}).json()
    assert len(p1) == 20 and len(p2) == 3
    budget = [e for e in drain(sub) if e.type == "onshape_budget"]
    assert len(budget) == 2
    assert budget[-1].data["used_year"] == 2 and budget[-1].data["used_run"] == 2
    assert budget[-1].data["limit_year"] == 2500 and budget[-1].data["warn_at"] == 2000
    b = client.get("/onshape/budget", headers=AUTH).json()
    assert b["used_year"] == 2 and b["reset_at"] > 0


def test_thumbnail_proxy_cached_and_capped(client, onshape, rt):
    onshape.add_docs(1)
    row = client.get("/onshape/documents", headers=AUTH).json()[0]
    r = client.get(row["thumbnail_url"], headers=AUTH)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    client.get(row["thumbnail_url"], headers=AUTH)
    assert onshape.count(r"/thumbnails/") == 1
    assert list((rt.home / "onshape" / "thumbnails").iterdir())
    # a bigger image is refused and not cached
    onshape.add_docs(1, name="Big")
    onshape.thumb = PNG + b"x" * (600 * 1024)
    big = client.get("/onshape/documents", headers=AUTH, params={"q": "big"}).json()[0]
    assert client.get(big["thumbnail_url"], headers=AUTH).status_code == 400


def test_thumbnail_foreign_href_refused(client, onshape):
    r = client.get("/onshape/thumbnail", headers=AUTH,
                   params={"href": "https://evil.example.com/api/thumbnails/x"})
    assert r.status_code == 400 and onshape.requests == []


def test_elements(client, onshape):
    r = client.get(f"/onshape/documents/{onshape.did}/elements", headers=AUTH,
                   params={"wid": onshape.main})
    assert [e["type"] for e in r.json()] == ["partstudio", "assembly"]


@pytest.mark.slow
def test_import_mirrors_bbox_envelope_and_never_writes_onshape(client, onshape, rt):
    sub = rt.events.subscribe()
    body = {"did": onshape.did, "wid": onshape.main, "eid": onshape.eid, "project_id": "prj"}
    r = client.post("/onshape/import", headers=AUTH, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["part_id"] == "part_studio" and not out["existing"]
    assert out["metrics"]["bbox"]["size"] == [pytest.approx(100), pytest.approx(50),
                                              pytest.approx(10)]
    meta = json.loads((rt.home / "parts" / "part_studio" / "part.json").read_text())
    assert meta["onshape"]["eid"] == onshape.eid and meta["project_id"] == "prj"
    assert all(m == "GET" for m, _ in onshape.requests) and len(onshape.requests) == 2
    types = [e.type for e in drain(sub)]
    assert types.count("onshape_budget") == 2 and "part_changed" in types
    again = client.post("/onshape/import", headers=AUTH, json=body).json()
    assert again["existing"] and len(onshape.requests) == 3     # only the element list again


def test_catalog_search_get_categories(client):
    hits = client.get("/catalog/search", headers=AUTH, params={"q": "608"}).json()
    assert hits[0]["id"] == "bearing_608_deep_groove"
    assert client.get("/catalog/items/nema17_stepper", headers=AUTH).json()["mount"][
        "pattern"] == "31x31"
    assert "motor" in client.get("/catalog/categories", headers=AUTH).json()
    assert client.get("/catalog/items/nope", headers=AUTH).status_code == 404


def test_catalog_import_review_delete(client, web):
    url = "https://shop.example.com/p/falcon"
    web.fixture(url, "injection.html")
    r = client.post("/catalog/import", headers=AUTH, json={"url": url})
    assert r.status_code == 200, r.text
    it = r.json()
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in it["raw_text"]      # shown as text
    r = client.patch(f"/catalog/items/{it['id']}", headers=AUTH,
                     json={"name": "Clean name", "category": "other",
                           "dims": {"length_mm": 10, "width_mm": 10, "height_mm": 2}})
    assert r.status_code == 200 and r.json()["name"] == "Clean name"
    assert client.get(f"/catalog/items/{it['id']}", headers=AUTH).json()["name"] == "Clean name"
    assert client.patch(f"/catalog/items/{it['id']}", headers=AUTH,
                        json={"raw_text": "x"}).status_code == 400
    assert client.delete(f"/catalog/items/{it['id']}", headers=AUTH).status_code == 200
    assert client.delete("/catalog/items/m3_hex_nut", headers=AUTH).status_code == 400


def test_catalog_import_http_refused(client, web):
    r = client.post("/catalog/import", headers=AUTH, json={"url": "http://shop.example.com/p"})
    assert r.status_code == 400 and web.requests == []


@pytest.mark.slow
def test_catalog_insert_route(client, rt):
    sub = rt.events.subscribe()
    r = client.post("/catalog/items/prop_5in_class/insert", headers=AUTH,
                    json={"project_id": "prj"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["part_id"] == "prop_5in_class"
    assert out["metrics"]["bbox"]["size"][0] == pytest.approx(127)
    assert any(e.type == "part_changed" and e.data["by"] == "user" for e in drain(sub))
    # the parts list the app reads shows it
    parts = client.get("/parts", headers=AUTH).json()
    assert [p["part_id"] for p in parts] == ["prop_5in_class"]


def test_thumbnail_url_is_relative_and_encoded(client, onshape):
    onshape.add_docs(1)
    row = client.get("/onshape/documents", headers=AUTH).json()[0]
    href = unquote(row["thumbnail_url"].split("href=", 1)[1])
    assert href.startswith("https://cad.onshape.com/api/thumbnails/")
