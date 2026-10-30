"""Write real server fixtures for the app's agent Browser tab, part cards, parts list (BOM)
and suggested-sites editor (Plan 7 U4a contract tests).

Builds a throwaway Runtime (temp MISES_HOME) whose agent browser reads the core tests' fixture
pages (tests/browser/fixtures) through a mock transport, so nothing touches the network or the
user's real home. An agent run searches Adafruit, opens the servo product page, is refused by a
captcha page, and shows two part cards (from the page, and from a parts-directory item). The
build has one built part (an automatic, estimated row) plus user rows in USD, EUR and an
unknown currency, one named like a spreadsheet formula. It records what the real routes return:

    uv run python scripts/dump_browser_bom_fixtures.py --out ../app/test/fixtures/core-ui

browser_list.json, browser_list_full.json, browser_snapshot.json, browser_search_snapshot.json,
browser_card.json, browser_cards.json, part_show_result.json, bom.json, bom.csv, bom_add.json,
bom_patch.json, sites_defaults.json, sites_restore.json, profile_browser.json and bom_events.jsonl.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1] / "tests"
ADA = "https://www.adafruit.com"
PRODUCT = f"{ADA}/product/169"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, TESTS / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def dump(out: Path) -> None:
    import mises.runtime as runtime_mod
    from fastapi.testclient import TestClient
    from mises.providers.base import Done, TextDelta
    from mises.providers.fake import FakeProvider
    from mises.server.app import create_app
    from mises.types import Caps, SafetyClass as C

    runtime_mod.keychain_get = lambda *a: None
    web_mod = _load("browser/conftest.py", "_browser_conftest")

    home = Path(tempfile.mkdtemp(prefix="mises-fixtures-u4a-"))
    os.environ["MISES_HOME"] = str(home)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    rt = runtime_mod.Runtime(home, provider_resolver=lambda ref: (fake, "fake-model"))
    web = web_mod.FakeWeb()

    async def resolve(host):
        return ["93.184.216.34"]
    rt.gate.services["browser_http"] = {"transport": web.transport, "resolve": resolve}

    sub = rt.events.subscribe()            # every event from here on (drained at the end)

    project = rt.store.ensure_default_project().id
    run = rt.store.create_run("engineer", "fake", "m", "robot arm")
    rt.store.update_run(run.id, project_id=project)
    rt.store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])

    async def call(name, args, cid):
        o = await rt.gate.call(run.id, name, args, call_id=cid, workdir=rt.home)
        if not o.ok:
            raise SystemExit(f"{name} failed: {o.summary}")
        return o

    web.fixture(f"{ADA}/search", "search_adafruit.html")
    await call("web_search", {"query": "micro servo", "sites": ["Adafruit"]}, "c1")
    web.fixture(PRODUCT, "product_servo.html")
    opened = await call("web_open", {"url": PRODUCT}, "c2")
    sid = opened.data["snapshot_id"]
    web.fixture("https://www.servocity.com/wall", "captcha.html")
    await rt.gate.call(run.id, "web_open", {"url": "https://www.servocity.com/wall"},
                       call_id="c3", workdir=rt.home)       # blocked: a real "blocked" row
    shown = await call("part_show", {"snapshot_id": sid}, "c4")
    cat = rt.catalog.search("servo")[0]["id"]
    await call("part_show", {"catalog_id": cat, "price": 12.5, "currency": "EUR"}, "c5")

    # a built part: its automatic row carries the live estimate
    d = home / "parts" / "arm_bracket"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"volume_mm3": 10_000.0, "mass_g": 12.4,
                                                "bbox": {"size": [40.0, 20.0, 12.5]}}))
    rt.store.register_part("arm_bracket", project, label="Arm bracket", kind="part")

    with TestClient(create_app(rt, "tok")) as c:
        h = {"Authorization": "Bearer tok"}

        def get(path):
            r = c.get(path, headers=h)
            r.raise_for_status()
            return r

        def write(name, data):
            (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

        bom = f"/projects/{project}/bom"
        add = c.post(bom, headers=h, json={"kind": "bought", "name": "Micro servo SG92R",
                                           "qty": 4, "unit_cost": 5.95, "currency": "USD",
                                           "vendor": "Adafruit", "url": PRODUCT,
                                           "specs": [{"key": "mass", "value": 9, "unit": "g"}]})
        add.raise_for_status()
        c.post(bom, headers=h, json={"name": "Bearing 608ZZ", "qty": 2, "unit_cost": 1.2,
                                     "currency": "EUR", "vendor": "Misumi"}).raise_for_status()
        c.post(bom, headers=h, json={"name": "=HYPERLINK(\"https://evil.example\")",
                                     "unit_cost": 3.0, "vendor": "Shop"}).raise_for_status()
        c.post(bom, headers=h, json={"name": "M3 screws", "qty": 20}).raise_for_status()
        patched = c.patch(f"{bom}/{add.json()['id']}", headers=h,
                          json={"qty": 6, "notes": "two spares"})
        patched.raise_for_status()
        bad = c.post(bom, headers=h, json={"name": "x", "snapshot_id": sid})

        write("browser_list.json", get(f"/runs/{run.id}/browser").json())
        full = get(f"/runs/{run.id}/browser?full=1").json()
        write("browser_list_full.json", full)
        write("browser_snapshot.json", get(f"/browser/snapshots/{sid}").json())
        search = next(s for s in full if s["kind"] == "search")
        write("browser_search_snapshot.json", get(f"/browser/snapshots/{search['id']}").json())
        write("part_show_result.json", shown.data)
        write("browser_card.json", get(f"/browser/cards/{shown.data['card_id']}").json())
        write("browser_cards.json", get(f"/runs/{run.id}/part_cards").json())
        write("bom.json", get(bom).json())
        (out / "bom.csv").write_bytes(get(f"{bom}.csv").content)
        write("bom_add.json", add.json())
        write("bom_patch.json", patched.json())
        write("bom_add_extra_field.json", {"status": bad.status_code, "body": bad.json()})
        write("sites_defaults.json", get("/browser/sites/defaults").json())
        rs = c.post("/browser/sites/restore_defaults", headers=h)
        rs.raise_for_status()
        write("sites_restore.json", rs.json())
        write("profile_browser.json", get("/profile").json())

    events = []
    while not sub.queue.empty():
        e = sub.queue.get_nowait()
        events.append({"type": e.type, "run_id": e.run_id, "data": e.data})
    keep = {"bom_changed", "part_shown"}
    with (out / "bom_events.jsonl").open("w") as f:
        for e in events:
            if e["type"] in keep:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote browser/BOM fixtures to {out} ({project=}, run={run.id})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(dump(a.out))


if __name__ == "__main__":
    sys.exit(main())
