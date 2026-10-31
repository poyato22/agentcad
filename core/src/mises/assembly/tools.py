"""Agent tools for assemblies: read the assembly, add instances and joints, drive a joint, and
check interference plus the degrees of freedom left. Mutations run under local_mutate scope and
emit ``assembly_changed``; the solve rejects joint loops before anything is written."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import motion
from .geometry import face_resolver, resolve_face, run_geometry
from .model import AssemblyStore, Frame, Instance, Joint, JointKind, JointRef, check_part_id
from .solve import SolveResult, solve, translate


def _store(ctx: ToolContext) -> AssemblyStore:
    store = ctx.services.get("assembly")
    if store is None:
        raise MisesError(ErrorCode.TOOL_FAILED, "assembly store is not available")
    return store


def _parts_root(ctx: ToolContext) -> Path:
    return Path(ctx.workdir) / "parts"


def _resolve_build(ctx: ToolContext, requested: str) -> str:
    """The build this call may touch. Like catalog/engineering, an agent run can only reach its
    own build (requesting another is OUT_OF_SCOPE); the UI (admin) may name any build. The build
    must exist and not be trashed. A bare context (a unit test with no store) resolves to the
    requested id unchanged."""
    store = getattr(ctx, "store", None)
    if store is None or getattr(ctx, "run_id", None) is None:
        return requested
    from ..catalog.tools import resolve_project
    pid = resolve_project(ctx, requested) or requested
    if hasattr(store, "get_project"):
        try:
            proj = store.get_project(pid)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"no build {pid!r}",
                             hint="create or open the build first") from None
        if getattr(proj, "trashed_at", None) is not None:
            raise MisesError(ErrorCode.INVALID_INPUT, f"build {pid!r} is in the trash",
                             hint="restore it first")
    return pid


def _check_part_in_build(ctx: ToolContext, pid: str, part_id: str) -> None:
    """A part added to an assembly must belong to that build (or be in this run's scope)."""
    store = getattr(ctx, "store", None)
    if store is None or not hasattr(store, "get_part"):
        return
    row = store.get_part(part_id)
    if row is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no part {part_id!r}",
                         hint="build or import it into this build first")
    pol = getattr(ctx, "policy", None)
    scope = set(pol.edit_scope) if pol is not None else {"*"}
    if row.project_id != pid and "*" not in scope and part_id not in scope:
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"part {part_id!r} is not in build {pid!r}",
                         hint="add a part that belongs to this build")


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
        "overconstrained": res.overconstrained,
    }


class ProjectArgs(BaseModel):
    project_id: str


class AddInstanceArgs(BaseModel):
    project_id: str
    part_id: str
    name: str
    transform: list[list[float]] | None = None
    fixed: bool = False

    @field_validator("part_id")
    @classmethod
    def _part_id(cls, v: str) -> str:
        return check_part_id(v)


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
    asm, res = _solve(ctx, _resolve_build(ctx, args.project_id))
    return ToolOutput(summary=f"{len(asm.instances)} instance(s), {len(asm.joints)} joint(s), "
                              f"{res.dof_remaining} dof left",
                      data=_asm_data(asm, res), step_kind=StepKind.MEASURE)


@tool("assembly_add_instance", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Place a part in the build's assembly as a new instance. The first instance "
                  "is usually `fixed: true` to ground the assembly.")
