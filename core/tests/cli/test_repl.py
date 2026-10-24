import asyncio

from pydantic import BaseModel

from mises.cli.repl import Repl
from mises.providers.base import Done, TextDelta, ToolCall
from mises.tools.registry import tool
from mises.types import SafetyClass as C, ToolOutput

from .conftest import Lines, make_runtime


class In(BaseModel):
    n: int = 0
    name: str = ""


def add_tools(rt, ran):
    @tool("probe_measure", description="measure", safety=C.READ)
    async def probe(ctx, args: In) -> ToolOutput:
        return ToolOutput(summary="volume 1234 mm3", data={"volume": 1234})

    @tool("danger_merge", description="merge", safety=C.IRREVERSIBLE)
    async def merge(ctx, args: In) -> ToolOutput:
        ran.append(args.n)
        return ToolOutput(summary="merged")

    @tool("slow_probe", description="slow", safety=C.READ, timeout_s=60)
    async def slow(ctx, args: In) -> ToolOutput:
        await asyncio.sleep(30)
        return ToolOutput(summary="never")

    for t in (probe, merge, slow):
        rt.registry.register(t)


def danger_turns():
    return [[ToolCall(id="c1", name="danger_merge", input={"n": 1}), Done(stop_reason="tool_use")],
            [TextDelta(text="all settled"), Done(stop_reason="end_turn")]]


async def test_repl_streams_and_collapses_tool_line(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [
        [TextDelta(text="Measuring now. "),
         ToolCall(id="c1", name="probe_measure", input={"name": "bracket"}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="The bracket is fine."), Done(stop_reason="end_turn")]])
    add_tools(rt, [])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    assert await repl.handle_line("measure the bracket") is True
    text = out.getvalue()
    assert "Measuring now." in text and "The bracket is fine." in text
    line = next(ln for ln in text.splitlines() if "probe_measure" in ln)
    assert line.startswith("▸ probe_measure bracket") and "✓" in line and "ms" in line
    assert "volume 1234" not in text  # collapsed: output only via /expand
    await repl.handle_line("/expand 1")
    text = out.getvalue()
    assert "volume 1234" in text and '"name": "bracket"' in text


async def test_repl_failed_tool_shows_error_code(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [
        [ToolCall(id="c1", name="nonexistent_tool", input={}), Done(stop_reason="tool_use")],
        [TextDelta(text="ok"), Done(stop_reason="end_turn")]])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    await repl.handle_line("go")
    line = next(ln for ln in out.getvalue().splitlines() if "nonexistent_tool" in ln)
    assert "✗" in line and "not_found" in line


async def test_repl_approval_requires_explicit_y(tmp_path, console, out):
    ran = []
    rt, _ = make_runtime(tmp_path, danger_turns())
    add_tools(rt, ran)
    lines = Lines("")
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=lines)
    await repl.handle_line("merge it")
    assert ran == []
    assert "approve? [y/N/e(dit)]" in lines.prompts[0]
    text = out.getvalue()
    assert "danger_merge" in text and "rejected" in text.lower()

    ran2 = []
    rt2, _ = make_runtime(tmp_path / "b", danger_turns())
    add_tools(rt2, ran2)
    repl2 = Repl(rt2, "designer", "fake/x", console=console, read_line=Lines("y"))
    await repl2.handle_line("merge it")
    assert ran2 == [1]


async def test_repl_paste_not_approval(tmp_path, console, out):
    ran = []
    rt, _ = make_runtime(tmp_path, danger_turns())
    add_tools(rt, ran)
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines("y\nmore"))
    await repl.handle_line("merge it")
    assert ran == []
    assert "paste" in out.getvalue().lower()


async def test_repl_other_answers_do_not_approve(tmp_path, console):
    for i, answer in enumerate(("yes", "ok", "n", "yy", "y\n")):
        ran = []
        rt, _ = make_runtime(tmp_path / str(i), danger_turns())
        add_tools(rt, ran)
        repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines(answer))
        await repl.handle_line("merge it")
        assert ran == [], answer


