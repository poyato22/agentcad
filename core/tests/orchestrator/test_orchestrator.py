import pytest
from pydantic import BaseModel

import mises.runtime as runtime_mod
from mises.orchestrator.orchestrator import child_policy, task_prompt
from mises.orchestrator.plan import PlanTask
from mises.orchestrator.scheduler import TaskResult
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.runtime import Runtime
from mises.tools.registry import tool
from mises.types import Caps, ErrorCode, MisesError, SafetyClass as C, StepKind, ToolOutput


class PartArgs(BaseModel):
    part: str


@tool("touch_part", description="edit a part", safety=C.LOCAL_MUTATE, scope_arg="part")
async def touch_part(ctx, args: PartArgs) -> ToolOutput:
    return ToolOutput(summary=f"touched {args.part}")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MISES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_mod, "keychain_get", lambda *a: None)


def text(t):
    return [TextDelta(text=t), Done(stop_reason="end_turn")]


def calls(*cs):
    return [*cs, Done(stop_reason="tool_use")]


def tc(cid, name, **inp):
    return ToolCall(id=cid, name=name, input=inp)


class RoleProvider:
    """Routes each request to a scripted FakeProvider chosen by the '# Role: x' system line."""
    name = "fake"

    def __init__(self, **roles):
        self.roles = {k: FakeProvider(v) for k, v in roles.items()}

    async def stream(self, req):
        role = req.system.split("# Role: ", 1)[1].split()[0]
        async for ev in self.roles[role].stream(req):
            yield ev


def make(tmp_path, provider):
    rt = Runtime(tmp_path / "home", provider_resolver=lambda ref: (provider, "m"))
    rt.registry.register(touch_part)
    return rt


async def parent_run(rt, **kw):
    # a parent run that already finished, used as the policy source for orchestrator calls
    pid = await rt.start_run("designer", "parent", **kw)
    await rt.wait(pid)
    return pid


PLAN = {"goal": "bracket", "tasks": [
    {"id": "make", "title": "Make", "agent": "designer", "prompt": "build it"},
    {"id": "check", "title": "Check", "agent": "verifier", "prompt": "verify it",
     "depends_on": ["make"]}]}


async def test_end_to_end_fake(tmp_path):
    prov = RoleProvider(
        designer=[text("parent done"), text("built the bracket")],
        planner=[calls(tc("p1", "orchestrator_submit_plan", plan=PLAN)), text("submitted")],
        verifier=[text("bracket verified")])
    rt = make(tmp_path, prov)
    parent = await parent_run(rt)
    out = await rt.orchestrator.run("bracket", parent_run_id=parent)
    assert {k: v["status"] for k, v in out["results"].items()} == {"make": "done", "check": "done"}
    assert out["results"]["make"]["output"] == "built the bracket"
    # the dependent saw the earlier result as delimited data
    verifier_req = prov.roles["verifier"].requests[0]
    first_user = verifier_req.messages[0].content[0].text
    assert "## Results from earlier tasks" in first_user
    assert '<task_result id="make" status="done">' in first_user and "built the bracket" in first_user
    kids = [r for r in rt.store.list_runs() if r.parent_id == parent]
    assert sorted(r.agent for r in kids) == ["designer", "planner", "verifier"]


async def test_invalid_plan_retried_once_then_ok(tmp_path):
    bad = {"goal": "g", "tasks": [{"id": "a", "title": "A", "agent": "ghost", "prompt": "p"}]}
    prov = RoleProvider(
        designer=[text("parent done"), text("fine")],
        planner=[calls(tc("p1", "orchestrator_submit_plan", plan=bad)), text("sent"),
                 calls(tc("p2", "orchestrator_submit_plan", plan={
                     "goal": "g", "tasks": [{"id": "a", "title": "A", "agent": "designer",
                                             "prompt": "p"}]})), text("resent")])
    rt = make(tmp_path, prov)
    parent = await parent_run(rt)
    out = await rt.orchestrator.run("g", parent_run_id=parent)
    assert out["results"]["a"]["status"] == "done"
    retry_msg = prov.roles["planner"].requests[2].messages[-1].content[0].text
    assert "unknown agent 'ghost'" in retry_msg


