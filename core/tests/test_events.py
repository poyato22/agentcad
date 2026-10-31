import asyncio
from mises.events import ActivityTracker, Event, EventBus, stream_of, tool_label
from mises.store import Store


def test_concept_cards_ride_the_view_stream():
    # M-3 (FW-core): G-A's concept sketch / pitch cards are pictures of the design -> View.
    for kind in ("concept_sketch", "concept_pitch"):
        card = {"run_id": "r", "kind": kind, "title": "t", "detail": "", "ref": {}}
        assert stream_of("agent_announce", card) == "view"
    # the tools themselves stream to View too
    assert stream_of("agent_step", {"name": "concept_sketch"}) == "view"
    assert stream_of("agent_step", {"name": "pitch_options"}) == "view"


def test_explicit_stream_hint_wins():
    # M-4 (FW-core): topopt_accepted / studies_proposed carry stream="sim" in data.
    assert stream_of("topopt_accepted", {"stream": "sim"}) == "sim"
    assert stream_of("studies_proposed", {"stream": "sim"}) == "sim"
    assert Event(type="topopt_accepted", data={"stream": "sim"}).stream == "sim"
    # a bogus hint is ignored (only real streams win)
    assert stream_of("whatever", {"stream": "nonsense"}) == "text"


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    sub.close()
    return out


def test_tool_label_strips_prefix_and_maps():
    assert tool_label("mcp__mises__mirror_measure") == "measuring"
    assert tool_label("sim_static_run") == "running FEM study"
    assert tool_label("some_new_tool") == "some new tool"     # fallback: underscores to spaces


def test_calls_label_counts_parallel_calls():
    from mises.events import calls_label
    assert calls_label(["mirror_measure"]) == "measuring"
    assert calls_label(["mirror_measure", "mirror_part_list", "ui_note"]) == "measuring +2"
    assert calls_label([]) == ""


def test_activity_emits_once_per_transition():
    bus = EventBus()
    sub = bus.subscribe("r")
    clk = [100.0]
    a = ActivityTracker(bus, "r", None, clock=lambda: clk[0])
    a.set("starting")
    a.set("thinking")
    a.set("thinking")                       # a repeat is not re-emitted
    a.set("tool", "measuring")
    a.set("writing")
    a.set("done")
    states = [e.data["state"] for e in _drain(sub) if e.type == "agent_activity"]
    assert states == ["starting", "thinking", "tool", "writing", "done"]


def test_writing_state_is_throttled():
    bus = EventBus()
    sub = bus.subscribe("r")
    clk = [100.0]
    a = ActivityTracker(bus, "r", None, clock=lambda: clk[0])
    a.set("writing")                        # transition: emits
    for _ in range(5):
        a.set("writing")                    # same instant: throttled
    clk[0] += 0.3                           # past the >=4/s window: emits
    a.set("writing")
    clk[0] += 0.05                          # within the window: throttled
    a.set("writing")
    writes = [e for e in _drain(sub)
              if e.type == "agent_activity" and e.data["state"] == "writing"]
    assert len(writes) == 2


def test_thinking_is_throttled_and_stored_truncated(tmp_path):
    store = Store(tmp_path / "db")
    run = store.create_run("designer", "fake", "m", "t")
    bus = EventBus()
    sub = bus.subscribe(run.id)
    clk = [100.0]
    a = ActivityTracker(bus, run.id, store, clock=lambda: clk[0])
    for _ in range(10):
        a.thinking("x" * 1000)              # 10 deltas at one instant: coalesced
    clk[0] += 0.3
    a.thinking("y" * 10)
    a.flush_thinking()
    thinks = [e for e in _drain(sub) if e.type == "agent_thinking"]
    assert 1 <= len(thinks) <= 3            # far fewer events than the 11 deltas
    stored = [s.payload for s in store.steps(run.id) if s.payload.get("agent_thinking")]
    assert stored and len(stored[0]["agent_thinking"]) == a.THINKING_STORE_CHARS
    assert stored[0]["truncated"] is True


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


def test_activity_bumps_last_activity_at_at_most_every_5s(tmp_path):
    store = Store(tmp_path / "db")
    run = store.create_run("designer", "fake", "m", "t")
    clk = [1000.0]
    a = ActivityTracker(EventBus(), run.id, store, clock=lambda: clk[0])
    a.set("starting")
    assert store.get_run(run.id).last_activity_at == 1000.0
    clk[0] = 1003.0
    a.set("tool", "measuring")                 # an event, but within 5 s: no DB write
    assert store.get_run(run.id).last_activity_at == 1000.0
    clk[0] = 1006.0
    a.set("writing")
    assert store.get_run(run.id).last_activity_at == 1006.0
    store.delete_run(run.id)
    clk[0] = 1020.0
    a.set("done")                              # a deleted run: silently skipped
