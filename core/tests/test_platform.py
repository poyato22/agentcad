import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
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
    assert calls and calls[-1][-1] == "-i"
    monkeypatch.setattr(subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 44, "", "nf"))
    assert p.keychain_get("s", "a") is None


def test_sandbox_profile():
    s = p.sandbox_profile(Path("/tmp/w"))
    assert "(deny network*)" in s and "(version 1)" in s
    assert f'(subpath "{Path("/tmp/w").resolve()}")' in s
    assert "/private/var/folders" not in s


def test_sandbox_profile_resolves_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert f'(subpath "{real.resolve()}")' in p.sandbox_profile(link)


@pytest.mark.parametrize("bad", ['/tmp/a"b', "/tmp/a\\b", "/tmp/a\nb"])
def test_sandbox_profile_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        p.sandbox_profile(Path(bad))


def test_keychain_set_secret_not_in_argv_or_error(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")
        return subprocess.CompletedProcess(cmd, 1, "", "boom")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(Exception) as e:
        p.keychain_set("svc", "acct", "TOPSECRET")
    assert "TOPSECRET" not in " ".join(seen["cmd"]) and "TOPSECRET" not in str(e.value)
    assert "TOPSECRET" in seen["input"]


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
def test_sandbox_exec_real(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    other = Path(tempfile.mkdtemp(dir="/private/tmp"))
    try:
        subprocess.run(["/usr/bin/sandbox-exec", "-p", p.sandbox_profile(w), "/bin/sh", "-c",
                        f"echo x > {w}/ok; echo y > {other}/bad"], capture_output=True)
        assert (w / "ok").exists() and not (other / "bad").exists()
    finally:
        shutil.rmtree(other, ignore_errors=True)
