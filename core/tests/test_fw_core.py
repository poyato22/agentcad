"""Plan 9 final fix wave (FW-core): regression tests for the whole-branch review findings
that are not covered by an existing suite. Each is one of the reviewer's own probes."""
from mises.agent.definitions import load_definitions
from mises.orchestrator.orchestrator import inherited_model_ref
from mises.providers.registry import resolve
from mises.runtime import Runtime


# ---- I-2: an agent whose tools start a background job must also offer the reader the finish
# note names ("Read it with ..."), else it cannot read its own result on a native runtime.
#: tool that starts a background job -> the reader jobs.note_text tells the agent to use
_READER_FOR = {"design_review": "design_review_get", "sim_run": "sim_result_get",
               "sim_static_run": "sim_result_get"}


def test_every_builtin_agent_can_read_the_jobs_it_starts():
    defs = load_definitions()
    for name, defn in defs.items():
        if defn.tools is None:
            continue
        tools = set(defn.tools)
        for starter, reader in _READER_FOR.items():
            if starter in tools:
                assert reader in tools, (
                    f"{name} offers {starter} but not its reader {reader}; its background "
                    f"job's finish note would point at a tool it does not have")


def test_a_routed_lead_has_the_present_tools_its_prompt_names():
    # FW-core M-2: LEAD_PROMPT tells every lead to present_view motion/sweeps; the router can pick
    # `sim` as the lead, whose own tools omit present_view. The lead must still be offered it.
    from mises.orchestrator.lead import lead_definition
    defs = load_definitions()
    # `sim` restricts its tools, so the widening must add the present tools it would otherwise lack
    sim_tools = set(lead_definition(defs["sim"]).tools)
    assert "present_view" in sim_tools and "render_views" in sim_tools
    for name in ("sim", "designer", "engineer"):
        tools = lead_definition(defs[name]).tools
        if tools is not None:                      # None = every tool; present_view is included
            assert {"present_view", "render_views"} <= set(tools), name


def test_external_mcp_parent_does_not_leak_an_unresolvable_model_ref(tmp_path):
    # I-3: an external MCP run (POST /runs/external) carries a free-form provider/model Mises
    # cannot resolve. A child must fall back to its definition/profile, not inherit "p/m" and
    # 400 with "unknown vendor".
    rt = Runtime(tmp_path / "home", provider_resolver=resolve)
    parent = rt.create_external_run("engineer", provider="p", model="m")
    assert inherited_model_ref(rt, parent) is None
    # a real vendor ref still inherits
    good = rt.create_external_run("engineer", provider="claude-code", model="sonnet")
    assert inherited_model_ref(rt, good) == "claude-code/sonnet"
