"""Plan 9 B3 fix round 1: background jobs stay under the user's control. Stop cancels a run
family's background studies and reviews and no note wakes it afterwards (C1); a note is never
a new user turn and note turns are capped (C2); design_review and study_run run in the
background too (I1); a result already read sends no note, notes coalesce (I2); the note goes to
the agent that started the job (I3); concurrent studies share one memory budget (I4);
background jobs have an overall timeout and a per-family limit (I5); and an MCP agent's study
outlives its HTTP request."""
import asyncio
import dataclasses
import json
import time

import pytest

from mises import jobs
from mises.orchestrator import lead as lead_mod
from mises.providers.base import Done, ToolCall
from mises.providers.fake import FakeProvider
from mises.sim import studies
from mises.types import ErrorCode, MisesError, SafetyClass, StepKind

from .test_background_studies import (SETTINGS, _fresh, _part, _record, _settle,  # noqa: F401
                                      make_rt, solver, text)

SPEC = {"goal": "a stiff beam",
        "load_cases": [{"id": "lc1", "name": "tip", "fix": "x-min", "load": "x-max",
                        "newtons": 10, "direction": "-z", "note": "", "estimate": False}],
        "requirements": [{"id": "q1", "kind": "min_safety_factor", "target": 2.0,
                          "load_case_id": "lc1", "priority": 5, "source": "user"}]}


@pytest.fixture(autouse=True)
def _jobs():
    jobs.reset_for_tests()
    yield
    jobs.reset_for_tests()


def _sim_call(cid="t1"):
    return ToolCall(id=cid, name="sim_run", input={"type": "static", "part_id": "beam",
                                                   "settings": SETTINGS, "reason": "x"})


def _sid(rt, run_id, name="sim_run", key="sim_id"):
    return next(s.payload["output"]["data"][key] for s in rt.store.steps(run_id)
                if s.payload.get("name") == name and "output" in s.payload)


def _texts(rt, run_id):
    return [s.payload.get("text") or "" for s in rt.store.steps(run_id)
            if s.kind == StepKind.MESSAGE and s.payload.get("role") == "assistant"]


async def _quiet(rt, run_id, n=100):
    for _ in range(n):
        await asyncio.sleep(0.02)
        if not rt.run_busy(run_id) and not rt.lead_queues.get(run_id):
            return


async def _lead(rt, prompt="check the beam for stress"):
    out = await lead_mod.submit(rt, prompt, project_id=rt.active_project_id())
    await asyncio.wait_for(rt.wait(out["run_id"]), 5)
    return out["run_id"]


# ---- C1: Stop

async def test_stop_cancels_the_leads_background_study_and_no_note_wakes_it(make_rt, solver):
    solver["delay"] = 5.0
    prov = FakeProvider([[_sim_call(), Done(stop_reason="tool_use")], text("started"),
                         text("WOKE UP AFTER STOP")])
    rt = make_rt(prov)
    lead = await _lead(rt)
    sid = _sid(rt, lead)
    await asyncio.sleep(0.05)
    assert _record(rt, sid)["status"] == "running"
    rt.cancel(lead)                                  # the user presses Stop
    await _settle(sid, 3)
    assert _record(rt, sid)["status"] == "cancelled"
    assert studies.active_ids() == []                # the solver task is gone
    await _quiet(rt, lead)
    assert not any("WOKE UP" in t for t in _texts(rt, lead))
    assert not [s for s in rt.store.steps(lead) if s.payload.get("system_note")]


async def test_a_note_for_a_stopped_agent_is_parked_until_the_user_writes(make_rt):
    prov = FakeProvider([text("hi"), text("WOKE UP"), text("back")])
    rt = make_rt(prov)
    lead = await _lead(rt, "hello")
    rt.cancel(lead)
    await lead_mod.deliver_note(rt, lead, "study done", {"kind": "sim_finished"})
    await _quiet(rt, lead)
    assert not any("WOKE UP" in t for t in _texts(rt, lead))
    [parked] = [s.payload["system_note_parked"] for s in rt.store.steps(lead)
                if "system_note_parked" in s.payload]
    assert "stopped" in parked["reason"]
    await rt.send(lead, "carry on")                  # the user is back: notes may wake it
    await rt.wait(lead)
    assert lead not in rt.note_hold


