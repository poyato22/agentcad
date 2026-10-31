"""Plan 9 G-D fix round: the build's saved animations over HTTP (Play finds them after a reload),
and a thread step holding a non-finite number never makes /runs/{id}/steps fail."""
import pytest
from starlette.testclient import TestClient

from mises.server.app import create_app
from mises.types import StepKind

from .conftest import AUTH, TOKEN


@pytest.fixture
def client(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        yield c


def test_saved_animations_are_listed_per_build(client, rt):
    step = [{"joint_id": "j", "from": 0, "to": 90, "duration_s": 2}]
    a = rt.assembly_store.save_animation("p", "open", {"steps": step, "loops": 1, "duration_s": 2})
    b = rt.assembly_store.save_animation("p", "shut", {"steps": step, "loops": 2, "duration_s": 2})
    rt.assembly_store.save_animation("other", "theirs", {"steps": step, "loops": 1})
    r = client.get("/projects/p/assembly/animations", headers=AUTH)
    assert r.status_code == 200
    assert [(x["id"], x["name"], x["loops"]) for x in r.json()] == [(a["id"], "open", 1),
                                                                  (b["id"], "shut", 2)]
    assert client.get("/projects/none/assembly/animations", headers=AUTH).json() == []


def test_a_non_finite_number_in_a_step_is_null_not_a_500(client, rt):
    run = rt.store.create_run("designer", "fake", "fake/model", "t")
    rt.store.append_step(run.id, StepKind.DECISION, {"agent_announce": {
        "kind": "view_presented", "ref": {"view_spec": {"section": {"origin": [float("inf"),
                                                                              float("nan"), 1.5]}}}}})
    r = client.get(f"/runs/{run.id}/steps", headers=AUTH)
    assert r.status_code == 200, r.text
    origin = r.json()[-1]["payload"]["agent_announce"]["ref"]["view_spec"]["section"]["origin"]
    assert origin == [None, None, 1.5]
