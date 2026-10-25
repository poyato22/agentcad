import json

from mises.claude_code import detect
from mises.claude_code.detect import AuthStatus, ClaudeInfo, auth_status, find_claude, start_login

from .conftest import LEAK, make_shim


def test_find_claude_on_path(claude_bin):
    info = find_claude()
    assert info == ClaudeInfo(path=str(claude_bin), version="2.1.310")


def test_find_claude_in_fallback_dir(tmp_path, monkeypatch):
    shim = make_shim(tmp_path / "local" / "bin")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(detect, "FALLBACK_DIRS", (str(tmp_path / "nope"), str(shim.parent)))
    info = find_claude()
    assert info.path == str(shim) and info.version == "2.1.310"


def test_find_claude_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(detect, "FALLBACK_DIRS", ())
    assert find_claude() == ClaudeInfo(path=None, version=None)


def test_fallback_dirs_cover_documented_install_locations():
    dirs = [d.replace(str(detect.Path.home()), "~") for d in detect.FALLBACK_DIRS]
    assert dirs == ["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.claude/local"]


async def test_auth_status_signed_in(claude_bin, auth_state):
    auth_state.write_text("signed_in")
    st = await auth_status(find_claude())
    assert st == AuthStatus(signed_in=True, auth_method="claude.ai", email="user@example.com",
                            config_dir="/tmp/fake-claude-config", raw_ok=True)
    # only the non-secret fields are kept; nothing else from the JSON
    assert set(vars(st)) == {"signed_in", "auth_method", "email", "config_dir", "raw_ok"}


async def test_auth_status_signed_out_exit_1(claude_bin, auth_state):
    auth_state.write_text("signed_out")
    st = await auth_status(find_claude())
    assert st.signed_in is False and st.raw_ok is True and st.auth_method == "none"
    assert st.email is None


async def test_auth_status_malformed_json(claude_bin, auth_state):
    auth_state.write_text("malformed")
    st = await auth_status(find_claude())
    assert st.signed_in is False and st.raw_ok is False


async def test_auth_status_not_installed():
    st = await auth_status(ClaudeInfo(path=None, version=None))
    assert st.signed_in is False and st.raw_ok is False and st.auth_method == "none"


async def test_login_waits_and_rechecks_without_keeping_stdout(claude_bin, auth_state, tmp_path,
                                                               monkeypatch):
    args_file = tmp_path / "login-args.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOGIN_ARGS", str(args_file))
    auth_state.write_text("signed_out")
    handle = await start_login(find_claude())
    st = await handle.wait(timeout=10)
    assert st.signed_in is True and handle.returncode == 0
    assert json.loads(args_file.read_text()) == ["auth", "login"]
    # stdout is discarded; the stderr tail is kept with token-looking strings redacted
    assert LEAK not in repr(vars(handle)) and LEAK not in handle.stderr_tail
    assert "[redacted]" in handle.stderr_tail


async def test_login_console(claude_bin, auth_state, tmp_path, monkeypatch):
    args_file = tmp_path / "login-args.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOGIN_ARGS", str(args_file))
    handle = await start_login(find_claude(), console=True)
    await handle.wait(timeout=10)
    assert json.loads(args_file.read_text()) == ["auth", "login", "--console"]


def test_redact():
    text = f"x {LEAK} y sk-ant-api03-short_but_key-ish z ok"
    out = detect.redact(text)
    assert LEAK not in out and "sk-ant-api03" not in out and out.endswith("z ok")
