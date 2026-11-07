"""view-tab: a decision is made once. Approving a sketch, skipping, picking a pitch or answering
an ask a second time (a double click, a stale tab, a replayed request) is a 409 that carries the
decision already made, and nothing reaches the lead twice."""
from starlette.testclient import TestClient

from mises.runtime import Runtime
from mises.server.app import create_app

from .test_concept import AUTH, TOKEN, Capture, _seed_sketch, _wait_idle, home  # noqa: F401 (fixture)


def _lead(client, rt, prompt):
    lead = client.post("/runs", headers=AUTH, json={"prompt": prompt}).json()["run_id"]
    _wait_idle(client, lead)
    return lead, rt.store.get_run(lead).project_id


def test_approving_the_same_sketch_twice_is_409_and_tells_the_lead_once(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead, project = _lead(client, rt, "a car body")
        s1 = _seed_sketch(rt, project)
        url = f"/projects/{project}/concept/approve"
        assert client.post(url, headers=AUTH, json={"sketch_id": s1["sketch_id"]}).status_code == 200
        _wait_idle(client, lead)
        told = sum("approved concept sketch" in u[0].text for u in prov.users)
        again = client.post(url, headers=AUTH, json={"sketch_id": s1["sketch_id"]})
        assert again.status_code == 409
        body = again.json()
        assert body["error"] == "already_decided"
        assert body["decision"]["kind"] == "concept_approve"
        assert body["decision"]["sketch_id"] == s1["sketch_id"]
        # no sketch id: the latest, which is the approved one
        assert client.post(url, headers=AUTH, json={}).status_code == 409
        _wait_idle(client, lead)
        assert sum("approved concept sketch" in u[0].text for u in prov.users) == told
        assert rt.concepts.get(project)["status"] == "approved"


def test_skipping_twice_is_409(home):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead, project = _lead(client, rt, "a lamp")
        assert client.post(f"/projects/{project}/concept/skip", headers=AUTH).status_code == 200
        r = client.post(f"/projects/{project}/concept/skip", headers=AUTH)
        assert r.status_code == 409 and r.json()["decision"]["kind"] == "concept_skip"


def test_choosing_a_pitch_twice_is_409_with_the_first_choice(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead, project = _lead(client, rt, "a robot dog")
        p = rt.concepts.add_pitch(project, {"pitch_id": "ckp_" + "c" * 12, "options": [
            {"name": "Athlete"}, {"name": "Honest", "baseline": True}], "chosen": None})
        url = f"/concept/pitches/{p['pitch_id']}/choose"
        assert client.post(url, headers=AUTH, json={"option": "Athlete"}).status_code == 200
        _wait_idle(client, lead)
        told = sum("chose a direction" in u[0].text for u in prov.users)
        r = client.post(url, headers=AUTH, json={"option": "Honest"})
        assert r.status_code == 409
        assert r.json()["decision"] == {"kind": "pitch_choose", "pitch_id": p["pitch_id"],
                                        "label": "Athlete", "decided_at": r.json()["decision"]["decided_at"]}
        assert rt.concepts.get_pitch(p["pitch_id"])["chosen"]["option"] == "Athlete"
        _wait_idle(client, lead)
        assert sum("chose a direction" in u[0].text for u in prov.users) == told
