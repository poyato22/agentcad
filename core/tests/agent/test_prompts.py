import os

from mises.agent.definitions import load_definitions
from mises.agent.prompts import build_system_prompt
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

POLICY = AgentPolicy(run_id="r1", version=1, edit_scope=["bracket"], read_scope=["doc_a", "bracket"],
                     caps=Caps(onshape_calls_run=123), allowed_classes=[SafetyClass.READ],
                     created_at=0.0)


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
