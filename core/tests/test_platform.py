import os
import shutil
import subprocess
import sys
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


def test_sandbox_profile_denies_home_except_write_and_parts(tmp_path):
    home = tmp_path / "h"
    wd = home / "parts" / "x"
    wd.mkdir(parents=True)
    s = p.sandbox_profile(wd, home=home)
    assert f'(deny file-read* (subpath "{home.resolve()}"))' in s
    assert f'(allow file-read* (subpath "{(home / "parts").resolve()}"))' in s
    assert f'(allow file-read* (subpath "{wd.resolve()}"))' in s
    assert f'(literal "{home.resolve()}")' in s  # metadata so the worker can reach its write_dir
    # no home given: no read restriction, and nothing touches the real data_home()
    assert "deny file-read*" not in p.sandbox_profile(wd)


def test_sandbox_profile_read_dirs(tmp_path):
    home = tmp_path / "h"
    job = home / "runs" / "r" / "sim_tmp" / "s"
    wd = job / "coarse"
    wd.mkdir(parents=True)
    s = p.sandbox_profile(wd, home=home, read_dirs=[job])
    assert f'(allow file-read* (subpath "{job.resolve()}"))' in s


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


def test_sandbox_profile_denies_new_processes():
    assert "(deny process-fork)" in p.sandbox_profile(Path("/tmp/w"))


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
def test_sandbox_exec_cannot_fork(tmp_path):
    r = subprocess.run(["/usr/bin/sandbox-exec", "-p", p.sandbox_profile(tmp_path), sys.executable,
                        "-I", "-c", "import os\ntry:\n    os.fork()\nexcept OSError as e:\n"
                        "    print('denied', e.errno)\n"], capture_output=True, text=True)
    assert r.stdout.startswith("denied")


def test_kill_process_tree_kills_a_session(tmp_path):
    proc = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        assert proc.pid in p.kill_process_tree(proc.pid)
        assert proc.wait(timeout=5) != 0
    finally:
        proc.kill()


