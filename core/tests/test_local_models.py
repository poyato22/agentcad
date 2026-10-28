"""Ollama status / start / pull. Ollama and its binary are always mocked: nothing is pulled and no
process is started for real."""
import asyncio
import json
import subprocess

import httpx
import pytest

from mises import local_models as lm

BASE = "http://localhost:11434/v1"
VERSION = "http://localhost:11434/api/version"
TAGS = "http://localhost:11434/api/tags"
PULL = "http://localhost:11434/api/pull"

USER_TAGS = {"models": [
    {"name": "huihui_ai/Qwen3.8-abliterated:27b", "size": 17_000_000_000},
    {"name": "huihui_ai/qwen3.5-abliterated:9b", "size": 6_600_000_000},
]}


class Ollama:
    """A fake Ollama HTTP server: routes -> (status, json); /api/pull streams `pull_lines`."""

    def __init__(self, running=True, tags=USER_TAGS, pull_lines=()):
        self.running = running
        self.tags = tags
        self.pull_lines = list(pull_lines)
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if not self.running:
            raise httpx.ConnectError("refused")
        url = str(req.url)
        if url == VERSION:
            return httpx.Response(200, json={"version": "0.12.6"})
        if url == TAGS:
            return httpx.Response(200, json=self.tags)
        if url == PULL:
            body = "".join(json.dumps(x) + "\n" for x in self.pull_lines).encode()
            return httpx.Response(200, content=body,
                                  headers={"content-type": "application/x-ndjson"})
        return httpx.Response(404)


@pytest.fixture
def ollama(monkeypatch):
    o = Ollama()
    monkeypatch.setattr(lm, "TRANSPORT", httpx.MockTransport(o))
    monkeypatch.setattr(lm, "find_ollama", lambda: "/opt/homebrew/bin/ollama")
    return o


async def test_status_installed_running_with_models(ollama):
    s = await lm.ollama_status(BASE)
    assert s == {"installed": True, "running": True, "version": "0.12.6", "models": [
        {"name": "huihui_ai/Qwen3.8-abliterated:27b", "size_gb": 17.0},
        {"name": "huihui_ai/qwen3.5-abliterated:9b", "size_gb": 6.6}]}


async def test_status_installed_not_running(ollama):
    ollama.running = False
    assert await lm.ollama_status(BASE) == {"installed": True, "running": False,
                                            "version": None, "models": []}


async def test_status_not_installed():
    assert await lm.ollama_status(BASE) == {"installed": False, "running": False,
                                            "version": None, "models": []}


async def test_status_drops_bad_names_and_versions(ollama):
    ollama.tags = {"models": [{"name": "bad name", "size": 1}, {"name": "ok:1b", "size": "x"},
                              "junk"]}
    s = await lm.ollama_status(BASE)
    assert s["models"] == [{"name": "ok:1b", "size_gb": None}]


def test_find_ollama_checks_known_paths(monkeypatch):
    monkeypatch.setattr(lm.shutil, "which", lambda name: None)
    monkeypatch.setattr(lm.os.path, "isfile", lambda p: p == "/opt/homebrew/bin/ollama")
    monkeypatch.setattr(lm.os, "access", lambda p, mode: True)
    assert lm._find_ollama() == "/opt/homebrew/bin/ollama"
    monkeypatch.setattr(lm.os.path, "isfile", lambda p: False)
    assert lm._find_ollama() is None


def test_recommended_local_on_24_gb():
    m = {"cpu_cores": 15, "perf_cores": 6, "ram_gb": 24, "chip": "M5 Pro"}
    rec = lm.recommended_local(m)
    assert [r["name"] for r in rec] == ["qwen3-coder:30b", "gpt-oss:20b", "qwen3.6:27b"]
    # fits: size x 1.2 (KV cache, headroom) <= RAM - 5 GB = 19 GB
    assert {r["name"]: r["fits"] for r in rec} == {
        "qwen3-coder:30b": False, "gpt-oss:20b": True, "qwen3.6:27b": False}
    assert all(isinstance(r["note"], str) and r["note"] and "\n" not in r["note"] for r in rec)
    assert {r["name"]: r["size_gb"] for r in rec}["gpt-oss:20b"] == 14.0
    assert "tight" not in {r["name"]: r["note"] for r in rec}["gpt-oss:20b"]


