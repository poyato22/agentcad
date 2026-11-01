"""P11 F3 over MCP: a decorated name is the tool it names (never an internal-only one), an invented
one is a recoverable error naming the closest tools, and a note Mises holds for the model rides
on its next tool result, once."""
import json

from mcp_types import TextContent

from mises.claude_code.session import SOURCE as CLAUDE_CODE

from .mcp_helpers import mcp_client, running


async def test_names_and_notes_over_mcp(rt):
    async with running(rt) as app:
        rid = rt.create_external_run("designer", provider=CLAUDE_CODE, model="opus")
        rt.tool_notes[rid] = ["Mises note: you likely meant `mcp__mises__loads_impact`."]
        http, client = mcp_client(app, rid)
        async with http, client:
            bad = await client.call_tool("loads_impact_placeholder", {})
            ok = await client.call_tool("mises.mirror_part_list", {})
            internal = await client.call_tool("mcp__mises__orchestrator_submit_plan", {})
    texts = [c.text for c in bad.content if isinstance(c, TextContent)]
    assert bad.is_error and "loads_impact" in json.loads(texts[0])["hint"]
    assert texts[1].startswith("Mises note")                    # the held note, once
    assert ok.is_error is False and len(ok.content) == 1
    assert internal.is_error and "unknown tool" in internal.content[0].text
