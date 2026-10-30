"""C6: /system/check and /system/install/{dep} — the installer allowlist and streaming.

Installers are never actually run: the command runner is injected with a fake.
"""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import mises.server.system_api as sysapi
from mises.server.system_api import _do_install, build_system_router
from mises.types import MisesError


def _mount(rt, runner):
    a = FastAPI()
    a.include_router(build_system_router(rt, runner=runner))

    @a.exception_handler(MisesError)
    async def _h(_req, e: MisesError):
        from mises.server.app import _error_response
        return _error_response(e)

    return a


@pytest.fixture
def fake_runner_calls():
    return []


@pytest.fixture
def app(rt, fake_runner_calls, monkeypatch):
    monkeypatch.setattr(sysapi, "find_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(sysapi, "find_conda", lambda: "/opt/homebrew/bin/conda")

    async def fake_runner(argv, emit):
        fake_runner_calls.append(list(argv))
        emit("fake output")
        return 0

    return _mount(rt, fake_runner)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_install_unknown_dep_rejected(client):
    r = client.post("/system/install/rm_rf")
    assert r.status_code == 400


def test_install_uses_fixed_command_no_body_args(client, fake_runner_calls):
    # a hostile body must not reach argv
    r = client.post("/system/install/prusaslicer",
                    json={"args": ["; rm -rf /"], "cmd": "evil", "dep": "../../x"})
    assert r.status_code == 200, r.text
    assert r.json()["command"] == ["/opt/homebrew/bin/brew", "install", "--cask", "prusaslicer"]
    assert r.json()["ok"] is True and r.json()["fix"] is None


def test_calculix_uses_conda(client):
    r = client.post("/system/install/calculix")
    assert r.json()["command"] == ["/opt/homebrew/bin/conda", "install", "-y", "-c",
                                    "conda-forge", "calculix"]


def test_brew_missing_returns_open_url_fix(rt, monkeypatch):
    monkeypatch.setattr(sysapi, "find_brew", lambda: None)

    async def fake_runner(argv, emit):
        return 0

    with TestClient(_mount(rt, fake_runner), base_url="http://127.0.0.1") as c:
        r = c.post("/system/install/ollama")
    assert r.status_code == 200 and r.json()["ok"] is False     # A1's SystemInstallStart
    assert r.json()["fix"] == {"kind": "open_url", "label": "Get brew", "url": sysapi.BREW_URL}


async def test_install_streams_output(rt):
    lines = ["Downloading...", "==> Pouring", "done"]

    async def fake_runner(argv, emit):
        for ln in lines:
            emit(ln)
        return 0

    sub = rt.events.subscribe()
    ok = await _do_install(rt, "ollama", ["/opt/homebrew/bin/brew", "install", "ollama"],
                           fake_runner)
    assert ok is True
    got = []
    while not sub.queue.empty():
        got.append(sub.queue.get_nowait())
    sub.close()
    install_events = [e for e in got if e.type == "system_install"]
    assert [e.data["line"] for e in install_events if not e.data["done"]] == lines
    final = install_events[-1]
    assert final.data["done"] is True and final.data["ok"] is True


async def test_install_failure_reports_not_ok(rt):
    async def fake_runner(argv, emit):
        emit("boom")
        return 1

    sub = rt.events.subscribe()
    ok = await _do_install(rt, "ollama", ["/opt/homebrew/bin/brew", "install", "ollama"],
                           fake_runner)
    assert ok is False
    finals = [e for e in _drain(sub) if e.type == "system_install" and e.data["done"]]
    sub.close()
    assert finals[-1].data["ok"] is False


async def test_installs_are_serialized(rt):
    import asyncio
    active, peak = 0, 0

    async def slow_runner(argv, emit):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return 0

    lock = asyncio.Lock()
    oks = await asyncio.gather(
        _do_install(rt, "ollama", ["/b/brew", "install", "ollama"], slow_runner, serial=lock),
        _do_install(rt, "prusaslicer", ["/b/brew", "install", "--cask", "prusaslicer"],
                    slow_runner, serial=lock))
    assert oks == [True, True] and peak == 1


def test_openfoam_pull_is_the_fixed_pinned_image_no_body_args(rt, monkeypatch):
    from mises.sim.foam_image import OPENFOAM_IMAGE
    monkeypatch.setattr(sysapi, "find_docker", lambda: "/usr/local/bin/docker")
    calls = []

    async def fake_runner(argv, emit):
        calls.append(list(argv))
        return 0

    with TestClient(_mount(rt, fake_runner), base_url="http://127.0.0.1") as c:
        r = c.post("/system/install/openfoam",
                   json={"image": "evil/image:latest", "args": ["--network", "host"]})
    assert r.status_code == 200, r.text
    assert r.json()["command"] == ["/usr/local/bin/docker", "pull", OPENFOAM_IMAGE]


def test_openfoam_pull_without_docker_offers_orbstack(rt, monkeypatch):
    monkeypatch.setattr(sysapi, "find_docker", lambda: None)

    async def fake_runner(argv, emit):
        return 0

    with TestClient(_mount(rt, fake_runner), base_url="http://127.0.0.1") as c:
        r = c.post("/system/install/openfoam")
    assert r.json()["ok"] is False
    assert r.json()["fix"] == {"kind": "open_url", "label": "Get docker",
                               "url": sysapi.ORBSTACK_URL}


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out
