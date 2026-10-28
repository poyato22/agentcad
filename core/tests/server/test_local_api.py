"""GET /local/status, POST /local/start, POST /local/pull; /status parallel; /models
recommendations. Ollama is a fake (httpx.MockTransport); nothing is pulled or started."""
import time
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import mises.local_models as lm
import mises.server.model_lists as ml
from mises.profile import Profile, save_profile
from mises.server.app import create_app

from .conftest import AUTH, TOKEN
from .test_model_lists import entry

OR = "https://openrouter.ai/api/v1/models"


class FakeOllama:
    def __init__(self, running=True):
        self.running = running
        self.pulls: list[dict] = []
        self.tags = {"models": [
            {"name": "huihui_ai/Qwen3.8-abliterated:27b", "size": 17_000_000_000},
            {"name": "huihui_ai/qwen3.5-abliterated:9b", "size": 6_600_000_000},
            {"name": "gpt-oss:20b", "size": 14_000_000_000}]}

    def __call__(self, req):
        if not self.running:
            raise httpx.ConnectError("refused")
        url = str(req.url)
        if url.endswith("/api/version"):
            return httpx.Response(200, json={"version": "0.12.6"})
        if url.endswith("/api/tags"):
            return httpx.Response(200, json=self.tags)
        if url.endswith("/api/pull"):
            self.pulls.append(json.loads(req.content))
            lines = [{"status": "pulling manifest"},
                     {"status": "pulling x", "total": 10, "completed": 5},
                     {"status": "success"}]
            return httpx.Response(200, content="".join(json.dumps(x) + "\n" for x in lines))
        return httpx.Response(404)


@pytest.fixture
def ollama(monkeypatch):
    o = FakeOllama()
    monkeypatch.setattr(lm, "TRANSPORT", httpx.MockTransport(o))
    monkeypatch.setattr(lm, "find_ollama", lambda: "/opt/homebrew/bin/ollama")
    return o


