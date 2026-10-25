import logging
import stat

import pytest
from fastapi.testclient import TestClient

import mises.platform as plat
from mises.onshape.adapter import OnshapeAdapter
from mises.onshape.client import OnshapeClient
from mises.profile import Profile, load_profile
from mises.server.app import create_app
from mises.types import Caps
from onshape.fake_onshape import FakeOnshape

from .conftest import AUTH, TOKEN

MCP = "mcp-token-456"
MCP_AUTH = {"Authorization": f"Bearer {MCP}"}
SECRET = "sk-SUPER-SECRET-VALUE-123"


class FakeKeychain:
    def __init__(self):
        self.d = {}

    def get(self, s, a):
        return self.d.get((s, a))

    def set(self, s, a, v):
        self.d[(s, a)] = v

    def delete(self, s, a):
        return self.d.pop((s, a), None) is not None


@pytest.fixture
def kc(monkeypatch):
    k = FakeKeychain()
    monkeypatch.setattr(plat, "keychain_get", k.get)
    monkeypatch.setattr(plat, "keychain_set", k.set)
    monkeypatch.setattr(plat, "keychain_delete", k.delete)
    import mises.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "keychain_get", k.get)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return k


@pytest.fixture
def client(rt, kc):
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP)) as c:
        yield c


def test_get_and_put_profile_partial_merge(client, rt):
    r = client.get("/profile", headers=AUTH)
    assert r.status_code == 200 and r.json()["units"] == "mm"
    r = client.put("/profile", headers=AUTH, json={"display_name": "Ada", "max_parallel_agents": 3})
    assert r.status_code == 200 and r.json()["display_name"] == "Ada"
    r = client.put("/profile", headers=AUTH, json={"default_material": "PETG"})
    body = r.json()
    assert body["display_name"] == "Ada" and body["default_material"] == "PETG"
    assert load_profile(rt.home).max_parallel_agents == 3
    assert stat.S_IMODE((rt.home / "profile.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("body", [
    {"units": "inch"}, {"max_parallel_agents": 0}, {"bogus": 1}, {"setup_completed_at": 5},
    {"local_endpoints": {"ollama": "http://evil.example.com"}}, [1, 2],
])
def test_put_profile_validation_422(client, body, rt):
    r = client.put("/profile", headers=AUTH, json=body)
    assert r.status_code == 422
    assert load_profile(rt.home) == Profile()


def test_keys_status_never_leaks(client, kc, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET + "-env")
    r = client.post("/keys/anthropic", headers=AUTH, json={"secret": SECRET})
    assert r.status_code == 200 and r.json() == {"vendor": "anthropic", "present": True,
                                                 "source": "keychain"}
    assert SECRET not in r.text
    r = client.get("/keys", headers=AUTH)
    rows = {x["vendor"]: x for x in r.json()}
    assert set(rows) == {"anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio",
                         "onshape"}
    assert rows["anthropic"] == {"vendor": "anthropic", "present": True, "source": "keychain"}
    assert rows["openai"]["source"] == "env" and rows["gemini"] == {
        "vendor": "gemini", "present": False, "source": "none"}
    assert all(set(x) == {"vendor", "present", "source"} for x in rows.values())
    assert SECRET not in r.text
    assert SECRET not in caplog.text
    assert kc.d[("mises", "anthropic")] == SECRET


def test_key_errors_do_not_echo_secret(client, kc, caplog):
    caplog.set_level(logging.DEBUG)
    for body in ({"secret": ""}, {"secret": 12345}, {"nope": SECRET}, [SECRET]):
        r = client.post("/keys/anthropic", headers=AUTH, json=body)
        assert r.status_code in (400, 422) and SECRET not in r.text
    r = client.post("/keys/ollama", headers=AUTH, json={"secret": SECRET})
    assert r.status_code == 400 and SECRET not in r.text
    r = client.post("/keys/nosuch", headers=AUTH, json={"secret": SECRET})
    assert r.status_code == 400 and SECRET not in r.text
    r = client.post("/keys/onshape", headers=AUTH, json={"access_key": "a:b", "secret_key": SECRET})
    assert r.status_code == 400 and SECRET not in r.text
    assert SECRET not in caplog.text and kc.d == {}


def test_keychain_failure_is_clean(client, monkeypatch, caplog):
    def boom(*a):
        raise RuntimeError("keychain_set failed for service 'mises' (exit 1)")
    monkeypatch.setattr(plat, "keychain_set", boom)
    r = client.post("/keys/anthropic", headers=AUTH, json={"secret": SECRET})
    assert r.status_code >= 400 and SECRET not in r.text and SECRET not in caplog.text