def test_recommended_local_tight_within_ten_percent():
    # 28 GB: 23 GB budget; qwen3-coder 19 x 1.2 = 22.8 fits but is within 10% -> tight
    m = {"cpu_cores": 14, "perf_cores": 10, "ram_gb": 28, "chip": "M5 Pro"}
    rec = {r["name"]: r for r in lm.recommended_local(m)}
    assert rec["qwen3-coder:30b"]["fits"] and "tight" in rec["qwen3-coder:30b"]["note"]
    assert rec["gpt-oss:20b"]["fits"] and "tight" not in rec["gpt-oss:20b"]["note"]


def test_recommended_local_on_16_gb_nothing_fits():
    m = {"cpu_cores": 8, "perf_cores": 4, "ram_gb": 16, "chip": "M2"}
    fits = {r["name"]: r["fits"] for r in lm.recommended_local(m)}
    assert fits == {"qwen3-coder:30b": False, "gpt-oss:20b": False, "qwen3.6:27b": False}


@pytest.mark.parametrize("name,ok", [
    ("qwen3-coder:30b", True), ("gpt-oss:20b", True), ("library/qwen3.6:27b-mlx", True),
    ("Qwen3:8b", False), ("", False), ("-rf", False), ("a b", False), ("a;b", False),
    ("a" * 122, False), ("a" * 121, True), ("../x", False), ("x\n", False), (None, False),
    (3, False)])
def test_pull_name_validation(name, ok):
    assert lm.valid_pull_name(name) is ok


async def test_pull_streams_progress_events(ollama):
    ollama.pull_lines = [{"status": "pulling manifest"},
                         {"status": "pulling abc", "digest": "sha256:abc", "total": 100,
                          "completed": 10},
                         {"status": "pulling abc", "digest": "sha256:abc", "total": 100,
                          "completed": 100},
                         {"status": "success"}]
    events = []
    await lm.pull("gpt-oss:20b", BASE, events.append, min_interval=0)
    body = json.loads(ollama.requests[-1].content)
    assert body == {"model": "gpt-oss:20b", "stream": True}
    assert events[0] == {"model": "gpt-oss:20b", "completed": 0, "total": 0,
                         "status": "pulling manifest"}
    assert events[1] == {"model": "gpt-oss:20b", "completed": 10, "total": 100,
                         "status": "pulling abc"}
    assert events[-1] == {"model": "gpt-oss:20b", "completed": 100, "total": 100,
                          "status": "success"}


async def test_pull_throttles_but_always_sends_the_last_event(ollama):
    ollama.pull_lines = [{"status": "pulling abc", "total": 1000, "completed": i}
                         for i in range(0, 1001, 10)] + [{"status": "success"}]
    events = []
    await lm.pull("gpt-oss:20b", BASE, events.append, min_interval=60)
    assert len(events) <= 3
    assert events[-1]["status"] == "success" and events[-1]["completed"] == 1000


async def test_pull_error_line_ends_with_error_event(ollama):
    ollama.pull_lines = [{"status": "pulling manifest"},
                         {"error": "pull model manifest: file does not exist"}]
    events = []
    await lm.pull("gpt-oss:20b", BASE, events.append, min_interval=0)
    assert events[-1]["status"] == "error"
    assert "does not exist" in events[-1]["error"]


async def test_pull_refuses_bad_names_before_any_request(ollama):
    with pytest.raises(ValueError):
        await lm.pull("Bad Name; rm -rf", BASE, lambda e: None)
    assert ollama.requests == []