async def assembly_add_instance(ctx: ToolContext, args: AddInstanceArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    _check_part_in_build(ctx, pid, args.part_id)
    kw = {"part_id": args.part_id, "name": args.name, "fixed": args.fixed}
    if args.transform is not None:
        kw["transform"] = args.transform
    inst = _store(ctx).add_instance(pid, Instance(**kw))
    _changed(ctx, pid)
    return ToolOutput(summary=f"added instance {inst.name} ({inst.id}) of {inst.part_id}",
                      data={"instance": inst.model_dump()}, step_kind=StepKind.DIFF)


@tool("assembly_add_joint", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Join two instances. kind is fixed/revolute/slider/cylindrical/ball; each side "
                  "gives a frame (origin+axis in part mm, or a face_id). Rejected if it closes a "
                  "loop. revolute value is degrees, slider mm, cylindrical [deg, mm], ball "
                  "[rx, ry, rz] deg. limits [lo, hi] clamp the value.")
async def assembly_add_joint(ctx: ToolContext, args: AddJointArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    store = _store(ctx)
    joint = Joint(kind=args.kind, a=args.a, b=args.b, value=args.value, limits=args.limits)
    # validate against the would-be assembly before writing: solve rejects loops / unknown refs
    asm = store.get(pid)
    trial = asm.model_copy(update={"joints": [*asm.joints, joint]})
    solve(trial, resolve_face=face_resolver(_parts_root(ctx)))
    store.add_joint(pid, joint)
    _changed(ctx, pid)
    return ToolOutput(summary=f"added {joint.kind} joint {joint.id}",
                      data={"joint": joint.model_dump()}, step_kind=StepKind.DIFF)


@tool("assembly_drive", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Set a joint's value and return the solved world transforms. The value is "
                  "clamped to the joint's limits.")
async def assembly_drive(ctx: ToolContext, args: DriveArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    store = _store(ctx)
    try:
        store.get_joint(pid, args.joint_id)
    except KeyError:
        raise MisesError(ErrorCode.NOT_FOUND, f"no joint {args.joint_id!r}")
    store.update_joint(pid, args.joint_id, value=args.value)
    asm, res = _solve(ctx, pid)
    _changed(ctx, pid)
    applied = res.applied.get(args.joint_id, args.value)
    return ToolOutput(summary=f"drove joint {args.joint_id} to {applied}",
                      data={"applied": applied,
                            "transforms": {i.id: res.transforms[i.id] for i in asm.instances}},
                      step_kind=StepKind.DIFF)


@tool("assembly_remove", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Remove one instance (and any joints that used it) or one joint.")
async def assembly_remove(ctx: ToolContext, args: RemoveArgs) -> ToolOutput:
    pid = _resolve_build(ctx, args.project_id)
    store = _store(ctx)
    if (args.instance_id is None) == (args.joint_id is None):
        raise MisesError(ErrorCode.INVALID_INPUT, "give exactly one of instance_id or joint_id")
    try:
        if args.instance_id is not None:
            dropped = store.delete_instance(pid, args.instance_id)
            summary = (f"removed instance {args.instance_id}"
                       + (f" and {len(dropped)} joint(s)" if dropped else ""))
        else:
            store.delete_joint(pid, args.joint_id)
            summary = f"removed joint {args.joint_id}"
    except KeyError as e:
        raise MisesError(ErrorCode.NOT_FOUND, f"nothing to remove: {e}")
    _changed(ctx, pid)
    return ToolOutput(summary=summary, step_kind=StepKind.DIFF)


class SweepArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    joint_id: str
    from_: float | list[float] | None = Field(
        default=None, alias="from", description="Start value; default the joint's current one.")
    to: float | list[float]
    steps: int = Field(default=5, ge=2, le=21, description="Positions checked, ends included.")


class CheckArgs(ProjectArgs):
    sweep: SweepArgs | None = Field(
        default=None, description="Check interference across a joint's range of motion: the "
        "joint is set to `steps` evenly spaced values from `from` to `to` (nothing is stored) "
        "and every position is checked.")


SWEEP_TIMEOUT_S = 240.0        # one worker for the whole sweep; the tool's own cap is above it


@tool("assembly_check", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=300,
      description="Check the assembly: interfering instance pairs (OCCT common volume), the "
                  "degrees of freedom left, and over-constraint: groups of 2+ fixed instances "
                  "linked by a joint path (a fixed instance never moves, so the joint closing "
                  "that path cannot be satisfied; unfix one or drop a joint). Fixed instances "
                  "with no joint path between them are independent and fine. With `sweep` "
                  "{joint_id, from, to, steps} it checks interference at each position across "
                  "that joint's motion instead.")
async def assembly_check(ctx: ToolContext, args: CheckArgs) -> ToolOutput:
    sweep = getattr(args, "sweep", None)
    if sweep is not None:
        return await _check_sweep(ctx, _resolve_build(ctx, args.project_id), sweep)
    asm, res = _solve(ctx, _resolve_build(ctx, args.project_id))
    interferences: list[dict] = []
    try:
        geo = await run_geometry(_parts_root(ctx), asm, res, want="interference")
        interferences = geo.interferences
    except MisesError as e:
        if e.code != ErrorCode.NOT_FOUND:  # NOT_FOUND = no built parts yet; report dof only
            raise
    n = len(interferences)
    summary = f"{n} interference(s), {res.dof_remaining} dof left"
    if res.overconstrained:
        groups = "; ".join(", ".join(g) for g in res.overconstrained)
        summary += f"; over-constrained (fixed instances joined by joints): {groups}"
    return ToolOutput(summary=summary,
                      data={"interferences": interferences, "dof_remaining": res.dof_remaining,
                            "overconstrained": res.overconstrained},
                      step_kind=StepKind.MEASURE)


async def _check_sweep(ctx: ToolContext, pid: str, sweep: SweepArgs) -> ToolOutput:
    asm = _store(ctx).get(pid)
    joint = _joint_or_404(asm, sweep.joint_id)
    start = joint.value if sweep.from_ is None else sweep.from_
    resolver = face_resolver(_parts_root(ctx))
    values, poses = [], []
    for k in range(sweep.steps):
        value = motion.clamp_value(motion.lerp(start, sweep.to, k / (sweep.steps - 1)),
                                   joint.limits)
        res = solve(motion.with_values(asm, {joint.id: value}), resolve_face=resolver)
        values.append(value)
        poses.append(res.transforms)
    # ONE sandboxed worker for every position (each STEP read once), inside the tool timeout
    try:
        geo = await run_geometry(_parts_root(ctx), asm, solve(asm, resolve_face=resolver),
                                 poses=poses, timeout_s=SWEEP_TIMEOUT_S)
    except MisesError as e:
        if e.code == ErrorCode.NOT_FOUND:
            raise MisesError(ErrorCode.NOT_FOUND, "no built parts in this assembly",
                             hint="build the parts before sweeping for interference") from None
        if "timed out" in e.message:
            raise MisesError(ErrorCode.TOOL_FAILED, e.message,
                             hint="sweep fewer steps, or check a narrower range") from None
        raise
    names = {i.id: i.name for i in asm.instances}
    colliding, best = [], None
    for value, pos in zip(values, geo.positions):
        if pos.get("interferences"):
            colliding.append({"value": value, "pairs": [
                {"a": names.get(h["a"], h["a"]), "b": names.get(h["b"], h["b"]),
                 "volume_mm3": h.get("volume_mm3")} for h in pos["interferences"]]})
        c = pos.get("min_clearance_mm")
        if c is not None and (best is None or c < best[0]):
            best = (c, value, [names.get(n, n) for n in pos.get("closest") or []])
    summary = (f"swept joint {joint.id} ({joint.kind}) from {start} to {sweep.to} in "
               f"{len(values)} positions: ")
    summary += (f"interference at {len(colliding)} of {len(values)} (first at "
                f"{colliding[0]['value']})" if colliding else "no interference at any position")
    if best is not None:
        summary += f"; min clearance {best[0]:g} mm at {best[1]}"
    # compact: the agent gets the verdict; the app plays the motion from assembly_animate
    return ToolOutput(summary=summary,
                      data={"joint_id": joint.id, "from": start, "to": sweep.to,
                            "steps": len(values), "interfering_positions": len(colliding),
                            "colliding": colliding,
                            "min_clearance_mm": None if best is None else best[0],
                            "min_clearance_at": None if best is None else best[1],
                            "closest_pair": None if best is None else best[2]},
                      step_kind=StepKind.MEASURE)


# ---- Plan 7 R11 / R19: agents bring parts into the assembly, move them and show the motion
def _build_of(ctx: ToolContext, requested: str | None) -> str:
    """The build a call works on: the one named, else the run's own build, else the active one."""
    if requested:
        return _resolve_build(ctx, requested)
    pid = None
    if getattr(ctx, "store", None) is not None and getattr(ctx, "run_id", None) is not None:
        from ..catalog.tools import _default_project, resolve_project
        pid = resolve_project(ctx, None) or _default_project(ctx)
    if not pid:
        raise MisesError(ErrorCode.INVALID_INPUT, "no build to work on",
                         hint="pass project_id")
    return _resolve_build(ctx, pid)


def _joint_or_404(asm, joint_id: str):
    for j in asm.joints:
        if j.id == joint_id:
            return j
    raise MisesError(ErrorCode.NOT_FOUND, f"no joint {joint_id!r}",
                     hint="assembly_get lists the joints")


def _instance_or_404(asm, iid: str):
    for i in asm.instances:
        if i.id == iid:
            return i
    raise MisesError(ErrorCode.NOT_FOUND, f"no instance {iid!r}",
                     hint="assembly_get lists the instances")


def _vec3(v: list[float] | None) -> list[float] | None:
    if v is not None and len(v) != 3:
        raise ValueError("must be 3 numbers [x, y, z] in mm")
    return v


class Mate(BaseModel):
    instance_id: str | None = Field(
        default=None, description="The instance to join the new part to; default the grounded "
        "(first fixed) instance.")
    face_id: str | None = Field(default=None,
                                description="A face of that instance's part to sit on.")
    kind: JointKind = "fixed"
    value: float | list[float] = 0.0
    limits: list[float] | None = None


class Place(BaseModel):
    on_face: str | None = Field(
        default=None, description="The new part's face that rests on the mate face (its "
        "outward normal is flipped so the faces meet instead of overlapping).")
    mate: Mate | None = None
    offset: list[float] | None = Field(
        default=None, description="[x, y, z] mm: shifts the mate point (in the target part's "
        "coordinates); for the first, grounded instance it is its world position.")

    @field_validator("offset")
    @classmethod
    def _offset3(cls, v):
        return _vec3(v)


class AddPartArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    part_id: str
    name: str | None = Field(default=None, description="Instance name; default the part id.")
    place: Place = Field(default_factory=Place)

    @field_validator("part_id")
    @classmethod
    def _part_id(cls, v: str) -> str:
        return check_part_id(v)


def _flipped_face(parts_root: Path, part_id: str, face_id: str) -> Frame:
    origin, normal = resolve_face(parts_root, part_id, face_id)
    return Frame(origin=list(origin), axis=[-c for c in normal])


@tool("assembly_add_part", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Bring a part into the build's assembly in one call: adds an instance and a "
                  "default joint. The first part is grounded (fixed); later ones are joined to "
                  "the grounded instance (or place.mate.instance_id) with a fixed joint unless "
                  "place.mate.kind says otherwise. place.on_face + place.mate.face_id sit one "
                  "face on another; place.offset shifts the placement in mm.")
async def assembly_add_part(ctx: ToolContext, args: AddPartArgs) -> ToolOutput:
    pid = _build_of(ctx, args.project_id)
    _check_part_in_build(ctx, pid, args.part_id)
    store = _store(ctx)
    asm = store.get(pid)
    place, mate = args.place, args.place.mate or Mate()
    name = args.name or args.part_id
    root = _parts_root(ctx)
    # Plan 9 D10: a new part is already placed by itself (assembly/autoplace.py). Adding it
    # joins THAT instance instead of placing a second copy (once: it is then the agent's own);
    # a later call for the same part adds another instance as before.
    auto = store.auto_ids(pid)
    reuse = next((i for i in asm.instances if i.part_id == args.part_id and i.id in auto), None)
    if reuse is not None:
        store.mark_auto(pid, reuse.id, False)
    if reuse is not None and (reuse.fixed or len(asm.instances) == 1):
        # it is the grounded first part already: only the name and the offset can change
        upd: dict = {"fixed": True, **({"name": args.name} if args.name else {})}
        if place.offset is not None:
            upd["transform"] = translate(place.offset)
        inst = store.update_instance(pid, reuse.id, **upd)
        _changed(ctx, pid)
        return ToolOutput(summary=f"{args.part_id} is instance {inst.name} ({inst.id}), "
                                  "grounded: the first part of the assembly",
                          data={"project_id": pid, "instance": inst.model_dump(), "joint": None},
                          step_kind=StepKind.DIFF)
    if reuse is not None:
        asm = asm.model_copy(update={"instances": [i for i in asm.instances if i.id != reuse.id]})
        name = args.name or reuse.name
    if not asm.instances:
        kw: dict = {"part_id": args.part_id, "name": name, "fixed": True}
        if place.offset is not None:
            kw["transform"] = translate(place.offset)
        inst = store.add_instance(pid, Instance(**kw))
        _changed(ctx, pid)
        return ToolOutput(summary=f"added {args.part_id} as instance {inst.name} ({inst.id}), "
                                  "grounded: the first part of the assembly",
                          data={"project_id": pid, "instance": inst.model_dump(), "joint": None},
                          step_kind=StepKind.DIFF)
    target = (_instance_or_404(asm, mate.instance_id) if mate.instance_id
              else next((i for i in asm.instances if i.fixed), asm.instances[0]))
    off = place.offset or [0.0, 0.0, 0.0]
    if mate.face_id is not None:
        if place.offset is not None:     # a face frame plus a shift: resolve the face now
            origin, normal = resolve_face(root, target.part_id, mate.face_id)
            fa = Frame(origin=[o + d for o, d in zip(origin, off)], axis=list(normal))
        else:
            fa = Frame(face_id=mate.face_id)
    else:
        fa = Frame(origin=list(off), axis=[0.0, 0.0, 1.0])
    fb = (_flipped_face(root, args.part_id, place.on_face) if place.on_face is not None
          else Frame(origin=[0.0, 0.0, 0.0], axis=[0.0, 0.0, 1.0]))
    inst = (Instance(id=reuse.id, part_id=args.part_id, name=name) if reuse is not None
            else Instance(part_id=args.part_id, name=name))
    joint = Joint(kind=mate.kind, a=JointRef(instance_id=target.id, frame=fa),
                  b=JointRef(instance_id=inst.id, frame=fb), value=mate.value,
                  limits=mate.limits)
    # validate the would-be assembly before writing anything (loops, unknown faces)
    trial = asm.model_copy(update={"instances": [*asm.instances, inst],
                                   "joints": [*asm.joints, joint]})
    res = solve(trial, resolve_face=face_resolver(root))
    if reuse is not None:
        store.delete_instance(pid, reuse.id)       # no joints touch it (checked above)
    store.add_instance(pid, inst)
    store.add_joint(pid, joint)
    _changed(ctx, pid)
    return ToolOutput(summary=f"added {args.part_id} as instance {inst.name} ({inst.id}), "
                              f"{joint.kind} joint {joint.id} to {target.name} ({target.id})",
                      data={"project_id": pid, "instance": inst.model_dump(),
                            "joint": {**joint.model_dump(), "target": target.id},
                            "world_transform": motion.round_mat(res.transforms[inst.id])},
                      step_kind=StepKind.DIFF)


class Rotate(BaseModel):
    axis: list[float]
    angle_deg: float
    about: list[float] | None = Field(
        default=None, description="World pivot point (mm) for a free instance; default its "
        "origin. A joint-attached instance always turns about its joint axis.")

    @field_validator("axis", "about")
    @classmethod
    def _xyz(cls, v):
        return _vec3(v)


class MoveArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    instance_id: str
    translate: list[float] | None = Field(default=None, description="[x, y, z] mm.")
    rotate: Rotate | None = None
    relative: bool = Field(
        default=True, description="true: translate/rotate are deltas. false: translate is the "
        "new world origin and rotate.angle_deg the absolute angle.")

    @field_validator("translate")
    @classmethod
    def _xyz(cls, v):
        return _vec3(v)

    @model_validator(mode="after")
    def _something(self) -> "MoveArgs":
        if self.translate is None and self.rotate is None:
            raise ValueError("give translate and/or rotate")
        return self


@tool("assembly_move", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id",
      description="Move an instance. A free instance (grounded, or not joined to anything) moves "
                  "anywhere and its attached parts follow. A joint-attached instance moves only "
                  "along its joint: a slider along its axis, a revolute about its axis, a "
                  "cylindrical both, a ball about its frame axes; the move becomes the joint's "
                  "new value (clamped to its limits). A fixed-joint instance cannot move.")
async def assembly_move(ctx: ToolContext, args: MoveArgs) -> ToolOutput:
    pid = _build_of(ctx, args.project_id)
    store = _store(ctx)
    asm = store.get(pid)
    inst = _instance_or_404(asm, args.instance_id)
    resolver = face_resolver(_parts_root(ctx))
    res = solve(asm, resolve_face=resolver)
    rot = args.rotate.model_dump() if args.rotate is not None else None
    if inst.id not in motion.placement(asm):
        new_t = motion.move_free(res.transforms[inst.id], translate_by=args.translate,
                                 rotate=rot, relative=args.relative)
        store.update_instance(pid, inst.id, transform=new_t)
        asm2, res2 = _solve(ctx, pid)
        _changed(ctx, pid)
        return ToolOutput(summary=f"moved free instance {inst.name} ({inst.id})",
                          data={"instance_id": inst.id, "joint_id": None,
                                "world_transform": motion.round_mat(res2.transforms[inst.id])},
                          step_kind=StepKind.DIFF)
    joint, value, warnings = motion.joint_move(asm, inst.id, res.transforms,
                                               translate_by=args.translate, rotate=rot,
                                               relative=args.relative, resolve_face=resolver)
    applied = motion.clamp_value(value, joint.limits)
    if applied != value:
        warnings.append(f"clamped to the joint limits {joint.limits}: asked {value}, "
                        f"set {applied}")
    store.update_joint(pid, joint.id, value=applied)
    asm2, res2 = _solve(ctx, pid)
    _changed(ctx, pid)
    return ToolOutput(summary=f"moved {inst.name} ({inst.id}) along {joint.kind} joint "
                              f"{joint.id}: value {applied}",
                      data={"instance_id": inst.id, "joint_id": joint.id, "value": applied,
                            "world_transform": motion.round_mat(res2.transforms[inst.id])},
                      warnings=warnings, step_kind=StepKind.DIFF)


MAX_LOOPS = 10               # a looping animation plays at most this many times
MAX_POSE_MATRICES = 120      # transforms returned to the model; beyond, step ends only


class AnimStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    joint_id: str
    from_: float | list[float] | None = Field(
        default=None, alias="from", description="Default: the joint's current value.")
    to: float | list[float]
    duration_s: float = Field(default=2.0, gt=0, le=60)


class AnimateArgs(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    joint_id: str | None = None
    from_: float | list[float] | None = Field(default=None, alias="from")
    to: float | list[float] | None = None
    duration_s: float = Field(default=2.0, gt=0, le=60)
    sequence: list[AnimStep] | None = Field(default=None, max_length=12,
                                            description="Joints moved one after another.")
    loop: bool = Field(default=False, description="Repeat the motion (MAX_LOOPS times unless "
                                                  "`loops` says fewer).")
    loops: int | None = Field(default=None, ge=1, le=10,
                              description="How many times the app plays it (1-10). The user can "
                                          "always stop it.")
    keyframes: int = Field(default=5, ge=2, le=21,
                           description="Poses returned per step, ends included.")

    @model_validator(mode="after")
    def _one_form(self) -> "AnimateArgs":
        if (self.joint_id is None) == (not self.sequence):
            raise ValueError("give joint_id (+ to) or a non-empty sequence, not both")
        if self.joint_id is not None and self.to is None:
            raise ValueError("joint_id needs `to`")
        return self


@tool("assembly_animate", safety=SafetyClass.READ, scope_arg="project_id",
      description="Show a joint's motion to the user: the app plays the joint (or a sequence of "
                  "joints, one after another) from `from` to `to` over duration_s, optionally "
                  "looping. Nothing is stored. Returns the solved poses at key frames; check "
                  "interference across the same range with assembly_check sweep.")
async def assembly_animate(ctx: ToolContext, args: AnimateArgs) -> ToolOutput:
    pid = _build_of(ctx, args.project_id)
    asm = _store(ctx).get(pid)
    steps = (args.sequence if args.sequence else
             [AnimStep(joint_id=args.joint_id, to=args.to, duration_s=args.duration_s,
                       **({"from": args.from_} if args.from_ is not None else {}))])
    resolver = face_resolver(_parts_root(ctx))
    values = {j.id: j.value for j in asm.joints}
    plan, step_poses, frames, t0 = [], [], [], 0.0
    for st in steps:
        joint = _joint_or_404(asm, st.joint_id)
        start = values[joint.id] if st.from_ is None else st.from_
        start = motion.clamp_value(start, joint.limits)
        end = motion.clamp_value(st.to, joint.limits)
        plan.append({"joint_id": joint.id, "from": start, "to": end,
                     "duration_s": st.duration_s})
        for k in range(args.keyframes):
            f = k / (args.keyframes - 1)
            values[joint.id] = motion.lerp(start, end, f)
            res = solve(motion.with_values(asm, values), resolve_face=resolver)
            frames.append((round(t0 + f * st.duration_s, 4), values[joint.id],
                           {i: motion.round_mat(m) for i, m in res.transforms.items()}))
        # only the instances this step moves (the rest hold still), so the result stays small
        moving = {i for i in frames[0][2] if any(fr[2][i] != frames[0][2][i] for fr in frames)}
        step_poses.append([{"t": t, "joint_id": joint.id, "value": v,
                            "transforms": {i: m for i, m in tr.items() if i in moving}}
                           for t, v, tr in frames])
        frames = []
        values[joint.id] = end
        t0 += st.duration_s
    loops = args.loops or (MAX_LOOPS if args.loop else 1)
    if args.sequence:
        event = {"project_id": pid, "sequence": plan, "loop": loops > 1, "loops": loops}
    else:
        event = {"project_id": pid, **plan[0], "loop": loops > 1, "loops": loops}
    ctx.events.publish(Event(type="assembly_animate", run_id=getattr(ctx, "run_id", None),
                             data=event))
    size = sum(len(p["transforms"]) for sp in step_poses for p in sp)
    if size > MAX_POSE_MATRICES:       # a big assembly: the ends of each step only
        step_poses = [[sp[0], sp[-1]] for sp in step_poses]
    poses = [p for sp in step_poses for p in sp]
    what = (f"joint {plan[0]['joint_id']} from {plan[0]['from']} to {plan[0]['to']}"
            if len(plan) == 1 else f"{len(plan)} joints in sequence")
    return ToolOutput(summary=f"animating {what} over {t0:g} s"
                              + (f" ({loops}x)" if loops > 1 else "")
                              + f"; {len(poses)} key poses returned",
                      data={"animation": event, "poses": poses}, step_kind=StepKind.MEASURE)


def register_assembly_tools(registry: ToolRegistry) -> None:
    for t in (assembly_get, assembly_add_instance, assembly_add_joint, assembly_drive,
              assembly_remove, assembly_check, assembly_add_part, assembly_move,
              assembly_animate):
        registry.register(t)
