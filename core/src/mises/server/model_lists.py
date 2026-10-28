"""Live model lists for GET /models: downloaded Ollama models, LM Studio models, and
OpenRouter's free tool-capable models.

Ollama / LM Studio answers are cached 10 s. OpenRouter's public catalog (GET /api/v1/models
works without an API key; each entry has `id`, `pricing.prompt/completion` as strings and
`supported_parameters`) is cached 10 min, and /models never waits on it for more than
OPENROUTER_BLOCK_S: a stale or empty answer is served while a background task refreshes.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

log = logging.getLogger(__name__)

LOCAL_TTL_S = 10.0
LOCAL_TIMEOUT_S = 1.5
OPENROUTER_TTL_S = 600.0
OPENROUTER_BLOCK_S = 2.0
OPENROUTER_FETCH_TIMEOUT_S = 15.0
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
OLLAMA_DOWN_HINT = "Ollama isn't running — start it with `ollama serve`"
LMSTUDIO_DOWN_HINT = "LM Studio isn't running — start its local server (Developer tab)"

# Model ids come from third parties (a local server, OpenRouter's public catalog): anything
# that does not look like a plain id is dropped, lists are capped, and bodies are read only up
# to MAX_BODY_BYTES.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,200}$")
MAX_MODELS = 200
MAX_BODY_BYTES = 5 * 1024 * 1024

# Tests swap in an httpx.MockTransport here; None means the real network.
TRANSPORT: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None


@dataclass
class LocalList:
    models: list[str] = field(default_factory=list)
    hint: str | None = None


def _valid_ids(ids) -> list[str]:
    out = [i for i in ids if isinstance(i, str) and MODEL_ID_RE.fullmatch(i)]
    return out[:MAX_MODELS]


def _price(v) -> float | None:
    try:
        f = float(str(v).strip())
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


class ModelLists:
    def __init__(self, transport=None, monotonic: Callable[[], float] = time.monotonic):
        self._transport = transport if transport is not None else TRANSPORT
        self._now = monotonic
        self._local: dict[tuple[str, str], tuple[float, LocalList]] = {}
        self._or: tuple[float, list[str]] | None = None
        self._or_task: asyncio.Task | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=timeout,
                                 follow_redirects=False)

    async def _get_json(self, url: str, timeout: float):
        async with self._client(timeout) as c:
            async with c.stream("GET", url, headers={"Accept": "application/json"}) as r:
                r.raise_for_status()
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        raise ValueError(f"response from {url} is larger than {MAX_BODY_BYTES} bytes")
            return json.loads(bytes(body))

    async def _cached_local(self, kind: str, base_url: str, fetch) -> LocalList:
        key = (kind, base_url)
        hit = self._local.get(key)
        if hit and self._now() - hit[0] < LOCAL_TTL_S:
            return hit[1]
        value = await fetch()
        self._local[key] = (self._now(), value)
        return value

    async def ollama(self, base_url: str) -> LocalList:
        async def fetch() -> LocalList:
            try:
                data = await self._get_json(f"{_root(base_url)}/api/tags", LOCAL_TIMEOUT_S)
            except (httpx.HTTPError, ValueError):
                return LocalList(hint=OLLAMA_DOWN_HINT)
            items = data.get("models") if isinstance(data, dict) else None
            names = _valid_ids(m.get("name") for m in items or [] if isinstance(m, dict))
            return LocalList([f"ollama/{n}" for n in names])
        return await self._cached_local("ollama", base_url, fetch)

    async def lmstudio(self, base_url: str) -> LocalList:
        async def fetch() -> LocalList:
            try:
                data = await self._get_json(f"{base_url.rstrip('/')}/models", LOCAL_TIMEOUT_S)
            except (httpx.HTTPError, ValueError):
                return LocalList(hint=LMSTUDIO_DOWN_HINT)
            items = data.get("data") if isinstance(data, dict) else None
            ids = _valid_ids(m.get("id") for m in items or [] if isinstance(m, dict))
            return LocalList([f"lmstudio/{i}" for i in ids])
        return await self._cached_local("lmstudio", base_url, fetch)

    # -- OpenRouter ------------------------------------------------------------------------

    @staticmethod
    def _free_tool_models(data) -> list[str]:
        out: list[str] = []
        for m in (data.get("data") if isinstance(data, dict) else None) or []:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                continue
            if not MODEL_ID_RE.fullmatch(m["id"]):
                continue
            pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
            zero = all(_price(pricing.get(k)) == 0.0 for k in ("prompt", "completion"))
            params = m.get("supported_parameters")
            free = zero or (m["id"].endswith(":free") and not pricing)
            if free and isinstance(params, list) and "tools" in params:
                out.append(f"openrouter/{m['id']}")
                if len(out) >= MAX_MODELS:
                    break
        return out

    async def _refresh_openrouter(self) -> list[str]:
        try:
            data = await self._get_json(OPENROUTER_URL, OPENROUTER_FETCH_TIMEOUT_S)
            models = self._free_tool_models(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - any failure backs off; never an unretrieved error
            log.warning("openrouter model list fetch failed", exc_info=True)
            old = self._or[1] if self._or else []
            # retry in 30 s rather than on every /models call
            self._or = (self._now() - OPENROUTER_TTL_S + 30.0, old)
            return old
        self._or = (self._now(), models)
        return models

    async def openrouter(self) -> list[str]:
        fresh = self._or is not None and self._now() - self._or[0] < OPENROUTER_TTL_S
        if fresh:
            return self._or[1]
        if self._or_task is None or self._or_task.done():
            self._or_task = asyncio.ensure_future(self._refresh_openrouter())
        task = self._or_task
        if self._or is not None:  # stale: serve it now, refresh in the background
            return self._or[1]
        done, _ = await asyncio.wait({task}, timeout=OPENROUTER_BLOCK_S)
        if task in done and not task.cancelled() and task.exception() is None:
            return task.result()
        return []

    def forget_local(self) -> None:
        self._local.clear()

    async def aclose(self) -> None:
        """Server shutdown: stop a background OpenRouter refresh."""
        task, self._or_task = self._or_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
