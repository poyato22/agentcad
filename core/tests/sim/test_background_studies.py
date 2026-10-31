"""Plan 9 B3 (D4 core half): a study an agent starts runs in the background. sim_run /
sim_static_run return {sim_id, status: running} at once, sim_wait / sim_result_get read it, the
core publishes sim_finished and notes the owning lead, and every event / step carries its
stream (text, sim or view)."""
import asyncio
import dataclasses
import json
import time

import pytest

import mises.runtime as runtime_mod
from mises import heavy, jobs
from mises.events import Event, stream_of
from mises.orchestrator import lead as lead_mod
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.sim import studies
from mises.sim import tools as sim_tools
from mises.store import Step
from mises.types import SafetyClass, StepKind

SETTINGS = {"load_cases": [{"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
                            "loads": [{"kind": "force", "face": "x-max", "newtons": 10,
                                       "direction": "-z"}]}]}
RESULTS = {"max_von_mises_mpa": 12.0, "max_displacement_mm": 0.2, "safety_factor": 3.1,
           "error_band": 0.04}


def text(t):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


@pytest.fixture(autouse=True)
def _fresh():
    heavy.reset_for_tests()
    studies._sems.clear()
    yield
    heavy.reset_for_tests()
    studies._sems.clear()


@pytest.fixture
def solver(monkeypatch):
    """A fake mesher + solver: each study takes `delay` seconds (in a heavy solve slot)."""
    state = {"delay": 0.3, "started": 0}

    async def dispatch(type_id, job_root, metrics, material, ccx, settings, env, progress,
                       sim_id, part_id, *a, **kw):
        state["started"] += 1
        progress("solving", phase="solve", pct=50)
        async with heavy.heavy_slot("CalculiX"):
            await asyncio.sleep(state["delay"])
        return {"results": dict(RESULTS), "warnings": [],
                "mesh": {"fine": {"size_mm": 1.0}, "coarse": {"size_mm": 2.0}}}
    monkeypatch.setattr(studies, "_dispatch", dispatch)
    monkeypatch.setattr(sim_tools, "find_ccx", lambda: "/usr/bin/true")
    monkeypatch.setattr(studies, "ccx_version", lambda ccx: "2.21")
    return state


def _part(home, pid="beam"):
    d = home / "parts" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": 1000.0}))
    (d / "model.step").write_text("ISO-10303-21; fake")
    return d


@pytest.fixture
def make_rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)

    def make(prov=None):
        prov = prov or FakeProvider([text("hi")])
        rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (prov, "m"))
        _part(rt.home)
        return rt
    return make


def _run(rt):
    return rt.create_external_run("engineer", provider="fake", model="m",
                                  allowed_classes={SafetyClass.READ, SafetyClass.LOCAL_MUTATE})


async def _settle(job_id, timeout=10):
    """Wait for a background job to end WITHOUT reading it (sim_wait would mark it read and
    suppress its note)."""
    j = jobs.get(job_id)
    if j is not None and j.task is not None:
        await asyncio.wait({j.task}, timeout=timeout)
    for _ in range(5):
        await asyncio.sleep(0)


def _record(rt, sim_id):
    d = studies.find_sim(rt.home / "parts", sim_id)
    return json.loads((d / "result.json").read_text()) if d else None


# ---- background studies

async def test_an_agents_sim_run_returns_at_once_while_the_solver_runs(make_rt, solver):
    solver["delay"] = 2.0
    rt = make_rt()
    rid = _run(rt)
    sub = rt.events.subscribe()
    t0 = time.monotonic()
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(rid), agent=True)
    assert time.monotonic() - t0 < 1.0
    assert out.ok and out.data["status"] == "running" and out.data["stream"] == "sim"
    sim_id = out.data["sim_id"]
    assert _record(rt, sim_id)["status"] == "running"
    # sim_result_get while it runs, then sim_wait to the end
    got = await rt.gate.call(rid, "sim_result_get", {"sim_id": sim_id}, call_id="c2",
                             workdir=rt.workdir(rid), agent=True)
    assert got.ok and got.data["status"] == "running" and "still running" in got.summary
    done = await rt.gate.call(rid, "sim_wait", {"sim_id": sim_id, "timeout_s": 10},
                              call_id="c3", workdir=rt.workdir(rid), agent=True)
    assert done.ok and done.data["status"] == "done", done.summary
    assert done.data["results"]["safety_factor"] == pytest.approx(3.1)
    assert _record(rt, sim_id)["status"] == "done"
    fin = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "sim_finished":
            fin.append(ev)
    sub.close()
    assert len(fin) == 1 and fin[0].data["sim_id"] == sim_id and fin[0].stream == "sim"
    assert fin[0].data["status"] == "done" and fin[0].data["run_id"] == rid
    assert fin[0].data["build_id"] == rt.store.get_run(rid).project_id
    assert "GUIDE ONLY" in fin[0].data["summary"]


