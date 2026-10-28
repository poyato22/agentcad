import asyncio

import pytest

from mises.parallel import Limits, ParallelLimiter


def limits(table, total=3):
    """table: ref -> (group, group_cap); refs not listed are cloud (no group)."""
    def fn(ref):
        g, cap = table.get(ref, (None, 0))
        return Limits(total=total, group=g, group_cap=cap)
    return fn


async def test_grants_up_to_the_total_then_queues_fifo():
    lim = ParallelLimiter(limits({}, total=2))
    assert lim.try_acquire("a", "x") and lim.try_acquire("b", "x")
    assert not lim.try_acquire("c", "x")
    fc, fd = lim.enqueue("c", "x"), lim.enqueue("d", "x")
    assert lim.position("c") == 1 and lim.position("d") == 2
    lim.release("a")
    assert await asyncio.wait_for(fc, 1) is True and not fd.done()
    assert lim.active() == {"b", "c"}
    lim.release("b")
    assert await fd is True


async def test_group_cap_one_local_model_at_a_time_cloud_skips_ahead():
    lim = ParallelLimiter(limits({"ollama/m": ("ollama/m", 1)}, total=3))
    assert lim.try_acquire("l1", "ollama/m")
    assert not lim.try_acquire("l2", "ollama/m")
    f_l2 = lim.enqueue("l2", "ollama/m")
    # a cloud run is not stuck behind the waiting local one
    assert lim.try_acquire("c1", "anthropic/x")
    assert not f_l2.done()
    lim.release("l1")
    assert await f_l2 is True


async def test_withdraw_resolves_false_and_never_holds_a_slot():
    lim = ParallelLimiter(limits({}, total=1))
    assert lim.try_acquire("a", "x")
    f = lim.enqueue("b", "x")
    assert lim.withdraw("b") is True
    assert await f is False
    assert lim.withdraw("b") is False
    lim.release("a")
    assert lim.active() == set()


async def test_release_unknown_is_noop_and_reacquire_is_idempotent():
    lim = ParallelLimiter(limits({}, total=1))
    lim.release("ghost")
    assert lim.try_acquire("a", "x") and lim.try_acquire("a", "x")
    assert lim.active() == {"a"}


async def test_yield_slot_lets_children_run_and_parent_takes_it_back_first():
    lim = ParallelLimiter(limits({}, total=1))
    assert lim.try_acquire("parent", "x")
    async with lim.yielding("parent"):
        assert lim.try_acquire("child", "x")
        other = lim.enqueue("other", "x")   # queued while the parent waits on its child
        lim.release("child")
        await asyncio.sleep(0)
        # the slot went to the queued run; the parent waits for it after its child is done
        assert await other is True
        lim.release("other")
    assert lim.active() == {"parent"}


async def test_yield_slot_waits_when_full_and_parent_has_priority():
    lim = ParallelLimiter(limits({}, total=1))
    assert lim.try_acquire("parent", "x")
    order = []

    async def parent():
        async with lim.yielding("parent"):
            assert lim.try_acquire("child", "x")
            late = lim.enqueue("late", "x")
            late.add_done_callback(lambda f: order.append("late"))
        order.append("parent")

    t = asyncio.create_task(parent())
    await asyncio.sleep(0.01)
    assert not t.done()          # child still holds the only slot
    lim.release("child")
    await asyncio.wait_for(t, 1)
    assert order == ["parent"]   # re-acquiring parents go before the queue
    lim.release("parent")
    await asyncio.sleep(0)
    assert order == ["parent", "late"]


async def test_yield_without_a_slot_is_a_plain_block():
    lim = ParallelLimiter(limits({}, total=1))
    async with lim.yielding("not-holding"):
        pass
    assert lim.active() == set()


async def test_cancel_while_reacquiring_withdraws():
    lim = ParallelLimiter(limits({}, total=1))
    assert lim.try_acquire("parent", "x")

    async def parent():
        async with lim.yielding("parent"):
            assert lim.try_acquire("child", "x")

    t = asyncio.create_task(parent())
    await asyncio.sleep(0.01)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    lim.release("child")
    assert lim.active() == set()


async def test_pump_after_a_limit_change_grants_waiters():
    total = [1]
    lim = ParallelLimiter(lambda ref: Limits(total=total[0]))
    assert lim.try_acquire("a", "x")
    f = lim.enqueue("b", "x")
    total[0] = 2
    lim.pump()
    assert await f is True
