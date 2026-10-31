"""Plan 9 G-B x G-A, end to end on the real core: a build's lead is classified from the user's
own prompt (G-B stores `design_class` on the run), and G-A's concept gate reads it. A
design-class build refuses `mirror_part_create` of a new part until the concept is approved;
an engineering build never does; the user's class on the build (PATCH /projects/{id})
overrides the classifier both ways."""
import pytest
from fastapi.testclient import TestClient

import mises.runtime as runtime_mod
from mises.providers.base import Done, TextDelta
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.server.app import create_app
from mises.types import ErrorCode, MisesError

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PART = {"part_id": "torso_shell", "script": "VARS = {}\ndef build(v):\n    return None\n"}


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)
    fake = FakeProvider([[TextDelta(text="ok"), Done(stop_reason="end_turn")]
                         for _ in range(6)])
    return Runtime(tmp_path / "home", provider_resolver=lambda ref: (fake, "fake-model"))


async def _lead(rt, prompt):
    pid = rt.active_project_id()
    rid = await rt.start_run("engineer", prompt, project_id=pid, role="lead")
    await rt.wait(rid)
    return pid, rid


async def _create(rt, rid):
    return await rt.gate.call(rid, "mirror_part_create", dict(PART), call_id="c1",
                              workdir=rt.workdir(rid))


async def test_design_build_refuses_parts_until_the_concept_is_approved(rt):
    pid, rid = await _lead(rt, "a sleek humanoid robot")
    assert rt.store.get_run(rid).design_class["cls"] == "design"
    with pytest.raises(MisesError) as e:
        await _create(rt, rid)
    assert e.value.code == ErrorCode.NEEDS_APPROVAL and "concept" in e.value.message
    rt.concepts.update(pid, status="approved")
    try:                                   # past the gate now: whatever the build does next
        await _create(rt, rid)
    except MisesError as err:
        assert err.code != ErrorCode.NEEDS_APPROVAL
    await rt.aclose()


async def test_engineering_build_is_never_gated(rt):
    pid, rid = await _lead(rt, "an L bracket for a NEMA17 motor with four M3 holes")
    assert rt.store.get_run(rid).design_class["cls"] == "engineering"
    try:
        await _create(rt, rid)
    except MisesError as err:
        assert err.code != ErrorCode.NEEDS_APPROVAL
    await rt.aclose()


def test_the_users_class_on_the_build_drives_the_gate_both_ways(rt):
    with TestClient(create_app(rt, TOKEN), base_url="http://127.0.0.1") as c:
        pid, rid = c.portal.call(_lead, rt, "a sleek humanoid robot")
        r = c.patch(f"/projects/{pid}", headers=AUTH, json={"design_class": "engineering"})
        assert r.status_code == 200
        try:
            c.portal.call(_create, rt, rid)
        except MisesError as err:
            assert err.code != ErrorCode.NEEDS_APPROVAL
        c.patch(f"/projects/{pid}", headers=AUTH, json={"design_class": "design"})
        with pytest.raises(MisesError) as e:
            c.portal.call(_create, rt, rid)
        assert e.value.code == ErrorCode.NEEDS_APPROVAL
