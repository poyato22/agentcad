"""P11 F9 at the Gate (every runtime, Claude Code included): the same call failing 3 times
comes back with the last error and a strategy-switch note (never ends the turn), a success
clears it, and edits that go back and forth (A, B, A, B) get an oscillation warning."""
import pytest
from pydantic import BaseModel

from mises.tools.registry import tool
from mises.types import ErrorCode, MisesError, SafetyClass as C, StepKind, ToolOutput

from test_gate import Env


class V(BaseModel):
    w: float = 1.0


def _stuck(env):
    return [s.payload["stuck"] for s in env.store.steps(env.run.id, [StepKind.DECISION])
            if "stuck" in s.payload]


async def test_the_same_failing_call_three_times_gets_a_strategy_switch_note(tmp_path):
    env = Env(tmp_path)

    @tool("flaky_edit", description="d", safety=C.LOCAL_MUTATE)
    async def flaky_edit(ctx, args: V) -> ToolOutput:
        if args.w < 0:
            raise MisesError(ErrorCode.INVALID_INPUT, "w must be positive", hint="use w > 0")
        return ToolOutput(summary="ok")
    env.reg.register(flaky_edit)
    hints = []
    for i in range(4):
        with pytest.raises(MisesError) as e:
            await env.gate.call(env.run.id, "flaky_edit", {"w": -1}, call_id=f"c{i}",
                                workdir=tmp_path)
        hints.append(e.value.hint)
    assert hints[0] == hints[1] == "use w > 0"
    assert "failed 3 times" in hints[2] and "Switch strategy" in hints[2]
    assert hints[2].startswith("use w > 0")
    assert [s["times"] for s in _stuck(env)] == [3]               # noted once
    # a different call is its own count; a success clears the failures
    await env.gate.call(env.run.id, "flaky_edit", {"w": 2}, call_id="ok", workdir=tmp_path)
    with pytest.raises(MisesError) as e:
        await env.gate.call(env.run.id, "flaky_edit", {"w": -2}, call_id="x", workdir=tmp_path)
    assert e.value.hint == "use w > 0"


async def test_edits_that_undo_each_other_get_an_oscillation_warning(tmp_path):
    env = Env(tmp_path)

    @tool("set_w", description="d", safety=C.LOCAL_MUTATE)
    async def set_w(ctx, args: V) -> ToolOutput:
        return ToolOutput(summary=f"w={args.w}")
    env.reg.register(set_w)
    outs = [await env.gate.call(env.run.id, "set_w", {"w": w}, call_id=f"c{i}", workdir=tmp_path)
            for i, w in enumerate([20, 10, 20, 10])]
    assert outs[2].warnings == [] and "back and forth" in outs[3].warnings[0]
    assert [s["kind"] for s in _stuck(env)] == ["oscillation"]
