"""The web caps are anti-runaway limits, not walls a normal build hits: a big build reads a few
hundred pages, dozens from one manufacturer's store."""
import json

from mises.browser import tools as bt
from mises.profile import DEFAULT_BROWSER_FETCHES, PROFILE_SCHEMA_VERSION, load_profile


def test_the_caps_leave_room_for_a_big_build():
    assert DEFAULT_BROWSER_FETCHES >= 300
    assert bt.PER_DOMAIN_FETCHES >= 100
    assert bt.NON_SUGGESTED_FETCHES >= 400


def test_a_saved_profile_holding_the_old_default_gets_the_new_one(tmp_path):
    # save_profile writes the whole model, so 60 on disk is the old default, not a choice
    (tmp_path / "profile.json").write_text(json.dumps(
        {"schema_version": 3, "browser_fetches_per_run": 60}))
    p = load_profile(tmp_path)
    assert p.browser_fetches_per_run == DEFAULT_BROWSER_FETCHES
    assert p.schema_version == PROFILE_SCHEMA_VERSION


def test_a_users_own_budget_is_kept(tmp_path):
    (tmp_path / "profile.json").write_text(json.dumps(
        {"schema_version": 3, "browser_fetches_per_run": 25}))
    assert load_profile(tmp_path).browser_fetches_per_run == 25
    (tmp_path / "profile.json").write_text(json.dumps(
        {"schema_version": PROFILE_SCHEMA_VERSION, "browser_fetches_per_run": 60}))
    assert load_profile(tmp_path).browser_fetches_per_run == 60     # chosen after the change
