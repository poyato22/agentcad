"""Plan 9 G-B fix round 3 (re-review 2, I3: N3 / N4): agent-written text never moves the build's
design class. The reviewer's two reproductions on a real Runtime:
- N3: a studies agent's message to the lead ("the car body shell passed the stress study") made
  an L-bracket build design work and closed G-A's concept gate mid-build;
- N4: choosing a pitch option the agent named "Utilitarian" lowered a humanoid build to
  engineering and opened the gate before the concept was approved.
Only the user's own words (a typed message, a pick's comment) may move it."""
import pytest
from fastapi.testclient import TestClient

from mises.concept import gate
from mises.runtime import CLASSIFY, Runtime, _public_log, _user_words
from mises.server.app import create_app
from mises.sim.studies_agent import lead_log, lead_message

from .test_concept import AUTH, TOKEN, Capture, _wait_idle, home  # noqa: F401 (fixture)


async def _lead(rt, prompt):
    pid = rt.active_project_id()
    rid = await rt.start_run("engineer", prompt, project_id=pid, role="lead")
    await rt.wait(rid)
    return pid, rid


@pytest.mark.parametrize("body", [
    "the car body shell passed the stress study", "lamp shade deflects 2 mm",
    "the humanoid torso cover is too thin"])
async def test_a_studies_agent_message_never_moves_the_leads_class(home, body):
    rt = Runtime(home, provider_resolver=lambda ref: (Capture(), "m"))
    pid, lead = await _lead(rt, "an L bracket with four M3 holes")
    assert gate.design_class(rt.store, pid)["cls"] == "engineering"
    await rt.send(lead, lead_message("studies", body), log=lead_log("studies", body))
    await rt.wait(lead)
    assert rt.store.get_run(lead).design_class["cls"] == "engineering"
    dc = gate.design_class(rt.store, pid)
    assert dc["cls"] == "engineering" and dc.get("stage", "off") in ("off", "skip"), dc
    # the stored step shows the agent's message, without the classifier's private key
    users = [s for s in rt.store.steps(lead) if s.kind == "message"
             and s.payload.get("role") == "user"]
    assert users[-1].payload["text"] == f"From the studies agent: {body}"
    assert all(CLASSIFY not in s.payload for s in users)
    await rt.aclose()


def test_choosing_an_agent_named_option_never_moves_the_class(home):
    prov = Capture()
    rt = Runtime(home, provider_resolver=lambda ref: (prov, "m"))
    with TestClient(create_app(rt, TOKEN)) as client:
        lead = client.post("/runs", headers=AUTH,
                           json={"prompt": "a sleek humanoid robot"}).json()["run_id"]
        _wait_idle(client, lead)
        project = rt.store.get_run(lead).project_id
        before = gate.design_class(rt.store, project)
        assert before["cls"] == "design"
        p = rt.concepts.add_pitch(project, {"pitch_id": "ckp_" + "c" * 12, "options": [
            {"name": "Utilitarian"}, {"name": "Simple Monolith", "baseline": True}],
            "chosen": None})
        url = f"/concept/pitches/{p['pitch_id']}/choose"
        assert client.post(url, headers=AUTH, json={"option": "Utilitarian"}).status_code == 200
        _wait_idle(client, lead)
        assert rt.store.get_run(lead).design_class["cls"] == "design"
        after = gate.design_class(rt.store, project)
        assert after["cls"] == "design" and after.get("stage") == before.get("stage"), after
        # a mix of agent-named options, no comment: still nothing classified
        r = client.post(url, headers=AUTH, json={"mix": [
            {"option": "Simple Monolith", "take": "plain body"},
            {"option": "Utilitarian", "take": "basic legs"}]})
        assert r.status_code == 200
        _wait_idle(client, lead)
        assert rt.store.get_run(lead).design_class["cls"] == "design"


def test_only_the_users_own_words_are_classified():
    # a typed message (no log): the message itself
    assert _user_words("make it sleek", None) == "make it sleek"
    # a feedback pin: the user's note, not the wrapped message with agent part labels
    assert _user_words("<part labels> thinner here", {"text": "thinner here"}) == "thinner here"
    # an agent's message, a concept pick, a concept feedback summary, a system note: nothing
    assert _user_words("x", lead_log("studies", "the car body shell passed")) == ""
    assert _user_words("x", {"text": "Chose Utilitarian", "concept_choice": {}}) == ""
    assert _user_words("x", {"text": "v2: 3 marks", "concept_feedback": {}}) == ""
    assert _user_words("x", {"text": "sim done", "system_note": {}}) == ""
    # the caller names the user's words: exactly those
    assert _user_words("x", {"text": "Chose A", "concept_choice": {}, CLASSIFY: "sleeker"}) \
        == "sleeker"
    # the private key never reaches a stored step
    assert _public_log({"text": "t", CLASSIFY: None}) == {"text": "t"}
    assert _public_log({CLASSIFY: None}) is None
