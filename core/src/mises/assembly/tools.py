"""Agent tools for assemblies: read the assembly, add instances and joints, drive a joint, and
check interference plus the degrees of freedom left. Mutations run under local_mutate scope and
emit ``assembly_changed``; the solve rejects joint loops before anything is written."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from .geometry import face_resolver, run_geometry
from .model import AssemblyStore, Frame, Instance, Joint, JointRef, JointKind
from .solve import SolveResult, solve


def _store(ctx: ToolContext) -> AssemblyStore:
    store = ctx.services.get("assembly")
    if store is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "assembly store is not available")
    return store


def _parts_root(ctx: ToolContext) -> Path:
    return Path(ctx.workdir) / "parts"


def _solve(ctx: ToolContext, project_id: str) -> tuple:
    asm = _store(ctx).get(project_id)
    res = solve(asm, resolve_face=face_resolver(_parts_root(ctx)))
    return asm, res


def _changed(ctx: ToolContext, project_id: str) -> None:
    ctx.events.publish(Event(type="assembly_changed", data={"project_id": project_id}))


def _asm_data(asm, res: SolveResult) -> dict:
    return {
        "instances": [{**i.model_dump(), "world_transform": res.transforms[i.id]}
                      for i in asm.instances],
        "joints": [j.model_dump() for j in asm.joints],
        "dof_remaining": res.dof_remaining,
    }


class ProjectArgs(BaseModel):
    project_id: str


class AddInstanceArgs(BaseModel):
    project_id: str
    part_id: str
    name: str
    transform: list[list[float]] | None = None
    fixed: bool = False


class AddJointArgs(BaseModel):
    project_id: str
    kind: JointKind
    a: JointRef
    b: JointRef
    value: float | list[float] = 0.0
    limits: list[float] | None = None


class DriveArgs(BaseModel):
    project_id: str
    joint_id: str
    value: float | list[float]


class RemoveArgs(BaseModel):
    project_id: str
    instance_id: str | None = None
    joint_id: str | None = None


@tool("assembly_get", safety=SafetyClass.READ, scope_arg="project_id",
      description="Read a build's assembly: its instances (with solved world transforms), its "
                  "joints and the degrees of freedom left.")
async def assembly_get(ctx: ToolContext, args: ProjectArgs) -> ToolOutput:
    asm, res = _solve(ctx, args.project_id)
    return ToolOutput(summary=f"{len(asm.instances)} instance(s), {len(asm.joints)} joint(s), "
                              f"{res.dof_remaining} dof left",
                      data=_asm_data(asm, res), step_kind=StepKind.MEASURE)


@tool("assembly_add_instance", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Place a part in the build's assembly as a new instance. The first instance "
                  "is usually `fixed: true` to ground the assembly.")
async def assembly_add_instance(ctx: ToolContext, args: AddInstanceArgs) -> ToolOutput:
    kw = {"part_id": args.part_id, "name": args.name, "fixed": args.fixed}
    if args.transform is not None:
        kw["transform"] = args.transform
    inst = _store(ctx).add_instance(args.project_id, Instance(**kw))
    _changed(ctx, args.project_id)
    return ToolOutput(summary=f"added instance {inst.name} ({inst.id}) of {inst.part_id}",
                      data={"instance": inst.model_dump()}, step_kind=StepKind.DIFF)


@tool("assembly_add_joint", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Join two instances. kind is fixed/revolute/slider/cylindrical/ball; each side "
                  "gives a frame (origin+axis in part mm, or a face_id). Rejected if it closes a "
                  "loop. revolute value is degrees, slider mm, cylindrical [deg, mm], ball "
                  "[rx, ry, rz] deg. limits [lo, hi] clamp the value.")
async def assembly_add_joint(ctx: ToolContext, args: AddJointArgs) -> ToolOutput:
    store = _store(ctx)
    joint = Joint(kind=args.kind, a=args.a, b=args.b, value=args.value, limits=args.limits)
    # validate against the would-be assembly before writing: solve rejects loops / unknown refs
    asm = store.get(args.project_id)
    trial = asm.model_copy(update={"joints": [*asm.joints, joint]})
    solve(trial, resolve_face=face_resolver(_parts_root(ctx)))
    store.add_joint(args.project_id, joint)
    _changed(ctx, args.project_id)
    return ToolOutput(summary=f"added {joint.kind} joint {joint.id}",
                      data={"joint": joint.model_dump()}, step_kind=StepKind.DIFF)


@tool("assembly_drive", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Set a joint's value and return the solved world transforms. The value is "
                  "clamped to the joint's limits.")
async def assembly_drive(ctx: ToolContext, args: DriveArgs) -> ToolOutput:
    store = _store(ctx)
    try:
        store.get_joint(args.project_id, args.joint_id)
    except KeyError:
        raise MisesError(ErrorCode.NOT_FOUND, f"no joint {args.joint_id!r}")
    store.update_joint(args.project_id, args.joint_id, value=args.value)
    asm, res = _solve(ctx, args.project_id)
    _changed(ctx, args.project_id)
    applied = res.applied.get(args.joint_id, args.value)
    return ToolOutput(summary=f"drove joint {args.joint_id} to {applied}",
                      data={"applied": applied,
                            "transforms": {i.id: res.transforms[i.id] for i in asm.instances}},
                      step_kind=StepKind.DIFF)


@tool("assembly_remove", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Remove one instance (and any joints that used it) or one joint.")
async def assembly_remove(ctx: ToolContext, args: RemoveArgs) -> ToolOutput:
    store = _store(ctx)
    if (args.instance_id is None) == (args.joint_id is None):
        raise MisesError(ErrorCode.INVALID_INPUT, "give exactly one of instance_id or joint_id")
    try:
        if args.instance_id is not None:
            dropped = store.delete_instance(args.project_id, args.instance_id)
            summary = (f"removed instance {args.instance_id}"
                       + (f" and {len(dropped)} joint(s)" if dropped else ""))
        else:
            store.delete_joint(args.project_id, args.joint_id)
            summary = f"removed joint {args.joint_id}"
    except KeyError as e:
        raise MisesError(ErrorCode.NOT_FOUND, f"nothing to remove: {e}")
    _changed(ctx, args.project_id)
    return ToolOutput(summary=summary, step_kind=StepKind.DIFF)


@tool("assembly_check", safety=SafetyClass.READ, scope_arg="project_id",
      description="Check the assembly: interfering instance pairs (OCCT common volume) and the "
                  "degrees of freedom left. Uses the built parts' geometry.")
async def assembly_check(ctx: ToolContext, args: ProjectArgs) -> ToolOutput:
    asm, res = _solve(ctx, args.project_id)
    interferences: list[dict] = []
    try:
        geo = await run_geometry(_parts_root(ctx), asm, res, want="interference")
        interferences = geo.interferences
    except MisesError as e:
        if e.code != ErrorCode.NOT_FOUND:  # NOT_FOUND = no built parts yet; report dof only
            raise
    n = len(interferences)
    return ToolOutput(summary=f"{n} interference(s), {res.dof_remaining} dof left",
                      data={"interferences": interferences, "dof_remaining": res.dof_remaining},
                      step_kind=StepKind.MEASURE)


def register_assembly_tools(registry: ToolRegistry) -> None:
    for t in (assembly_get, assembly_add_instance, assembly_add_joint, assembly_drive,
              assembly_remove, assembly_check):
        registry.register(t)
