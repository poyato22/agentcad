"""Plan 5 final fix I5: the app's own ui run id is never shown to an agent (user-edit context,
selected run, tool results), and the MCP endpoint refuses to act as any ui run."""
import httpx2
from pydantic import BaseModel

from mises import edits
from mises.context_tools import build_context, ui_context
from mises.tools.registry import tool
from mises.types import SafetyClass, ToolOutput

from .conftest import AUTH
from .test_mcp import running

INIT = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
HDRS = {**AUTH, "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}


async def test_mcp_refuses_every_ui_run(rt):
    pid = rt.active_project_id()
    build_ui = rt._ui_run_id(pid)               # the per-build ui run (running, external)
    global_ui = rt._ui_run_id()
    studies = rt.create_external_run("studies", provider="ui", model="studies-page")
    async with running(rt) as app:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://127.0.0.1") as http:
            for rid in (build_ui, global_ui, studies):
                assert rt.store.get_run(rid).status == "running"
                r = await http.post("/mcp", json=INIT, headers={**HDRS, "X-Mises-Run": rid})
                assert r.status_code == 403 and r.json()["error"] == "denied"
            own = rt.create_external_run("codex", provider="acp", model="m")
            r = await http.post("/mcp", json=INIT, headers={**HDRS, "X-Mises-Run": own})
            assert r.status_code != 403


def test_user_edit_context_has_no_run_id(rt):
    ui = rt._ui_run_id(rt.active_project_id())
    edits.log_user_edit(rt.home, "mount_plate", version=2, summary="set width=40", run_id=ui,
                        project_id=rt.active_project_id())
    agent = rt.create_external_run("codex", provider="acp", model="m")
    ctx = build_context(rt, agent)
    got = ctx["untrusted_user_data"]["recent_user_edits"]
    assert len(got) == 1 and "run_id" not in got[0]
    assert ui not in str(ctx)


def test_selected_ui_run_is_not_named_in_context(rt):
    ui = rt._ui_run_id(rt.active_project_id())
    ui_context(rt).update(selected_part_id=None, selected_run_id=ui, picks=[])
    agent = rt.create_external_run("codex", provider="acp", model="m")
    assert build_context(rt, agent)["selected_run_id"] is None
    other = rt.create_external_run("worker", provider="acp", model="m")
    ui_context(rt).update(selected_part_id=None, selected_run_id=other, picks=[])
    assert build_context(rt, agent)["selected_run_id"] == other


class _In(BaseModel):
    pass


async def test_gate_scrubs_ui_run_ids_from_agent_results_only(rt):
    ui = rt._ui_run_id(rt.active_project_id())

    @tool("echo_ui_run", description="d", safety=SafetyClass.READ)
    async def echo(ctx, args: _In) -> ToolOutput:
        return ToolOutput(summary=f"study started by run {ui}", data={"run_id": ui})
    rt.registry.register(echo)

    agent = rt.create_external_run("codex", provider="acp", model="m")
    out = await rt.gate.call(agent, "echo_ui_run", {}, call_id="c1", workdir=rt.workdir(agent))
    assert ui not in out.model_dump_json() and out.data["run_id"] == "user"
    # the app's own ui run still sees its own id
    mine = await rt.gate.call(ui, "echo_ui_run", {}, call_id="c2", workdir=rt.workdir(ui))
    assert mine.data["run_id"] == ui
