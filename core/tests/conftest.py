import os

import pytest

from mises.claude_code import detect


def pytest_collection_modifyitems(config, items):
    """cfd_slow tests (CFD validation) and fem_slow tests (nonlinear FEM benchmarks), minutes
    each, run only when asked for."""
    for mark, env in (("cfd_slow", "MISES_CFD_SLOW"), ("fem_slow", "MISES_FEM_SLOW")):
        if mark in (config.getoption("-m") or "") or os.environ.get(env) == "1":
            continue
        skip = pytest.mark.skip(reason=f"benchmark: run with -m {mark} or {env}=1")
        for item in items:
            if mark in item.keywords:
                item.add_marker(skip)

REAL_FIND_CLAUDE = detect.find_claude


@pytest.fixture(autouse=True)
def _no_leaked_sandbox_home():
    """A Runtime registers its home for the build sandbox (platform.register_sandbox_home); a
    test's temp home must not leak into the next test's sandboxed workers or profile checks."""
    from mises import platform
    platform.register_sandbox_home(None)
    yield
    platform.register_sandbox_home(None)


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """Never run the user's real `claude` from tests: code that looks it up through the module
    (Runtime, routes, CLI) sees it as not installed unless the test asks for the fake one
    (tests/claude_code/conftest.py `claude_bin` puts the real lookup back, with a fake on PATH)."""
    monkeypatch.setattr(detect, "find_claude", lambda: detect.ClaudeInfo(path=None, version=None))


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """The real docker CLI is never run from tests (S2): the pinned OpenFOAM image reads as not
    pulled unless a test fakes it present."""
    from mises.sim import cfd, foam_image
    monkeypatch.setattr(foam_image, "image_status", lambda d: "absent")
    monkeypatch.setattr(cfd, "image_status", lambda d: "absent")


FAKE_MAC = {"hw.ncpu": "15", "hw.perflevel0.physicalcpu": "6", "hw.memsize": str(24 * 2**30),
            "machdep.cpu.brand_string": "Apple M5 Pro"}


@pytest.fixture(autouse=True)
def _no_real_machine(monkeypatch):
    """sysctl is never run from tests: every test sees a 24 GB M5 Pro with 6 performance cores
    (auto parallel agents for cloud models: 3). Ollama is not installed and unreachable."""
    from mises import hardware, local_models, platform

    monkeypatch.setattr(platform, "sysctl", lambda name: FAKE_MAC.get(name))
    hardware.machine.cache_clear()
    monkeypatch.setattr(local_models, "find_ollama", lambda: None)
    monkeypatch.setattr(local_models, "TRANSPORT", _offline_transport())
    monkeypatch.setattr(local_models, "_popen", _no_popen)
    yield
    hardware.machine.cache_clear()


def _offline_transport():
    import httpx

    def refuse(req):
        raise httpx.ConnectError("offline in tests")
    return httpx.MockTransport(refuse)


def _no_popen(*a, **k):
    raise AssertionError("tests must not start real processes (ollama serve)")
