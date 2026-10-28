from importlib import resources
from pathlib import Path

from mises.agent.definitions import load_definitions


def test_builtins_load():
    defs = load_definitions()
    assert {"designer", "planner", "verifier", "sim"} <= set(defs)
    assert defs["designer"].tools is None
    assert "orchestrator_submit_plan" in defs["planner"].tools
    assert "onshape_propose" not in defs["verifier"].tools
    assert all(d.body.strip() for d in defs.values())


def test_builtin_tool_lists_name_real_tools(tmp_path: Path):
    """Every tool a built-in agent lists exists in the runtime (sim.md stays current)."""
    from mises.runtime import Runtime
    rt = Runtime(tmp_path / "home")
    for d in load_definitions().values():
        for name in d.tools or []:
            rt.registry.get(name)          # raises "unknown tool" if a definition went stale
    sim = load_definitions()["sim"]
    assert {"sim_env_list", "sim_static_run", "sim_result_get"} <= set(sim.tools)


def test_builtins_packaged():
    base = resources.files("mises.agent") / "builtin"
    names = {p.name for p in base.iterdir()}
    assert {"designer.md", "planner.md", "verifier.md", "sim.md"} <= names


def test_user_override(tmp_path: Path):
    (tmp_path / "designer.md").write_text(
        "---\nname: designer\ndescription: mine\nmax_turns: 7\n---\nCustom body.\n")
    d = load_definitions([tmp_path])["designer"]
    assert d.body.strip() == "Custom body."
    assert d.max_turns == 7
    assert d.source == tmp_path / "designer.md"


def test_bad_frontmatter_skipped(tmp_path: Path):
    (tmp_path / "bad.md").write_text("no frontmatter here")
    (tmp_path / "bad2.md").write_text("---\nname: [unclosed\n---\nbody")
    (tmp_path / "bad3.md").write_text("---\ndescription: no name\n---\nbody")
    (tmp_path / "good.md").write_text("---\nname: good\ndescription: ok\ntools: [mirror_measure]\n---\nhi")
    defs = load_definitions([tmp_path])
    assert "good" in defs and defs["good"].tools == ["mirror_measure"]
    assert "bad" not in defs
    assert len(defs.warnings) == 3
    assert len(defs) == 5
