import subprocess

import pytest

from mises import hardware, platform

REAL_SYSCTL = platform.sysctl  # the autouse conftest fixture fakes platform.sysctl

M5_PRO = {"hw.ncpu": "15", "hw.perflevel0.physicalcpu": "10", "hw.memsize": str(24 * 2**30),
          "machdep.cpu.brand_string": "Apple M5 Pro"}


@pytest.fixture
def mac(monkeypatch):
    vals = dict(M5_PRO)
    monkeypatch.setattr(platform, "sysctl", lambda name: vals.get(name))
    hardware.machine.cache_clear()
    yield vals
    hardware.machine.cache_clear()


def test_sysctl_runs_the_system_binary(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout="12\n", stderr="")

    monkeypatch.setattr(platform.subprocess, "run", run)
    assert REAL_SYSCTL("hw.ncpu") == "12"
    assert seen["argv"] == ["/usr/sbin/sysctl", "-n", "hw.ncpu"]
    assert seen["kw"]["timeout"] > 0


def test_sysctl_failure_is_none(monkeypatch):
    monkeypatch.setattr(platform.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, stdout="", stderr="unknown oid"))
    assert REAL_SYSCTL("hw.nope") is None

    def boom(*a, **k):
        raise FileNotFoundError("no sysctl")
    monkeypatch.setattr(platform.subprocess, "run", boom)
    assert REAL_SYSCTL("hw.ncpu") is None


def test_machine_reads_sysctl(mac):
    assert hardware.machine() == {"cpu_cores": 15, "perf_cores": 10, "ram_gb": 24,
                                  "chip": "M5 Pro"}


def test_machine_tolerates_missing_values(mac, monkeypatch):
    mac.clear()
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 8)
    m = hardware.machine()
    assert m == {"cpu_cores": 8, "perf_cores": 8, "ram_gb": 0, "chip": "this Mac"}


@pytest.mark.parametrize("perf,expected", [(1, 1), (2, 1), (6, 3), (10, 4), (16, 4)])
def test_auto_parallel_cloud(mac, perf, expected):
    mac["hw.perflevel0.physicalcpu"] = str(perf)
    for ref in ("anthropic/claude-opus-5-5", "claude-code/opus", "moonshot/kimi-k3.1-pro",
                "openrouter/anthropic/claude-x"):
        assert hardware.auto_parallel(ref) == expected


def test_auto_parallel_openrouter_free_is_one(mac):
    assert hardware.auto_parallel("openrouter/a/b:free") == 1


def test_auto_parallel_local_depends_on_memory(mac):
    # 24 GB Mac: about 19 GB usable for models
    assert hardware.auto_parallel("ollama/huihui_ai/Qwen3.8-abliterated:27b",
                                  model_size_gb=17.0) == 1
    assert hardware.auto_parallel("ollama/huihui_ai/qwen3.5-abliterated:9b",
                                  model_size_gb=6.6) == 2
    assert hardware.auto_parallel("ollama/unknown-size") == 1
    assert hardware.auto_parallel("lmstudio/some-model") == 1


def test_model_kind():
    assert hardware.model_kind("ollama/x") == "local"
    assert hardware.model_kind("lmstudio/x") == "local"
    assert hardware.model_kind("openrouter/x/y:free") == "free"
    assert hardware.model_kind("openrouter/x/y") == "cloud"
    assert hardware.model_kind("claude-code/sonnet") == "cloud"
    assert hardware.model_kind("claude-sonnet-5-5") == "cloud"


def test_is_free_model_shared_predicate():
    # C5: one predicate — zero-priced OR id ends in :free (OpenRouter only) → free class.
    assert hardware.is_free_model("openrouter/a/b:free") is True
    assert hardware.is_free_model("openrouter/a/b") is False
    assert hardware.is_free_model("anthropic/claude-x") is False
    zero = {"prompt": "0", "completion": "0"}
    paid = {"prompt": "0.001", "completion": "0"}
    assert hardware.is_free_model("openrouter/a/zero", zero) is True
    assert hardware.is_free_model("openrouter/a/paid", paid) is False
    # a zero-priced non-OpenRouter model is not in the free class
    assert hardware.is_free_model("anthropic/x", zero) is False
    # the registry limiter uses the same predicate
    from mises.providers.registry import is_openrouter_free
    assert is_openrouter_free("openrouter", "a/b:free") is True
    assert is_openrouter_free("anthropic", "claude-x") is False


def test_parallel_reason(mac):
    assert hardware.parallel_reason("anthropic/claude-opus-5-5") == \
        "M5 Pro · 24 GB · cloud model → 4"
    assert hardware.parallel_reason("openrouter/a:free") == \
        "M5 Pro · 24 GB · OpenRouter free model (20 requests/min) → 1"
    assert hardware.parallel_reason("ollama/m:27b", model_size_gb=17.0) == \
        "M5 Pro · 24 GB · local model (17 GB) → 1 · local models share 19 GB"
