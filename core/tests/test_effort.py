"""Plan 11 F8a: the per-turn effort decision and what each runtime is sent."""
import pytest

from mises import effort
from mises.store import Store
from mises.types import StepKind

EGG_TASKS = [   # the user's three egg-drop builds, verbatim
    ("Make a design for the egg drop challenge. the only constrain is ht must be less than 3 "
     "hours of priniting in the 3d printer", "high"),
    ("Design a egg drop build no limits think of the best possible design to house the egg "
     "from really far up", "max"),
    ("Design  an thing to house the egg for the egg drop challenge. It should be fully 3D "
     "printable no other materials can be used it must be fullly 3D printed. other than that "
     "no limits", "max"),
    ("for the egg drop challenge it keeps making the same boring cage but I want something "
     "really interesting", "max"),
]


@pytest.mark.parametrize("text,level", EGG_TASKS)
def test_open_ended_challenges_think_hard_or_max(text, level):
    got, reasons = effort.auto_pick(text)
    assert got == level
    assert any("open-ended" in r for r in reasons)


@pytest.mark.parametrize("text", [
    "make the wall 2 mm thicker", "what is the mass of the bracket?", "rename it to lid",
    "move the hinge 5 mm left", "make it red"])
def test_routine_edits_and_measurements_think_light(text):
    assert effort.auto_pick(text)[0] == "low"


def test_a_regular_request_is_medium():
    assert effort.auto_pick("Design a motor mount for a NEMA 17 stepper")[0] == "medium"


def test_design_class_failures_and_critique_raise_it():
    t = "Design a motor mount for a NEMA 17 stepper"
    assert effort.auto_pick(t, design_class="design")[0] == "high"
    assert effort.auto_pick(t, failures=1)[0] == "high"
    lvl, why = effort.auto_pick(t, failures=2)
    assert lvl == "max" and "the last 2 turns failed" in why
    assert effort.auto_pick(t, critique_repeats=1)[0] == "high"
    assert effort.auto_pick("think hard about the gear ratio")[0] == "high"


def test_a_challenge_build_follow_up():
    ch = {"kind": "egg_drop", "creative": True}
    assert effort.auto_pick("add a hinge to the lid", challenge=ch)[0] == "high"
    assert effort.auto_pick("make it red", challenge=ch)[0] == "low"
    # "just build it" turns the challenge reading off for the turn
    assert effort.auto_pick("just build it, egg drop cage is fine")[0] == "medium"


def test_choices_map_to_levels():
    assert effort.normalise("Hard") == "high" and effort.normalise("normal") == "medium"   # Plan 11 names
    assert effort.normalise("low") == "low" and effort.normalise("Max") == "max"
    assert effort.normalise("bogus") is None and effort.normalise(None) is None
    assert effort.CHOICES == ("auto", "low", "medium", "high", "max")
    assert effort.CHOICE_LEVEL == {"low": "low", "medium": "medium", "high": "high", "max": "max"}


@pytest.mark.parametrize("provider,model,level,expect", [
    ("claude-code", "sonnet", "max", {"param": "--effort / apply_flag_settings.effortLevel",
                                      "value": "max"}),
    ("claude-code", "opus", "low", {"value": "low"}),
    ("anthropic", "claude-opus-5-5", "max", {"param": "output_config.effort", "value": "max"}),
    ("anthropic", "claude-sonnet-4-6", "high", {"value": "high"}),
    ("anthropic", "claude-opus-4-5", "max", {"value": "high"}),       # Opus 4.5 tops at high
    ("anthropic", "claude-haiku-4-5", "max", {"supported": False}),   # no effort there
    ("openai", "gpt-5.1", "max", {"param": "reasoning_effort", "value": "high"}),
    ("openai", "gpt-4o", "high", {"supported": False}),
    ("ollama", "qwen3:32b", "high", {"supported": False}),
])
def test_runtime_controls(provider, model, level, expect):
    got = effort.control(provider, model, level)
    for k, v in expect.items():
        assert got[k] == v


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db")


def test_decide_user_choice_beats_build_default_beats_auto(store):
    p = store.create_project("Egg")
    run = store.create_run("engineer", "anthropic", "claude-opus-5-5", "x", project_id=p.id)
    text = "rename it to lid"
    assert effort.decide(store, run.id, text)["level"] == "low"
    effort.set_build_default(store, p.id, "hard")
    d = effort.decide(store, run.id, text)
    assert (d["level"], d["source"]) == ("high", "build")
    d = effort.decide(store, run.id, text, "max")
    assert (d["level"], d["source"], d["choice"]) == ("max", "user", "max")
    assert effort.set_build_default(store, p.id, "auto") == "auto"
    assert effort.decide(store, run.id, text, "auto")["source"] == "auto"


def test_run_signals_count_failed_turns_and_critique_switches(store):
    run = store.create_run("engineer", "anthropic", "m", "x")
    for _ in range(2):
        store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": "go"})
        store.append_step(run.id, StepKind.ERROR, {"message": "boom"})
    store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": "again"})
    store.append_step(run.id, StepKind.VERIFY, {"name": "critique_model", "output": {
        "data": {"strategy_switch": True}}})
    sig = effort.run_signals(store, run.id)
    assert sig == {"failures": 0, "critique_repeats": 1}
    store.append_step(run.id, StepKind.ERROR, {"message": "boom"})
    assert effort.run_signals(store, run.id)["failures"] == 3
    # a system note is not a user turn
    store.append_step(run.id, StepKind.MESSAGE, {"role": "user", "text": "study done",
                                                 "system_note": {"kind": "sim_finished"}})
    assert effort.run_signals(store, run.id)["failures"] == 3
