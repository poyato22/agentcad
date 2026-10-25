import os
import sys
from pathlib import Path

import pytest

import mises.runtime as runtime_mod
from mises.claude_code import detect
from mises.runtime import Runtime

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py"
# a token-looking string the fake prints; it must never be kept by Mises
LEAK = "sk-ant-oat01-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop0123456789"


def make_shim(bindir: Path) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "claude"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    shim.chmod(0o755)
    return shim


@pytest.fixture
def claude_bin(tmp_path, monkeypatch):
    """A fake `claude` first on PATH; no fallback dirs, so the real one is never found."""
    shim = make_shim(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}/usr/bin:/bin")
    monkeypatch.setattr(detect, "FALLBACK_DIRS", ())
    # the fake keeps its state under $HOME: Mises runs claude with an allowlisted environment
    user_home = tmp_path / "userhome"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    return shim


@pytest.fixture
def user_home(claude_bin, tmp_path):
    return tmp_path / "userhome"


@pytest.fixture
def auth_state(user_home):
    return user_home / "fake-claude-state"


@pytest.fixture
def rt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    return Runtime(home, provider_resolver=lambda ref: (_ for _ in ()).throw(AssertionError(ref)))