async def test_cancelling_a_sub_agent_cancels_its_study_without_a_note(make_rt, solver):
    solver["delay"] = 5.0
    rt = make_rt()
    pid = rt.active_project_id()
    lead = await _lead(rt, "hello")
    child = rt.create_external_run("sim", provider="fake", model="m", parent_id=lead,
                                   allowed_classes={SafetyClass.READ, SafetyClass.LOCAL_MUTATE},
                                   project_id=pid)
    out = await rt.gate.call(child, "sim_run", {"type": "static", "part_id": "beam",
                                                "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(child), agent=True)
    sid = out.data["sim_id"]
    rt.cancel(child)
    await _settle(sid, 3)
    assert _record(rt, sid)["status"] == "cancelled"
    await _quiet(rt, lead)
    assert not [s for s in rt.store.steps(lead) if s.payload.get("system_note")]


# ---- C2: notes are not user turns

async def test_a_note_turn_keeps_the_users_budget_window(make_rt, solver):
    prov = FakeProvider([[_sim_call(), Done(stop_reason="tool_use")], text("started"),
                         text("noted")])
    rt = make_rt(prov)
    lead = await _lead(rt)
    window = rt.store.get_run(lead).turn_started_at
    sid = _sid(rt, lead)
    await _settle(sid)
    for _ in range(200):
        await asyncio.sleep(0.02)
        if any("noted" in t for t in _texts(rt, lead)) and not rt.run_busy(lead):
            break
    assert any("noted" in t for t in _texts(rt, lead))
    assert rt.store.get_run(lead).turn_started_at == window      # no new budget window
    assert rt.note_turns[lead] == 1


async def test_note_turns_are_capped_until_the_user_writes(make_rt):
    prov = FakeProvider([text("hi")] + [text(f"note turn {i}") for i in range(6)])
    rt = make_rt(prov)
    lead = await _lead(rt, "hello")
    for i in range(lead_mod.NOTE_TURNS_MAX + 1):
        await lead_mod.deliver_note(rt, lead, f"study {i} done", {"kind": "sim_finished"})
        await _quiet(rt, lead)
    turns = [t for t in _texts(rt, lead) if t.startswith("note turn")]
    assert len(turns) == lead_mod.NOTE_TURNS_MAX
    parked = [s.payload["system_note_parked"] for s in rt.store.steps(lead)
              if "system_note_parked" in s.payload]
    assert len(parked) == 1 and "in a row" in parked[0]["reason"]
    await rt.send(lead, "thanks")                    # a user message resets the count
    await rt.wait(lead)
    assert rt.note_turns.get(lead) is None


# ---- I1: design_review and study_run in the background

async def test_design_review_runs_in_the_background_and_notes_once(make_rt, solver):
    solver["delay"] = 1.0
    prov = FakeProvider([
        [ToolCall(id="r1", name="design_review", input={"part_id": "beam", "spec": SPEC}),
         Done(stop_reason="tool_use")],
        text("Reviewing in the background."),
        text("Review: see the table."),
    ])
    rt = make_rt(prov)
    t0 = time.monotonic()
    lead = await _lead(rt, "review the beam")
    assert time.monotonic() - t0 < 1.0
    rid = _sid(rt, lead, "design_review", "review_id")
    assert "background" in _texts(rt, lead)[-1]
    assert jobs.get(rid).status == "running"
    running = await rt.gate.call(lead, "design_review_get", {"review_id": rid}, call_id="g1",
                                 workdir=rt.workdir(lead), agent=True)
    assert running.data["status"] == "running"
    await _settle(rid, 10)
    for _ in range(200):
        await asyncio.sleep(0.02)
        if "Review: see the table." in _texts(rt, lead) and not rt.run_busy(lead):
            break
    notes = [s for s in rt.store.steps(lead) if s.kind == StepKind.MESSAGE
             and s.payload.get("system_note")]
    assert len(notes) == 1 and notes[0].payload["system_note"]["job"] == "design_review"
    got = await rt.gate.call(lead, "design_review_get", {"review_id": rid}, call_id="g2",
                             workdir=rt.workdir(lead), agent=True)
    assert got.data["verdict"] in ("pass", "warn", "fail") and got.data["studies"]


async def test_stopping_the_lead_cancels_its_review_and_the_child_studies(make_rt, solver):
    solver["delay"] = 5.0
    prov = FakeProvider([
        [ToolCall(id="r1", name="design_review", input={"part_id": "beam", "spec": SPEC}),
         Done(stop_reason="tool_use")],
        text("Reviewing."), text("WOKE UP"),
    ])
    rt = make_rt(prov)
    lead = await _lead(rt, "review the beam")
    rid = _sid(rt, lead, "design_review", "review_id")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if studies.active_ids():
            break
    [child] = studies.active_ids()
    rt.cancel(lead)
    await _settle(rid, 3)
    assert jobs.get(rid).status == "cancelled"
    await asyncio.sleep(0.05)
    assert _record(rt, child)["status"] == "cancelled" and studies.active_ids() == []
    await _quiet(rt, lead)
    assert "WOKE UP" not in _texts(rt, lead)


async def test_a_waiting_review_blocks_and_job_wait_reads_it(make_rt, solver):
    rt = make_rt()
    from .test_background_studies import _run
    rid = _run(rt)
    out = await rt.gate.call(rid, "design_review", {"part_id": "beam", "spec": SPEC,
                                                    "wait": True},
                             call_id="w1", workdir=rt.workdir(rid), agent=True)
    assert out.data["verdict"] and "review_id" not in out.data
    bg = await rt.gate.call(rid, "design_review", {"part_id": "beam", "spec": SPEC},
                            call_id="w2", workdir=rt.workdir(rid), agent=True)
    got = await rt.gate.call(rid, "job_wait", {"job_id": bg.data["review_id"], "timeout_s": 10},
                             call_id="w3", workdir=rt.workdir(rid), agent=True)
    assert got.data["verdict"] and jobs.get(bg.data["review_id"]).consumed


# ---- I2: a result already read sends no note

async def test_a_study_read_with_sim_wait_sends_no_note(make_rt, solver):
    solver["delay"] = 0.3
    prov = FakeProvider([
        [_sim_call(), Done(stop_reason="tool_use")],
        [ToolCall(id="w1", name="sim_wait", input={"sim_id": "PLACEHOLDER"}),
         Done(stop_reason="tool_use")],
        text("done waiting"), text("SPURIOUS NOTE TURN"),
    ])
    rt = make_rt(prov)
    orig = prov.stream

    async def stream(req):           # the model "knows" the sim id from its tool result
        async for ev in orig(req):
            if isinstance(ev, ToolCall) and ev.name == "sim_wait":
                ev = ToolCall(id=ev.id, name="sim_wait",
                              input={"sim_id": studies.list_sims(rt.home / "parts")[0]
                                     ["sim_id"]})
            yield ev
    prov.stream = stream
    lead = await _lead(rt)
    await _quiet(rt, lead)
    assert "done waiting" in _texts(rt, lead)
    assert "SPURIOUS NOTE TURN" not in _texts(rt, lead)
    assert not [s for s in rt.store.steps(lead) if s.payload.get("system_note")]


# ---- I3: the note goes to the agent that started the job

async def test_a_non_lead_agents_study_notes_that_agent_not_the_lead(make_rt):
    rt = make_rt()
    pid = rt.active_project_id()
    lead = rt.store.create_run("engineer", "fake", "m", "t", project_id=pid).id
    rt.store.update_run(lead, role="lead")
    other = rt.store.create_run("sim", "fake", "m", "t", project_id=pid).id
    rt._runs[lead] = rt._runs[other] = object()
    assert lead_mod.note_owner(rt, other).id == other
    child = rt.store.create_run("sim", "fake", "m", "t", parent_id=lead, project_id=pid).id
    assert lead_mod.note_owner(rt, child).id == lead


# ---- I4: one memory budget for concurrent studies

async def test_concurrent_studies_share_one_memory_budget():
    budget = studies.memory_budget_mb(None)           # the test Mac: 24 GB - 6 GB reserve
    assert budget == 24 * 1024 - 6 * 1024
    assert studies.study_memory_mb(None, "wind") <= budget
    assert studies.study_memory_mb(None, "static") == studies.FEM_MEMORY_MB
    big = int(budget * 0.6)                           # two of these would not fit
    assert await studies._mem_acquire(big, None, 1)
    t0 = time.monotonic()
    second = asyncio.create_task(studies._mem_acquire(big, None, 2))
    await asyncio.sleep(0.1)
    assert not second.done()                          # the second waits for room
    studies._mem_release(big)
    assert await second and time.monotonic() - t0 < 1.5
    assert studies.memory_in_use_mb() == big
    assert not await studies._mem_acquire(big, None, 0.05)   # times out: "busy"
    studies._mem_release(big)
    # FEM studies fit side by side
    assert await studies._mem_acquire(studies.FEM_MEMORY_MB, None, 1)
    assert await studies._mem_acquire(studies.FEM_MEMORY_MB, None, 1)
    studies._mem_release(studies.FEM_MEMORY_MB)
    studies._mem_release(studies.FEM_MEMORY_MB)
    assert studies.memory_in_use_mb() == 0


# ---- I5: an overall timeout and a per-family limit

async def test_a_background_study_has_an_overall_timeout(make_rt, solver, monkeypatch):
    solver["delay"] = 5.0
    from mises.sim import tools as sim_tools
    monkeypatch.setattr(sim_tools, "SIM_RUN_TIMEOUT_S", 0.3)
    rt = make_rt()
    from .test_background_studies import _run
    rid = _run(rt)
    out = await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                              "settings": SETTINGS},
                             call_id="c1", workdir=rt.workdir(rid), agent=True)
    await _settle(out.data["sim_id"], 3)
    rec = _record(rt, out.data["sim_id"])
    assert rec["status"] == "failed" and "limit" in rec["error"]


