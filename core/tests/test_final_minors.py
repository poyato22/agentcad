"""Cheap Minors from the Plan 5 final reviews (core M1, M3, M7; harness M-3, M-5, M-6, M-7, M-9)."""
import json

from mises import projects


def test_part_disk_never_follows_a_link_to_a_secret(tmp_path):
    """M1: a link a racing build left at metrics.json is refused, never followed."""
    (tmp_path / "server.json").write_text('{"token": "SECRET"}')
    d = tmp_path / "parts" / "p"
    d.mkdir(parents=True)
    (d / "vars.json").write_text('{"a": 1}')
    (d / "metrics.json").symlink_to(tmp_path / "server.json")
    disk = projects._part_disk(tmp_path, "p")
    assert disk["vars"] == {"a": 1} and disk["metrics"] is None
    assert "SECRET" not in json.dumps(disk)


def test_spec_set_needs_the_whole_build_in_scope():
    """M7: a run narrowed to some parts cannot rewrite the build's spec."""
    from mises.engineering.tools import SpecSetArgs, spec_set
    keys = spec_set.scope_keys(SpecSetArgs(project_id="b1"))
    assert keys == ["project:b1"] and "bracket" not in keys


def test_new_tools_have_human_activity_labels():
    from mises.events import tool_label
    for t in ("sim_run", "design_review", "study_apply", "interview_plan"):
        assert tool_label(t) != t.replace("_", " ")


def test_acp_agents_get_the_harness_rules():
    """M-5: an ACP run's first prompt carries the shared rules and response rules."""
    from mises.acp_host.session import acp_rules_block
    from mises.agent.prompts import RESPONSE_RULES
    block = acp_rules_block()
    assert block.startswith("<mises-rules>") and "# Mises harness rules" in block
    assert RESPONSE_RULES.strip() in block


def test_claude_mcp_tool_timeout_covers_a_design_review(monkeypatch):
    """M-6: Claude Code's MCP client waits at least as long as design_review may run."""
    from mises.claude_code import detect
    from mises.sim.tools import REVIEW_TIMEOUT_S
    monkeypatch.delenv("MCP_TOOL_TIMEOUT", raising=False)
    assert int(detect.claude_environment()["MCP_TOOL_TIMEOUT"]) >= REVIEW_TIMEOUT_S * 1000


def test_review_result_is_trimmed_to_fit():
    """M-7: many fat study cards are trimmed (charts first) so the review never spills."""
    from mises.gate import MAX_OUTPUT_BYTES
    from mises.sim.tools import _fit_review
    card = {"title": "x", "charts": [{"series": list(range(400))}], "warnings": ["w" * 300] * 5,
            "checks": [{"status": "pass"}] * 5}
    data = {"verdict": "pass", "table": [{"requirement_id": "r", "status": "pass"}],
            "studies": [{"sim_id": f"sim_{i}", "card": dict(card)} for i in range(8)]}
    _fit_review(data)
    assert len(json.dumps(data).encode()) <= MAX_OUTPUT_BYTES and data["trimmed"]
    assert [s["sim_id"] for s in data["studies"]] == [f"sim_{i}" for i in range(8)]


def test_finishing_rule_is_short():
    from mises.agent.prompts import RULES
    assert "at most 6 lines or one table" in RULES
