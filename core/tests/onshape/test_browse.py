import pytest

from mises.onshape import browse
from mises.types import ErrorCode, MisesError

from .fake_onshape import API, PNG

YEAR0 = 0.0


async def test_documents_paging_and_rows(fake, client, store):
    fake.add_docs(25)
    first = await browse.list_documents(client, offset=0, limit=20)
    second = await browse.list_documents(client, offset=20, limit=20)
    assert len(first) == 20 and len(second) == 5
    assert {d["did"] for d in first}.isdisjoint({d["did"] for d in second})
    row = first[0]
    assert row["name"] == "Bracket 0" and row["owner"] == "Ada Lovelace"
    assert row["default_wid"] == fake.docs[0]["defaultWorkspace"]["id"]
    assert row["modified_at"].startswith("2026-10-")
    # the 300x170 size is preferred, and the URL is our proxy, never Onshape's directly
    assert row["thumbnail_url"].startswith("/onshape/thumbnail?href=https%3A%2F%2F")
    assert "300x170" in row["thumbnail_url"]
    # one ledgered call per page
    assert fake.count(r"/documents$") == 2
    assert store.ledger_used("me", since=YEAR0) == 2


async def test_documents_query_params(fake, client):
    fake.add_docs(3, name="Drone arm")
    fake.add_docs(2, name="Bracket")
    out = await browse.list_documents(client, q="drone", owner="me")
    assert [d["name"] for d in out] == ["Drone arm 0", "Drone arm 1", "Drone arm 2"]
    # the signed query carried the search, the My Documents filter and the sort
    assert "q=drone" in fake.last_query and "filter=0" in fake.last_query
    assert "sortColumn=modifiedAt" in fake.last_query


async def test_documents_limit_capped(client):
    with pytest.raises(MisesError) as e:
        await browse.list_documents(client, limit=50)
    assert e.value.code == ErrorCode.INVALID_INPUT


async def test_document_rows_drop_folders_and_bad_ids(fake, client):
    fake.docs = [{"jsonType": "folder", "id": "f" * 24, "name": "Folder"},
                 {"jsonType": "document-summary", "id": "../../etc", "name": "Bad"},
                 {"jsonType": "document-summary", "id": "1" * 24, "name": "Ok",
                  "thumbnail": {"sizes": [{"size": "300x170",
                                           "href": "https://evil.example.com/api/thumbnails/x"}]}}]
    out = await browse.list_documents(client)
    assert [d["name"] for d in out] == ["Ok"]
    assert out[0]["thumbnail_url"] is None       # foreign host is never proxied


async def test_elements_only_partstudios_and_assemblies(fake, client):
    out = await browse.list_elements(client, fake.did, fake.main)
    assert [(e["name"], e["type"]) for e in out] == [("Part Studio 1", "partstudio"),
                                                     ("Assembly 1", "assembly")]


async def test_elements_reject_bad_ids(client):
    with pytest.raises(MisesError) as e:
        await browse.list_elements(client, "../x", "y")
    assert e.value.code == ErrorCode.INVALID_INPUT


async def test_thumbnail_cached_once(fake, client, store, tmp_path):
    fake.add_docs(1)
    href = fake.docs[0]["thumbnail"]["sizes"][1]["href"]
    data, mime = await browse.thumbnail(client, tmp_path, href)
    assert data == PNG and mime == "image/png"
    again, _ = await browse.thumbnail(client, tmp_path, href)
    assert again == PNG
    assert fake.count(r"/thumbnails/") == 1          # the second read hit the cache
    assert store.ledger_used("me", since=YEAR0) == 1


async def test_thumbnail_size_cap(fake, client, tmp_path):
    fake.add_docs(1)
    fake.thumb = PNG + b"x" * (browse.THUMB_MAX_BYTES + 1)
    href = fake.docs[0]["thumbnail"]["sizes"][1]["href"]
    with pytest.raises(MisesError) as e:
        await browse.thumbnail(client, tmp_path, href)
    assert e.value.code == ErrorCode.INVALID_INPUT and "larger" in e.value.message
    assert list(tmp_path.iterdir()) == []            # nothing cached


async def test_thumbnail_not_an_image(fake, client, tmp_path):
    fake.add_docs(1)
    fake.thumb = b"<html>not an image</html>"
    href = fake.docs[0]["thumbnail"]["sizes"][1]["href"]
    with pytest.raises(MisesError) as e:
        await browse.thumbnail(client, tmp_path, href)
    assert e.value.code == ErrorCode.TOOL_FAILED


@pytest.mark.parametrize("href", [
    "http://cad.onshape.com/api/thumbnails/d/x/w/y/s/300x170",     # not https
    "https://evil.example.com/api/thumbnails/d/x",                 # foreign host
    "https://cad.onshape.com/api/v17/documents",                   # not a thumbnail
    "https://cad.onshape.com.evil.com/api/thumbnails/d/x",         # suffix trick
    "https://user:pw@cad.onshape.com/api/thumbnails/d/x",          # credentials
])
async def test_thumbnail_href_refused(fake, client, tmp_path, href):
    with pytest.raises(MisesError) as e:
        await browse.thumbnail(client, tmp_path, href)
    assert e.value.code == ErrorCode.INVALID_INPUT
    assert fake.requests == []


async def test_on_request_hook_after_every_real_request(fake, client, tmp_path):
    seen = []
    client.on_request = seen.append
    fake.add_docs(1)
    await browse.list_documents(client)
    href = fake.docs[0]["thumbnail"]["sizes"][1]["href"]
    await browse.thumbnail(client, tmp_path, href)
    await browse.thumbnail(client, tmp_path, href)     # cached: no request, no hook
    assert seen == [None, None]


async def test_on_request_hook_failure_does_not_fail_request(fake, client):
    client.on_request = lambda run_id: 1 / 0
    fake.add_docs(1)
    assert len(await browse.list_documents(client)) == 1


async def test_bounding_box_mm(fake, client):
    bb = await browse.bounding_box_mm(client, fake.did, fake.main, fake.eid, "partstudio")
    assert bb == {"min": [0, 0, 0], "max": [100, 50, 10]}
    await browse.bounding_box_mm(client, fake.did, fake.main, fake.eid, "assembly")
    assert fake.count(rf"{API}/assemblies/") == 1


def test_budget_shape(store):
    b = browse.budget(store, "me", 2500)
    assert b["used_year"] == 0 and b["limit_year"] == 2500 and b["warn_at"] == 2000
    assert b["reset_at"] > browse.year_start()
