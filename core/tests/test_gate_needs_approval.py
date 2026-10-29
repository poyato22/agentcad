"""The Gate's per-call approval flag (ToolDef.needs_approval): a non-irreversible tool can ask
for a card on one call without changing its safety class."""
import asyncio

import pytest

from mises.types import ErrorCode, MisesError, SafetyClass as C

from test_gate import Env


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


async def card(env):
    for _ in range(300):
        p = env.store.pending_approvals(env.run.id)
        if p:
            return p[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no card")


async def test_flagged_call_waits_for_approval(env):
    env.add("fetch", safety=C.LOCAL_MUTATE,
            needs_approval=lambda ctx, a: "risky" if a.n > 1 else None)
    assert (await env.call("fetch", {"n": 1})).summary == "ran 1"       # no card
    task = asyncio.create_task(env.call("fetch", {"n": 5}, cid="c2"))
    c = await card(env)
    assert env.calls == 1 and c.deltas["approval_reason"] == "risky"
    env.approvals.decide(c.id, c.payload_hash, True, decided_by="local-user")
    assert (await task).summary == "ran 5" and env.calls == 2


async def test_flagged_call_rejected_never_runs(env):
    env.add("fetch", safety=C.READ, needs_approval=lambda ctx, a: "always")
    task = asyncio.create_task(env.call("fetch", {}))
    c = await card(env)
    env.approvals.decide(c.id, c.payload_hash, False, decided_by="local-user")
    with pytest.raises(MisesError) as e:
        await task
    assert e.value.code == ErrorCode.DENIED and env.calls == 0


def test_class_semantics_unchanged(env):
    t = env.add("plain", safety=C.LOCAL_MUTATE)
    assert t.needs_approval is None and not t.open_world