def test_sandboxed_command(tmp_path):
    cmd = p.sandboxed_command(["python", "-I", "x.py"], tmp_path)
    assert cmd[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert cmd[2] == p.sandbox_profile(tmp_path)
    assert cmd[3:] == ["python", "-I", "x.py"]
    # with a Runtime-registered home, the command's profile denies reads of that home
    p.register_sandbox_home(tmp_path / "h")
    assert p.sandboxed_command(["x"], tmp_path)[2] == p.sandbox_profile(tmp_path,
                                                                        home=tmp_path / "h")


def test_agent_sandbox_profile_shape(tmp_path):
    home = tmp_path / "home"
    wd = home / "runs" / "r1"
    wd.mkdir(parents=True)
    s = p.agent_sandbox_profile(home, wd)
    assert "(allow default)" in s and "network" not in s
    assert f'(subpath "{home.resolve()}")' in s and f'(subpath "{wd.resolve()}")' in s
    with pytest.raises(ValueError):
        p.agent_sandbox_profile(home, tmp_path / "elsewhere")   # workdir must be inside home
    with pytest.raises(ValueError):
        p.agent_sandbox_profile(Path('/tmp/a"b'), wd)


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
def test_agent_sandbox_real(tmp_path):
    # C2b: an ACP agent cannot read server.json / mises.db / other runs / parts, can use its workdir
    home = tmp_path / "home"
    wd = home / "runs" / "r1"
    other = home / "runs" / "r2"
    for d in (wd, other, home / "parts" / "box"):
        d.mkdir(parents=True)
    (home / "server.json").write_text('{"token": "admin"}')
    (home / "mises.db").write_text("db")
    (home / "mises.db-wal").write_text("wal")
    (other / "secret.txt").write_text("s")
    (home / "parts" / "box" / "vars.json").write_text("{}")
    (wd / "parts").symlink_to(home / "parts", target_is_directory=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("fine")
    script = (
        "import os, sys\n"
        "def tryread(p):\n"
        "    try:\n"
        "        open(p).read(); return 'ok'\n"
        "    except OSError: return 'denied'\n"
        "def trywrite(p):\n"
        "    try:\n"
        "        open(p, 'w').write('x'); return 'ok'\n"
        "    except OSError: return 'denied'\n"
        f"h = {str(home)!r}; w = {str(wd)!r}\n"
        "print(tryread(h + '/server.json'), tryread(h + '/mises.db'), tryread(h + '/mises.db-wal'),"
        " tryread(h + '/runs/r2/secret.txt'), tryread(h + '/parts/box/vars.json'),"
        " tryread(w + '/parts/box/vars.json'), trywrite(h + '/mises.db'),"
        " trywrite(h + '/evil.txt'), trywrite(w + '/ok.txt'), tryread(w + '/ok.txt'),"
        f" tryread({str(outside)!r}), os.getcwd() == os.path.realpath(w))\n")
    cmd = p.agent_sandboxed_command(["/usr/bin/python3", "-c", script], home, wd)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=wd)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["denied"] * 6 + ["denied", "denied", "ok", "ok", "ok", "True"]
    assert (home / "mises.db").read_text() == "db" and not (home / "evil.txt").exists()


def test_claude_sandbox_profile_shape(tmp_path):
    home = tmp_path / "home"
    wd = home / "runs" / "r1"
    wd.mkdir(parents=True)
    cfg = tmp_path / "dot-claude"
    bindir = tmp_path / "local" / "bin"
    s = p.claude_sandbox_profile(home, wd, cfg, binary_dir=bindir)
    # the agent profile, plus Claude's own config dir (read/write) and its install dir (read)
    assert s.startswith(p.agent_sandbox_profile(home, wd))
    assert f'(allow file-read* file-write* (subpath "{cfg.resolve()}"))' in s
    assert f'(allow file-read* (subpath "{bindir.resolve()}"))' in s
    assert "deny network" not in s
    # a config or install dir that would re-open the Mises home is refused
    with pytest.raises(ValueError):
        p.claude_sandbox_profile(home, wd, tmp_path)
    with pytest.raises(ValueError):
        p.claude_sandbox_profile(home, wd, cfg, binary_dir=home)
    with pytest.raises(ValueError):
        p.claude_sandbox_profile(home, wd, home / "runs" / "r2")
    with pytest.raises(ValueError):
        p.claude_sandbox_profile(home, wd, Path('/tmp/a"b'))


def test_claude_sandboxed_command_refuses_without_sandbox_exec(tmp_path, monkeypatch):
    home = tmp_path / "home"
    wd = home / "runs" / "r1"
    wd.mkdir(parents=True)
    monkeypatch.setattr(p, "SANDBOX_EXEC", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError):
        p.claude_sandboxed_command(["claude"], home, wd, tmp_path / "cfg")


@pytest.mark.skipif(not os.path.exists("/usr/bin/sandbox-exec"), reason="no sandbox-exec")
def test_claude_sandbox_real(tmp_path):
    home = tmp_path / "home"
    wd = home / "runs" / "r1"
    wd.mkdir(parents=True)
    (home / "server.json").write_text('{"token": "admin"}')
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    script = (
        "import sys\n"
        "def tryread(p):\n"
        "    try:\n"
        "        open(p).read(); return 'ok'\n"
        "    except OSError: return 'denied'\n"
        "def trywrite(p):\n"
        "    try:\n"
        "        open(p, 'w').write('x'); return 'ok'\n"
        "    except OSError: return 'denied'\n"
        f"print(tryread({str(home / 'server.json')!r}), trywrite({str(cfg / 'session.jsonl')!r}),"
        f" trywrite({str(wd / 'ok.txt')!r}))\n")
    cmd = p.claude_sandboxed_command(["/usr/bin/python3", "-c", script], home, wd, cfg)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=wd)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["denied", "ok", "ok"]
