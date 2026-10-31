"""Plan 9 D29: agents delete parts with `part_delete`, the same delete the app's part menu does
(projects.delete_part: files to the trash, children lifted, the part's assembly instances removed
with it). A part the user edited by hand asks the user first (needs_approval), so an agent never
quietly throws away the user's work; the result's `undo` lets the app restore it."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from . import edits
from .events import Event
from .tools.registry import ToolContext, ToolRegistry, tool
from .types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput


class PartDeleteArgs(BaseModel):
    part_id: str
    cascade: bool = Field(default=True, description="Also remove the part's instances (and "
                          "their joints) from the build's assembly. false: refused while the "
                          "assembly uses the part.")


def _home(ctx: ToolContext) -> Path:
    rt = (getattr(ctx, "services", None) or {}).get("runtime")
    home = getattr(rt, "home", None)
    if home is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "part_delete needs the runtime")
    return Path(home)


def _user_edited(ctx: ToolContext, args: PartDeleteArgs) -> str | None:
    try:
        if edits.recent_user_edits(_home(ctx), part_id=args.part_id):
            return f"delete {args.part_id}, which you edited yourself"
    except MisesError:
        return None
    return None


async def _preview(ctx: ToolContext, args: PartDeleteArgs) -> dict:
    asm = (ctx.services or {}).get("assembly")
    row = ctx.store.get_part(args.part_id) if getattr(ctx, "store", None) is not None else None
    n = 0
    if asm is not None and row is not None and row.project_id:
        n = sum(1 for i in asm.get(row.project_id).instances if i.part_id == args.part_id)
    return {"part_id": args.part_id, "instances": n,
            "note": "Approving moves the part to the trash"
                    + (f" and removes its {n} instance(s) from the assembly." if n else ".")}


def _lineage(ctx: ToolContext) -> list[str]:
    """This run and the runs above it: they are the ones asking, never a reason to refuse."""
    out, rid = [], getattr(ctx, "run_id", None)
    while rid is not None and rid not in out:
        out.append(rid)
        try:
            rid = ctx.store.get_run(rid).parent_id
        except Exception:  # noqa: BLE001 - no store (a bare test context) or an unknown run
            break
    return out


@tool("part_delete", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      needs_approval=_user_edited, preview=_preview,
      description="Delete a part of this build: its files go to the trash (the user can undo), "
                  "its child parts move up a level, and (cascade, the default) its instances "
                  "leave the assembly. A part the user edited asks the user first.")
async def part_delete(ctx: ToolContext, args: PartDeleteArgs) -> ToolOutput:
    from . import projects
    from .cad.tools import PART_ID_RE, _lock
    home = _home(ctx)
    rt = ctx.services.get("runtime")
    row = ctx.store.get_part(args.part_id)
    if row is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no part {args.part_id!r}")

    def run() -> dict:
        return projects.delete_part(ctx.store, home, args.part_id,
                                    assembly=ctx.services.get("assembly"),
                                    busy=getattr(rt, "_busy_for_trash", None),
                                    cascade=args.cascade, ignore_runs=_lineage(ctx))
    try:
        if PART_ID_RE.match(args.part_id):
            async with _lock(home / "parts" / args.part_id):   # never mid-build
                out = run()
        else:
            out = run()
    except projects.PartBusy as e:
        raise MisesError(ErrorCode.INVALID_INPUT, e.message, hint=e.hint) from None
    events = getattr(ctx, "events", None)
    if events is not None:
        if out["removed_instances"] and row.project_id:
            events.publish(Event(type="assembly_changed", data={"project_id": row.project_id}))
        events.publish(Event(type="part_changed", run_id=ctx.run_id, data={
            "part_id": args.part_id, "version": row.version, "by": "agent", "run_id": ctx.run_id,
            "summary": f"deleted {args.part_id}", "deleted": True}))
    n = len(out["removed_instances"])
    return ToolOutput(summary=f"deleted {args.part_id}"
                              + (f" and its {n} assembly instance(s)" if n else ""),
                      data=out, step_kind=StepKind.DIFF)


def register_part_tools(registry: ToolRegistry) -> None:
    if not registry.list([part_delete.name]):
        registry.register(part_delete)