async def test_pull_connection_failure_is_an_error_event(ollama):
    ollama.running = False
    events = []
    await lm.pull("gpt-oss:20b", BASE, events.append)
    assert events == [{"model": "gpt-oss:20b", "completed": 0, "total": 0, "status": "error",
                       "error": "Ollama isn't running — start it with `ollama serve`"}]


class FakeProc:
    pid = 4242


async def test_start_launches_ollama_serve_detached(ollama, monkeypatch, tmp_path):
    ollama.running = False
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        ollama.running = True
        return FakeProc()

    monkeypatch.setattr(lm, "_popen", popen)
    r = await lm.start_ollama(BASE, tmp_path, wait_s=1)
    assert r["ok"] is True and r["running"] is True
    assert "ollama serve" in r["note"] and "\n" not in r["note"]
    assert seen["argv"] == ["/opt/homebrew/bin/ollama", "serve"]
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdin"] is subprocess.DEVNULL
    assert seen["kw"]["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert (tmp_path / "logs").is_dir()


async def test_start_when_running_does_nothing(ollama, monkeypatch, tmp_path):
    monkeypatch.setattr(lm, "_popen", lambda *a, **k: pytest.fail("must not start"))
    r = await lm.start_ollama(BASE, tmp_path)
    assert r["ok"] is True and r["running"] is True and "already" in r["note"]


async def test_start_when_not_installed(tmp_path):
    r = await lm.start_ollama(BASE, tmp_path)
    assert r["ok"] is False and r["error"] == "not_installed"
    assert "brew install ollama" in r["hint"]


async def test_puller_runs_one_pull_per_model(ollama):
    gate = asyncio.Event()

    async def slow(model, base, publish, **kw):
        await gate.wait()

    p = lm.Puller(pull_fn=slow)
    assert p.start("gpt-oss:20b", BASE, lambda e: None) == "started"
    assert p.start("gpt-oss:20b", BASE, lambda e: None) == "already"
    assert p.active() == ["gpt-oss:20b"]
    gate.set()
    await asyncio.sleep(0.01)
    assert p.active() == []
    await p.aclose()


async def test_puller_caps_concurrent_pulls_at_two():
    gate = asyncio.Event()

    async def slow(model, base, publish, **kw):
        await gate.wait()

    p = lm.Puller(pull_fn=slow)
    assert p.start("a:1b", BASE, lambda e: None) == "started"
    assert p.start("b:1b", BASE, lambda e: None) == "started"
    assert p.start("c:1b", BASE, lambda e: None) == "busy"
    gate.set()
    await asyncio.sleep(0.01)
    assert p.start("c:1b", BASE, lambda e: None) == "started"
    await p.aclose()


async def test_puller_cancel_stops_the_pull_and_says_so():
    started = asyncio.Event()

    async def slow(model, base, publish, **kw):
        started.set()
        await asyncio.sleep(30)

    events = []
    p = lm.Puller(pull_fn=slow)
    p.start("gpt-oss:20b", BASE, events.append)
    await started.wait()
    assert await p.cancel("gpt-oss:20b") is True
    assert p.active() == []
    assert events[-1] == {"model": "gpt-oss:20b", "completed": 0, "total": 0,
                          "status": "cancelled"}
    assert await p.cancel("gpt-oss:20b") is False


async def test_is_running_is_public(ollama):
    assert await lm.is_running(BASE) is True
    ollama.running = False
    assert await lm.is_running(BASE) is False


async def test_concurrent_starts_launch_one_server(ollama, monkeypatch, tmp_path):
    ollama.running = False
    calls = []

    def popen(argv, **kw):
        calls.append(argv)

        async def later():
            await asyncio.sleep(0.1)
            ollama.running = True
        asyncio.get_running_loop().create_task(later())
    monkeypatch.setattr(lm, "_popen", popen)
    lock = asyncio.Lock()
    r1, r2 = await asyncio.gather(lm.start_ollama(BASE, tmp_path, wait_s=2, lock=lock),
                                  lm.start_ollama(BASE, tmp_path, wait_s=2, lock=lock))
    assert len(calls) == 1
    assert r1["ok"] and r2["ok"]