async def test_repl_edit_payload(tmp_path, console, out):
    ran = []
    rt, _ = make_runtime(tmp_path, danger_turns())
    add_tools(rt, ran)
    # e -> invalid JSON (rejected, re-asked) -> e -> valid JSON -> explicit y
    lines = Lines("e", "{not json", "e", '{"n": 7, "name": ""}', "y")
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=lines)
    await repl.handle_line("merge it")
    assert ran == [7]
    assert "invalid json" in out.getvalue().lower()


async def test_repl_question_numbered_pick(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [
        [ToolCall(id="c1", name="ui_ask_user",
                  input={"question": "Which metal?", "options": ["steel", "aluminium"]}),
         Done(stop_reason="tool_use")],
        [TextDelta(text="chosen"), Done(stop_reason="end_turn")]])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines("2"))
    await repl.handle_line("pick")
    text = out.getvalue()
    assert "Which metal?" in text and "1) steel" in text and "2) aluminium" in text
    steps = rt.store.steps(repl.run_id)
    assert any("aluminium" in str(s.payload) for s in steps)


async def test_slash_cost(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    await repl.handle_line("/cost")
    assert "no run yet" in out.getvalue().lower()
    await repl.handle_line("hello")
    rt.store.add_usage(repl.run_id, input_tokens=1200, output_tokens=345, usd=0.0123)
    await repl.handle_line("/cost")
    text = out.getvalue()
    assert "1200" in text and "345" in text and "$0.0123" in text and "Onshape" in text
    assert "/200" in text and "/2500" in text


async def test_slash_commands(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [[TextDelta(text="hi"), Done(stop_reason="end_turn")]])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    await repl.handle_line("hello")
    await repl.handle_line("/runs")
    await repl.handle_line("/steps")
    text = out.getvalue()
    assert repl.run_id in text and "designer" in text and "message" in text
    await repl.handle_line("/agent nope")
    assert "unknown agent" in out.getvalue()
    await repl.handle_line("/agent verifier")
    assert repl.agent == "verifier" and repl.run_id is None
    await repl.handle_line("/model bogus")
    assert repl.model_ref == "fake/x"
    await repl.handle_line("/frobnicate")
    assert "unknown command" in out.getvalue().lower()
    assert await repl.handle_line("/quit") is False


async def test_ctrl_c_cancels_run(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [
        [ToolCall(id="c1", name="slow_probe", input={}), Done(stop_reason="tool_use")]])
    add_tools(rt, [])
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    task = asyncio.create_task(repl.handle_line("go slow"))
    for _ in range(250):
        if repl.renderer.calls:
            break
        await asyncio.sleep(0.02)
    repl.interrupt()
    assert await asyncio.wait_for(task, 5) is True
    assert rt.store.get_run(repl.run_id).status == "cancelled"
    assert "cancelled" in out.getvalue().lower()
    assert rt.store.pending_approvals(repl.run_id) == []


async def test_double_ctrl_c_at_prompt_exits(tmp_path, console):
    rt, _ = make_runtime(tmp_path, [])

    class Interrupts:
        n = 0

        async def __call__(self, prompt, default=""):
            self.n += 1
            raise KeyboardInterrupt

    li = Interrupts()
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=li)
    await asyncio.wait_for(repl.run(), 5)
    assert li.n == 2


async def test_plan_runs_orchestrator(tmp_path, console, out):
    rt, _ = make_runtime(tmp_path, [])

    async def fake_run(goal, *, parent_run_id, model_ref=None):
        return {"plan": {}, "results": {"t1": {"task_id": "t1", "status": "done",
                                                "output": "built it", "run_id": "r"}}}
    rt.orchestrator.run = fake_run
    repl = Repl(rt, "designer", "fake/x", console=console, read_line=Lines())
    await repl.handle_line("/plan make a gearbox")
    text = out.getvalue()
    assert "t1" in text and "done" in text and "built it" in text
