"""profile.json schema_version: a pre-versioned profile holding the old default 2 parallel agents
becomes "auto" once; anything saved since carries schema_version and is kept as is."""
import json

from mises.profile import PROFILE_SCHEMA_VERSION, Profile, load_profile, save_profile


def write(tmp_path, data):
    (tmp_path / "profile.json").write_text(json.dumps(data))


def test_legacy_two_becomes_auto(tmp_path):
    write(tmp_path, {"display_name": "Ada", "max_parallel_agents": 2})
    p = load_profile(tmp_path)
    assert p.max_parallel_agents == "auto" and p.display_name == "Ada"
    assert p.schema_version == PROFILE_SCHEMA_VERSION


def test_legacy_other_numbers_are_kept(tmp_path):
    write(tmp_path, {"max_parallel_agents": 3})
    assert load_profile(tmp_path).max_parallel_agents == 3


def test_save_writes_schema_version_and_a_later_two_is_kept(tmp_path):
    write(tmp_path, {"max_parallel_agents": 2})
    p = load_profile(tmp_path)
    save_profile(tmp_path, p)
    on_disk = json.loads((tmp_path / "profile.json").read_text())
    assert on_disk["schema_version"] == PROFILE_SCHEMA_VERSION
    assert on_disk["max_parallel_agents"] == "auto"
    # the user then chooses 2 on purpose: it stays 2
    save_profile(tmp_path, p.model_copy(update={"max_parallel_agents": 2}))
    assert load_profile(tmp_path).max_parallel_agents == 2


def test_new_profile_has_the_current_version():
    assert Profile().schema_version == PROFILE_SCHEMA_VERSION >= 2
    assert Profile().model_dump()["schema_version"] == PROFILE_SCHEMA_VERSION


def test_put_profile_migrated_legacy_file(tmp_path):
    write(tmp_path, {"display_name": "Ada", "max_parallel_agents": 2})
    p = load_profile(tmp_path)
    merged = Profile.model_validate({**p.model_dump(), "display_name": "Bo"})
    save_profile(tmp_path, merged)
    assert load_profile(tmp_path).max_parallel_agents == "auto"


# A Plan-4 (schema_version 2) profile shaped like the real one. NOT the user's real profile.
PLAN4_PROFILE = {
    "schema_version": 2, "display_name": "Ada", "units": "mm", "default_material": "PLA",
    "default_agent": "designer", "default_model": "anthropic/claude-sonnet-4",
    "onshape_calls_year_cap": 2500, "onshape_calls_run_cap": 200,
    "max_parallel_agents": "auto", "local_endpoints": {}, "theme": "system",
    "setup_completed_at": 1750000000.0, "printers": [], "active_project_id": "default",
    "open_project_ids": ["default"], "hidden_agents": [], "catalog_allowed_domains": [],
}


def test_plan4_designer_default_becomes_engineer(tmp_path):
    """H1/C-1: a pre-v3 profile saved 'designer' because that was the old default, not a choice,
    so it migrates to 'engineer' once. Everything else is preserved."""
    write(tmp_path, PLAN4_PROFILE)
    p = load_profile(tmp_path)
    assert p.default_agent == "engineer"
    assert p.display_name == "Ada" and p.default_model == "anthropic/claude-sonnet-4"
    assert p.schema_version == PROFILE_SCHEMA_VERSION


def test_legacy_unversioned_designer_becomes_engineer(tmp_path):
    write(tmp_path, {"default_agent": "designer"})
    assert load_profile(tmp_path).default_agent == "engineer"


def test_v3_designer_choice_is_kept(tmp_path):
    """After the bump, choosing the designer on purpose (saved at v3) stays."""
    write(tmp_path, {**PLAN4_PROFILE, "schema_version": PROFILE_SCHEMA_VERSION,
                     "default_agent": "designer"})
    assert load_profile(tmp_path).default_agent == "designer"


def test_migration_leaves_other_agents_alone(tmp_path):
    write(tmp_path, {**PLAN4_PROFILE, "default_agent": "my_custom"})
    assert load_profile(tmp_path).default_agent == "my_custom"
