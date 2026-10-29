from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeWeb:
    """A fake web for the importer: path -> response factory; records every request."""

    def __init__(self) -> None:
        self.pages: dict[str, httpx.Response | callable] = {}
        self.requests: list[httpx.Request] = []
        self.connected_to: list[str] = []
        self.sni: list[str | None] = []
        self.transport = httpx.MockTransport(self.handler)

    def page(self, url: str, body: str | bytes, ctype: str = "text/html; charset=utf-8",
             headers: dict | None = None) -> None:
        content = body.encode() if isinstance(body, str) else body
        self.pages[url] = lambda req: httpx.Response(
            200, content=content, headers={"Content-Type": ctype, **(headers or {})})

    def fixture(self, url: str, name: str) -> None:
        self.page(url, (FIXTURES / name).read_text())

    def redirect(self, url: str, to: str, status: int = 302, headers: dict | None = None) -> None:
        self.pages[url] = lambda req: httpx.Response(status, headers={"Location": to,
                                                                      **(headers or {})})

    def handler(self, req: httpx.Request) -> httpx.Response:
        # the importer connects to the vetted IP and names the site in Host (and TLS SNI)
        self.requests.append(req)
        self.connected_to.append(req.url.host)
        self.sni.append(req.extensions.get("sni_hostname"))
        key = str(req.url.copy_with(query=None, host=req.headers["host"]))
        fn = self.pages.get(key)
        return fn(req) if fn else httpx.Response(404)


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def public_dns():
    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]
    return resolve