async def test_sim_static_run_runs_in_the_background_too_and_wait_true_blocks(make_rt, solver):
    rt = make_rt()
    rid = _run(rt)
    args = {"part_id": "beam", "fix": "x-min",
            "load": {"face": "x-max", "newtons": 10, "direction": "-z"}}
    out = await rt.gate.call(rid, "sim_static_run", args, call_id="s1",
                             workdir=rt.workdir(rid), agent=True)
    assert out.data["status"] == "running"
    out2 = await rt.gate.call(rid, "sim_static_run", {**args, "wait": True}, call_id="s2",
                              workdir=rt.workdir(rid), agent=True)
    assert out2.ok and out2.data["status"] == "done"
    await studies.wait_for(out.data["sim_id"], 5)


async def test_the_apps_own_calls_still_wait(make_rt, solver):
    """Not an agent call (the Studies page, design_review's children): the default blocks."""
    rt = make_rt()
    rid = _run(rt)
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="u1", workdir=rt.workdir(rid))
    assert out.ok and out.data["status"] == "done"


async def test_sim_wait_times_out_as_running(make_rt, solver):
    solver["delay"] = 1.0
    rt = make_rt()
    rid = _run(rt)
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(rid), agent=True)
    w = await rt.gate.call(rid, "sim_wait", {"sim_id": out.data["sim_id"], "timeout_s": 0.05},
                           call_id="c2", workdir=rt.workdir(rid), agent=True)
    assert w.ok and w.data["status"] == "running"
    assert sim_tools.WaitArgs.model_json_schema()["properties"]["timeout_s"]["maximum"] == 600
    await studies.wait_for(out.data["sim_id"], 5)


async def test_a_background_study_can_be_cancelled(make_rt, solver):
    solver["delay"] = 5.0
    rt = make_rt()
    rid = _run(rt)
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(rid), agent=True)
    sid = out.data["sim_id"]
    await asyncio.sleep(0.05)
    c = await rt.gate.call(rid, "sim_cancel", {"sim_id": sid}, call_id="c2",
                           workdir=rt.workdir(rid), agent=True)
    assert c.ok
    await studies.wait_for(sid, 2)
    await asyncio.sleep(0.05)
    assert _record(rt, sid)["status"] == "cancelled"


async def test_two_studies_solve_at_once_on_a_wide_pool(make_rt, solver, monkeypatch):
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "3")
    assert studies.study_limit() == 2
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "2")
    assert studies.study_limit() == 1
    monkeypatch.setenv("MISES_CONCURRENT_STUDIES", "3")
    assert studies.study_limit() == 3
    monkeypatch.delenv("MISES_CONCURRENT_STUDIES")
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "3")
    solver["delay"] = 0.4
    rt = make_rt()
    rid = _run(rt)
    t0 = time.monotonic()
    outs = [await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                                "settings": SETTINGS},
                               call_id=f"c{i}", workdir=rt.workdir(rid), agent=True)
            for i in range(2)]
    for o in outs:
        await studies.wait_for(o.data["sim_id"], 5)
    assert time.monotonic() - t0 < 0.75           # in parallel, not 2 x 0.4 s


async def test_an_approval_card_still_comes_before_the_study_starts(make_rt, solver):
    rt = make_rt()
    rid = _run(rt)
    t = rt.registry.get("sim_run")
    rt.registry._tools["sim_run"] = dataclasses.replace(
        t, needs_approval=lambda ctx, args: "a big study")
    task = asyncio.create_task(rt.gate.call(
        rid, "sim_run", {"type": "static", "part_id": "beam", "settings": SETTINGS},
        call_id="a1", workdir=rt.workdir(rid), agent=True))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if rt.store.pending_approvals(rid):
            break
    [card] = rt.store.pending_approvals(rid)
    assert solver["started"] == 0 and not list((rt.home / "parts" / "beam").glob("sims/*"))
    rt.approvals.decide(card.id, card.payload_hash, True, decided_by="local-user")
    out = await asyncio.wait_for(task, 2)
    assert out.data["status"] == "running"
    await studies.wait_for(out.data["sim_id"], 5)


