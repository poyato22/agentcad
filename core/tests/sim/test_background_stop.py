"""Plan 9 B3 fix round 2: Stop all reaches an idle agent's background jobs (N1); one job can
be stopped from the app with POST /jobs/{id}/cancel and its note starts no turn (N2); GET /jobs
lists the running ones; a user message to a busy run resets the note counters (M1); job_wait
reads a blocking design study (M4, in tests/engineering)."""
import asyncio

import httpx2
import pytest

from mises import jobs
from mises.orchestrator import lead as lead_mod
from mises.providers.base import Done, ToolCall
from mises.providers.fake import FakeProvider
from mises.server.app import create_app
from mises.types import StepKind

from .test_background_studies import (SETTINGS, _fresh, _record, _settle,  # noqa: F401
                                      make_rt, solver, text)

TOKEN = "stop-token"
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


def _texts(rt, rid):
    return [s.payload.get("text") or "" for s in rt.store.steps(rid)
            if s.kind == StepKind.MESSAGE and s.payload.get("role") == "assistant"]


def _out(rt, rid, name, key):
    return next(s.payload["output"]["data"][key] for s in rt.store.steps(rid)
                if s.payload.get("name") == name and "output" in s.payload)


async def _idle_lead(rt, call):
    out = await lead_mod.submit(rt, "check the beam for stress", project_id=rt.active_project_id())
    lead = out["run_id"]
    await asyncio.wait_for(rt.wait(lead), 5)
    assert not rt.run_busy(lead) and rt.store.get_run(lead).status == "done"
    return lead


def _client(app):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                              base_url="http://127.0.0.1",
                              headers={"Authorization": f"Bearer {TOKEN}"})


async def _wait_text(rt, rid, needle, n=150):
    for _ in range(n):
        await asyncio.sleep(0.02)
        if any(needle in t for t in _texts(rt, rid)):
            return True
    return False


async def test_stop_all_stops_an_idle_leads_background_study(make_rt, solver):
    solver["delay"] = 3.0
    prov = FakeProvider([
        [ToolCall(id="t1", name="sim_run", input={"type": "static", "part_id": "beam",
                                                  "settings": SETTINGS}),
         Done(stop_reason="tool_use")],
        text("started"), text("WOKE UP AFTER STOP ALL")])
    rt = make_rt(prov)
    lead = await _idle_lead(rt, None)
    sid = _out(rt, lead, "sim_run", "sim_id")
    app = create_app(rt, TOKEN)
    async with app.router.lifespan_context(app), _client(app) as http:
        r = await http.post("/runs/cancel_all")
        assert r.status_code == 200 and lead in r.json()["cancelled"]
        await _settle(sid, 3)
        assert _record(rt, sid)["status"] == "cancelled"
        assert not await _wait_text(rt, lead, "WOKE UP", 40)


async def test_stop_all_stops_an_idle_leads_background_review(make_rt, solver):
    solver["delay"] = 3.0
    prov = FakeProvider([
        [ToolCall(id="r1", name="design_review", input={"part_id": "beam", "spec": SPEC}),
         Done(stop_reason="tool_use")],
        text("reviewing"), text("WOKE UP")])
    rt = make_rt(prov)
    lead = await _idle_lead(rt, None)
    rid = _out(rt, lead, "design_review", "review_id")
    app = create_app(rt, TOKEN)
    async with app.router.lifespan_context(app), _client(app) as http:
        assert [j["job_id"] for j in (await http.get("/jobs")).json()] == [rid]
        r = await http.post("/runs/cancel_all")
        assert lead in r.json()["cancelled"]
        await _settle(rid, 3)
        assert jobs.get(rid).status == "cancelled"
        assert (await http.get("/jobs")).json() == []
        assert not await _wait_text(rt, lead, "WOKE UP", 40)


async def test_one_job_cancel_from_the_app_starts_no_note_turn(make_rt, solver):
    solver["delay"] = 3.0
    prov = FakeProvider([
        [ToolCall(id="t1", name="sim_run", input={"type": "static", "part_id": "beam",
                                                  "settings": SETTINGS}),
         Done(stop_reason="tool_use")],
        text("started"), text("NOTE TURN AFTER USER CANCEL")])
    rt = make_rt(prov)
    lead = await _idle_lead(rt, None)
    sid = _out(rt, lead, "sim_run", "sim_id")
    app = create_app(rt, TOKEN)
    async with app.router.lifespan_context(app), _client(app) as http:
        [listed] = (await http.get(f"/jobs?project_id={rt.active_project_id()}")).json()
        assert listed["job_id"] == sid and listed["cancel_url"] == f"/jobs/{sid}/cancel"
        assert (await http.get("/jobs?project_id=other")).json() == []
        r = await http.post(listed["cancel_url"])
        assert r.status_code == 200 and r.json() == {"job_id": sid, "status": "cancelling"}
        await _settle(sid, 3)
        assert _record(rt, sid)["status"] == "cancelled"
        assert not await _wait_text(rt, lead, "NOTE TURN", 40)
        parked = [s.payload["system_note_parked"] for s in rt.store.steps(lead)
                  if "system_note_parked" in s.payload]
        assert parked and "cancelled by the user" in parked[0]["text"]
        again = await http.post(f"/jobs/{sid}/cancel")
        assert again.status_code == 409
        assert (await http.post("/jobs/review_0123456789ab/cancel")).status_code == 404
        assert (await http.post("/jobs/nope/cancel")).status_code == 400
        assert (await http.post("/jobs/x/cancel", headers={"Authorization": "Bearer bad"})
                ).status_code == 401


async def test_a_review_can_be_cancelled_on_its_own(make_rt, solver):
    solver["delay"] = 3.0
    prov = FakeProvider([
        [ToolCall(id="r1", name="design_review", input={"part_id": "beam", "spec": SPEC}),
         Done(stop_reason="tool_use")],
        text("reviewing"), text("NOTE TURN")])
    rt = make_rt(prov)
    lead = await _idle_lead(rt, None)
    rid = _out(rt, lead, "design_review", "review_id")
    app = create_app(rt, TOKEN)
    async with app.router.lifespan_context(app), _client(app) as http:
        assert (await http.post(f"/jobs/{rid}/cancel")).status_code == 200
        await _settle(rid, 3)
        assert jobs.get(rid).status == "cancelled"
        assert not await _wait_text(rt, lead, "NOTE TURN", 40)


async def test_a_message_to_a_busy_run_resets_the_note_counters(make_rt):
    rt = make_rt()
    rid = rt.create_external_run("engineer", provider="fake", model="m")
    rt.note_turns[rid] = 3
    rt.note_hold.add(rid)

    async def fake_send(run_id, text, log=None):
        return None
    rt._send = fake_send
    rt.run_busy = lambda r: True                     # noqa: E731 - the message joins a turn
    await rt.send(rid, "and also this")
    assert rid not in rt.note_turns and rid not in rt.note_hold
    rt.note_turns[rid] = 2
    await rt.send(rid, "note", user_turn=False)      # a note never resets them
    assert rt.note_turns[rid] == 2
