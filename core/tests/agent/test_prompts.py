import os

from mises.agent.definitions import load_definitions
from mises.agent.prompts import build_system_prompt
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

POLICY = AgentPolicy(run_id="r1", version=1, edit_scope=["bracket"], read_scope=["doc_a", "bracket"],
                     caps=Caps(onshape_calls_run=123), allowed_classes=[SafetyClass.READ],
                     created_at=0.0)


def test_post_study_replies_point_at_the_cards():
    # Plan 7 R18: no walls of text after studies, in every agent's shared rules and the
    # engineer, sim and lead bodies
    from mises.agent.prompts import RESPONSE_RULES
    from mises.orchestrator.lead import LEAD_PROMPT
    assert "study cards above" in RESPONSE_RULES and "Never restate" in RESPONSE_RULES
    defs = load_definitions()
    for body in (defs["engineer"].body, defs["sim"].body, LEAD_PROMPT):
        flat = " ".join(body.split())
        assert "study cards above" in flat and "2-3 short lines" in flat
    assert "assembly_animate" in defs["engineer"].body and "sweep" in defs["engineer"].body
    assert "assembly_add_part" in LEAD_PROMPT and "assembly_animate" in LEAD_PROMPT


def test_prompt_stable_prefix():
    d = load_definitions()
    a = build_system_prompt(d["designer"], POLICY, ["mirror_measure"])
    b = build_system_prompt(d["verifier"], POLICY, ["mirror_measure"], project_notes="x")
    assert os.path.commonprefix([a, b]).__len__() >= 500
    rules = a.split("\n\n---\n\n")[0]
    assert 500 <= len(rules.split()) <= 1200
    assert os.path.commonprefix([a, b]).startswith(rules)


def test_prompt_contains_scope_and_tools():
    d = load_definitions()
    p = build_system_prompt(d["designer"], POLICY, ["mirror_measure", "onshape_propose"],
                            project_notes="Units are mm.")
    for s in ("bracket", "doc_a", "123", "mirror_measure", "onshape_propose", "Units are mm."):
        assert s in p
    assert p.index(d["designer"].body.strip()[:40]) < p.index("mirror_measure")


def test_prompt_mentions_untrusted():
    p = build_system_prompt(load_definitions()["sim"], POLICY, [])
    assert "untrusted" in p.lower()
    for code in ("stale_version", "budget_exhausted", "needs_approval", "transient"):
        assert code in p
