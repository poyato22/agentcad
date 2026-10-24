import json
import os
import socket
import stat
import time

import httpx2
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mises.providers.base import TextDelta
import mises.server.app as app_mod
from mises.server.app import create_app, serve, stop
from mises.store import Approval

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def wait_status(client, rid, status="done", timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/runs/{rid}", headers=AUTH)
        assert r.status_code == 200
        if r.json()["status"] == status:
            return r.json()
        time.sleep(0.02)
    raise AssertionError(f"run {rid} never reached {status}")


def test_auth_required(client):
    for method, path in [("get", "/health"), ("get", "/runs"), ("post", "/runs"),
                         ("get", "/approvals"), ("post", "/mcp")]:
        assert getattr(client, method)(path).status_code == 401, path
        r = getattr(client, method)(path, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401, path
    r = client.get("/health", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ok": True}
    for url in ["/events", "/events?token=wrong"]:
        with client.websocket_connect(url) as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
        assert e.value.code == 4401


def test_create_run_and_steps(client, fake):
    r = client.post("/runs", headers=AUTH, json={"agent": "designer", "prompt": "make a bracket",
                                                "edit_scope": ["part_a"]})
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    run = wait_status(client, rid)
    assert run["agent"] == "designer" and run["task"] == "make a bracket"
    assert rid in [x["id"] for x in client.get("/runs", headers=AUTH).json()]
    steps = client.get(f"/runs/{rid}/steps", headers=AUTH).json()
    assert [s["kind"] for s in steps] == ["message", "message"]
    later = client.get(f"/runs/{rid}/steps", headers=AUTH,
                       params={"after_id": steps[0]["id"]}).json()
    assert [s["id"] for s in later] == [steps[1]["id"]]
    assert client.get("/runs/nope", headers=AUTH).status_code == 404
    r = client.post("/runs", headers=AUTH, json={"agent": "nobody", "prompt": "x"})
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    # follow-up message continues the run
    fake._turns.append([TextDelta(text="again")])
    assert client.post(f"/runs/{rid}/message", headers=AUTH, json={"text": "more"}).status_code == 200
    wait_status(client, rid)
    assert len(client.get(f"/runs/{rid}/steps", headers=AUTH).json()) == 4


def test_cancel_endpoint(client):
    rid = client.post("/runs/external", headers=AUTH,
                      json={"agent": "codex", "provider": "openai", "model": "gpt-x"}).json()["run_id"]
    assert client.post(f"/runs/{rid}/cancel", headers=AUTH).status_code == 200
    assert client.get(f"/runs/{rid}", headers=AUTH).json()["status"] == "cancelled"


def test_create_external_run_endpoint(client, rt):
    r = client.post("/runs/external", headers=AUTH,
                    json={"agent": "claude-cli", "provider": "anthropic", "model": "m1",
                          "edit_scope": ["part_a"], "read_scope": ["part_b"]})
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    run = client.get(f"/runs/{rid}", headers=AUTH).json()
    assert (run["agent"], run["provider"], run["model"], run["status"]) == (
        "claude-cli", "anthropic", "m1", "running")
    pol = rt.store.current_policy(rid)
    assert pol.edit_scope == ["part_a"] and pol.read_scope == ["part_b"]


def _pending_approval(rt):
    rid = rt.create_external_run("codex", provider="openai", model="gpt-x")
    from mises.approvals import hash_payload
    payload = {"n": 1}
    return rt.store.insert_approval(Approval(
        id="ap1", run_id=rid, action="danger", payload=payload,
        payload_hash=hash_payload(payload), expires_at=time.time() + 600))


def test_decide_approval_endpoint(client, rt):
    a = _pending_approval(rt)
    pending = client.get("/approvals", headers=AUTH, params={"status": "pending"}).json()
    assert [x["id"] for x in pending] == ["ap1"]
    r = client.post("/approvals/ap1/decide", headers=AUTH,
                    json={"payload_hash": "0" * 64, "approve": True})
    assert r.status_code == 409 and r.json()["error"] == "stale_version"
    assert rt.store.get_approval("ap1").status == "pending"
    r = client.post("/approvals/ap1/decide", headers=AUTH,
                    json={"payload_hash": a.payload_hash, "approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    got = rt.store.get_approval("ap1")
    assert got.decided_by == "local-user"
    assert client.get("/approvals", headers=AUTH).json() == []
    assert client.post("/approvals/zzz/decide", headers=AUTH,
                       json={"payload_hash": "x", "approve": False}).status_code == 404


def test_answer_unknown_question(client):
    r = client.post("/questions/nope/answer", headers=AUTH, json={"text": "yes"})
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_events_ws_streams_run_events(client):
    with client.websocket_connect(f"/events?token={TOKEN}") as ws:
        rid = client.post("/runs", headers=AUTH,
                          json={"agent": "designer", "prompt": "hi"}).json()["run_id"]
        seen = []
        while True:
            ev = ws.receive_json()
            assert ev["run_id"] == rid
            seen.append(ev["type"])
            if ev["type"] == "run_finished":
                assert ev["data"]["status"] == "done"
                break
    assert seen[0] == "run_started"
    # filtered subscription only sees its own run
    other = client.post("/runs/external", headers=AUTH,
                        json={"agent": "codex", "provider": "p", "model": "m"}).json()["run_id"]
    with client.websocket_connect(f"/events?token={TOKEN}&run_id={other}") as ws:
        client.post(f"/runs/{other}/cancel", headers=AUTH)
        ev = ws.receive_json()
        assert ev["run_id"] == other and ev["type"] == "run_finished"


def test_bind_localhost_only(rt, home):
    with pytest.raises(TypeError):
        serve(rt, host="0.0.0.0")
    port, token = serve(rt)
    try:
        info_path = home / "server.json"
        info = json.loads(info_path.read_text())
        assert info == {"port": port, "token": token, "pid": os.getpid()}
        assert stat.S_IMODE(info_path.stat().st_mode) == 0o600
        r = httpx2.get(f"http://127.0.0.1:{port}/health",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert app_mod._servers[port].sock.getsockname() == ("127.0.0.1", port)
        # not reachable on any non-loopback address
        try:
            addrs = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None,
                                                           socket.AF_INET)}
        except socket.gaierror:
            addrs = set()
        for addr in addrs - {"127.0.0.1"}:
            with pytest.raises(OSError):
                socket.create_connection((addr, port), timeout=0.5).close()
    finally:
        stop(port)
    assert not (home / "server.json").exists()


MCP_TOKEN = "mcp-only-token-456"
MCP_AUTH = {"Authorization": f"Bearer {MCP_TOKEN}"}


@pytest.fixture
def split_client(rt):
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP_TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def test_mcp_token_limited_to_mcp_and_health(split_client, rt):
    # C2a: the token handed to agents cannot decide approvals, answer questions or drive runs
    c = split_client
    a = _pending_approval(rt)
    assert c.get("/health", headers=MCP_AUTH).status_code == 200
    for method, path, body in [
            ("post", "/approvals/ap1/decide", {"payload_hash": a.payload_hash, "approve": True}),
            ("post", "/questions/q1/answer", {"text": "yes"}),
            ("get", "/runs", None), ("post", "/runs", {"agent": "designer", "prompt": "x"}),
            ("post", "/runs/external", {"agent": "a", "provider": "p", "model": "m"}),
            ("post", f"/runs/{a.run_id}/cancel", None), ("post", "/runs/cancel_all", None),
            ("get", "/approvals", None), ("get", "/status", None), ("get", "/parts", None)]:
        kw = {"json": body} if body is not None else {}
        r = getattr(c, method)(path, headers=MCP_AUTH, **kw)
        assert r.status_code == 403, (path, r.status_code)
    assert rt.store.get_approval("ap1").status == "pending"
    with c.websocket_connect(f"/events?token={MCP_TOKEN}") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4403
    # the admin token still works everywhere
    r = c.post("/approvals/ap1/decide", headers=AUTH,
               json={"payload_hash": a.payload_hash, "approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert c.get("/runs", headers=AUTH).status_code == 200


def test_mcp_token_reaches_mcp_endpoint(split_client, rt):
    hdrs = {**MCP_AUTH, "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    r = split_client.post("/mcp", headers=hdrs,
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 400 and r.json()["error"] == "invalid_input"  # past auth: no run header


def test_tokens_must_differ(rt):
    with pytest.raises(ValueError):
        create_app(rt, TOKEN, mcp_token=TOKEN)