async def test_a_render_after_sim_start_finishes_first(make_rt, solver, monkeypatch):
    """Every solve slot is busy with the study; the render takes the reserved slot."""
    monkeypatch.setenv("MISES_HEAVY_WORKERS", "2")
    solver["delay"] = 1.5
    rt = make_rt()
    rid = _run(rt)
    order = []

    async def render():
        async with heavy.heavy_slot("the render"):
            await asyncio.sleep(0.05)
        order.append("render")
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(rid), agent=True)
    await asyncio.sleep(0.05)
    assert heavy.in_use().get("CalculiX") == 1
    await asyncio.wait_for(render(), 1.0)
    await studies.wait_for(out.data["sim_id"], 5)
    order.append("study")
    assert order == ["render", "study"]


# ---- the lead hears about it

async def test_the_lead_replies_before_a_5s_study_finishes_and_gets_a_note(make_rt, solver):
    solver["delay"] = 5.0
    prov = FakeProvider([
        [ToolCall(id="t1", name="sim_run", input={"type": "static", "part_id": "beam",
                                                  "settings": SETTINGS, "reason": "stiffness"}),
         Done(stop_reason="tool_use")],
        text("Started the stress study; it runs in the background."),
        text("The study is done: safety factor 3.1 (guide only)."),
    ])
    rt = make_rt(prov)
    pid = rt.active_project_id()
    t0 = time.monotonic()
    out = await lead_mod.submit(rt, "check the beam for stress", project_id=pid)
    lead = out["run_id"]
    res = await asyncio.wait_for(rt.wait(lead), 3)
    assert time.monotonic() - t0 < 3
    assert "background" in res.final_text
    texts = [s for s in rt.store.steps(lead) if s.kind == StepKind.MESSAGE
             and s.payload.get("role") == "assistant"]
    assert texts and texts[-1].stream == "text"
    sid = next(s.payload["output"]["data"]["sim_id"] for s in rt.store.steps(lead)
               if s.payload.get("name") == "sim_run" and "output" in s.payload)
    assert _record(rt, sid)["status"] == "running"     # the reply came first
    # the study ends: a system note starts the lead's next turn
    await _settle(sid)
    for _ in range(200):
        await asyncio.sleep(0.02)
        notes = [s for s in rt.store.steps(lead) if s.kind == StepKind.MESSAGE
                 and s.payload.get("system_note")]
        if notes and not rt.run_busy(lead):
            break
    [note] = notes
    assert note.payload["system_note"] == {"kind": "sim_finished", "job": "sim", "id": sid,
                                           "sim_id": sid, "status": "done"}
    assert f"of beam ({sid}) finished" in note.payload["text"]
    assert 'Result summary (data): "GUIDE ONLY' in note.payload["text"]
    await rt.wait(lead)
    assert "safety factor 3.1" in rt.store.steps(lead)[-1].payload.get("text", "") or any(
        "safety factor 3.1" in (s.payload.get("text") or "") for s in rt.store.steps(lead))


async def test_a_note_for_a_busy_lead_is_queued_and_never_429s(make_rt):
    rt = make_rt()
    pid = rt.active_project_id()
    lead = rt.store.create_run("engineer", "fake", "m", "t", project_id=pid).id
    rt.store.update_run(lead, role="lead")
    rt._runs[lead] = object()        # reachable from this process
    busy = {lead}
    rt.run_busy = lambda r: r in busy           # noqa: E731 - its turn never ends here
    rt.lead_queues[lead] = [{"message_id": f"msg_u{i}", "text": "u", "log": None,
                             "shown": "u"} for i in range(lead_mod.QUEUE_MAX - 1)]
    rt.lead_drainers[lead] = asyncio.get_running_loop().create_future()   # not done: no drain
    first = await lead_mod.deliver_note(rt, lead, "note 1", {"kind": "sim_finished"})
    assert first is not None and len(rt.lead_queues[lead]) == lead_mod.QUEUE_MAX
    second = await lead_mod.deliver_note(rt, lead, "note 2", {"kind": "sim_finished",
                                                              "id": "sim_2"})
    q = rt.lead_queues[lead]
    # notes waiting for the same turn are one message, so the cap is never hit by notes
    assert second["coalesced"] and len(q) == lead_mod.QUEUE_MAX
    [waiting] = [m for m in q if m.get("note")]
    assert waiting["text"] == "note 1\nnote 2"
    assert [i.get("id") for i in waiting["log"]["system_note"]["items"]] == [None, "sim_2"]
    # a queue full of user messages: the note is dropped, nothing raises
    rt.lead_queues[lead] = [{"message_id": f"msg_v{i}", "text": "u", "log": None,
                             "shown": "u"} for i in range(lead_mod.QUEUE_MAX)]
    assert await lead_mod.deliver_note(rt, lead, "note 3", {}) is None
    assert len(rt.lead_queues[lead]) == lead_mod.QUEUE_MAX
    with pytest.raises(lead_mod.QueueFull):      # user messages still get the 429
        lead_mod._enqueue(rt, lead, "more", None, lead_mod.Routed(agent="engineer", reason="x",
                                                                confidence=1.0))
    rt.lead_drainers.pop(lead).cancel()


