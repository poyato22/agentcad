"""P11 F3: a bare, decorated or invented tool name never fails a run: a name that names exactly one
real tool takes that tool's normal Gate path; anything else is a recoverable error that lists the
closest real tools."""
import pytest

from mises.tools.names import closest_tools, resolve_tool_name, unknown_tool_hint
from mises.types import ErrorCode, MisesError, SafetyClass as C, StepKind

from test_gate import Env

NAMES = ["mirror_part_create", "mirror_part_list", "loads_impact", "assembly_get", "sim_run"]


@pytest.mark.parametrize("name, real", [
    ("mirror_part_create", "mirror_part_create"),
    ("mcp__mises__mirror_part_create", "mirror_part_create"),
    ("mises.assembly_get", "assembly_get"),
    ("AgentCAD.assembly_get", "assembly_get"),
    ("Mirror-Part-Create", "mirror_part_create"),
    ("loads_impact_placeholder", None),
    ("mirror_part", None),                     # never a fuzzy guess
    ("mcp__other__sim_run", None),
])
def test_resolve_maps_only_exact_undecorated_names(name, real):
    assert resolve_tool_name(name, NAMES) == real


def test_closest_tools_lead_with_the_shared_words():
    assert closest_tools("loads_impact_placeholder", NAMES)[0] == "loads_impact"
    assert closest_tools("mirror_part_creat", NAMES)[0] == "mirror_part_create"
    assert closest_tools("zzz", NAMES) == []
    hint = unknown_tool_hint("loads_impact_placeholder", NAMES, prefix="mcp__mises__")
    assert "`mcp__mises__loads_impact`" in hint and "exact names" in hint


async def test_gate_runs_a_decorated_name_as_the_real_tool(tmp_path):
    env = Env(tmp_path)
    env.add("mirror_part_create", safety=C.LOCAL_MUTATE)
    out = await env.call("mcp__mises__mirror_part_create", {"n": 3})
    assert out.data == {"n": 3} and env.calls == 1
    call = env.store.steps(env.run.id, [StepKind.TOOL_CALL])[-1].payload
    assert call["name"] == "mirror_part_create"


async def test_gate_unknown_name_is_a_recoverable_error_with_the_closest_tools(tmp_path):
    env = Env(tmp_path)
    env.add("loads_impact")
    with pytest.raises(MisesError) as ei:
        await env.call("loads_impact_placeholder", {})
    assert ei.value.code == ErrorCode.NOT_FOUND
    assert "loads_impact" in (ei.value.hint or "")
    assert env.calls == 0
