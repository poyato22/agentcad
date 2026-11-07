import asyncio

import pytest

from mises.approvals import ApprovalService
from mises.events import EventBus
from mises.store import Store
from mises.types import ErrorCode, MisesError, canonical_json, sha256_hex

P = {"a": 1}


def h(p):
    return sha256_hex(canonical_json(p))


@pytest.fixture
def svc():
    return ApprovalService(Store(":memory:"), EventBus())


async def test_request_publishes_event():
    bus = EventBus()
    svc = ApprovalService(Store(":memory:"), bus)
    sub = bus.subscribe()
    a = await svc.request("r1", "merge", P)
    ev = await asyncio.wait_for(sub.__anext__(), 1)
    assert ev.type == "approval_requested" and ev.data["id"] == a.id
    assert a.payload_hash == h(P) and a.status == "pending"


async def test_decide_hash_mismatch_is_stale(svc):
    a = await svc.request("r1", "x", P)
    with pytest.raises(MisesError) as e:
        svc.decide(a.id, "bad", True, "u")
    assert e.value.code == ErrorCode.STALE_VERSION


async def test_first_decision_wins(svc):
    a = await svc.request("r1", "x", P)
    first = svc.decide(a.id, a.payload_hash, True, "u1")
    second = svc.decide(a.id, a.payload_hash, False, "u2")
    assert first.status == "approved" and second.status == "approved"
    assert second.decided_by == "u1"


async def test_consume_requires_approval(svc):
    a = await svc.request("r1", "x", P)
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, a.payload_hash)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL
    svc.decide(a.id, a.payload_hash, False, "u")
    with pytest.raises(MisesError):
        svc.consume(a.id, a.payload_hash)


async def test_consume_twice_fails(svc):
    a = await svc.request("r1", "x", P)
    svc.decide(a.id, a.payload_hash, True, "u")
    assert svc.consume(a.id, a.payload_hash) == P
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, a.payload_hash)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL


async def test_consume_wrong_hash(svc):
    a = await svc.request("r1", "x", P)
    svc.decide(a.id, a.payload_hash, True, "u")
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, h({"a": 2}))
    assert e.value.code == ErrorCode.NEEDS_APPROVAL


async def test_edited_payload_executes_edit(svc):
    a = await svc.request("r1", "x", P)
    svc.decide(a.id, a.payload_hash, True, "u", edited_payload={"a": 5})
    with pytest.raises(MisesError):
        svc.consume(a.id, a.payload_hash)
    assert svc.consume(a.id, h({"a": 5})) == {"a": 5}


async def test_expiry(svc):
    a = await svc.request("r1", "x", P, ttl_s=0.05)
    got = await svc.wait(a.id)
    assert got.status == "expired"
    with pytest.raises(MisesError):
        svc.consume(a.id, a.payload_hash)


async def test_consume_after_ttl_without_wait(svc):
    a = await svc.request("r1", "x", P, ttl_s=0.05)
    svc.decide(a.id, a.payload_hash, True, "u")
    await asyncio.sleep(0.1)
    with pytest.raises(MisesError):
        svc.consume(a.id, a.payload_hash)


async def test_wait_wakes_on_decide(svc):
    a = await svc.request("r1", "x", P)
    t = asyncio.create_task(svc.wait(a.id))
    await asyncio.sleep(0.02)
    svc.decide(a.id, a.payload_hash, True, "u")
    assert (await asyncio.wait_for(t, 1)).status == "approved"


async def test_wait_timeout_returns_pending(svc):
    a = await svc.request("r1", "x", P)
    assert (await svc.wait(a.id, timeout_s=0.02)).status == "pending"


async def test_cancel_run_rejects_pending(svc):
    a = await svc.request("r1", "x", P)
    b = await svc.request("r2", "x", P)
    t = asyncio.create_task(svc.wait(a.id))
    await asyncio.sleep(0.01)
    assert svc.cancel_run("r1") == 1
    got = await asyncio.wait_for(t, 1)
    assert got.status == "rejected" and got.decided_by == "system:cancel"
    assert svc.store.get_approval(b.id).status == "pending"


async def test_db_forged_approval_is_not_accepted(svc):
    # C2c: a row flipped to "approved" outside decide() (e.g. an agent writing mises.db)
    a = await svc.request("r1", "x", P)
    svc.store._db.execute("UPDATE approvals SET status='approved', decided_by='me' WHERE id=?",
                          (a.id,))
    got = await asyncio.wait_for(svc.wait(a.id), 1)
    assert got.status != "approved"
    assert svc.store.get_approval(a.id).status == "rejected"
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, h(P))
    assert e.value.code == ErrorCode.NEEDS_APPROVAL


async def test_forged_approval_consume_refused(svc):
    a = await svc.request("r1", "x", P)
    svc.store._db.execute("UPDATE approvals SET status='approved' WHERE id=?", (a.id,))
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, h(P))
    assert e.value.code == ErrorCode.NEEDS_APPROVAL


async def test_payload_swapped_in_db_after_decide_refused(svc):
    a = await svc.request("r1", "x", P)
    svc.decide(a.id, a.payload_hash, True, "u")
    evil = {"a": 666}
    svc.store._db.execute("UPDATE approvals SET edited_payload=? WHERE id=?",
                          (canonical_json(evil), a.id))
    with pytest.raises(MisesError) as e:
        svc.consume(a.id, h(evil))
    assert e.value.code == ErrorCode.NEEDS_APPROVAL


async def test_payload_swapped_in_db_before_decide_is_stale(svc):
    a = await svc.request("r1", "x", P)
    evil = {"a": 666}
    svc.store._db.execute("UPDATE approvals SET payload=?, payload_hash=? WHERE id=?",
                          (canonical_json(evil), h(evil), a.id))
    with pytest.raises(MisesError) as e:
        svc.decide(a.id, h(evil), True, "u")
    assert e.value.code == ErrorCode.STALE_VERSION


async def test_a_second_decision_is_a_no_op_even_after_execution(svc):
    """view-tab: a replayed decide on a used approval changes nothing (never a second action)."""
    a = await svc.request("r1", "x", P)
    svc.decide(a.id, a.payload_hash, True, "u1")
    svc.consume(a.id, a.payload_hash)
    again = svc.decide(a.id, a.payload_hash, True, "u2")
    assert again.status == "executed" and again.decided_by == "u1"
