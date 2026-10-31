"""Plan 9 G-D (D11 / D18): the lead (and the designer) are told when to present an animation
(motion matters) and when a section sweep (internals matter: design or packing)."""
from mises.agent.definitions import load_definitions
from mises.agent.prompts import build_system_prompt
from mises.claude_code.prompts import mises_rules_md
from mises.orchestrator.lead import lead_definition
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

POLICY = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                     allowed_classes=[SafetyClass.READ], created_at=0.0)


def test_every_lead_says_when_to_present_motion_and_a_sweep_once():
    defs = load_definitions()
    for name in ("engineer", "sim", "designer"):
        lead = lead_definition(defs[name])
        native = " ".join(build_system_prompt(lead, POLICY, []).split())
        cc = " ".join(mises_rules_md(lead.body, defs, lead=True).split())
        for text in (native, cc):
            assert text.count("kind `animation`") == 1, name
            assert text.count("kind `section_sweep`") == 1, name
            assert "motion matters" in text and "internals matter" in text
            assert "packing" in text
            # the assembly ordering guidance is still stated once
            assert text.count("assembly_animate") == 1, name


def test_the_designer_presents_a_sweep_for_internals():
    body = load_definitions()["designer"].body
    assert "section_sweep" in body and "animation" in body
