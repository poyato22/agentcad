"""A recorded drone transcript: a scripted fake model drives the engineer through the loop
(estimate an impact load, then write the spec) and must leave a spec with >= 3 requirements and
>= 2 load cases persisted for the project."""
import pytest

from mises.agent.definitions import load_definitions
from mises.agent.loop import AgentLoop, LoopConfig
from mises.approvals import ApprovalService
from mises.engineering.tools import DEFAULT_PROJECT, register_engineering_tools
from mises.events import EventBus
from mises.gate import Gate
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.store import Store
from mises.tools.registry import ToolRegistry
from mises.types import Caps, SafetyClass as C
from mises.ui_tools import register_ui_tools

pytestmark = pytest.mark.asyncio


def _call(cid, name, **inp):
    return ToolCall(id=cid, name=name, input=inp)


SPEC = dict(
    goal='5" freestyle drone frame',
    requirements=[
        {"id": "mass_budget", "kind": "max_mass_g", "target": 120, "priority": 5,
         "source": "user"},
        {"id": "arm_safety", "kind": "min_safety_factor", "target": 2.0,
         "load_case_id": "crash_drop", "priority": 5},
        {"id": "frame_envelope", "kind": "envelope_mm", "target": [220, 220, 40],
         "priority": 2},
    ],
    load_cases=[
        {"id": "hover_thrust", "name": "hover thrust", "fix": "bottom", "load": "top",
         "newtons": 40, "direction": "-z"},
        {"id": "crash_drop", "name": "crash drop", "fix": "bottom", "load": "top",
         "newtons": 125, "direction": "-z", "estimate": True},
    ],
    answers={"use": "freestyle", "size_class": '5"'},
)

TRANSCRIPT = [
    [_call("t1", "loads_impact", mass_kg=0.5, drop_m=2.5, k=10), Done(stop_reason="tool_use")],
    [_call("t2", "spec_set", **SPEC), Done(stop_reason="tool_use")],
    [TextDelta(text="> [!note] Spec written: 3 requirements, 2 load cases."),
     Done(stop_reason="end_turn")],
]


async def test_drone_transcript_writes_a_spec(tmp_path):
    store = Store(":memory:")
    bus = EventBus()
    reg = ToolRegistry()
    register_engineering_tools(reg)
    register_ui_tools(reg)
    gate = Gate(reg, store, ApprovalService(store, bus), bus)
    run = store.create_run("engineer", "fake", "m", "design a 5 inch drone")
    store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])

    loop = AgentLoop(run_id=run.id, defn=load_definitions()["engineer"],
                     provider=FakeProvider([list(t) for t in TRANSCRIPT]), model="m", gate=gate,
                     registry=reg, store=store, events=bus, workdir=tmp_path,
                     config=LoopConfig(retry_base_delay=0))
    await loop.run("Build me a 5 inch drone.")

    spec = store.get_spec(DEFAULT_PROJECT)
    assert spec is not None
    assert len(spec["requirements"]) >= 3
    assert len(spec["load_cases"]) >= 2
    assert any(c["estimate"] for c in spec["load_cases"])