@pytest.fixture
def client(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as c:
        yield c


@pytest.mark.parametrize("method,path", [("get", "/local/status"), ("post", "/local/start"),
                                         ("post", "/local/pull")])
def test_local_routes_need_the_admin_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_local_status_shape(client, ollama):
    r = client.get("/local/status", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"installed", "running", "version", "models", "recommended"}
    assert body["installed"] and body["running"] and body["version"] == "0.12.6"
    assert {"name": "huihui_ai/qwen3.5-abliterated:9b", "size_gb": 6.6} in body["models"]
    assert [x["name"] for x in body["recommended"]] == ["qwen3-coder:30b", "gpt-oss:20b",
                                                        "qwen3.6:27b"]
    assert all(set(x) == {"name", "size_gb", "fits", "note"} for x in body["recommended"])


def test_local_status_not_installed(client):
    body = client.get("/local/status", headers=AUTH).json()
    assert body["installed"] is False and body["running"] is False and body["models"] == []
    assert len(body["recommended"]) == 3


def test_local_start_not_installed(client):
    r = client.post("/local/start", headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.json()["error"] == "not_installed"


def test_local_start_launches_serve(client, ollama, monkeypatch):
    ollama.running = False
    calls = []

    def popen(argv, **kw):
        calls.append(argv)
        ollama.running = True
    monkeypatch.setattr(lm, "_popen", popen)
    r = client.post("/local/start", headers=AUTH)
    assert r.json()["ok"] is True and calls == [["/opt/homebrew/bin/ollama", "serve"]]
    assert "ollama serve" in r.json()["note"]


def test_local_pull_streams_events(client, ollama, rt, monkeypatch):
    events = []
    real = rt.events.publish

    def spy(ev):
        if ev.type == "local_pull":
            events.append((ev.run_id, ev.data))
        real(ev)
    monkeypatch.setattr(rt.events, "publish", spy)
    r = client.post("/local/pull", headers=AUTH, json={"model": "qwen3-coder:30b"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    # the pull is a background task on the app's loop
    for _ in range(500):
        if events and events[-1][1]["status"] in ("success", "error"):
            break
        time.sleep(0.01)
    assert ollama.pulls == [{"model": "qwen3-coder:30b", "stream": True}]
    assert all(run_id is None for run_id, _ in events)
    assert events[-1][1] == {"model": "qwen3-coder:30b", "completed": 10, "total": 10,
                             "status": "success"}
    assert all(set(d) == {"model", "completed", "total", "status"} for _, d in events)


@pytest.mark.parametrize("body", [{}, {"model": ""}, {"model": "Qwen3:8B"},
                                  {"model": "x; rm -rf /"}, {"model": "../../etc"},
                                  {"model": 5}, ["qwen3:8b"], {"model": "a" * 200}])
def test_local_pull_rejects_bad_names_without_echo(client, ollama, body):
    r = client.post("/local/pull", headers=AUTH, json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_input"
    if isinstance(body, dict) and isinstance(body.get("model"), str) and body["model"]:
        assert body["model"] not in r.text
    assert ollama.pulls == []


def test_local_pull_duplicate_cap_and_cancel(client, ollama, monkeypatch):
    gate = {"open": False}

    async def slow(model, base, publish, **kw):
        import asyncio
        while not gate["open"]:
            await asyncio.sleep(0.01)
    monkeypatch.setattr(client.app.state.ui_router.puller, "_pull", slow)
    ok = client.post("/local/pull", headers=AUTH, json={"model": "gpt-oss:20b"})
    assert ok.json() == {"ok": True}
    dup = client.post("/local/pull", headers=AUTH, json={"model": "gpt-oss:20b"})
    assert dup.json() == {"ok": False, "already": True}
    assert client.post("/local/pull", headers=AUTH, json={"model": "b:1b"}).json() == {"ok": True}
    busy = client.post("/local/pull", headers=AUTH, json={"model": "c:1b"}).json()
    assert busy["ok"] is False and busy["error"] == "too_many_pulls"
    r = client.post("/local/pull/cancel", headers=AUTH, json={"model": "gpt-oss:20b"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = client.post("/local/pull/cancel", headers=AUTH, json={"model": "gpt-oss:20b"})
    assert r.json() == {"ok": False}
    bad = client.post("/local/pull/cancel", headers=AUTH, json={"model": "Bad Name"})
    assert bad.status_code == 400 and "Bad Name" not in bad.text
    gate["open"] = True


def test_local_pull_cancel_needs_the_admin_token(client):
    assert client.post("/local/pull/cancel", json={"model": "x"}).status_code == 401


def test_local_pull_when_ollama_is_not_running(client, ollama):
    ollama.running = False
    r = client.post("/local/pull", headers=AUTH, json={"model": "gpt-oss:20b"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["error"] == "not_running"


def test_status_has_parallel_and_queued(client):
    body = client.get("/status", headers=AUTH).json()
    assert body["parallel"] == {"limit": 3, "auto": True,
                                "reason": "M5 Pro · 24 GB · cloud model → 3"}
    assert body["runs_queued"] == 0


def test_status_parallel_manual(client, rt):
    save_profile(rt.home, Profile(max_parallel_agents=2))
    body = client.get("/status", headers=AUTH).json()
    assert body["parallel"]["limit"] == 2 and body["parallel"]["auto"] is False


def test_models_recommendations(rt, ollama, monkeypatch):
    def route(req):
        if str(req.url).startswith("https://openrouter.ai"):
            return httpx.Response(200, json={"data": [entry("x/y:free")]})
        return ollama(req)
    monkeypatch.setattr(ml, "TRANSPORT", httpx.MockTransport(route))
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    with TestClient(create_app(rt, TOKEN)) as client:
        body = client.get("/models", headers=AUTH).json()
    v = {x["vendor"]: x for x in body["vendors"]}
    ent = {e["model"]: e for x in body["vendors"] for e in x.get("entries", [])}
    assert ent["claude-code/opus"]["recommended"] == "best"
    assert ent["anthropic/claude-opus-5-5"]["recommended"] == "best"
    assert ent["ollama/gpt-oss:20b"]["recommended"] == "local"
    assert ent["ollama/huihui_ai/qwen3.5-abliterated:9b"]["recommended"] is None
    assert ent["openrouter/x/y:free"]["recommended"] == "free"
    assert ent["moonshot/kimi-k3.1-pro"]["recommended"] is None
    assert v["moonshot"]["models"] == ["moonshot/kimi-k3.1-pro",
                                       "moonshot/kimi-k3.1-pro-highspeed", "moonshot/kimi-k3"]
    for e in ent.values():
        assert set(e) == {"model", "recommended", "note"}
        assert e["note"] is None or (isinstance(e["note"], str) and "\n" not in e["note"])
    # every listed model has an entry, in the same order
    for x in body["vendors"]:
        if x["vendor"] not in ("claude-code", "anthropic") and x.get("models"):
            assert [e["model"] for e in x["entries"]] == x["models"]
    assert [e["model"] for e in body["recommended"]] == [
        "claude-code/opus", "anthropic/claude-opus-5-5", "ollama/gpt-oss:20b",
        "openrouter/x/y:free"]
