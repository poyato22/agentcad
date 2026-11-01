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


def test_claude_environment_forwards_profile_env(tmp_path, monkeypatch, caplog):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("MISES_HOME", str(home))
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    save_profile(home, load_profile(home).model_copy(
        update={"claude_code_env": {"NODE_EXTRA_CA_CERTS": str(ca)}}))
    assert claude_environment().get("NODE_EXTRA_CA_CERTS") == str(ca)
    ca.unlink()                       # M1: a path that went away is dropped, by name in the log
    with caplog.at_level("WARNING"):
        assert "NODE_EXTRA_CA_CERTS" not in claude_environment()
    assert "NODE_EXTRA_CA_CERTS" in caplog.text and str(ca) not in caplog.text


@pytest.mark.parametrize("name,value", [
    ("NODE_EXTRA_CA_CERTS", "ca.pem"),                 # relative
    ("NODE_EXTRA_CA_CERTS", "~/ca.pem"),
    ("SSL_CERT_FILE", "/etc/../tmp/x"),
    ("SSL_CERT_DIR", "/etc/ssl\x00/x"),               # NUL
    ("HTTPS_PROXY", "socks4://p:1080"),
    ("HTTPS_PROXY", "attacker"),
    ("HTTP_PROXY", "http://p:8080/path"),
    ("HTTPS_PROXY", "http://p:99999"),
    ("NO_PROXY", "localhost, bad host"),
    ("NO_PROXY", "a,,b"),
])
def test_claude_env_values_are_validated(name, value):
    with pytest.raises(Exception):
        Profile(claude_code_env={name: value})


@pytest.mark.parametrize("name,value", [
    ("NODE_EXTRA_CA_CERTS", "/etc/ssl/corp-ca.pem"),
    ("SSL_CERT_DIR", "/etc/ssl/certs"),
    ("HTTPS_PROXY", "http://proxy.corp:8080"),
    ("HTTPS_PROXY", "socks5h://user:pw@127.0.0.1:1080"),
    ("NO_PROXY", "localhost,127.0.0.1,.corp.example,10.0.0.0/8,[::1]"),
])
def test_claude_env_good_values(name, value):
    assert Profile(claude_code_env={name: value}).claude_code_env[name] == value


def test_proxy_credentials_masked_and_kept():
    from mises.profile import mask_claude_env, unmask_claude_env
    stored = {"HTTPS_PROXY": "http://alice:s3cret@proxy.corp:8080"}
    shown = mask_claude_env(stored)
    assert shown["HTTPS_PROXY"] == "http://***@proxy.corp:8080" and "s3cret" not in str(shown)
    assert unmask_claude_env(shown, stored) == stored            # a round-trip keeps them
    assert unmask_claude_env({"HTTPS_PROXY": "http://***@other:1"}, stored) == {
        "HTTPS_PROXY": "http://***@other:1"}


def test_profile_routes_mask_and_check_paths(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    import mises.runtime as runtime_mod
    from mises.runtime import Runtime
    from mises.server.app import create_app
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    rt = Runtime(tmp_path / "home", provider_resolver=lambda r: None)
    h = {"Authorization": "Bearer tok"}
    with TestClient(create_app(rt, "tok"), base_url="http://127.0.0.1") as c:
        r = c.put("/profile", headers=h, json={"claude_code_env": {
            "NODE_EXTRA_CA_CERTS": str(tmp_path / "nope.pem")}})
        assert r.status_code == 422 and "NODE_EXTRA_CA_CERTS" in r.text
        r = c.put("/profile", headers=h, json={"claude_code_env": {
            "HTTPS_PROXY": "http://alice:s3cret@proxy.corp:8080"}})
        assert r.status_code == 200 and "s3cret" not in r.text
        got = c.get("/profile", headers=h).json()
        assert got["claude_code_env"]["HTTPS_PROXY"] == "http://***@proxy.corp:8080"
        assert c.put("/profile", headers=h, json=got).status_code == 200   # app round-trip
    assert rt.profile().claude_code_env["HTTPS_PROXY"] == "http://alice:s3cret@proxy.corp:8080"