def test_delete_key(client, kc):
    client.post("/keys/openai", headers=AUTH, json={"secret": SECRET})
    r = client.delete("/keys/openai", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"vendor": "openai", "present": False,
                                                 "source": "none"}
    assert kc.d == {}
    assert client.delete("/keys/openai", headers=AUTH).status_code == 200  # idempotent


def test_onshape_keys_stored_and_runtime_reloaded(client, kc, rt):
    assert "onshape" not in rt.gate.services
    r = client.post("/keys/onshape", headers=AUTH,
                    json={"access_key": "AK", "secret_key": SECRET})
    assert r.status_code == 200 and r.json()["source"] == "keychain" and SECRET not in r.text
    assert kc.d[("mises", "onshape")] == f"AK:{SECRET}"
    assert "onshape" in rt.gate.services
    client.delete("/keys/onshape", headers=AUTH)
    assert "onshape" not in rt.gate.services


def _install_fake_onshape(rt, client_keys=("AK", "SK")):
    fake = FakeOnshape("AK", "SK")
    c = OnshapeClient(*client_keys, store=rt.store, user="local", transport=fake.transport)
    rt.gate.services["onshape"] = OnshapeAdapter(c, rt.store)
    return fake


def test_onshape_test_one_ledgered_call(client, rt):
    fake = _install_fake_onshape(rt)
    r = client.post("/onshape/test", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ok": True, "user_name": "Ada Lovelace"}
    assert fake.requests == [("GET", "/api/v17/users/sessioninfo")]
    assert rt.store.ledger_used("local", since=0) == 1


def test_onshape_test_wrong_keys_denied_single_call(client, rt):
    _install_fake_onshape(rt, client_keys=("AK", "WRONG"))
    r = client.post("/onshape/test", headers=AUTH)
    assert r.json() == {"ok": False, "error": "denied"}
    # exactly one ledgered request (settled 401, so Onshape does not count it); no retry
    rows = rt.store._all("SELECT endpoint, status_code, state FROM ledger")
    assert [tuple(r) for r in rows] == [("GET /api/v17/users/sessioninfo", 401, "settled")]


def test_onshape_test_not_configured(client):
    r = client.post("/onshape/test", headers=AUTH)
    assert r.json() == {"ok": False, "error": "not_configured"}


def test_setup_and_complete(client, kc, rt):
    s = client.get("/setup", headers=AUTH).json()
    assert s["complete"] is False and "profile" in s["missing"]
    client.post("/keys/anthropic", headers=AUTH, json={"secret": SECRET})
    assert client.get("/setup", headers=AUTH).json()["has_model"] is True
    r = client.post("/setup/complete", headers=AUTH)
    assert r.status_code == 200 and r.json()["complete"] is True
    assert load_profile(rt.home).setup_completed_at is not None


def test_setup_uses_injected_claude_status(rt, kc):
    async def signed_in():
        return True
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP, claude_status=signed_in)) as c:
        assert c.get("/setup", headers=AUTH).json()["has_model"] is True


ROUTES = [("GET", "/profile"), ("PUT", "/profile"), ("GET", "/keys"), ("POST", "/keys/openai"),
          ("DELETE", "/keys/openai"), ("POST", "/onshape/test"), ("GET", "/setup"),
          ("POST", "/setup/complete")]


@pytest.mark.parametrize("method,path", ROUTES)
def test_mcp_token_forbidden_and_no_token_401(client, method, path):
    r = client.request(method, path, headers=MCP_AUTH, json={})
    assert r.status_code == 403
    assert client.request(method, path, json={}).status_code == 401


def test_cors_allows_put_and_delete(client):
    for path, method in (("/profile", "PUT"), ("/keys/openai", "DELETE")):
        r = client.options(path, headers={"Origin": "tauri://localhost",
                                          "Access-Control-Request-Method": method})
        assert r.status_code == 200 and method in r.headers["access-control-allow-methods"]


async def test_runtime_uses_profile_caps_and_defaults(rt, fake):
    from mises.profile import save_profile
    save_profile(rt.home, Profile(onshape_calls_run_cap=7, onshape_calls_year_cap=70,
                                  default_model="ollama/qwen3"))
    refs = []
    rt.provider_resolver = lambda ref: (refs.append(ref), (fake, "m"))[1]
    rid = await rt.start_run("designer", "hi")
    await rt.wait(rid)
    caps = rt.store.current_policy(rid).caps
    assert (caps.onshape_calls_run, caps.onshape_calls_year) == (7, 70)
    assert refs == ["ollama/qwen3"]
    rid = await rt.start_run("designer", "hi", caps=Caps(onshape_calls_run=3),
                             model_ref="anthropic/x")
    await rt.wait(rid)
    assert rt.store.current_policy(rid).caps.onshape_calls_run == 3 and refs[-1] == "anthropic/x"


