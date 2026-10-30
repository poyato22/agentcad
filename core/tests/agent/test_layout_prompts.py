"""Plan 8 G3: the skeleton-first rules live in ONE delimited block per prompt (other plans edit
these prompts too), and every tool they name exists."""
import re

import pytest

from mises.agent.definitions import load_definitions
from mises.assembly.internals import register_layout_tools
from mises.assembly.tools import register_assembly_tools
from mises.tools.registry import ToolRegistry

BEGIN, END = "<!-- BEGIN plan8-g3", "<!-- END plan8-g3 -->"


@pytest.mark.parametrize("agent", ["engineer", "designer"])
def test_one_delimited_block_naming_real_tools(agent):
    body = load_definitions()[agent].body
    assert body.count(BEGIN) == 1 and body.count(END) == 1
    block = body[body.index(BEGIN):body.index(END)]
    assert "skeleton_set" in block and "section_check" in block
    reg = ToolRegistry()
    register_layout_tools(reg)
    register_assembly_tools(reg)
    named = {t for t in re.findall(r"`([a-z_]+)`", block)
             if t.startswith(("skeleton_", "inner_", "internals_", "section_", "assembly_"))}
    assert named
    for t in named:
        reg.get(t)  # raises for an unknown tool
