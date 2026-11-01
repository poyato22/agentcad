"""FWC I5 (E3): the TLS / proxy error hints point at the Profile control the app adds,
"Profile & settings → Claude Code → Network", and name only fields that control can set (the
profile's claude_code_env allowlist)."""
import re

from mises.claude_code.session import NETWORK_SETTINGS, classify_claude_error
from mises.profile import CLAUDE_CODE_ENV_KEYS, Profile

TLS = ("API Error: Connection error. (cause: unable to get local issuer certificate, code "
       "UNABLE_TO_GET_ISSUER_CERT_LOCALLY)")


def test_the_network_settings_path_is_the_apps():
    assert NETWORK_SETTINGS == "Profile & settings → Claude Code → Network"


def test_tls_hint_points_at_the_network_fields():
    c = classify_claude_error(TLS)
    assert c["kind"] == "tls"
    assert NETWORK_SETTINGS in c["hint"] and "NODE_EXTRA_CA_CERTS" in c["hint"]
    assert "Profile → Claude Code" not in c["hint"]           # the old, non-existent control


def test_proxy_hint_points_at_the_network_fields():
    c = classify_claude_error("request failed: EPROXY tunnel refused")
    assert c["kind"] == "proxy"
    assert NETWORK_SETTINGS in c["hint"]
    assert "HTTPS_PROXY" in c["hint"] and "NO_PROXY" in c["hint"]


def test_every_variable_a_hint_names_can_be_set_there():
    for text in (TLS, "EPROXY"):
        hint = classify_claude_error(text)["hint"]
        names = set(re.findall(r"\b[A-Z][A-Z_]{5,}\b", hint)) - {"HTTPS"}
        assert names and names <= CLAUDE_CODE_ENV_KEYS, names
    # the three Network fields are accepted values of the env the control writes
    Profile(claude_code_env={"NODE_EXTRA_CA_CERTS": "/etc/ca.pem",
                             "HTTPS_PROXY": "http://proxy:8080", "NO_PROXY": "localhost,.corp"})
