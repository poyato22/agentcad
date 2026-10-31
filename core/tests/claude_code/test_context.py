import json
from contextlib import asynccontextmanager

import httpx2

from mises.context_tools import build_context, ui_context
from mises.server.app import create_app

TOKEN = "admin-token-123"
MCP_TOKEN = "mcp-only-456"


@asynccontextmanager
async def client(rt):
    app = create_app(rt, TOKEN, mcp_token=MCP_TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://127.0.0.1") as http:
            yield http


def _part(rt, part_id="mount_plate"):
    d = rt.home / "parts" / part_id
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mass_g": 12.5, "volume_mm3": 4630.0}))
    (d / "vars.json").write_text(json.dumps({"width": 40.0}))


async def test_put_context_sets_selection(rt):
    _part(rt)
    async with client(rt) as http:
        r = await http.put("/context", json={"selected_part_id": "mount_plate",
                                             "selected_run_id": None, "picks": [{"face": 3}]},
                           headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.json()["selected_part_id"] == "mount_plate"
        g = await http.get("/context", headers={"Authorization": f"Bearer {TOKEN}"})
    ctx = g.json()
    assert ctx["selected_part_id"] == "mount_plate" and ctx["picks"] == [{"face": 3}]
    assert ctx["metrics"] == {"mass_g": 12.5, "volume_mm3": 4630.0}
    assert ctx["vars"] == {"width": 40.0}
    assert ui_context(rt).selected_part_id == "mount_plate"


def test_user_edits_are_labelled_untrusted_and_filtered_by_part(rt):
    from mises import edits
    from mises.cad.tools import _seen, _seen_key
    _part(rt)
    edits.log_user_edit(rt.home, "mount_plate", version=2, summary="set width=40", run_id="ui")
    edits.log_user_edit(rt.home, "other_part", version=5, summary="set x=1", run_id="ui")
    ui_context(rt).update(selected_part_id="mount_plate", selected_run_id=None, picks=[])
    ctx = build_context(rt, "run_a")
    assert "recent_user_edits" not in ctx
    got = ctx["untrusted_user_data"]["recent_user_edits"]
    assert [e["part_id"] for e in got] == ["mount_plate"]
    # reading the context counts as having seen the selected part's version
    assert _seen[_seen_key("run_a", rt.home / "parts" / "mount_plate")] == ctx["version"]


def test_selection_from_another_build_does_not_leak(rt):
    # D26: a part and run selected in build A never enter a lead's context in build B; the
    # lead in build A still sees its own selection.
    a = rt.store.create_project("A").id
    b = rt.store.create_project("B").id
    d = rt.home / "parts" / "mount_plate"
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mass_g": 12.5}))
    rt.store.register_part("mount_plate", a, label="Mount")
    run_a = rt.store.create_run("designer", "claude-code", "sonnet", "t", project_id=a).id
    ui_context(rt).update(selected_part_id="mount_plate", selected_run_id=run_a, picks=[])

    lead_b = rt.store.create_run("designer", "claude-code", "sonnet", "t", project_id=b).id
    ctx = build_context(rt, lead_b)
    assert ctx["selected_part_id"] is None and ctx["metrics"] is None
    assert ctx["selected_run_id"] is None

    lead_a = rt.store.create_run("designer", "claude-code", "sonnet", "t", project_id=a).id
    ctx = build_context(rt, lead_a)
    assert ctx["selected_part_id"] == "mount_plate" and ctx["metrics"] == {"mass_g": 12.5}
    assert ctx["selected_run_id"] == run_a


async def test_context_route_needs_admin_token(rt):
    async with client(rt) as http:
        r = await http.put("/context", json={"selected_part_id": "x"},
                           headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        assert r.status_code == 403
        r = await http.put("/context", json={"selected_part_id": "x"})
        assert r.status_code == 401
    assert ui_context(rt).selected_part_id is None


async def test_context_rejects_bad_part_id(rt):
    async with client(rt) as http:
        r = await http.put("/context", json={"selected_part_id": "../../server"},
                           headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code in (400, 422)


async def test_mises_context_get_tool_through_gate(rt):
    _part(rt)
    ui_context(rt).update(selected_part_id="mount_plate", selected_run_id=None, picks=[])
    async with client(rt):                       # registers the tool
        rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
        out = await rt.gate.call(rid, "mises_context_get", {}, call_id="c1",
                                 workdir=rt.workdir(rid))
    assert out.ok and out.data["selected_part_id"] == "mount_plate"
    assert out.data["metrics"]["mass_g"] == 12.5


def test_build_context_doc_url_and_recent_sim_from_selected_run(rt):
    from mises.types import StepKind
    rid = rt.create_external_run("designer", provider="claude-code", model="sonnet")
    url = "https://cad.onshape.com/documents/a1/w/b2/e/c3"
    rt.store.append_step(rid, StepKind.TOOL_CALL, {"call_id": "x", "name": "onshape_doc_open",
                                                   "input": {"url": url}})
    rt.store.append_step(rid, StepKind.SIM_RUN, {"max_stress_mpa": 80})
    ui_context(rt).update(selected_part_id=None, selected_run_id=rid, picks=[])
    ctx = build_context(rt)
    assert ctx["doc_url"] == url and ctx["recent_sim"] == {"max_stress_mpa": 80}
    assert ctx["metrics"] is None and ctx["vars"] is None
