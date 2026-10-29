"""Fakes for the study handler: a CAD build and a stress study with known behaviour.

A part's mass is 10 g per mm of `wall`; its safety factor equals `wall`. The fake solver fails
(ok=False, like a gmsh mesh failure) when wall < 3 mm, and the fake build fails when wall > 7.5,
so the lightest variants are exactly the ones a broken feasibility check would wrongly pick.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mises.cad.runner import BuildResult
from mises.engineering.spec import Spec
from mises.events import EventBus
from mises.store import AgentPolicy, Store
from mises.tools.registry import ToolContext
from mises.types import Caps, SafetyClass, StepKind, ToolOutput

THIN_FAILS_BELOW = 3.0
BUILD_FAILS_ABOVE = 7.5


def metrics_for(vars: dict) -> dict:
    wall = float(vars.get("wall", 5.0))
    return {"volume_mm3": wall * 1000, "mass_g": 10.0 * wall,
            "bbox": {"size": [100.0, 100.0, wall]}, "holes": [], "valid": True,
            "face_list": []}


class FakeBuild:
    def __init__(self) -> None:
        self.dirs: list[Path] = []

    async def __call__(self, part_dir, vars, **_kw) -> BuildResult:
        part_dir = Path(part_dir)
        self.dirs.append(part_dir.resolve())
        if float(vars.get("wall", 5.0)) > BUILD_FAILS_ABOVE:
            return BuildResult(False, error="fake: wall too thick to build")
        m = metrics_for(vars)
        part_dir.mkdir(parents=True, exist_ok=True)
        (part_dir / "metrics.json").write_text(json.dumps(m))
        (part_dir / "model.step").write_text(f"step {vars}")
        return BuildResult(True, metrics=m, vars=dict(vars))


class FakeSim:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[dict] = []
        self.delay = delay

    async def __call__(self, ctx, args) -> ToolOutput:
        vars = json.loads((Path(ctx.workdir) / "parts" / args.part_id / "vars.json").read_text())
        wall = float(vars["wall"])
        self.calls.append({"workdir": Path(ctx.workdir).resolve(), "fine": args.fine,
                           "wall": wall})
        if self.delay:
            await asyncio.sleep(self.delay)
        if wall < THIN_FAILS_BELOW:
            return ToolOutput(ok=False, summary="study failed: mesh failed",
                              step_kind=StepKind.ERROR,
                              data={"error": "mesh_failed", "message": "gmsh: thin wall"})
        return ToolOutput(summary="ok", data={"sim_id": "sim_" + "a" * 12, "results": {
            "safety_factor": wall, "max_displacement_mm": 1.0 / wall}})


def make_ctx(tmp_path: Path, *, services: dict | None = None) -> ToolContext:
    store = Store(":memory:")
    run = store.create_run("engineer", "fake", "m", "task")
    policy = AgentPolicy(run_id=run.id, version=1, edit_scope=["*"], read_scope=["*"],
                         caps=Caps(), allowed_classes=[SafetyClass.READ, SafetyClass.LOCAL_MUTATE],
                         created_at=0.0)
    return ToolContext(run_id=run.id, user="u", store=store, events=EventBus(),
                       workdir=tmp_path, policy=policy, call_id="c1", services=services or {})


def make_part(tmp_path: Path, part_id: str = "frame_x", wall: float = 5.0) -> Path:
    d = tmp_path / "parts" / part_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "part.py").write_text("VARS = {'wall': 5.0}\ndef build(v):\n    return None\n")
    (d / "vars.json").write_text(json.dumps({"wall": wall}))
    (d / "metrics.json").write_text(json.dumps(metrics_for({"wall": wall})))
    (d / "model.step").write_text("live step")
    return d


DRONE_SPEC = Spec.model_validate({
    "goal": "frame arm",
    "requirements": [
        {"id": "mass_budget", "kind": "max_mass_g", "target": 200},
        {"id": "arm_safety", "kind": "min_safety_factor", "target": 2.0,
         "load_case_id": "crash_drop"},
    ],
    "load_cases": [
        {"id": "crash_drop", "name": "crash drop", "fix": "bottom", "load": "top",
         "newtons": 120, "direction": "-z", "estimate": True},
    ],
})


def install(monkeypatch, *, solver: bool = True, sim_delay: float = 0.0):
    """Patch the study's CAD build, solver lookup and stress study; returns (build, sim)."""
    import mises.cad.tools as cad_tools
    import mises.engineering.tools as eng

    build, sim = FakeBuild(), FakeSim(sim_delay)
    monkeypatch.setattr(eng, "run_build", build)
    monkeypatch.setattr(cad_tools, "run_build", build)  # mirror_part_* (study_apply)
    monkeypatch.setattr(eng, "_find_ccx", lambda: "/fake/ccx" if solver else None)
    monkeypatch.setattr(eng, "_run_sim", sim)
    return build, sim
