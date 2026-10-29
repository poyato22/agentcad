"""Write real server fixtures for the app's Onshape browser, budget meter and parts directory
(Plan 5 Task A6 contract tests).

Builds a throwaway Runtime (temp MISES_HOME) whose Onshape adapter talks to the core tests'
fake Onshape server and whose catalog importer reads the core tests' fixture pages through a
mock transport, so nothing touches the network or the user's real home. It records what the
real routes return:

    uv run python scripts/dump_onshape_catalog_fixtures.py --out tests/fixtures/ui

onshape_*.json, catalog_*.json, onshape_thumbnail.png and onshape_events.jsonl (the
`onshape_budget` events that followed the requests).
"""
import argparse
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

TESTS = Path(__file__).resolve().parents[1] / "tests"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, TESTS / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app

    runtime_mod.keychain_get = lambda *a: None
    fake_onshape = _load("onshape/fake_onshape.py", "_fake_onshape")
    web_mod = _load("catalog/conftest.py", "_catalog_conftest")

    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-a6-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))

    onshape = fake_onshape.FakeOnshape("AK", "SK")
    names = ["Quad frame 5in", "Motor mount", "Battery strap"]
    onshape.add_docs(len(names))
    for doc, name in zip(onshape.docs, names):
        doc["name"] = name
    # the first document is the fake's own (its Part Studio + Assembly live there)
    onshape.docs[0]["id"] = onshape.did
    onshape.docs[0]["defaultWorkspace"]["id"] = onshape.main
    for s in onshape.docs[0]["thumbnail"]["sizes"]:
        s["href"] = (f"https://{onshape.host}/api/thumbnails/d/{onshape.did}/w/{onshape.main}"
                     f"/s/{s['size']}")
    ad = rt._onshape_adapter("AK:SK")
    ad.client._http = httpx.AsyncClient(transport=onshape.transport, follow_redirects=False)
    rt.gate.services["onshape"] = ad

    web = web_mod.FakeWeb()
    motor_url = "https://shop.example.com/p/falcon-2207"
    inject_url = "https://shop.example.com/p/sketchy"
    web.page(motor_url, (TESTS / "catalog/fixtures/table_motor.html").read_text())
    web.page(inject_url, (TESTS / "catalog/fixtures/injection.html").read_text())

    async def resolve(host):
        return ["93.184.216.34"]
    rt.gate.services["catalog_http"] = {"transport": web.transport, "resolve": resolve}

    token = "fixture-token"
    auth = {"Authorization": f"Bearer {token}"}
    sub = rt.events.subscribe()
    try:
        transport = httpx.ASGITransport(app=create_app(rt, token))
        async with httpx.AsyncClient(transport=transport, base_url="http://core",
                                     timeout=300) as c:
            async def call(method: str, path: str, body=None, params=None):
                r = await c.request(method, path, headers=auth, json=body, params=params)
                r.raise_for_status()
                return r

            def write(name: str, data) -> None:
                (out / name).write_text(json.dumps(data, indent=2) + "\n")

            out.mkdir(parents=True, exist_ok=True)
            project_id = (await call("POST", "/projects", {"name": "Quad"})).json()["id"]

            docs = (await call("GET", "/onshape/documents", params={"limit": 20})).json()
            write("onshape_documents.json", docs)
            write("onshape_elements.json", (await call(
                "GET", f"/onshape/documents/{onshape.did}/elements",
                params={"wid": onshape.main})).json())
            thumb = await call("GET", docs[0]["thumbnail_url"])
            (out / "onshape_thumbnail.png").write_bytes(thumb.content)
            write("onshape_import.json", (await call("POST", "/onshape/import", {
                "did": onshape.did, "wid": onshape.main, "eid": onshape.eid,
                "project_id": project_id})).json())
            write("onshape_budget.json", (await call("GET", "/onshape/budget")).json())

            write("catalog_categories.json", (await call("GET", "/catalog/categories")).json())
            write("catalog_search.json", (await call(
                "GET", "/catalog/search", params={"category": "motor"})).json())
            write("catalog_all.json", (await call(
                "GET", "/catalog/search", params={"limit": 200})).json())
            write("catalog_item.json", (await call("GET", "/catalog/items/motor_2207_class")).json())
            imp = (await call("POST", "/catalog/import", {"url": motor_url,
                                                          "project_id": project_id})).json()
            write("catalog_import.json", imp)
            write("catalog_import_injection.json", (await call(
                "POST", "/catalog/import", {"url": inject_url})).json())
            write("catalog_patch.json", (await call(
                "PATCH", f"/catalog/items/{imp['id']}", {"name": "Falcon 2207 1950KV",
                                                         "vendor": "Example Hobby"})).json())
            write("catalog_insert.json", (await call(
                "POST", "/catalog/items/motor_2207_class/insert",
                {"project_id": project_id})).json())
            write("catalog_allowed_domains_add.json", (await call(
                "POST", "/catalog/allowed_domains", {"domain": "shop.example.com"})).json())
            write("catalog_allowed_domains.json",
                  (await call("GET", "/catalog/allowed_domains")).json())
            write("catalog_allowed_domains_remove.json", (await call(
                "DELETE", "/catalog/allowed_domains/shop.example.com")).json())
            inj_id = json.loads((out / "catalog_import_injection.json").read_text())["id"]
            write("catalog_delete.json", (await call("DELETE", f"/catalog/items/{inj_id}")).json())

        events = []
        while not sub.queue.empty():
            ev = sub.queue.get_nowait()
            if ev.type == "onshape_budget":
                events.append(ev.model_dump_json())
        (out / "onshape_events.jsonl").write_text("".join(e + "\n" for e in events))
    finally:
        sub.close()
        await rt.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(dump(args.out))
    print(f"wrote fixtures to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
