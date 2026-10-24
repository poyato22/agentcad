import pytest

import mises.cli.main as main_mod
from mises.cli.main import main
from mises.types import Caps, StepKind


def test_models_lists_vendors(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(main_mod, "keychain_get",
                        lambda s, a: "k" if a == "gemini" else None)
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln.split()}
    assert "env" in rows["anthropic"] and "keychain" in rows["gemini"]
    assert "none" in rows["openai"] and "no key needed" in rows["ollama"]
    assert "anthropic/claude-sonnet-5-5" in out
    assert "sk-test" not in out


def test_agents_lists_builtins(capsys):
    assert main(["agents"]) == 0
    out = capsys.readouterr().out
    for name in ("designer", "planner", "verifier", "sim"):
        assert name in out


def test_keys_set_stores_in_keychain(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(main_mod, "keychain_set", lambda s, a, v: saved.append((s, a, v)))
    answers = iter(["sk-secret"])
    monkeypatch.setattr(main_mod.getpass, "getpass", lambda prompt="": next(answers))
    assert main(["keys", "set", "anthropic"]) == 0
    assert saved == [("mises", "anthropic", "sk-secret")]
    assert "sk-secret" not in capsys.readouterr().out


def test_keys_set_onshape_stores_access_colon_secret(monkeypatch):
    saved = []
    monkeypatch.setattr(main_mod, "keychain_set", lambda s, a, v: saved.append((s, a, v)))
    answers = iter(["ACCESS", "SECRET"])
    monkeypatch.setattr(main_mod.getpass, "getpass", lambda prompt="": next(answers))
    assert main(["keys", "set", "onshape"]) == 0
    assert saved == [("mises", "onshape", "ACCESS:SECRET")]


def test_keys_set_unknown_vendor(capsys):
    assert main(["keys", "set", "nope"]) != 0
    assert "unknown vendor" in capsys.readouterr().err


def test_runs_empty_and_steps(capsys):
    from mises.runtime import Runtime
    assert main(["runs"]) == 0
    rt = Runtime()
    run = rt.store.create_run("designer", "fake", "m", "build a thing")
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [])
    rt.store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": "build a thing"})
    capsys.readouterr()
    assert main(["runs"]) == 0
    assert run.id in capsys.readouterr().out
    assert main(["runs", "--steps", run.id]) == 0
    assert "message" in capsys.readouterr().out
    assert main(["runs", "--steps", "nope"]) != 0


def test_no_command_prints_help(capsys):
    with pytest.raises(SystemExit):
        main([])
    assert "chat" in capsys.readouterr().err


def test_mcp_stdio_delegates_to_proxy(monkeypatch):
    seen = []
    import mises.server.stdio_proxy as sp
    monkeypatch.setattr(sp, "main", lambda argv=None: seen.append(argv) or 0)
    assert main(["mcp-stdio", "--run", "abc"]) == 0
    assert seen == [["--run", "abc"]]


def test_serve_prints_url_and_stops(monkeypatch, capsys):
    import mises.server.app as app_mod
    calls = []
    monkeypatch.setattr(app_mod, "serve",
                        lambda rt, port=0, token=None: calls.append(("serve", port)) or (4321, "s3cr3t-9"))

    def fake_wait(port):
        raise KeyboardInterrupt
    monkeypatch.setattr(app_mod, "wait", fake_wait)
    monkeypatch.setattr(app_mod, "stop", lambda port, timeout=10.0: calls.append(("stop", port)))
    assert main(["serve", "--port", "4321"]) == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:4321" in out and "s3cr3t-9" not in out
    assert calls == [("serve", 4321), ("stop", 4321)]
