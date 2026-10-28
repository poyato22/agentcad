import os
import stat

import pytest
from pydantic import ValidationError

from mises.profile import Profile, SetupState, load_profile, save_profile, setup_state


def test_defaults_when_missing(tmp_path):
    p = load_profile(tmp_path)
    assert p == Profile() and p.units == "mm" and p.onshape_calls_year_cap == 2500
    assert p.onshape_calls_run_cap == 200 and p.max_parallel_agents == "auto"
    assert p.default_agent == "designer" and p.default_material == "PLA"


def test_round_trip_is_0600_and_atomic(tmp_path, monkeypatch):
    p = Profile(display_name="Ada", default_model="ollama/qwen3",
                local_endpoints={"ollama": "http://localhost:11434/v1"}, setup_completed_at=12.5)
    save_profile(tmp_path, p)
    f = tmp_path / "profile.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert load_profile(tmp_path) == p
    assert [x.name for x in tmp_path.iterdir()] == ["profile.json"]

    def boom(*a):
        raise OSError("disk")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_profile(tmp_path, Profile(display_name="Bob"))
    assert load_profile(tmp_path).display_name == "Ada"
    assert [x.name for x in tmp_path.iterdir()] == ["profile.json"]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "profile.json").write_text("{nope")
    assert load_profile(tmp_path) == Profile()


def test_validation():
    with pytest.raises(ValidationError):
        Profile(units="inch")
    with pytest.raises(ValidationError):
        Profile(max_parallel_agents=0)
    for bad in ("Auto", "3", 17, True, 2.5, None):
        with pytest.raises(ValidationError):
            Profile(max_parallel_agents=bad)
    assert Profile(max_parallel_agents="auto").max_parallel_agents == "auto"
    assert Profile(max_parallel_agents=16).max_parallel_agents == 16
    with pytest.raises(ValidationError):
        Profile(onshape_calls_run_cap=-1)
    with pytest.raises(ValidationError):
        Profile(local_endpoints={"ollama": "http://evil.example.com/v1"})
    with pytest.raises(ValidationError):
        Profile(local_endpoints={"ollama": "file:///etc/passwd"})
    with pytest.raises(ValidationError):
        Profile(secret="x")


async def _no():
    return False


async def _yes():
    return True


async def _none_probe(urls):
    return False


async def _up_probe(urls):
    return True


def keys(**src):
    base = {"anthropic": "none", "openai": "none", "onshape": "none"}
    base.update(src)
    return lambda: base


@pytest.mark.parametrize("keysrc,claude,local,onshape,done,has_model,complete,missing", [
    ({}, False, False, False, False, False, False, ["model", "onshape", "profile"]),
    ({"anthropic": "env"}, False, False, False, False, True, False, ["onshape", "profile"]),
    ({"openai": "keychain"}, False, False, False, True, True, True, ["onshape"]),
    ({}, True, False, False, True, True, True, ["onshape"]),
    ({}, False, True, True, True, True, True, []),
    # complete = the user finished (or skipped) the wizard; a missing model is only a notice
    ({"onshape": "keychain"}, False, False, True, True, False, True, ["model"]),
    ({}, False, False, False, True, False, True, ["model", "onshape"]),
])
async def test_setup_truth_table(tmp_path, keysrc, claude, local, onshape, done, has_model,
                                 complete, missing):
    save_profile(tmp_path, Profile(setup_completed_at=1.0 if done else None))
    if onshape and not keysrc.get("onshape"):
        keysrc = {**keysrc, "onshape": "keychain"}
    s = await setup_state(tmp_path, keys(**keysrc), _yes if claude else _no,
                          _up_probe if local else _none_probe)
    assert s == SetupState(complete=complete, has_model=has_model, has_onshape=onshape,
                           missing=missing)


async def test_setup_claude_status_default_and_errors(tmp_path):
    s = await setup_state(tmp_path, keys(), None, _none_probe)
    assert s.has_model is False

    async def bad():
        raise RuntimeError("x")
    s = await setup_state(tmp_path, keys(), bad, _none_probe)
    assert s.has_model is False


async def test_local_probe_gets_profile_endpoints(tmp_path):
    save_profile(tmp_path, Profile(local_endpoints={"ollama": "http://localhost:9/v1"}))
    seen = []

    async def probe(urls):
        seen.extend(urls)
        return False
    await setup_state(tmp_path, keys(), _no, probe)
    assert "http://localhost:9/v1" in seen
