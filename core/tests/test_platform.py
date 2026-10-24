import subprocess
from pathlib import Path
from mises import platform as p


def test_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "h"))
    assert p.data_home() == tmp_path / "h" and (tmp_path / "h").is_dir()


def test_keychain(monkeypatch):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        if "find-generic-password" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "sekret\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake)
    assert p.keychain_get("s", "a") == "sekret"
    p.keychain_set("s", "a", "x")
    assert any("add-generic-password" in c for c in calls)
    monkeypatch.setattr(subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 44, "", "nf"))
    assert p.keychain_get("s", "a") is None


def test_sandbox_profile():
    s = p.sandbox_profile(Path("/tmp/w"))
    assert "(deny network*)" in s and '(subpath "/tmp/w")' in s and "(version 1)" in s
