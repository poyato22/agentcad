import httpx
import pytest

from mises.onshape.client import OnshapeClient
from mises.store import Store
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C

from .fake_onshape import API

YEAR0 = 0.0


def mv_path(fake):
    return f"{API}/documents/d/{fake.did}/w/{fake.main}/currentmicroversion"


async def test_signature_verified(fake, client, run):
    out = await client.request("GET", mv_path(fake), run_id=run.id)
    assert out == {"microversion": fake.mv()}
    assert fake.requests == [("GET", mv_path(fake))]


async def test_signature_covers_query_and_body(fake, client, run):
    path = f"{API}/partstudios/d/{fake.did}/w/{fake.main}/e/{fake.eid}/features/updates"
    out = await client.request("POST", path, run_id=run.id, query={"a": "1 2", "b": "x"},
                               json={"btType": "BTUpdateFeaturesCall-1748", "features": [],
                                     "sourceMicroversion": fake.mv(),
                                     "rejectMicroversionSkew": True})
    assert out["btType"] == "BTUpdateFeaturesResponse-1333"


async def test_wrong_secret_denied_and_not_counted(fake, store, run):
    bad = OnshapeClient("AK", "WRONG", store=store, user="me", transport=fake.transport)
    with pytest.raises(MisesError) as e:
        await bad.request("GET", mv_path(fake), run_id=run.id)
    assert e.value.code == ErrorCode.DENIED and fake.requests == []
    assert store.ledger_used("me", since=YEAR0) == 0


async def test_redirect_resigned(fake, client, store, run):
    fake.redirects[mv_path(fake)] = f"{API}/redirected/abc"
    out = await client.request("GET", mv_path(fake), run_id=run.id)
    assert out == {"redirected": "abc", "via": "redirect"}
    assert [p for _, p in fake.requests] == [mv_path(fake), f"{API}/redirected/abc"]
    # Both hops were signed (the fake 401s an unsigned or wrongly signed hop) and both count.
    assert store.ledger_used("me", since=YEAR0) == 2
    assert store.get_run(run.id).onshape_calls == 2


async def test_redirect_to_foreign_host_refused(fake, client, store, run):
    fake.redirects[mv_path(fake)] = "https://evil.example.com/steal"
    with pytest.raises(MisesError) as e:
        await client.request("GET", mv_path(fake), run_id=run.id)
    assert e.value.code == ErrorCode.DENIED
    assert len(fake.requests) == 1


async def test_redirect_limit(fake, client, run):
    a, b = f"{API}/loop/a", f"{API}/loop/b"
    fake.redirects.update({mv_path(fake): a, a: b, b: a})
    with pytest.raises(MisesError) as e:
        await client.request("GET", mv_path(fake), run_id=run.id)
    assert e.value.code == ErrorCode.TOOL_FAILED and len(fake.requests) == 4


async def test_ledger_counts_success_only(fake, client, store, run):
    await client.request("GET", mv_path(fake), run_id=run.id)
    fake.fail[mv_path(fake)] = [500]
    with pytest.raises(MisesError) as e:
        await client.request("GET", mv_path(fake), run_id=run.id)
    assert e.value.code == ErrorCode.TRANSIENT
    assert store.ledger_used("me", since=YEAR0) == 1
    assert store.get_run(run.id).onshape_calls == 1
    rows = store._all("SELECT status_code, counted, state FROM ledger ORDER BY id")
    assert [tuple(r) for r in rows] == [(200, 1, "settled"), (500, 0, "settled")]


async def test_exception_releases_reservation(store, run):
    def boom(req):
        raise httpx.ConnectError("no route", request=req)
    c = OnshapeClient("AK", "SK", store=store, user="me", transport=httpx.MockTransport(boom))
    with pytest.raises(MisesError) as e:
        await c.request("GET", "/api/v17/documents/d/x/w/y/currentmicroversion", run_id=run.id)
    assert e.value.code == ErrorCode.TRANSIENT
    rows = store._all("SELECT state FROM ledger")
    assert [r[0] for r in rows] == ["released"]
    assert store.ledger_used("me", since=YEAR0) == 0


@pytest.mark.parametrize("status,body,code", [
    (409, {"message": "Microversion skew"}, ErrorCode.STALE_VERSION),
    (412, {"message": "microversion mismatch"}, ErrorCode.STALE_VERSION),
    (409, {"message": "duplicate name"}, ErrorCode.INVALID_INPUT),
    (429, {}, ErrorCode.TRANSIENT),
    (402, {}, ErrorCode.BUDGET_EXHAUSTED),
    (401, {}, ErrorCode.DENIED),
    (403, {}, ErrorCode.DENIED),
    (400, {}, ErrorCode.INVALID_INPUT),
    (404, {}, ErrorCode.INVALID_INPUT),
    (503, {}, ErrorCode.TRANSIENT),
])
async def test_status_mapping(store, run, status, body, code):
    t = httpx.MockTransport(lambda req: httpx.Response(status, json=body))
    c = OnshapeClient("AK", "SK", store=store, user="me", transport=t)
    with pytest.raises(MisesError) as e:
        await c.request("GET", "/api/v17/x", run_id=run.id)
    assert e.value.code == code and e.value.hint
    assert getattr(e.value, "status", None) == status


async def test_hard_year_cap_blocks_before_send(fake, store):
    r = store.create_run("designer", "fake", "m", "t")
    store.create_policy(r.id, ["*"], ["*"], Caps(onshape_calls_year=1), [C.READ])
    c = OnshapeClient("AK", "SK", store=store, user="me", transport=fake.transport)
    await c.request("GET", mv_path(fake), run_id=r.id)
    with pytest.raises(MisesError) as e:
        await c.request("GET", mv_path(fake), run_id=r.id)
    assert e.value.code == ErrorCode.BUDGET_EXHAUSTED and len(fake.requests) == 1


async def test_idempotency_keys_unique_per_request(fake, client, store, run):
    await client.request("GET", mv_path(fake), run_id=run.id)
    await client.request("GET", mv_path(fake), run_id=run.id)
    keys = [r[0] for r in store._all("SELECT idempotency_key FROM ledger")]
    assert len(set(keys)) == 2 and all(len(k) == 64 for k in keys)