async def test_a_sub_agents_study_notes_its_lead(make_rt):
    rt = make_rt()
    pid = rt.active_project_id()
    lead = rt.store.create_run("engineer", "fake", "m", "t", project_id=pid).id
    rt.store.update_run(lead, role="lead")
    child = rt.store.create_run("sim", "fake", "m", "t", parent_id=lead, project_id=pid).id
    rt.store.update_run(child, role="sub")
    rt._runs[lead] = object()
    assert lead_mod.owning_lead(rt, child).id == lead
    ui = rt.store.create_run("ui", "ui", "studies-page", "t").id
    assert lead_mod.owning_lead(rt, ui) is None


# ---- stream tags

@pytest.mark.parametrize("ev,stream", [
    (("sim_progress", {}), "sim"), (("sim_finished", {}), "sim"),
    (("study_progress", {}), "sim"),
    (("tool_call", {"name": "sim_run"}), "sim"), (("tool_result", {"name": "sim_wait"}), "sim"),
    (("tool_call", {"name": "mcp__mises__design_review"}), "sim"),
    (("tool_call", {"name": "study_run"}), "sim"),
    (("tool_call", {"name": "render_views"}), "view"),
    (("tool_result", {"name": "present_view"}), "view"),
    (("tool_call", {"name": "critique_model"}), "view"),
    (("part_shown", {}), "view"), (("assembly_animate", {}), "view"),
    (("tool_call", {"name": "mirror_part_create"}), "text"),
    (("text_delta", {}), "text"), (("agent_activity", {}), "text"),
    (("message_queued", {}), "text"),
    (("agent_announce", {"kind": "view_presented"}), "view"),
    (("agent_announce", {"kind": "critique_round"}), "view"),
    (("agent_announce", {"kind": "part_shown"}), "view"),
    (("agent_announce", {"kind": "part_created"}), "text"),
])
def test_every_event_carries_its_stream(ev, stream):
    e = Event(type=ev[0], run_id="r", data=ev[1])
    assert e.stream == stream and stream_of(ev[0], ev[1]) == stream
    assert json.loads(e.model_dump_json())["stream"] == stream


def test_every_step_carries_its_stream():
    def step(kind, payload):
        return Step(id=1, run_id="r", ts=0, kind=kind, payload=payload, evidence_hash="x")
    assert step(StepKind.SIM_RUN, {"name": "sim_result_get"}).stream == "sim"
    assert step(StepKind.TOOL_CALL, {"name": "sim_run"}).stream == "sim"
    assert step(StepKind.ERROR, {"name": "render_views"}).stream == "view"
    assert step(StepKind.TOOL_RESULT, {"name": "present_view"}).stream == "view"
    assert step(StepKind.MESSAGE, {"role": "assistant", "text": "hi"}).stream == "text"
    assert step(StepKind.DECISION, {"agent_thinking": "x"}).stream == "text"
    assert step(StepKind.DECISION, {"agent_announce": {"kind": "view_presented"}}).stream == "view"
    assert step(StepKind.DECISION, {"agent_announce": {"kind": "agent_spawned"}}).stream == "text"
    assert step(StepKind.MESSAGE, {"role": "user", "system_note": {"kind": "sim_finished"}}
                ).stream == "text"
    assert step(StepKind.TOOL_CALL, {"name": "sim_run"}).model_dump()["stream"] == "sim"
