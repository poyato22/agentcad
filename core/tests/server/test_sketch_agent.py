"""A sketch run builds the drawn geometry with the designer, even when the app sends the
profile default ("engineer") explicitly, as the sketch page does."""
from .conftest import AUTH
from .test_sketch_api import client, upload, wait_status  # noqa: F401  (client is a fixture)


def test_sketch_run_with_profile_default_gets_designer(client):
    sid = upload(client)["sketch_id"]
    r = client.post("/runs", headers=AUTH, json={"prompt": "Build my sketch.",
                                                 "sketch_id": sid, "agent": "engineer"})
    assert r.status_code == 200, r.text
    assert wait_status(client, r.json()["run_id"])["agent"] == "designer"


def test_non_sketch_run_uses_engineer_default(client):
    r = client.post("/runs", headers=AUTH, json={"prompt": "Build me a drone."})
    assert r.status_code == 200, r.text
    assert wait_status(client, r.json()["run_id"])["agent"] == "engineer"


def test_explicit_other_agent_on_a_sketch_is_kept(client):
    sid = upload(client)["sketch_id"]
    r = client.post("/runs", headers=AUTH, json={"prompt": "Check it.", "sketch_id": sid,
                                                 "agent": "verifier"})
    assert wait_status(client, r.json()["run_id"])["agent"] == "verifier"
