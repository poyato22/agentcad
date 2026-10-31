"""Plan 9 G-B fix round (I3): the user's design class on a build. PATCH /projects/{id}
{design_class} (admin token only; the MCP token an agent holds is refused), the override wins
over the classifier for every run of the build, "auto" hands it back, and agents read it with
the design_class_get tool."""
import pytest
from starlette.testclient import TestClient

from mises.design_tools import DesignClassGetArgs, design_class_get
from mises.server.app import create_app
from mises.tools.registry import ToolContext
from mises.events import EventBus

from .conftest import AUTH, TOKEN

MCP_AUTH = {"Authorization": "Bearer mcp-only-token-456"}


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN, mcp_token="mcp-only-token-456"),
                    base_url="http://127.0.0.1") as c:
        yield c


def _ctx(rt, run_id):
    return ToolContext(run_id=run_id, user="local", store=rt.store, events=EventBus(),
                       workdir=rt.home, policy=None, call_id="c1", services={"runtime": rt})


async def _run(rt, prompt):
    rid = await rt.start_run("designer", prompt, project_id=rt.active_project_id())
    await rt.wait(rid)
    return rid


def test_patch_sets_and_clears_the_override(client, rt):
    pid = rt.active_project_id()
    rid = client.portal.call(_run, rt, "a humanoid robot")
    assert rt.store.get_run(rid).design_class["cls"] == "design"
    r = client.patch(f"/projects/{pid}", headers=AUTH, json={"design_class": "engineering"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["design_class"]["cls"] == "engineering" and body["id"] == pid
    assert rt.store.get_run(rid).design_class["source"] == "user"
    # a new run in the build takes the user's class, whatever its prompt says
    rid2 = client.portal.call(_run, rt, "a sleek premium humanoid robot")
    assert rt.store.get_run(rid2).design_class["cls"] == "engineering"
    # a follow-up cannot move it either
    client.portal.call(rt.send, rid2, "make it beautiful and sculpted")
    client.portal.call(rt.wait, rid2)
    assert rt.store.get_run(rid2).design_class["cls"] == "engineering"
    r = client.patch(f"/projects/{pid}", headers=AUTH, json={"design_class": "auto"})
    assert r.status_code == 200 and r.json()["design_class"] is None
    assert rt.store.get_run(rid).design_class["cls"] == "design"


def test_patch_rejects_bad_values_and_the_agent_token(client, rt):
    pid = rt.active_project_id()
    assert client.patch(f"/projects/{pid}", headers=AUTH,
                        json={"design_class": "pretty"}).status_code == 422
    assert client.patch(f"/projects/{pid}", headers=MCP_AUTH,
                        json={"design_class": "design"}).status_code in (401, 403)
    assert client.patch("/projects/nope", headers=AUTH,
                        json={"design_class": "design"}).status_code == 404


def test_name_only_patch_is_unchanged(client, rt):
    pid = rt.active_project_id()
    r = client.patch(f"/projects/{pid}", headers=AUTH, json={"name": "Renamed"})
    assert r.status_code == 200 and "design_class" not in r.json()


def test_design_class_get_tool(client, rt):
    pid = rt.active_project_id()
    rid = client.portal.call(_run, rt, "jet engine compressor")
    out = client.portal.call(design_class_get.handler, _ctx(rt, rid), DesignClassGetArgs())
    assert out.data["design_class"]["cls"] == "mixed" and out.data["set_by_user"] is False
    assert out.data["knobs"]["critique"]["rounds"] == 3
    client.patch(f"/projects/{pid}", headers=AUTH, json={"design_class": "design"})
    out = client.portal.call(design_class_get.handler, _ctx(rt, rid), DesignClassGetArgs())
    assert out.data["set_by_user"] is True and "set by the user" in out.summary
    assert design_class_get.safety.value == "read"
