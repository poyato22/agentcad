from mises.agent.definitions import load_definitions
from mises.agent.prompts import RESPONSE_RULES, build_system_prompt
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

POLICY = AgentPolicy(run_id="r", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                     allowed_classes=[SafetyClass.READ], created_at=0.0)


def test_every_builtin_prompt_carries_response_rules():
    defs = load_definitions()
    builtins = {n for n, d in defs.items() if d.source is None}
    assert {"engineer", "designer", "planner", "verifier", "sim"} <= builtins
    marker = RESPONSE_RULES.strip().splitlines()[0]
    for name in builtins:
        p = build_system_prompt(defs[name], POLICY, ["mirror_measure"])
        assert marker in p, f"{name} prompt missing RESPONSE_RULES"
        assert "Mises markup" in p and "[[part:" in p


def test_engineer_is_a_builtin_with_the_loop_tools():
    engineer = load_definitions()["engineer"]
    assert engineer.source is None
    for t in ("spec_set", "study_run", "loads_impact", "ui_ask_user"):
        assert t in (engineer.tools or [])
