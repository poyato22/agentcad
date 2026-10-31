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
