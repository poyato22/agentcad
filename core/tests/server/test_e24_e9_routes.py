"""E24 one chat per build, and E9 message edit / queue cancel / retry / stall."""
import time

import pytest
from starlette.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime

from .conftest import AUTH, TOKEN


def _turn(t):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    prov = FakeProvider([_turn("one"), _turn("two"), _turn("three")])
    from mises.server.app import create_app
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (prov, "fake-model"))
    rt._app = create_app(rt, TOKEN)
    return rt


@pytest.fixture
def client(rt):
    with TestClient(rt._app, base_url="http://127.0.0.1") as c:
        yield c


def wait_status(client, rid, status="done", timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/runs/{rid}", headers=AUTH)
        if r.status_code == 200 and r.json()["status"] == status:
            return r.json()
        time.sleep(0.02)
    raise AssertionError(f"run {rid} never reached {status}")


def _pid(client):
    return client.post("/projects", headers=AUTH, json={"name": "Robot"}).json()["id"]


def test_chat_creates_the_lead_and_reuses_it(client):
    pid = _pid(client)
    r = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "Build me a drone"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_lead"] is True and body["role"] == "lead" and body["routed"]
    rid = body["run_id"]
    wait_status(client, rid)
    r2 = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "add a camera mount"})
    assert r2.json()["run_id"] == rid and r2.json()["new_lead"] is False


def test_message_edit_records_the_edited_id(client):
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "hi"}).json()["run_id"]
    wait_status(client, rid)
    r = client.post(f"/runs/{rid}/message", headers=AUTH,
                    json={"text": "actually, make it bigger", "edits": "step_42"})
    assert r.status_code == 200
    wait_status(client, rid)
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    edited = [s for s in steps if s["payload"].get("edits") == "step_42"]
    assert edited and edited[0]["payload"].get("role") == "user"


def test_cancel_queued_message(client, rt):
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "hi"}).json()["run_id"]
    wait_status(client, rid)
    rt.lead_queues[rid] = [{"message_id": "msg_abc", "text": "t", "shown": "t", "log": None}]
    r = client.delete(f"/runs/{rid}/queue/msg_abc", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not rt.lead_queues.get(rid)
    assert client.delete(f"/runs/{rid}/queue/msg_abc", headers=AUTH).status_code == 404


def test_retry_resends_last_user_turn(client):
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH,
                      json={"text": "Build a drone"}).json()["run_id"]
    wait_status(client, rid)
    r = client.post(f"/runs/{rid}/retry", headers=AUTH)
    assert r.status_code == 200, r.text
    wait_status(client, rid)
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    user_msgs = [s for s in steps
                 if s["payload"].get("role") == "user" and s["payload"].get("text") == "Build a drone"]
    assert len(user_msgs) == 2       # original + the retried turn

    r2 = client.post("/runs/does-not-exist/retry", headers=AUTH)
    assert r2.status_code == 404


def test_continue_carries_the_run_on_without_the_users_words(client):
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH,
                      json={"text": "Build a drone"}).json()["run_id"]
    wait_status(client, rid)
    r = client.post(f"/runs/{rid}/continue", headers=AUTH, json={"cause": "the tools hung"})
    assert r.status_code == 200, r.text
    wait_status(client, rid)
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    users = [s["payload"] for s in steps if s["payload"].get("role") == "user"]
    assert [u["text"] for u in users if "system_note" not in u] == ["Build a drone"]
    note = [u for u in users if "system_note" in u]
    assert note and note[0]["system_note"]["kind"] == "continuation"
    assert [s["payload"]["continuation"]["text"] for s in steps
            if "continuation" in s["payload"]] == ["AgentCAD continued after: the tools hung"]
    assert client.post("/runs/nope/continue", headers=AUTH).status_code == 404


def test_scan_stalls_publishes_once(rt, monkeypatch):
    pid = rt.store.create_project("Robot").id
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    monkeypatch.setattr(rt, "run_busy", lambda rid: True)
    rt._runs[run.id] = object()      # make it "live" so scan considers it
    old = time.time() - 200
    rt.store.update_run(run.id, last_activity_at=old)
    got = []
    sub = rt.events.subscribe(run.id)
    newly = rt.scan_stalls()
    assert run.id in newly
    # P11 F9: the stuck turn's next tool result carries a strategy-switch note (once)
    assert len(rt.tool_notes[run.id]) == 1 and "no progress" in rt.tool_notes[run.id][0]
    assert rt.scan_stalls() == []    # not reported twice
    assert len(rt.tool_notes[run.id]) == 1
    # a fresh activity timestamp clears the flag
    rt.store.update_run(run.id, last_activity_at=time.time())
    assert rt.scan_stalls() == []
    rt.store.update_run(run.id, last_activity_at=time.time() - 200)
    assert run.id in rt.scan_stalls()
    del got, sub


def test_scan_stalls_counts_idle_from_the_new_turn_not_the_last_one(rt, monkeypatch):
    """A follow-up message hours after the last turn is not 'no progress for hours': the
    stall clock starts when the new turn begins (DRONE2: a 28572 s note on the first step)."""
    pid = rt.store.create_project("Drone").id
    run = rt.store.create_run("engineer", "fake", "m", "go", project_id=pid)
    monkeypatch.setattr(rt, "run_busy", lambda rid: True)
    rt._runs[run.id] = object()
    rt.store.update_run(run.id, last_activity_at=time.time() - 8 * 3600)
    rt.note_turn_began(run.id)
    assert rt.scan_stalls() == []
    assert not rt.tool_notes.get(run.id)
    # the turn itself going quiet is still caught
    assert run.id in rt.scan_stalls(now=time.time() + 200)


def test_post_queued_cancel_alias_drops_with_reason_cancelled(client, rt):
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "hi"}).json()["run_id"]
    wait_status(client, rid)
    rt.lead_queues[rid] = [{"message_id": "msg_abc", "text": "t", "shown": "t", "log": None}]
    sub = rt.events.subscribe(rid)
    r = client.post(f"/runs/{rid}/queued/msg_abc/cancel", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ok": True}
    dropped = []
    while not sub.queue.empty():
        e = sub.queue.get_nowait()
        if e.type == "message_dropped":
            dropped.append(e.data)
    assert dropped and dropped[0]["reason"] == "cancelled"
    assert client.post(f"/runs/{rid}/queued/msg_abc/cancel", headers=AUTH).status_code == 404


def test_retry_skips_system_notes_and_agent_messages(client, rt, monkeypatch):
    # M3: a later system note / agent message in the thread is never resent as the user's words
    from mises.types import StepKind
    pid = _pid(client)
    rid = client.post(f"/projects/{pid}/chat", headers=AUTH,
                      json={"text": "Build a drone"}).json()["run_id"]
    wait_status(client, rid)
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "Study finished",
                                                 "system_note": {"kind": "sim_finished"}})
    rt.store.append_step(rid, StepKind.MESSAGE, {"role": "user", "text": "From the studies agent",
                                                 "from_agent": "studies"})
    sent = []

    async def fake_send(run_id, text, **kw):
        sent.append((text, kw.get("log")))
    monkeypatch.setattr(rt, "send", fake_send)
    assert client.post(f"/runs/{rid}/retry", headers=AUTH).status_code == 200
    assert sent == [("Build a drone", None)]


def test_chat_answers_like_post_runs(client):
    pid = _pid(client)
    a = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "x"}).json()
    b = client.post("/runs", headers=AUTH, json={"prompt": "y", "project_id": pid}).json()
    assert set(a) == set(b)