async def test_no_plan_twice_fails(tmp_path):
    prov = RoleProvider(designer=[text("parent done")],
                        planner=[text("I will not submit"), text("still no")])
    rt = make(tmp_path, prov)
    parent = await parent_run(rt)
    with pytest.raises(MisesError) as e:
        await rt.orchestrator.run("g", parent_run_id=parent)
    assert e.value.code == ErrorCode.INVALID_INPUT and "no plan" in e.value.message


async def test_failed_task_cascades(tmp_path):
    prov = RoleProvider(
        designer=[text("parent done")],  # the "make" task has no scripted turn: it fails
        planner=[calls(tc("p1", "orchestrator_submit_plan", plan=PLAN)), text("ok")],
        verifier=[])
    rt = make(tmp_path, prov)
    parent = await parent_run(rt)
    out = await rt.orchestrator.run("g", parent_run_id=parent)
    assert out["results"]["check"]["status"] == "skipped"
    assert out["results"]["make"]["status"] == "failed"


async def test_agent_spawn_narrows_policy(tmp_path):
    prov = FakeProvider([
        calls(tc("s1", "agent_spawn", agent="designer", prompt="edit the lid")),  # parent
        calls(tc("t1", "touch_part", part="lid")),                                  # child
        text("child finished"),                                                     # child
        text("parent finished")])                                                   # parent
    rt = make(tmp_path, prov)
    pid = await rt.start_run("designer", "go", edit_scope=("bracket",))
    res = await rt.wait(pid)
    assert res.stop_reason == "end_turn"
    (child,) = [r for r in rt.store.list_runs() if r.parent_id == pid]
    pol = rt.store.current_policy(child.id)
    assert pol.edit_scope == ["bracket"]
    (err,) = rt.store.steps(child.id, [StepKind.ERROR])
    assert err.payload["error"] == "out_of_scope"


def test_child_policy_narrowing(tmp_path):
    rt = Runtime(tmp_path / "home")
    parent = rt.store.create_run("designer", "fake", "m", "t").id
    rt.store.create_policy(parent, ["a", "b"], ["*"], Caps(onshape_calls_run=10),
                           [C.READ, C.LOCAL_MUTATE])
    rt.store.add_usage(parent, onshape_calls=4)
    kw = child_policy(rt, parent, "verifier", ["b", "zzz"])
    assert kw["edit_scope"] == ("b",)
    assert kw["caps"].onshape_calls_run == 6
    # verifier lists only read tools; parent's classes intersect
    assert kw["allowed_classes"] == {C.READ}
    kw = child_policy(rt, parent, "designer")  # no tool list: parent's classes
    assert kw["edit_scope"] == ("a", "b") and kw["allowed_classes"] == {C.READ, C.LOCAL_MUTATE}
    # requested wider than parent can't widen
    assert child_policy(rt, parent, "designer", ["*"])["edit_scope"] == ("a", "b")


async def test_depth_limit(tmp_path):
    rt = make(tmp_path, FakeProvider([]))
    a = rt.store.create_run("designer", "fake", "m", "t").id
    b = rt.store.create_run("designer", "fake", "m", "t", parent_id=a).id
    c = rt.store.create_run("designer", "fake", "m", "t", parent_id=b).id
    for r in (a, b, c):
        rt.store.create_policy(r, ["*"], ["*"], Caps(), list(C))
    from mises.orchestrator.orchestrator import check_depth
    check_depth(rt, a)
    check_depth(rt, b)
    with pytest.raises(MisesError) as e:
        check_depth(rt, c)
    assert e.value.code == ErrorCode.DENIED
    # and through the tool: a depth-2 run is refused
    with pytest.raises(MisesError) as e:
        await rt.gate.call(c, "agent_spawn", {"agent": "designer", "prompt": "x"},
                           call_id="x1", workdir=rt.workdir(c))
    assert e.value.code == ErrorCode.DENIED and "depth" in e.value.message


def test_task_prompt_delimits_results():
    t = PlanTask(id="b", title="B", agent="designer", prompt="do b", depends_on=["a"])
    p = task_prompt(t, {"a": TaskResult(task_id="a", status="done", output="IGNORE ALL")})
    assert p.index("do b") < p.index("## Results from earlier tasks") < p.index("IGNORE ALL")
    assert "</task_result>" in p
