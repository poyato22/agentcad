"""GET /renders/{id}.png, cards surviving a reload (/runs/{id}/steps), and the card contract
fixture shared with the app (tests/fixtures/g1_cards.json, taken from real tool output).

Regenerate the fixture after an intentional contract change with
`MISES_WRITE_FIXTURES=1 uv run pytest tests/server/test_renders_api.py`, then copy it to the
app's fake core so G4's cards are tested against exactly what the core sends."""
import json
import os
from pathlib import Path

import httpx2

from mises.claude_code.session import SOURCE as CLAUDE_CODE

from .conftest import AUTH
from .mcp_helpers import put_part as _put_part, running

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "g1_cards.json"


def _client(app):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=AUTH,
                              base_url="http://127.0.0.1")


async def _call(rt, rid, name, args, cid):
    return await rt.gate.call(rid, name, args, call_id=cid, workdir=rt.workdir(rid))


def _cards(steps):
    return [s["payload"]["agent_announce"] for s in steps if "agent_announce" in s["payload"]]


async def test_render_route_serves_card_thumbnails_and_cards_survive_reload(rt):
    _put_part(rt, "body")
    _put_part(rt, "lid")
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider=CLAUDE_CODE, model="opus")
        await _call(rt, rid, "present_view", {"target": "body", "kind": "orbit",
                                              "caption": "the hull"}, "c1")
        async with _client(app) as http:
            steps = (await http.get(f"/runs/{rid}/steps")).json()
            cards = _cards(steps)
            assert [c["kind"] for c in cards] == ["view_presented"]
            thumb = cards[0]["ref"]["thumbnail_id"]
            r = await http.get(f"/renders/{thumb}.png")
            assert r.status_code == 200 and r.headers["content-type"] == "image/png"
            assert r.content.startswith(b"\x89PNG")
            assert (await http.get("/renders/pv_nope_iso.png")).status_code == 404


async def test_render_route_rejects_bad_ids_links_and_no_auth(rt):
    renders = rt.home / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    secret = rt.home / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (renders / "linked.png").symlink_to(secret)
    async with running(rt) as app:
        async with _client(app) as http:
            for bad in ("bad id.png", "x.svg", "pv_x", "a.b.png", "..%2Fsecret.png"):
                assert (await http.get(f"/renders/{bad}")).status_code in (400, 404), bad
            assert (await http.get("/renders/linked.png")).status_code == 404   # never a link
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://127.0.0.1") as anon:
            assert (await anon.get("/renders/linked.png")).status_code == 401


def _shape(v):
    """The structure of a JSON value: dict keys (recursively), list element shape, value type."""
    if isinstance(v, dict):
        return {k: _shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_shape(v[0])] if v else []
    return type(v).__name__


async def test_card_contract_matches_the_shared_fixture(rt):
    _put_part(rt, "body")
    _put_part(rt, "lid")
    rid = rt.create_external_run("designer", provider=CLAUDE_CODE, model="opus")
    for p in ("body", "lid"):                     # parts belong to the run's build
        rt.store.register_part(p, rt.store.get_run(rid).project_id)
    report = {"score": 0.4, "issues": [{"view": "front", "issue": "flat wing", "severity": "high",
                                        "suggested_fix": "loft NACA sections"}]}
    base = {"part_id": "body", "request": "a B-2 bomber"}
    await _call(rt, rid, "critique_model", base, "k1")
    await _call(rt, rid, "critique_model", {**base, "report": report}, "k2")
    await _call(rt, rid, "critique_model", base, "k3")
    await _call(rt, rid, "critique_model", {**base, "report": {"score": 0.9, "issues": []}}, "k4")
    project = rt.store.get_run(rid).project_id
    await _call(rt, rid, "present_view", {
        "target": {"kind": "project", "id": project, "parts": ["lid"]}, "kind": "subset",
        "camera": {"azimuth": 45, "elevation": 30}, "orbit": {"duration_s": 12, "turns": 1},
        "caption": "the lid"}, "v1")
    steps = [s.model_dump(mode="json") for s in rt.store.steps(rid)]
    cards = _cards(steps)
    got = {"critique_round": [c for c in cards if c["kind"] == "critique_round"][-1],
           "view_presented": [c for c in cards if c["kind"] == "view_presented"][-1]}
    assert got["critique_round"]["ref"]["issues_fixed"][0]["issue"] == "flat wing"
    if os.environ.get("MISES_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    fixture = json.loads(FIXTURE.read_text())
    assert _shape(got) == _shape(fixture), "the card contract changed: regenerate the fixture " \
        "and update the app's fake core"
