import asyncio
from types import SimpleNamespace

import pytest

from mises.approvals import ApprovalService
from mises.catalog import Catalog
from mises.catalog.tools import register_catalog_tools
from mises.events import EventBus
from mises.gate import Gate
from mises.profile import Profile, save_profile
from mises.store import Store
from mises.tools.registry import ToolRegistry
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C, StepKind

URL = "https://shop.example.com/p/falcon"


class Env:
    def __init__(self, tmp_path, web, resolve, scope=("*",)):
        self.store = Store(":memory:")
        self.run = self.store.create_run("designer", "fake", "m", "t")
        self.store.create_policy(self.run.id, list(scope), list(scope), Caps(),
                                 [C.READ, C.LOCAL_MUTATE])
        self.bus = EventBus()
        self.reg = ToolRegistry()
        register_catalog_tools(self.reg)
        self.catalog = Catalog(self.store)
        self.approvals = ApprovalService(self.store, self.bus)
        # just enough of a Runtime: the profile's home and the app's own UI calls
        self.runtime = SimpleNamespace(home=tmp_path, ui_calls={})
        self.gate = Gate(self.reg, self.store, self.approvals, self.bus, user="me",
                         services={"catalog": self.catalog, "runtime": self.runtime,
                                   "catalog_http": {"transport": web.transport,
                                                    "resolve": resolve}})
        self.tmp = tmp_path
        (tmp_path / "parts").mkdir(exist_ok=True)
        self.n = 0

    async def call(self, name, inp, *, ui_meta=None):
        self.n += 1
        cid = f"c{self.n}"
        if ui_meta is not None:
            self.runtime.ui_calls[cid] = SimpleNamespace(run_id=self.run.id, meta=ui_meta)
        return await self.gate.call(self.run.id, name, inp, call_id=cid, workdir=self.tmp)

    async def next_card(self):
        for _ in range(500):
            pending = self.store.pending_approvals(self.run.id)
            if pending:
                return pending[0]
            await asyncio.sleep(0.01)
        raise AssertionError("no approval card")

    def decide(self, card, approve=True):
        self.approvals.decide(card.id, card.payload_hash, approve, decided_by="local-user")


@pytest.fixture
def env(tmp_path, web, public_dns):
    return Env(tmp_path, web, public_dns)


async def approved(env, coro_fn):
    task = asyncio.create_task(coro_fn())
    card = await env.next_card()
    env.decide(card)
    return await task, card


def test_classes_and_flags():
    reg = ToolRegistry()
    register_catalog_tools(reg)
    assert {t.name: t.safety for t in reg.list()} == {
        "catalog_search": C.READ, "catalog_get": C.READ,
        "catalog_import_url": C.LOCAL_MUTATE, "catalog_insert": C.LOCAL_MUTATE}
    imp = reg.get("catalog_import_url")
    assert imp.needs_approval is not None and imp.open_world
    assert reg.get("catalog_insert").needs_approval is None


async def test_search_and_get_builtin(env):
    out = await env.call("catalog_search", {"q": "2207"})
    assert out.data["items"][0]["id"] == "motor_2207_class"
    assert out.data["items"][0]["mass_source"] == "catalog"
    got = await env.call("catalog_get", {"item_id": "motor_2207_class"})
    assert got.data["item"]["mount"]["pattern"] == "16x16"
    assert "untrusted_page_data" not in got.data


async def test_agent_import_without_a_card(env, web):
    # E31: an agent imports from a public https page itself; query/fragment dropped, no card
    web.fixture(URL, "injection.html")
    out = await env.call("catalog_import_url", {"url": URL + "?d=c2VjcmV0#frag"})
    assert env.store.pending_approvals(env.run.id) == []
    assert [r.url.query for r in web.requests] == [b""]
    assert out.data["domain"] == "shop.example.com"
    assert "IGNORE" not in out.summary and "exfiltrate" not in out.summary
    assert "name" not in out.data["item"] and "raw_text" not in out.data["item"]
    block = out.data["untrusted_page_data"]
    assert block.startswith('<untrusted_page domain="shop.example.com" nonce="')
    assert block.count("</untrusted_page") == 1 and "IGNORE ALL" in block
    calls = env.store.steps(env.run.id, [StepKind.TOOL_CALL])
    assert URL in str(calls[-1].payload)
    hits = await env.call("catalog_search", {"q": "exfiltrate"})
    assert hits.data["items"][0]["source"] == "url"
    assert "exfiltrate" in hits.data["untrusted_page_data"]
    assert "exfiltrate" not in hits.summary


async def test_exfil_shaped_import_is_refused_without_a_card(env, web):
    # browsing never asks the user: a link that looks like it carries data is refused, unfetched
    bad = "https://shop.example.com/" + "8f14e45fceea167a5a36" + "/x"
    web.fixture(bad, "table_motor.html")
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": bad})
    assert e.value.code == ErrorCode.INVALID_INPUT and web.requests == []
    assert env.store.pending_approvals(env.run.id) == []


