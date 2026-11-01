"""E3: Claude Code errors are classified ({kind, cause, hint}) on the error step, and the
user's extra environment (CA bundle / proxy) is forwarded to every `claude` Mises starts."""
import pytest

from mises.claude_code import ClaudeCodeSession
from mises.claude_code.detect import ClaudeInfo, claude_environment
from mises.claude_code.session import classify_claude_error
from mises.profile import Profile, load_profile, save_profile
from mises.types import StepKind


def test_classify_tls():
    c = classify_claude_error("Error: UNABLE_TO_GET_ISSUER_CERT_LOCALLY")
    assert c["kind"] == "tls" and "NODE_EXTRA_CA_CERTS" in c["hint"]


@pytest.mark.parametrize("text,kind", [
    ("fetch failed: ECONNREFUSED 127.0.0.1", "network"),
    ("HTTP 401 Unauthorized", "auth"),
    ("429 rate limit exceeded", "rate_limit"),
    ("Error 529: overloaded_error", "overloaded"),
    ("tunneling socket could not be established via proxy", "proxy"),
])
def test_classify_variants(text, kind):
    assert classify_claude_error(text)["kind"] == kind


def test_classify_unknown_is_none():
    assert classify_claude_error("some unrelated failure") is None


def _session(rt):
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    return ClaudeCodeSession(rt, rid, model_alias="sonnet", info=ClaudeInfo(None, None),
                             mcp_url="http://127.0.0.1:1/mcp", mcp_token="t"), rid


def test_on_result_error_is_classified(rt):
    s, rid = _session(rt)
    s._init_ok = True
    s._on_result({"type": "result", "is_error": True, "usage": {},
                  "result": "API Error: unable to get local issuer certificate "
                            "(UNABLE_TO_GET_ISSUER_CERT_LOCALLY)"})
    errs = [st.payload for st in rt.store.steps(rid, [StepKind.ERROR])]
    assert errs and errs[-1]["kind"] == "tls"
    assert errs[-1]["cause"] and "NODE_EXTRA_CA_CERTS" in errs[-1]["hint"]


def test_profile_env_allowlist(tmp_path):
    p = Profile(claude_code_env={"NODE_EXTRA_CA_CERTS": "/etc/ca.pem", "HTTPS_PROXY": "http://p:8080"})
    assert p.claude_code_env["NODE_EXTRA_CA_CERTS"] == "/etc/ca.pem"
    with pytest.raises(Exception):
        Profile(claude_code_env={"ANTHROPIC_API_KEY": "sk-secret"})
    with pytest.raises(Exception):
        Profile(claude_code_env={"NODE_EXTRA_CA_CERTS": "line\nbreak"})


def test_claude_environment_forwards_profile_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    save_profile(home, load_profile(home).model_copy(
        update={"claude_code_env": {"NODE_EXTRA_CA_CERTS": "/etc/ca.pem"}}))
    assert claude_environment().get("NODE_EXTRA_CA_CERTS") == "/etc/ca.pem"