async def test_a_run_family_has_a_bounded_number_of_background_jobs(make_rt, solver,
                                                                    monkeypatch):
    monkeypatch.setenv("MISES_CONCURRENT_STUDIES", "1")
    solver["delay"] = 5.0
    rt = make_rt()
    from .test_background_studies import _run
    rid = _run(rt)
    cap = 1 + jobs.EXTRA_QUEUED
    for i in range(cap):
        await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                            "settings": SETTINGS},
                           call_id=f"c{i}", workdir=rt.workdir(rid), agent=True)
    with pytest.raises(MisesError) as e:
        await rt.gate.call(rid, "sim_run", {"type": "static", "part_id": "beam",
                                            "settings": SETTINGS},
                           call_id="cx", workdir=rt.workdir(rid), agent=True)
    assert e.value.code == ErrorCode.INVALID_INPUT and "wait for their notes" in e.value.hint
    jobs.cancel_run({rid})


async def test_a_busy_failure_rides_along_with_the_next_note(make_rt):
    prov = FakeProvider([text("hi"), text("noted")])
    rt = make_rt(prov)
    lead = await _lead(rt, "hello")
    await lead_mod.deliver_note(rt, lead, "study A failed: busy", {"id": "a"}, defer=True)
    await _quiet(rt, lead)
    assert "noted" not in _texts(rt, lead)            # no turn of its own
    await lead_mod.deliver_note(rt, lead, "study B finished", {"id": "b"})
    await _quiet(rt, lead)
    [note] = [s for s in rt.store.steps(lead) if s.kind == StepKind.MESSAGE
              and s.payload.get("system_note")]
    assert note.payload["text"] == "study A failed: busy\nstudy B finished"
    assert [i["id"] for i in note.payload["system_note"]["items"]] == ["a", "b"]