async def test_agent_import_redirect_off_site_is_vetted_like_a_link(env, web):
    # M4: an agent's import follows a redirect to another site only when the target passes
    # the link guard (test_import_resilience covers an ordinary target being followed)
    web.redirect(URL, "https://elsewhere.example.net/p/8f14e45fceea167a5a36")
    web.fixture("https://elsewhere.example.net/p/8f14e45fceea167a5a36", "table_motor.html")
    with pytest.raises(MisesError):
        await env.call("catalog_import_url", {"url": URL})
    assert {r.headers["host"] for r in web.requests} == {"shop.example.com"}


async def test_allowlisted_domain_asks_no_card(env, web):
    save_profile(env.tmp, Profile(catalog_allowed_domains=["Shop.Example.com"]))
    web.fixture(URL, "table_motor.html")
    out = await asyncio.wait_for(env.call("catalog_import_url", {"url": URL}), 5)
    assert out.data["existing"] is False and env.store.pending_approvals(env.run.id) == []


async def test_ui_import_asks_no_card_and_keeps_the_query(env, web):
    web.fixture(URL, "table_motor.html")
    full = URL + "?variant=2207"
    out = await asyncio.wait_for(env.call("catalog_import_url", {"url": full},
                                          ui_meta={"url": full, "project_id": "prj"}), 5)
    assert web.requests[0].url.query == b"variant=2207"
    assert env.catalog.get(out.data["item"]["id"])["project_id"] == "prj"


async def test_reimport_keeps_user_edits_and_build(env, web):
    web.fixture(URL, "table_motor.html")
    first = await env.call("catalog_import_url", {"url": URL}, ui_meta={"url": URL,
                                                                        "project_id": "prj"})
    iid = first.data["item"]["id"]
    env.catalog.update(iid, {"name": "My corrected motor"})
    # an agent in another run/build imports the same page: no card, no fetch, nothing changes
    out = await asyncio.wait_for(env.call("catalog_import_url", {"url": URL}), 5)
    assert out.data["existing"] is True and len(web.requests) == 1
    kept = env.catalog.get(iid)
    assert kept["name"] == "My corrected motor" and kept["project_id"] == "prj"
    assert kept["edited_by_user"] is True
    # only the app's explicit refresh re-reads it (and keeps its build)
    await env.call("catalog_import_url", {"url": URL}, ui_meta={"url": URL, "refresh": True,
                                                                "project_id": "other"})
    again = env.catalog.get(iid)
    assert len(web.requests) == 2 and again["project_id"] == "prj"
    assert again["name"] != "My corrected motor"


async def test_import_refused_outside_web_scope(tmp_path, web, public_dns):
    env = Env(tmp_path, web, public_dns, scope=("web:other.example.com",))
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": URL})
    assert e.value.code == ErrorCode.OUT_OF_SCOPE and web.requests == []


async def test_import_http_refused(env, web):
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": "http://shop.example.com/p"})
    assert e.value.code == ErrorCode.INVALID_INPUT and web.requests == []


async def test_insert_into_another_build_refused(env, monkeypatch):
    # a run that belongs to a build (C1 adds run.project_id)
    run = env.store.get_run(env.run.id)
    monkeypatch.setattr(type(env.store), "get_run",
                        lambda self, rid: SimpleNamespace(**{**run.model_dump(),
                                                             "project_id": "prj_mine"}))
    with pytest.raises(MisesError) as e:
        await env.call("catalog_insert", {"item_id": "m3_hex_nut", "project_id": "prj_other"})
    assert e.value.code == ErrorCode.OUT_OF_SCOPE
    assert not (env.tmp / "parts" / "m3_hex_nut").exists()


@pytest.mark.slow
async def test_insert_creates_catalog_part_and_event(env):
    sub = env.bus.subscribe()
    out = await env.call("catalog_insert", {"item_id": "fc_stack_30_5_full", "project_id": "prj"})
    assert out.data["part_id"] == "fc_stack_30_5_full" and not out.data["existing"]
    assert out.data["bbox"]["size"] == [pytest.approx(36), pytest.approx(36), pytest.approx(18)]
    assert out.data["mass_source"] == "catalog"
    assert (env.tmp / "parts" / "fc_stack_30_5_full" / "part.json").is_file()
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    pc = [e for e in events if e.type == "part_changed"]
    assert pc and pc[0].data["by"] == "agent" and pc[0].data["part_id"] == "fc_stack_30_5_full"
    again = await env.call("catalog_insert", {"item_id": "fc_stack_30_5_full", "project_id": "prj"})
    assert again.data["existing"]


@pytest.mark.slow
async def test_concurrent_inserts_with_the_same_base_get_different_ids(env):
    for n in ("a", "b"):
        env.catalog.save({"id": f"url_{n * 16}", "source": "url", "name": "Motor",
                          "category": "motor", "url": f"https://s.example.com/{n}", "specs": [],
                          "dims": {"length_mm": 10, "width_mm": 10, "height_mm": 5}, "mount": None})
    a, b = await asyncio.gather(
        env.call("catalog_insert", {"item_id": "url_" + "a" * 16, "project_id": "p"}),
        env.call("catalog_insert", {"item_id": "url_" + "b" * 16, "project_id": "p"}))
    assert a.data["part_id"] != b.data["part_id"]
    assert {a.data["mass_source"], b.data["mass_source"]} == {"estimate"}
