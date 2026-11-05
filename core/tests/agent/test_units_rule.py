"""Bench fix: the user's numbers are used as given (CADTestBench 00670259: "0.06 meters" with
"use every number exactly as given (1 unit = 1 mm)" was turned into 60 mm). Every agent,
native and Claude Code, carries the rule in the shared harness rules."""
from mises.agent.definitions import load_definitions
from mises.agent.prompts import RULES
from mises.claude_code.prompts import mises_rules_md


def test_shared_rules_keep_numbers_as_given():
    text = " ".join(RULES.split())
    assert "use the user's numbers as given" in text
    assert "never convert" in text and "1 unit = 1 mm" in text


def test_every_claude_code_agent_carries_it():
    defs = load_definitions()
    for name in ("designer", "engineer"):
        cc = " ".join(mises_rules_md(defs[name].body, defs, lead=True).split())
        assert "use the user's numbers as given" in cc, name
