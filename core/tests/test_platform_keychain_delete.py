import subprocess

import pytest
from mises import platform as p


def _run(code, calls):
    def f(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code, "", "x")
    return f


def test_keychain_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _run(0, calls))
    assert p.keychain_delete("s", "a") is True
    assert calls[-1][-4:] == ["-s", "s", "-a", "a"] and "delete-generic-password" in calls[-1]
    monkeypatch.setattr(subprocess, "run", _run(44, calls))
    assert p.keychain_delete("s", "a") is False
    monkeypatch.setattr(subprocess, "run", _run(1, calls))
    with pytest.raises(RuntimeError):
        p.keychain_delete("s", "a")
