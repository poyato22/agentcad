"""Local models through Ollama: status, recommended downloads, `ollama serve`, and pulls.

Ollama's HTTP API (docs.ollama.com/api): GET /api/version -> {version}; GET /api/tags ->
{models:[{name, size, ...}]}; POST /api/pull {"model", "stream": true} -> NDJSON lines with
`status` and, while downloading a layer, `digest`, `total`, `completed`; a failure is a line
with `error`. Everything Ollama sends back is untrusted text: names are validated, strings are
clipped, bodies are bounded. A model name sent to /api/pull is validated first (PULL_NAME_RE).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .hardware import Machine, usable_memory_gb
from .server.model_lists import MODEL_ID_RE, OLLAMA_DOWN_HINT

log = logging.getLogger(__name__)

# Tests swap in an httpx.MockTransport here; None means the real network.
TRANSPORT: httpx.AsyncBaseTransport | None = None
STATUS_TIMEOUT_S = 1.5
PULL_CONNECT_TIMEOUT_S = 5.0
PULL_MIN_INTERVAL_S = 0.5
MAX_LINE_BYTES = 64 * 1024
MAX_MODELS = 200
START_WAIT_S = 5.0

OLLAMA_PATHS = ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
                "/Applications/Ollama.app/Contents/Resources/ollama")
INSTALL_HINT = "Install Ollama: `brew install ollama` (or the app from ollama.com), then retry"
PULL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,120}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]{1,40}$")

# From research/models/model-choice-2026-10.md section 3 (Ollama library sizes).
RECOMMENDED: tuple[tuple[str, float, str], ...] = (
    ("qwen3-coder:30b", 19.0,
     "Best speed/quality on 24 GB: fast MoE with tools; keep context near 32K"),
    ("gpt-oss:20b", 14.0,
     "Safest memory fit: tools and thinking, leaves room for the CAD kernel"),
    ("qwen3.6:27b", 19.0,
     "Likely best raw quality, but dense (slower) and tight on 24 GB"),
)

_popen = subprocess.Popen


def _find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    for p in OLLAMA_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


find_ollama = _find_ollama


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=timeout, follow_redirects=False)


def _clip(s, n: int = 200) -> str:
    s = s if isinstance(s, str) else str(s)
    s = "".join(c if c.isprintable() else " " for c in s)
    return s[:n]


def _num(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


async def _get(c: httpx.AsyncClient, url: str):
    r = await c.get(url, headers={"Accept": "application/json"})
    r.raise_for_status()
    if len(r.content) > 5 * 1024 * 1024:
        raise ValueError("response too large")
    return r.json()


async def ollama_status(base_url: str) -> dict:
    """{installed, running, version, models:[{name, size_gb}]}. `installed` is true when the
    binary is found or the server answers (e.g. the Ollama app elsewhere on disk)."""
    installed = find_ollama() is not None
    root = _root(base_url)
    out = {"installed": installed, "running": False, "version": None, "models": []}
    try:
        async with _client(STATUS_TIMEOUT_S) as c:
            ver = await _get(c, f"{root}/api/version")
            out["running"] = out["installed"] = True
            v = ver.get("version") if isinstance(ver, dict) else None
            out["version"] = v if isinstance(v, str) and _VERSION_RE.fullmatch(v) else None
            tags = await _get(c, f"{root}/api/tags")
    except (httpx.HTTPError, ValueError):
        return out
    items = tags.get("models") if isinstance(tags, dict) else None
    models = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict):
            continue
        name = m.get("name")
        if not isinstance(name, str) or not MODEL_ID_RE.fullmatch(name):
            continue
        size = m.get("size")
        size_gb = round(size / 1e9, 1) if _num(size) else None
        models.append({"name": name, "size_gb": size_gb})
        if len(models) >= MAX_MODELS:
            break
    out["models"] = models
    return out


def recommended_local(m: Machine) -> list[dict]:
    """The researched local models with whether each fits this Mac's memory for models."""
    budget = usable_memory_gb(m)
    return [{"name": n, "size_gb": size, "fits": size <= budget, "note": note}
            for n, size, note in RECOMMENDED]


def valid_pull_name(name) -> bool:
    return (isinstance(name, str) and PULL_NAME_RE.fullmatch(name) is not None
            and ".." not in name)


Publish = Callable[[dict], None]


