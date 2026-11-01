"""FWC M2 / M3 (E9): an edited message tells the agent it replaces an earlier one (and `edits` is
bounded); POST /runs/{id}/retry works on a stalled turn (cancels it, then re-sends)."""
import asyncio
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


class Recording:
    """Answers each turn and keeps the requests the model saw."""
    name = "fake"

    def __init__(self, script):
        self.inner, self.requests = FakeProvider(script), []

    async def stream(self, req):
        self.requests.append(req)
        async for ev in self.inner.stream(req):
            yield ev


def _app(tmp_path, monkeypatch, prov):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    from mises.server.app import create_app
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (prov, "fake-model"))
    return rt, create_app(rt, TOKEN)


def _wait(client, rid, status="done", timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/runs/{rid}", headers=AUTH)
        if r.status_code == 200 and r.json()["status"] == status:
            return r.json()
        time.sleep(0.02)
    raise AssertionError(f"run {rid} never reached {status}")


def _last_user_text(req):
    msg = [m for m in req.messages if m.role == "user"][-1]
    return " ".join(getattr(b, "text", "") or "" for b in msg.content)


def test_an_edited_message_tells_the_agent_what_it_replaces(tmp_path, monkeypatch):
    prov = Recording([_turn("one"), _turn("two")])
    rt, app = _app(tmp_path, monkeypatch, prov)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        pid = client.post("/projects", headers=AUTH, json={"name": "Robot"}).json()["id"]
        rid = client.post(f"/projects/{pid}/chat", headers=AUTH,
                          json={"text": "make it red"}).json()["run_id"]
        _wait(client, rid)
        steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
        first = next(s for s in steps if s["payload"].get("role") == "user")
        r = client.post(f"/projects/{pid}/chat", headers=AUTH,
                        json={"text": "make it blue", "edits": str(first["id"])})
        assert r.status_code == 200, r.text
        _wait(client, rid)
        seen = _last_user_text(prov.requests[-1])
        assert "replaces it" in seen and '"make it red"' in seen and "make it blue" in seen
        # the stored step (what the app shows) is still the user's plain text + the reference
        steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
        edited = [s for s in steps if s["payload"].get("edits") == str(first["id"])]
        assert edited and edited[0]["payload"]["text"] == "make it blue"


@pytest.mark.parametrize("bad", ["x" * 65, "a b", "<script>"])
def test_edits_is_bounded(tmp_path, monkeypatch, bad):
    rt, app = _app(tmp_path, monkeypatch, Recording([_turn("one")]))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        pid = client.post("/projects", headers=AUTH, json={"name": "Robot"}).json()["id"]
        r = client.post(f"/projects/{pid}/chat", headers=AUTH, json={"text": "t", "edits": bad})
        assert r.status_code == 422
        rid = client.post(f"/projects/{pid}/chat", headers=AUTH,
                          json={"text": "t"}).json()["run_id"]
        _wait(client, rid)
        r = client.post(f"/runs/{rid}/message", headers=AUTH, json={"text": "u", "edits": bad})
        assert r.status_code == 422


class Hangs:
    """The first turn never answers (a stalled stream); later turns do."""
    name = "fake"

    def __init__(self):
        self.n = 0

    async def stream(self, req):
        self.n += 1
        if self.n == 1:
            await asyncio.sleep(3600)
        yield TextDelta(text="answered on retry")
        yield Done(stop_reason="end_turn")


async def test_retry_cancels_a_stalled_turn_then_resends(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    from mises.orchestrator import lead as lead_mod
    prov = Hangs()
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (prov, "m"))
    pid = rt.active_project_id()
    rid = (await lead_mod.submit(rt, "Build me a drone", project_id=pid))["run_id"]
    for _ in range(100):
        if prov.n:
            break
        await asyncio.sleep(0.01)
    assert rt.run_busy(rid)
    # busy and not stalled: still refused
    with pytest.raises(Exception, match="still working"):
        await rt.retry_last(rid)
    assert rt.scan_stalls(now=time.time() + rt.STALL_S + 5) == [rid]
    out = await rt.retry_last(rid)
    assert out == {"ok": True, "run_id": rid}
    res = await rt.wait(rid)
    assert res.stop_reason == "end_turn" and res.final_text == "answered on retry"
    users = [s.payload.get("text") for s in rt.store.steps(rid)
             if s.payload.get("role") == "user"]
    assert users.count("Build me a drone") == 2
