"""A recorded drone transcript: a scripted fake model drives the engineer through the whole loop
(interview plan, one batched question, impact estimate, spec, two concepts built, each studied)
and must leave a spec with >= 3 requirements and >= 2 load cases, the interview answers, and a
design study for every concept."""
import json

import pytest

from mises.agent.definitions import load_definitions
from mises.agent.loop import AgentLoop, LoopConfig
from mises.approvals import ApprovalService
from mises.cad.tools import register_mirror_tools
from mises.engineering.tools import DEFAULT_PROJECT, register_engineering_tools
from mises.events import EventBus
from mises.gate import Gate
from mises.providers.base import Done, TextDelta, ToolCall
from mises.providers.fake import FakeProvider
from mises.store import Store
from mises.tools.registry import ToolRegistry
from mises.types import Caps, SafetyClass as C
from mises.ui_tools import register_ui_tools

from .fakes import install


def _call(cid, name, **inp):
    return ToolCall(id=cid, name=name, input=inp)


class FakeRT:
    def __init__(self, home):
        self.home = home
        self.asked: list = []

    async def ask(self, run_id, question, options, *, questions=None):
        self.asked.append(questions)
        return json.dumps({"use": "freestyle", "size_class": '5"'})


SPEC = dict(
    goal='5" freestyle drone frame',
    requirements=[
        {"id": "mass_budget", "kind": "max_mass_g", "target": 120, "priority": 4,
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
SCRIPT = "VARS = {'wall': 5.0}\ndef build(v):\n    return Box(100, 100, v['wall'])\n"
# wait: the transcript checks each study's result in the same run (an agent's default is the
# background, Plan 9 D4)
STUDY = dict(variables=[{"name": "wall", "lo": 1.0, "hi": 9.0}], max_evals=6,
             priorities=["impact strength", "weight"], wait=True)

TRANSCRIPT = [
    [_call("t1", "interview_plan", goal="build me a 5 inch drone"), Done(stop_reason="tool_use")],
    [_call("t2", "ui_ask_user", question="A few questions about your drone", questions=[
        {"id": "use", "prompt": "What will this drone do?", "options": ["freestyle", "racing"]},
        {"id": "size_class", "prompt": "Prop / size class?", "options": ['3"', '5"', '7"']}]),
     Done(stop_reason="tool_use")],
    [_call("t3", "loads_impact", mass_kg=0.5, drop_m=2.5, k=10), Done(stop_reason="tool_use")],
    [_call("t4", "spec_set", **SPEC), Done(stop_reason="tool_use")],
    [_call("t5", "mirror_part_create", part_id="frame_x", script=SCRIPT, vars={"wall": 5.0}),
     _call("t6", "mirror_part_create", part_id="frame_h", script=SCRIPT, vars={"wall": 5.0}),
     Done(stop_reason="tool_use")],
    [_call("t7", "study_run", part_id="frame_x", **STUDY),
     _call("t8", "study_run", part_id="frame_h", **STUDY), Done(stop_reason="tool_use")],
    [TextDelta(text="| concept | mass | SF |\n|---|---|---|\n> [!pass] frame_x picked."),
     Done(stop_reason="end_turn")],
]


async def test_drone_transcript_studies_every_concept(tmp_path, monkeypatch):
    install(monkeypatch)
    store = Store(":memory:")
    bus = EventBus()
    reg = ToolRegistry()
    register_mirror_tools(reg)
    register_engineering_tools(reg)
    register_ui_tools(reg)
    rt = FakeRT(tmp_path / "home")
    gate = Gate(reg, store, ApprovalService(store, bus), bus, services={"runtime": rt})
    run = store.create_run("engineer", "fake", "m", "design a 5 inch drone")
    store.create_policy(run.id, ["*"], ["*"], Caps(), [C.READ, C.LOCAL_MUTATE])

    provider = FakeProvider([list(t) for t in TRANSCRIPT])
    loop = AgentLoop(run_id=run.id, defn=load_definitions()["engineer"], provider=provider,
                     model="m", gate=gate, registry=reg, store=store, events=bus,
                     workdir=tmp_path, config=LoopConfig(retry_base_delay=0))
    await loop.run("Build me a 5 inch drone.")
    assert not provider._turns, "the whole transcript ran"

    assert rt.asked and len(rt.asked[0]) == 2  # one batched question call
    spec = store.get_spec(DEFAULT_PROJECT)
    assert len(spec["requirements"]) >= 3 and len(spec["load_cases"]) >= 2
    assert spec["answers"]["use"] == "freestyle"

    studies = store.list_studies(DEFAULT_PROJECT)
    by_part = {s["part_id"]: s for s in studies}
    assert {"frame_x", "frame_h"} <= set(by_part)
    for s in by_part.values():
        assert s["status"] == "done" and s["iterations"] and s["best_id"] is not None
        assert s["objective"] == "weighted"
