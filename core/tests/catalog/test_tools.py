import pytest

from mises.approvals import ApprovalService
from mises.catalog import Catalog
from mises.catalog.tools import register_catalog_tools
from mises.events import EventBus
from mises.gate import Gate
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
        self.gate = Gate(self.reg, self.store, ApprovalService(self.store, self.bus), self.bus,
                         user="me", services={"catalog": self.catalog, "catalog_http": {
                             "transport": web.transport, "resolve": resolve}})
        self.tmp = tmp_path
        (tmp_path / "parts").mkdir(exist_ok=True)
        self.n = 0

    async def call(self, name, inp):
        self.n += 1
        return await self.gate.call(self.run.id, name, inp, call_id=f"c{self.n}", workdir=self.tmp)


@pytest.fixture
def env(tmp_path, web, public_dns):
    return Env(tmp_path, web, public_dns)


def test_classes():
    reg = ToolRegistry()
    register_catalog_tools(reg)
    assert {t.name: t.safety for t in reg.list()} == {
        "catalog_search": C.READ, "catalog_get": C.READ,
        "catalog_import_url": C.LOCAL_MUTATE, "catalog_insert": C.LOCAL_MUTATE}


async def test_search_and_get_builtin(env):
    out = await env.call("catalog_search", {"q": "2207"})
    assert out.data["items"][0]["id"] == "motor_2207_class"
    got = await env.call("catalog_get", {"item_id": "motor_2207_class"})
    assert got.data["item"]["mount"]["pattern"] == "16x16"
    assert "untrusted_page_data" not in got.data


async def test_import_injection_page_reaches_agent_only_as_delimited_data(env, web):
    web.fixture(URL, "injection.html")
    out = await env.call("catalog_import_url", {"url": URL})
    assert out.data["domain"] == "shop.example.com"
    assert "IGNORE" not in out.summary and "exfiltrate" not in out.summary
    assert "name" not in out.data["item"] and "raw_text" not in out.data["item"]
    block = out.data["untrusted_page_data"]
    assert block.startswith('<untrusted_page domain="shop.example.com" nonce="')
    assert block.count("</untrusted_page") == 1 and "IGNORE ALL" in block
    # the Gate's tool-call step shows the domain the agent asked for
    calls = env.store.steps(env.run.id, [StepKind.TOOL_CALL])
    assert URL in str(calls[-1].payload)
    # and the item is now searchable, still wrapped
    hits = await env.call("catalog_search", {"q": "exfiltrate"})
    assert hits.data["items"][0]["source"] == "url"
    assert "exfiltrate" in hits.data["untrusted_page_data"]
    assert "exfiltrate" not in hits.summary


async def test_import_refused_outside_web_scope(tmp_path, web, public_dns):
    env = Env(tmp_path, web, public_dns, scope=("web:other.example.com",))
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": URL})
    assert e.value.code == ErrorCode.OUT_OF_SCOPE and web.requests == []


async def test_import_http_refused(env, web):
    with pytest.raises(MisesError) as e:
        await env.call("catalog_import_url", {"url": "http://shop.example.com/p"})
    assert e.value.code == ErrorCode.INVALID_INPUT and web.requests == []


@pytest.mark.slow
async def test_insert_creates_catalog_part_and_event(env):
    sub = env.bus.subscribe()
    out = await env.call("catalog_insert", {"item_id": "fc_stack_30_5_full", "project_id": "prj"})
    assert out.data["part_id"] == "fc_stack_30_5_full" and not out.data["existing"]
    assert out.data["bbox"]["size"] == [pytest.approx(36), pytest.approx(36), pytest.approx(18)]
    assert (env.tmp / "parts" / "fc_stack_30_5_full" / "part.json").is_file()
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    pc = [e for e in events if e.type == "part_changed"]
    assert pc and pc[0].data["by"] == "agent" and pc[0].data["part_id"] == "fc_stack_30_5_full"
    again = await env.call("catalog_insert", {"item_id": "fc_stack_30_5_full", "project_id": "prj"})
    assert again.data["existing"]
