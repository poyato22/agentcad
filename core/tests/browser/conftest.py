import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from mises.approvals import ApprovalService
from mises.bom import BomStore
from mises.bom.tools import register_bom_tools
from mises.browser.snapshot import SnapshotStore
from mises.browser.tools import CardStore, register_browser_tools
from mises.catalog import Catalog
from mises.events import EventBus
from mises.gate import Gate
from mises.store import Store
from mises.tools.registry import ToolRegistry
from mises.types import Caps, SafetyClass as C

FIXTURES = Path(__file__).parent / "fixtures"


class FakeWeb:
    """A fake web: url (no query) -> response factory; records every request. Nothing here
    ever touches the network."""

    def __init__(self) -> None:
        self.pages: dict[str, callable] = {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handler)

    def page(self, url: str, body: str | bytes, ctype: str = "text/html; charset=utf-8",
             status: int = 200, headers: dict | None = None) -> None:
        content = body.encode() if isinstance(body, str) else body
        self.pages[url] = lambda req: httpx.Response(
            status, content=content, headers={"Content-Type": ctype, **(headers or {})})

    def fixture(self, url: str, name: str) -> None:
        self.page(url, (FIXTURES / name).read_text())

    def redirect(self, url: str, to: str, status: int = 302) -> None:
        self.pages[url] = lambda req: httpx.Response(status, headers={"Location": to})

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        key = str(req.url.copy_with(query=None, host=req.headers["host"]))
        fn = self.pages.get(key)
        return fn(req) if fn else httpx.Response(404)

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def public_dns():
    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]
    return resolve


class Env:
    """A Gate with the browser, BOM and catalog tools and just enough of a Runtime."""

    def __init__(self, tmp_path, web, resolve, *, project="p1"):
        self.store = Store(":memory:")
        self.project = self.store.create_project("Build").id if project else None
        self.run = self.store.create_run("engineer", "fake", "m", "t")
        if self.project:
            self.store.update_run(self.run.id, project_id=self.project)
        self.store.create_policy(self.run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])
        self.bus = EventBus()
        self.events = self.bus.subscribe()
        self.reg = ToolRegistry()
        register_browser_tools(self.reg)
        register_bom_tools(self.reg)
        self.catalog = Catalog(self.store)
        self.snaps = SnapshotStore(self.store)
        self.cards = CardStore(self.store)
        self.bom = BomStore(self.store)
        self.approvals = ApprovalService(self.store, self.bus)
        self.runtime = SimpleNamespace(home=tmp_path, ui_calls={},
                                       active_project_id=lambda: self.project)
        self.gate = Gate(self.reg, self.store, self.approvals, self.bus, user="me",
                         services={"catalog": self.catalog, "runtime": self.runtime,
                                   "browser": self.snaps, "part_cards": self.cards,
                                   "bom": self.bom,
                                   "browser_http": {"transport": web.transport,
                                                    "resolve": resolve}})
        self.tmp = tmp_path
        (tmp_path / "parts").mkdir(exist_ok=True)
        self.n = 0

    async def call(self, name, inp):
        self.n += 1
        return await self.gate.call(self.run.id, name, inp, call_id=f"c{self.n}",
                                    workdir=self.tmp)

    async def next_card(self):
        for _ in range(500):
            pending = self.store.pending_approvals(self.run.id)
            if pending:
                return pending[0]
            await asyncio.sleep(0.01)
        raise AssertionError("no approval card")

    def decide(self, card, approve=True):
        self.approvals.decide(card.id, card.payload_hash, approve, decided_by="local-user")

    def drained(self, type_=None):
        out = []
        while not self.events.queue.empty():
            e = self.events.queue.get_nowait()
            if type_ is None or e.type == type_:
                out.append(e)
        return out


@pytest.fixture
def env(tmp_path, web, public_dns):
    return Env(tmp_path, web, public_dns)
