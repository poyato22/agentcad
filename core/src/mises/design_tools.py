"""design_class_get (Plan 9 G-B, D27): an agent reads its build's design class and what it
means for the process (concept stage, skeleton, surfacing, critique, DFM). Read-only: the class
is set by the classifier, raised or lowered by the user's own messages, and overridden only by
the user on the build (PATCH /projects/{id} {design_class}; the admin token, never an agent)."""
from __future__ import annotations

from pydantic import BaseModel

from . import design_intensity as design_mod
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import SafetyClass, StepKind, ToolOutput


class DesignClassGetArgs(BaseModel):
    pass


def _run_class(ctx: ToolContext) -> dict | None:
    store, run_id = getattr(ctx, "store", None), getattr(ctx, "run_id", None)
    for _ in range(6):
        if store is None or run_id is None:
            return None
        try:
            run = store.get_run(run_id)
        except Exception:  # noqa: BLE001
            return None
        if run.design_class:
            return run.design_class
        run_id = run.parent_id
    return None


@tool("design_class_get", safety=SafetyClass.READ,
      description="Your build's design class (engineering | mixed | design), its score and "
                  "reasons, whether the user set it, and what it means for the process: "
                  "concept stage, skeleton, surfacing rules, critique rounds and weights, DFM "
                  "strictness. Only the user can change it (on the build); ask them when it "
                  "looks wrong.")
async def design_class_get(ctx: ToolContext, args: DesignClassGetArgs) -> ToolOutput:
    dc = _run_class(ctx)
    if dc is None:
        return ToolOutput(summary="no design class recorded for this run",
                          data={"design_class": None}, step_kind=StepKind.TOOL_RESULT)
    k = design_mod.knobs(dc)
    crit = k["critique"]
    knobs = {"concept": k["concept"], "skeleton": k["skeleton"], "surfacing": k["surfacing"],
             "critique": {"rounds": crit["rounds"], "weights": crit["weights"],
                          "views": crit["views"], "judge": crit["judge"]},
             "dfm": k["dfm"]}
    by_user = dc.get("source") == "user"
    return ToolOutput(
        summary=(f"design class {dc['cls']} (score {dc.get('score', 0):.2f}"
                 + (", set by the user" if by_user else "") + ")"),
        data={"design_class": dc, "set_by_user": by_user, "knobs": knobs},
        step_kind=StepKind.TOOL_RESULT)


def register_design_tools(registry: ToolRegistry) -> None:
    if not registry.list([design_class_get.name]):
        registry.register(design_class_get)
