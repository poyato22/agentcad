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
        assert "AgentCAD markup" in p and "[[part:" in p


def test_engineer_is_a_builtin_offered_every_tool():
    # Like the designer, the engineer lists no tools so it is offered the whole registry (and so
    # automatically gains catalog_* and design_review once those parallel tasks merge).
    engineer = load_definitions()["engineer"]
    assert engineer.source is None
    assert engineer.tools is None
    for t in ("spec_set", "study_run", "loads_impact", "ui_ask_user"):
        assert t in engineer.body


def test_engineer_and_verifier_use_design_review():
    # C7 owns the design_review tool; these prompts must call it (prompt-only on this branch).
    defs = load_definitions()
    for name in ("engineer", "verifier"):
        assert "design_review" in defs[name].body
