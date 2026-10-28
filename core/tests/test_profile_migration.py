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