# ---- every registered tool lands on its intended stream (catches a future topopt_* / view)

def test_every_tool_maps_to_its_intended_stream(make_rt):
    from mises.events import tool_stream
    rt = make_rt()
    names = {t.name for t in rt.registry.list(classes=set(SafetyClass))}
    sim = {n for n in names if tool_stream(n) == "sim"}
    view = {n for n in names if tool_stream(n) == "view"}
    assert {"sim_run", "sim_static_run", "sim_wait", "sim_result_get", "design_review",
            "design_review_get", "job_wait", "study_run", "study_get"} <= sim
    assert view == {"render_views", "present_view", "critique_model", "part_show",
                    "section_check", "assembly_animate",
                    "concept_sketch", "pitch_options"}       # G-A concept cards -> View (M-3)
    # a tool that sounds like a study or a view but is tagged text must be a conscious choice
    text_ok = {"study_apply", "interview_plan", "mirror_drawing", "drawing_spec_submit",
               "calibration_list",
               "calibration_get", "calibration_fit", "calibration_save", "calibration_delete"}
    suspicious = {n for n in names - sim - view
                  if any(w in n for w in ("sim", "study", "review", "render", "view", "topopt",
                                          "section", "animate", "critique", "calibration",
                                          "drawing"))}
    assert suspicious <= text_ok, suspicious - text_ok
