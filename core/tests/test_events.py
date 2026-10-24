import asyncio
from mises.events import Event, EventBus


async def test_eventbus_filters_by_run():
    bus = EventBus()
    sub = bus.subscribe("A")
    it = sub.__aiter__()
    task = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0)
    bus.publish(Event(type="x", run_id="B", data={}))
    bus.publish(Event(type="y", run_id="A", data={}))
    ev = await asyncio.wait_for(task, 1)
    assert ev.type == "y" and ev.run_id == "A"
    await it.aclose()


async def test_overflow_drops_oldest():
    bus = EventBus()
    it = bus.subscribe().__aiter__()
    task = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0)
    for i in range(1500):
        bus.publish(Event(type="e", run_id=None, data={"i": i}))
    first = await asyncio.wait_for(task, 1)
    rest = [await asyncio.wait_for(it.__anext__(), 1) for _ in range(999)]
    assert [first.data["i"]] + [e.data["i"] for e in rest] == list(range(500, 1500))
    await it.aclose()


async def test_subscribe_registers_eagerly():
    bus = EventBus()
    sub = bus.subscribe("A")
    bus.publish(Event(type="early", run_id="A", data={}))
    ev = await asyncio.wait_for(sub.__anext__(), 1)
    assert ev.type == "early"
    await sub.aclose()
    assert bus._subs == []


async def test_subscribe_context_manager_unregisters():
    bus = EventBus()
    async with bus.subscribe() as sub:
        bus.publish(Event(type="e", run_id=None, data={}))
        assert (await sub.__anext__()).type == "e"
    assert bus._subs == []