async def pull(model: str, base_url: str, publish: Publish, *,
               min_interval: float = PULL_MIN_INTERVAL_S) -> None:
    """Stream Ollama's /api/pull for `model`, publishing {model, completed, total, status}
    (at most one per `min_interval` unless the status changes; the last one always). Ends with
    status "success" or status "error" plus `error`. Raises ValueError for a bad name."""
    if not valid_pull_name(model):
        raise ValueError("invalid model name")
    state = {"model": model, "completed": 0, "total": 0, "status": "starting"}
    last_sent: tuple[float, str] | None = None
    pending = False

    def send(force: bool = False) -> None:
        nonlocal last_sent, pending
        now = time.monotonic()
        if (not force and last_sent is not None and last_sent[1] == state["status"]
                and now - last_sent[0] < min_interval):
            pending = True
            return
        publish(dict(state))
        last_sent, pending = (now, state["status"]), False

    def fail(msg: str) -> None:
        state["status"] = "error"
        publish({**state, "error": _clip(msg)})

    timeout = httpx.Timeout(PULL_CONNECT_TIMEOUT_S, read=None)
    try:
        async with _client(timeout) as c:
            async with c.stream("POST", f"{_root(base_url)}/api/pull",
                                json={"model": model, "stream": True}) as r:
                if r.status_code >= 400:
                    fail(f"Ollama refused the download (HTTP {r.status_code})")
                    return
                async for line in r.aiter_lines():
                    if len(line) > MAX_LINE_BYTES:
                        fail("Ollama sent an oversized progress line")
                        return
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    if "error" in ev:
                        fail(str(ev["error"]))
                        return
                    status = _clip(ev.get("status", ""))
                    if "total" in ev or "completed" in ev:
                        state["total"] = _num(ev.get("total"))
                        state["completed"] = _num(ev.get("completed"))
                    state["status"] = status
                    if status == "success":
                        if state["total"]:
                            state["completed"] = state["total"]
                        send(force=True)
                        return
                    send()
    except httpx.ConnectError:
        fail(OLLAMA_DOWN_HINT)
        return
    except httpx.HTTPError as e:
        fail(f"download interrupted: {type(e).__name__}")
        return
    if pending:
        send(force=True)
    if state["status"] != "success":
        fail("Ollama ended the download without reporting success")


class Puller:
    """At most one pull per model at a time, as background tasks; aclose() cancels them."""

    def __init__(self, pull_fn: Callable[..., Awaitable[None]] = pull):
        self._pull = pull_fn
        self._tasks: dict[str, asyncio.Task] = {}

    def active(self) -> list[str]:
        return [m for m, t in self._tasks.items() if not t.done()]

    def start(self, model: str, base_url: str, publish: Publish) -> bool:
        t = self._tasks.get(model)
        if t is not None and not t.done():
            return False

        async def run() -> None:
            try:
                await self._pull(model, base_url, publish)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reported as an event, never an unretrieved error
                log.exception("pulling %s failed", model)
                publish({"model": model, "completed": 0, "total": 0, "status": "error",
                         "error": "download failed"})
            finally:
                if self._tasks.get(model) is task:
                    del self._tasks[model]

        task = asyncio.get_running_loop().create_task(run())
        self._tasks[model] = task
        return True

    async def aclose(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()


async def _running(base_url: str) -> bool:
    try:
        async with _client(STATUS_TIMEOUT_S) as c:
            await _get(c, f"{_root(base_url)}/api/version")
        return True
    except (httpx.HTTPError, ValueError):
        return False


async def start_ollama(base_url: str, home: Path, *, wait_s: float = START_WAIT_S) -> dict:
    """Start `ollama serve` in the background when Ollama is installed but not running. The
    server is detached (its own session; output in <home>/logs/ollama.log) and keeps running
    after Mises quits; the returned note says so."""
    if await _running(base_url):
        return {"ok": True, "running": True, "note": "Ollama is already running"}
    binary = find_ollama()
    if binary is None:
        return {"ok": False, "error": "not_installed", "hint": INSTALL_HINT}
    u = urlsplit(base_url)
    env = dict(os.environ)
    env["OLLAMA_HOST"] = f"127.0.0.1:{u.port or 11434}"
    logs = Path(home) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "ollama.log", "ab") as out:
        try:
            _popen([binary, "serve"], stdin=subprocess.DEVNULL, stdout=out,
                   stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, env=env)
        except OSError as e:
            return {"ok": False, "error": "start_failed",
                    "hint": f"could not run {binary} serve: {type(e).__name__}"}
    note = ("Started `ollama serve` in the background; it keeps running after Mises quits "
            "(stop it with `pkill ollama`). Log: logs/ollama.log in the Mises data folder")
    deadline = time.monotonic() + wait_s
    while True:
        if await _running(base_url):
            return {"ok": True, "running": True, "note": note}
        if time.monotonic() >= deadline:
            return {"ok": True, "running": False, "note": note + "; still starting"}
        await asyncio.sleep(0.25)
