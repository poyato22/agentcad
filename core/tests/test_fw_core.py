"""Plan 9 final fix wave (FW-core): regression tests for the whole-branch review findings
that are not covered by an existing suite. Each is one of the reviewer's own probes."""
import time

from mises import jobs
from mises.agent.definitions import load_definitions
from mises.orchestrator.orchestrator import inherited_model_ref
from mises.providers.registry import resolve
from mises.runtime import Runtime


def test_finished_jobs_are_pruned_running_ones_are_kept():
    # FW-core M-8: jobs._jobs is pruned so finished ToolOutputs do not live for the whole process.
    jobs.reset_for_tests()
    now = time.time()
    live = jobs.register(jobs.Job(id="sim_live", kind="sim", run_id="r"))
    old = jobs.register(jobs.Job(id="sim_old", kind="sim", run_id="r"))
    old.status, old.finished_at = "done", now - jobs.FINISHED_KEEP_S - 1
    recent = jobs.register(jobs.Job(id="sim_new", kind="sim", run_id="r"))
    recent.status, recent.finished_at = "done", now
    jobs._prune(now)
    assert jobs.get("sim_old") is None                 # long finished: dropped
    assert jobs.get("sim_live") is live                # still running: kept
    assert jobs.get("sim_new") is recent               # finished but recent: kept (result readable)
    jobs.reset_for_tests()


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


def test_stopping_the_lead_does_not_cancel_a_user_started_generative_run():
    # B3 follow-up (FW-core): composer Stop cancels the lead run's family of background jobs; a
    # generative run the user started on their own (run_id = the build's ui run, not a descendant
    # of the lead) must keep going.
    jobs.reset_for_tests()
    agent = jobs.register(jobs.Job(id="to_a", kind="topopt", run_id="lead"))
    user = jobs.register(jobs.Job(id="to_u", kind="topopt", run_id="ui_run"))
    jobs.cancel_run({"lead"})                       # Stop reaches only the lead's family
    assert agent.stopped is True and user.stopped is False
    assert [j.id for j in jobs.outstanding({"ui_run"})] == ["to_u"]
    jobs.reset_for_tests()


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
