"""OpenRouter free models: 20 requests/min, shared across runs."""
import httpx
import pytest

from mises.providers import registry
from mises.providers.base import RateLimiter

from .test_kimi import req
from .test_openai import chunk, sse


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(registry, "keychain_get", lambda s, a: None)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


async def test_limiter_allows_max_calls_then_waits_for_the_window():
    c = Clock()
    lim = RateLimiter(20, 60.0, clock=c, sleep=c.sleep)
    for _ in range(20):
        await lim.acquire()
    assert c.slept == []
    await lim.acquire()
    assert len(c.slept) == 1 and 59.9 < c.slept[0] < 60.1
    assert c.t >= 60


def test_free_openrouter_models_share_the_20_per_minute_limiter():
    p1, _ = registry.resolve("openrouter/a/b:free")
    p2, _ = registry.resolve("openrouter/c/d:free")
    paid, _ = registry.resolve("openrouter/anthropic/claude-x")
    assert p1._rate is registry.OPENROUTER_FREE_LIMITER is p2._rate
    assert (registry.OPENROUTER_FREE_LIMITER.max_calls,
            registry.OPENROUTER_FREE_LIMITER.period) == (20, 60.0)
    assert paid._rate is None


async def test_provider_waits_on_the_limiter_before_each_request(monkeypatch):
    c = Clock()
    lim = RateLimiter(1, 60.0, clock=c, sleep=c.sleep)
    monkeypatch.setattr(registry, "OPENROUTER_FREE_LIMITER", lim)
    p, m = registry.resolve("openrouter/a/b:free")
    p._client = p._client.with_options(http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, content=sse(chunk({"content": "k"}, finish="stop")),
            headers={"content-type": "text/event-stream"}))))
    [e async for e in p.stream(req(model=m))]
    assert c.slept == []
    [e async for e in p.stream(req(model=m))]
    assert len(c.slept) == 1
