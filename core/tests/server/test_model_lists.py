import asyncio
import json

import httpx
from fastapi.testclient import TestClient

import mises.server.model_lists as ml
from mises.server.app import create_app

from .conftest import AUTH, TOKEN


class Net:
    """Records requests; `routes` maps URL -> (status, json) or an exception to raise."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []
        self.delay = 0.0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if self.delay:
            await asyncio.sleep(self.delay)
        r = self.routes.get(str(request.url))
        if r is None or isinstance(r, Exception):
            raise r or httpx.ConnectError("refused")
        return httpx.Response(r[0], json=r[1])


OLLAMA = "http://localhost:11434/api/tags"
LMS = "http://localhost:1234/v1/models"
OR = "https://openrouter.ai/api/v1/models"


def lists(net, **kw):
    return ml.ModelLists(transport=httpx.MockTransport(net), **kw)


def entry(id, prompt="0", completion="0", params=("tools", "max_tokens")):
    return {"id": id, "pricing": {"prompt": prompt, "completion": completion},
            "supported_parameters": list(params)}


async def test_ollama_lists_downloaded_models():
    net = Net({OLLAMA: (200, {"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:latest"}]})})
    r = await lists(net).ollama("http://localhost:11434/v1")
    assert r.models == ["ollama/qwen3:8b", "ollama/llama3.2:latest"]
    assert r.hint is None


async def test_ollama_not_running_gives_hint_and_no_models():
    r = await lists(Net({})).ollama("http://localhost:11434/v1")
    assert r.models == []
    assert r.hint == "Ollama isn't running — start it with `ollama serve`"


async def test_ollama_cached_10s_then_refetched():
    now = [100.0]
    net = Net({OLLAMA: (200, {"models": [{"name": "a"}]})})
    ls = lists(net, monotonic=lambda: now[0])
    await ls.ollama("http://localhost:11434/v1")
    now[0] += 9
    await ls.ollama("http://localhost:11434/v1")
    assert len(net.calls) == 1
    now[0] += 2
    await ls.ollama("http://localhost:11434/v1")
    assert len(net.calls) == 2


async def test_ollama_uses_profile_base_url():
    net = Net({"http://127.0.0.1:9999/api/tags": (200, {"models": [{"name": "x"}]})})
    r = await lists(net).ollama("http://127.0.0.1:9999/v1/")
    assert r.models == ["ollama/x"]


async def test_lmstudio_models():
    net = Net({LMS: (200, {"data": [{"id": "qwen2.5-coder"}, {"id": "gemma"}]})})
    r = await lists(net).lmstudio("http://localhost:1234/v1")
    assert r.models == ["lmstudio/qwen2.5-coder", "lmstudio/gemma"]


async def test_openrouter_free_tool_models_only():
    data = [entry("a/free:free"), entry("b/zero"), entry("c/paid", "0.001", "0.002"),
            entry("d/free-no-tools:free", params=("max_tokens",)),
            entry("e/half", "0", "0.5"), {"id": "f/malformed"}]
    net = Net({OR: (200, {"data": data})})
    r = await lists(net).openrouter()
    assert r == ["openrouter/a/free:free", "openrouter/b/zero"]


async def test_openrouter_sends_no_credentials():
    net = Net({OR: (200, {"data": []})})
    seen = []
    ls = ml.ModelLists(transport=httpx.MockTransport(
        lambda req: seen.append(req.headers) or httpx.Response(200, json={"data": []})))
    await ls.openrouter()
    assert "authorization" not in seen[0]
    assert net.calls == []


async def test_openrouter_slow_fetch_does_not_block_then_serves_it(monkeypatch):
    monkeypatch.setattr(ml, "OPENROUTER_BLOCK_S", 0.05)
    net = Net({OR: (200, {"data": [entry("a/b:free")]})})
    net.delay = 0.3
    ls = lists(net)
    assert await ls.openrouter() == []  # nothing cached yet: answer fast
    await asyncio.sleep(0.5)  # background fetch finishes
    assert await ls.openrouter() == ["openrouter/a/b:free"]


async def test_openrouter_stale_cache_served_while_refreshing():
    now = [0.0]
    net = Net({OR: (200, {"data": [entry("a/b:free")]})})
    ls = lists(net, monotonic=lambda: now[0])
    assert await ls.openrouter() == ["openrouter/a/b:free"]
    now[0] += 601
    net.routes[OR] = (200, {"data": [entry("c/d:free")]})
    net.delay = 0.2
    assert await ls.openrouter() == ["openrouter/a/b:free"]  # stale, instantly
    await asyncio.sleep(0.4)
    assert await ls.openrouter() == ["openrouter/c/d:free"]
    assert len(net.calls) == 2


async def test_openrouter_failure_keeps_stale_cache():
    now = [0.0]
    net = Net({OR: (200, {"data": [entry("a/b:free")]})})
    ls = lists(net, monotonic=lambda: now[0])
    await ls.openrouter()
    now[0] += 601
    net.routes[OR] = (500, {})
    assert await ls.openrouter() == ["openrouter/a/b:free"]
    await asyncio.sleep(0.05)
    assert await ls.openrouter() == ["openrouter/a/b:free"]


def test_models_route_includes_lists(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    net = Net({OLLAMA: (200, {"models": [{"name": "qwen3:8b"}]}),
               LMS: (200, {"data": [{"id": "m1"}]}),
               OR: (200, {"data": [entry("x/y:free")]})})
    monkeypatch.setattr(ml, "TRANSPORT", httpx.MockTransport(net))
    with TestClient(create_app(rt, TOKEN)) as c:
        body = c.get("/models", headers=AUTH).json()
    v = {x["vendor"]: x for x in body["vendors"]}
    assert v["ollama"]["models"] == ["ollama/qwen3:8b"] and v["ollama"].get("hint") is None
    assert v["lmstudio"]["models"] == ["lmstudio/m1"]
    assert v["openrouter"]["models"] == ["openrouter/x/y:free"] and v["openrouter"]["free"] is True
    json.dumps(body)


def test_models_route_ollama_down_has_hint(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    monkeypatch.setattr(ml, "TRANSPORT", httpx.MockTransport(Net({})))
    with TestClient(create_app(rt, TOKEN)) as c:
        v = {x["vendor"]: x for x in c.get("/models", headers=AUTH).json()["vendors"]}
    assert v["ollama"]["models"] == []
    assert "ollama serve" in v["ollama"]["hint"]
    assert v["openrouter"]["models"] == []


def test_models_route_uses_profile_ollama_endpoint(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    (rt.home).mkdir(parents=True, exist_ok=True)
    (rt.home / "profile.json").write_text(
        json.dumps({"local_endpoints": {"ollama": "http://127.0.0.1:9999/v1"}}))
    net = Net({"http://127.0.0.1:9999/api/tags": (200, {"models": [{"name": "z"}]})})
    monkeypatch.setattr(ml, "TRANSPORT", httpx.MockTransport(net))
    with TestClient(create_app(rt, TOKEN)) as c:
        v = {x["vendor"]: x for x in c.get("/models", headers=AUTH).json()["vendors"]}
    assert v["ollama"]["models"] == ["ollama/z"]


def test_ollama_models_not_a_dict_is_tolerated():
    async def run():
        return await lists(Net({OLLAMA: (200, ["weird"])})).ollama("http://localhost:11434/v1")
    r = asyncio.run(run())
    assert r.models == [] and r.hint is None


# ---- hardening: untrusted ids, list and body caps, refresh errors, float pricing

async def test_ollama_and_lmstudio_drop_ids_that_fail_validation():
    bad = ["", "has space", "semi;colon", "x" * 201, "new\nline", "$(rm)"]
    net = Net({OLLAMA: (200, {"models": [{"name": n} for n in [*bad, "qwen3:8b", "a/b@c+d"]]}),
               LMS: (200, {"data": [{"id": n} for n in [*bad, "m.1_x-2"]]})})
    ls = lists(net)
    assert (await ls.ollama("http://localhost:11434/v1")).models == ["ollama/qwen3:8b",
                                                                     "ollama/a/b@c+d"]
    assert (await ls.lmstudio("http://localhost:1234/v1")).models == ["lmstudio/m.1_x-2"]


async def test_openrouter_drops_ids_that_fail_validation():
    net = Net({OR: (200, {"data": [entry("ok/m:free"), entry("bad id:free"),
                                   entry("evil<script>:free")]})})
    assert await lists(net).openrouter() == ["openrouter/ok/m:free"]


async def test_lists_capped_at_200():
    net = Net({OLLAMA: (200, {"models": [{"name": f"m{i}"} for i in range(500)]}),
               OR: (200, {"data": [entry(f"a/m{i}:free") for i in range(500)]})})
    ls = lists(net)
    assert len((await ls.ollama("http://localhost:11434/v1")).models) == 200
    assert len(await ls.openrouter()) == 200


async def test_oversized_body_is_refused(monkeypatch):
    monkeypatch.setattr(ml, "MAX_BODY_BYTES", 1000)
    big = {"models": [{"name": f"m{i}", "pad": "x" * 100} for i in range(50)]}
    r = await lists(Net({OLLAMA: (200, big)})).ollama("http://localhost:11434/v1")
    assert r.models == []


async def test_openrouter_unexpected_error_still_backs_off(monkeypatch):
    now = [0.0]
    calls = []

    def boom(_):
        calls.append(1)
        raise RuntimeError("unexpected")

    monkeypatch.setattr(ml.ModelLists, "_free_tool_models", staticmethod(boom))
    ls = lists(Net({OR: (200, {"data": []})}), monotonic=lambda: now[0])
    assert await ls.openrouter() == []
    await asyncio.sleep(0.05)
    assert await ls.openrouter() == []      # backoff: no second fetch within 30 s
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    now[0] += 31
    await ls.openrouter()
    await asyncio.sleep(0.05)
    assert len(calls) == 2


async def test_openrouter_refresh_task_cancelled_on_close(monkeypatch):
    monkeypatch.setattr(ml, "OPENROUTER_BLOCK_S", 0.01)
    net = Net({OR: (200, {"data": []})})
    net.delay = 5
    ls = lists(net)
    await ls.openrouter()
    task = ls._or_task
    assert task is not None and not task.done()
    await ls.aclose()
    assert task.cancelled()


def test_models_route_closes_refresh_on_shutdown(rt, monkeypatch):
    monkeypatch.setattr("mises.platform.keychain_get", lambda *a: None)
    closed = []
    orig = ml.ModelLists.aclose

    async def spy(self):
        closed.append(1)
        await orig(self)

    monkeypatch.setattr(ml.ModelLists, "aclose", spy)
    with TestClient(create_app(rt, TOKEN)) as c:
        c.get("/models", headers=AUTH)
    assert closed == [1]


async def test_openrouter_pricing_parsed_as_float():
    data = [entry("a/zero-float", "0.0000", "0e0"), entry("b/tiny", "0.0000001", "0"),
            entry("c/junk", "free", "0"), entry("d/num", 0, 0)]
    r = await lists(Net({OR: (200, {"data": data})})).openrouter()
    assert r == ["openrouter/a/zero-float", "openrouter/d/num"]