def test_post_run_defaults_agent_from_profile(client):
    r = client.post("/runs", headers=AUTH, json={"prompt": "hi"})
    assert r.status_code == 200


def test_put_profile_accepts_unchanged_setup_completed_at(client, rt):
    client.post("/setup/complete", headers=AUTH)
    full = client.get("/profile", headers=AUTH).json()
    full["display_name"] = "Ada"
    r = client.put("/profile", headers=AUTH, json=full)        # the UI sends the whole profile
    assert r.status_code == 200 and r.json()["display_name"] == "Ada"
    r = client.put("/profile", headers=AUTH, json={"setup_completed_at": 5})
    assert r.status_code == 422
    r = client.put("/profile", headers=AUTH, json={"setup_completed_at": None})
    assert r.status_code == 422
    assert load_profile(rt.home).setup_completed_at == full["setup_completed_at"]


def test_put_profile_unchanged_null_setup_completed_at(client):
    full = client.get("/profile", headers=AUTH).json()
    assert full["setup_completed_at"] is None
    assert client.put("/profile", headers=AUTH, json=full).status_code == 200


def test_setup_complete_without_a_model(kc, rt):
    async def nothing_local(urls):
        return False
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP, local_probe=nothing_local)) as c:
        body = c.post("/setup/complete", headers=AUTH).json()
    assert body["complete"] is True and body["has_model"] is False and "model" in body["missing"]


def test_local_test_route(rt, kc):
    seen = []

    async def probe(urls):
        seen.append(list(urls))
        return urls == ["http://127.0.0.1:11434/v1"]
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP, local_probe=probe)) as c:
        r = c.post("/local/test", headers=AUTH, json={"url": "http://127.0.0.1:11434/v1"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        r = c.post("/local/test", headers=AUTH, json={"url": "http://localhost:1234/v1"})
        assert r.json() == {"ok": False, "error": "unreachable"}
        for bad in ({"url": "http://evil.example.com/v1"}, {"url": "file:///etc/passwd"},
                    {"url": 5}, {}, [1], {"url": "http://localhost/" + "a" * 3000}):
            r = c.post("/local/test", headers=AUTH, json=bad)
            assert r.status_code == 400, bad
        assert c.post("/local/test", headers=MCP_AUTH, json={"url": "http://localhost/v1"}
                      ).status_code == 403
    assert seen == [["http://127.0.0.1:11434/v1"], ["http://localhost:1234/v1"]]
    assert load_profile(rt.home).local_endpoints == {}          # testing never persists


def test_local_test_probe_timeout(rt, kc, monkeypatch):
    import asyncio
    import mises.server.profile_api as papi
    monkeypatch.setattr(papi, "LOCAL_TEST_TIMEOUT_S", 0.2)

    async def slow(urls):
        await asyncio.sleep(5)
        return True
    with TestClient(create_app(rt, TOKEN, mcp_token=MCP, local_probe=slow)) as c:
        r = c.post("/local/test", headers=AUTH, json={"url": "http://localhost:9/v1"})
    assert r.json() == {"ok": False, "error": "unreachable"}


async def test_set_onshape_credentials_survives_aclose_failure(rt):
    class Boom:
        async def aclose(self):
            raise RuntimeError("socket already closed")

    class Ad:
        client = Boom()
    rt.gate.services["onshape"] = Ad()
    await rt.set_onshape_credentials("AK:SK")
    assert isinstance(rt.gate.services["onshape"], OnshapeAdapter)
    await rt.set_onshape_credentials(None)
    assert "onshape" not in rt.gate.services


async def test_model_precedence(rt, fake):
    from mises.agent.definitions import load_definitions
    from mises.profile import save_profile
    from mises.providers.registry import DEFAULT_MODEL
    (rt.home / "agents").mkdir(parents=True, exist_ok=True)
    (rt.home / "agents" / "pinned.md").write_text(
        "---\nname: pinned\ndescription: pinned model\nmodel: openai/gpt-x\n---\nbody\n")
    rt.definitions = load_definitions([rt.home / "agents"])
    refs = []
    rt.provider_resolver = lambda ref: (refs.append(ref), (fake, "m"))[1]
    for agent, model_ref in (("pinned", "anthropic/explicit"), ("pinned", None),
                             ("designer", None)):
        await rt.wait(await rt.start_run(agent, "hi", model_ref=model_ref))
    save_profile(rt.home, Profile(default_model="ollama/qwen3"))
    for agent in ("pinned", "designer"):
        await rt.wait(await rt.start_run(agent, "hi"))
    # model_ref -> the definition's model (unless "inherit") -> profile default -> DEFAULT_MODEL
    assert refs == ["anthropic/explicit", "openai/gpt-x", DEFAULT_MODEL,
                    "openai/gpt-x", "ollama/qwen3"]
