"""int-678: the merged prompt sections of Plans 7 and 8 (U3c lead, U4c real components, G1
render-critique, G2 shape recipes, G3 skeleton) sit side by side, each delimited block intact and
once, and RESPONSE_RULES still reaches every agent, the lead and the Claude Code rules."""
import re

from mises.agent.definitions import load_definitions
from mises.agent.prompts import RESPONSE_RULES, build_system_prompt
from mises.cad.recipes import prompt_section
from mises.claude_code.prompts import mises_rules_md
from mises.orchestrator.lead import lead_definition
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass

POLICY = AgentPolicy(run_id="r1", version=1, edit_scope=["bracket"], read_scope=["bracket"],
                     caps=Caps(), allowed_classes=[SafetyClass.READ], created_at=0.0)
BLOCKS = [("<!-- mises-shapes:begin", "<!-- mises-shapes:end -->"),
          ("<!-- BEGIN plan8-g3", "<!-- END plan8-g3 -->")]


def test_each_delimited_block_is_whole_and_once():
    d = load_definitions()
    for name in ("designer", "engineer"):
        body = d[name].body
        for begin, end in BLOCKS:
            assert body.count(begin) == 1 and body.count(end) == 1, (name, begin)
            assert body.index(begin) < body.index(end)
        # G2's block is the generated recipes section, unedited by the merge
        shapes = body[body.index(BLOCKS[0][0]):body.index(BLOCKS[0][1]) + len(BLOCKS[0][1])]
        assert prompt_section().strip() in shapes or shapes.strip() in prompt_section()
        assert not re.search(r"^(<<<<<<<|=======|>>>>>>>)", body, re.M)


def test_the_plan_7_and_8_sections_are_all_there():
    d = load_definitions()
    eng = d["engineer"].body
    for h in ("## 3b. Real components", "## 6. Assemble", "## 7. See and present the result",
              "## Curved and organic shapes", "## Layout skeleton first"):
        assert eng.count(h) == 1, h
    assert len(re.findall(r"^## 6\.", eng, re.M)) == 1
    des = d["designer"].body
    for h in ("## See the result: render, critique, fix", "## Present what you built",
              "## Curved and organic shapes", "## Layout skeleton first"):
        assert des.count(h) == 1, h


def test_response_rules_reach_every_agent_the_lead_and_claude_code():
    d = load_definitions()
    rules = RESPONSE_RULES.strip()
    for name in d.names() if hasattr(d, "names") else ("designer", "engineer", "sim", "verifier"):
        assert rules in build_system_prompt(d[name], POLICY, ["mirror_measure"]), name
    lead = lead_definition(d["designer"])
    assert rules in build_system_prompt(lead, POLICY, ["mirror_measure"])
    assert rules in mises_rules_md(lead.body, d)
    assert rules in mises_rules_md(None, d, delegating=False)


def test_no_sketch_page_in_prompts_or_context():
    d = load_definitions()
    texts = [d[n].body for n in ("designer", "engineer", "sim", "verifier")]
    texts.append(mises_rules_md(None, d))
    from mises.context_tools import mises_context_get
    texts.append(str(getattr(mises_context_get, "description", "")
                     or getattr(getattr(mises_context_get, "spec", None), "description", "")))
    for t in texts:
        assert "Sketch page" not in t
