"""Agent tools for the mechanism library (Plan 10 E21/E27/E28/E32, research 2026-11-01 §6.3):
mech_list (pick by function), mech_insert (real parts + instances + joints incl. couplings and
loops + the card), mech_check (mobility, sizing, E27 joint design rules, loop closure, a
collision sweep through the range of motion), drivetrain_plan (actuator + reduction options for
a joint's torque and speed) and material_pick (ranked materials for a part, optionally applied).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..events import Event
from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import library
from .assemble import joint_ids, root_transform, to_models

MAX_PARALLEL_BUILDS = 3
SWEEP_STEPS = 7
CONTACT_TOL_MM3 = 2.0      # designed contacts (meshing teeth, a pin in its bore) up to this
PRINTED_CLEARANCE = (0.2, 0.4)     # FDM pin / bushing clearance band (E27)


# ---------------------------------------------------------------- mech_list

class ListArgs(BaseModel):
    function: str = Field(default="", description="What it must do, in words: 'convert "
                          "rotation to linear', 'high reduction for a robot joint', 'walk', "
                          "'grip'.")
    constraints: dict = Field(default_factory=dict, description="Optional: ratio, stroke_mm, "
                              "torque_nm, max_size_mm.")
    category: str | None = Field(default=None, description="gear, reducer, belt, linear, "
                                 "support, motor, mobility, linkage, leg, intermittent, joint, "
                                 "gripper.")
    limit: int = Field(default=8, ge=1, le=60)


@tool("mech_list", safety=SafetyClass.READ,
      description="Find mechanisms in the library (43: gear pairs, planetary and cycloidal "
                  "reducers, belts, screws, rails, shaft supports, motor mounts, linkages, "
                  "legs, grippers, hinges, ball/universal joints, cams, Geneva...) by what they "
                  "must do. Returns ranked ids with a summary, motion type, checks and the "
                  "parameter schema of the top hits. Insert one with mech_insert.")
async def mech_list(ctx: ToolContext, args: ListArgs) -> ToolOutput:
    rows = library.rank(args.function, args.constraints, args.category, args.limit)
    for r in rows[:3]:
        r["params"] = library.get(r["id"]).schema()
    return ToolOutput(summary=f"{len(rows)} mechanism(s): " + ", ".join(r["id"] for r in rows),
                      data={"mechanisms": rows, "total": len(library.all_mechanisms())},
                      step_kind=StepKind.TOOL_RESULT)


# ---------------------------------------------------------------- mech_insert

class At(BaseModel):
    origin: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0],
                              description="The mechanism's +Z goes along this.")


class InsertArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    mech_id: str
    params: dict = Field(default_factory=dict, description="Overrides of the defaults "
                         "(mech_list shows the schema; out-of-range values are refused).")
    at: At = Field(default_factory=At, description="Where the mechanism's base frame goes: in "
                   "the world, or (with attach_to) in that instance's part coordinates.")
    attach_to: str | None = Field(default=None, description="An instance to fix the "
                                  "mechanism's base to (a chassis, a link); default: the base "
                                  "is grounded at `at`.")
    name: str | None = Field(default=None, max_length=60, description="Group label.")


def _pid_base(mech_id: str, key: str) -> str:
    short = "".join(w[0] for w in mech_id.split("_"))[:4]
    s = re.sub(r"[^a-z0-9_]+", "_", f"{short}_{key}".lower()).strip("_")
    s = re.sub(r"(\d+)$", lambda m: "abcdefghij"[int(m.group(1)) % 10], s)
    s = re.sub(r"[\d_]+$", "", s)
    if not s or not s[0].isalpha():
        s = "m_" + s
    return s[:34]


def _script(mech_id: str, key: str, recipe: str, kwargs: dict) -> str:
    """part.py for a recipe part: only numbers, booleans, our own enum strings and lists of
    numbers reach the source (as a JSON literal), never agent text."""
    def ok(v: Any) -> bool:
        if isinstance(v, bool) or isinstance(v, (int, float)):
            return isinstance(v, bool) or math.isfinite(float(v))
        if isinstance(v, str):
            return bool(re.fullmatch(r"[A-Za-z0-9_ .-]{1,40}", v))
        if isinstance(v, (list, tuple)):
            return all(ok(x) for x in v)
        return False
    for k, v in kwargs.items():
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", k) or not ok(v):
            raise MisesError(ErrorCode.INVALID_INPUT, f"bad recipe argument {k!r}")
    from ..cad.recipes import recipe_names
    if recipe not in recipe_names():
        raise MisesError(ErrorCode.INVALID_INPUT, f"unknown recipe {recipe!r}")
    kw = json.dumps(kwargs, sort_keys=True)
    return (f"# Mises mechanism part: {mech_id} / {key}\n"
            f"# Built by ms.{recipe} (recipe_doc {recipe}); edit the arguments to resize.\n"
            f"import json\n\nARGS = json.loads({kw!r})\n\n\n"
            f"def build(v: dict):\n"
            f"    return ms.{recipe}(**ARGS)\n")


async def _build_parts(ctx: ToolContext, pid: str, mech_id: str, gid: str, plan,
                       parts_root: Path) -> dict[str, dict]:
    """Build every part of the plan (recipe parts in the sandboxed worker, catalog parts by the
    catalog insert). Returns {part key: {"part_id", "existing", "meta"}}. Tests replace this."""
    from ..catalog import geometry as cgeo
    from ..catalog.builtin import builtin_by_id
    from ..mass import density_g_cm3
    sem = asyncio.Semaphore(MAX_PARALLEL_BUILDS)

    async def one(spec) -> tuple[str, dict]:
        async with sem:
            if spec.catalog:
                item = builtin_by_id().get(spec.catalog)
                if item is None:
                    raise MisesError(ErrorCode.NOT_FOUND, f"no catalog item {spec.catalog}")
                out = await cgeo.insert_item(parts_root, item, pid)
                return spec.key, out
            owner = {"kind": "mechanism", "mech_group": gid, "key": spec.key, "project_id": pid}
            base = _pid_base(mech_id, spec.key)
            dens = density_g_cm3(spec.material or "PLA") or 1.24
            async with cgeo.choose_lock(parts_root, base):
                part_id, existing = cgeo.choose_part_id(parts_root, base, owner)
                if existing:
                    return spec.key, cgeo._existing_part(parts_root / part_id)
                meta = {**owner, "label": spec.label[:200], "mech_id": mech_id,
                        "recipe": spec.recipe}
                out = await cgeo.build_part(parts_root, part_id,
                                            _script(mech_id, spec.key, spec.recipe, spec.kwargs),
                                            {}, meta, density_g_cm3=dens)
            return spec.key, {**out, "existing": False}

    results = await asyncio.gather(*(one(s) for s in plan.parts))
    return dict(results)


def _register(ctx: ToolContext, pid: str, plan, built: dict, group_label: str,
              gid: str) -> str | None:
    """Tree rows: a group for the mechanism, each part under it with its material."""
    store = getattr(ctx, "store", None)
    if store is None or not hasattr(store, "register_part"):
        return None
    group_id = _pid_base("mech", f"{group_label}_{gid}")
    try:
        store.register_part(group_id, pid, label=group_label, kind="group")
    except Exception:  # noqa: BLE001 - a store without groups: parts stay at the top level
        group_id = None
    for spec in plan.parts:
        out = built[spec.key]
        meta = out.get("meta") or {}
        kind = "catalog" if spec.catalog else "part"
        store.register_part(out["part_id"], pid, label=meta.get("label") or spec.label,
                            kind=kind)
        fields = {}
        if spec.material and not spec.catalog:
            fields["material"] = spec.material
        if group_id and not out.get("existing"):
            fields["parent_id"] = group_id
        if fields:
            try:
                store.update_part(out["part_id"], **fields)
            except Exception:  # noqa: BLE001 - a material the store does not know is skipped
                pass
    return group_id


@tool("mech_insert", safety=SafetyClass.LOCAL_MUTATE, scope_arg="project_id", timeout_s=600,
      description="Insert a library mechanism into the build: builds its parts (real gears, "
                  "pulleys, shafts, bearings, links...), adds the instances and joints "
                  "(including gear/belt/rack/screw couplings and loop closures, so driving the "
                  "input moves everything), and returns its card: ports (where to attach "
                  "motors and loads), joints, mobility, transmission (ratio or curve), ROM, "
                  "sizing results and warnings. Then run mech_check.")
async def mech_insert(ctx: ToolContext, args: InsertArgs) -> ToolOutput:
    from ..assembly import tools as atools
    from ..assembly.geometry import face_resolver
    from ..assembly.model import Frame, Joint, JointRef
    from ..assembly.solve import solve
    pid = atools._build_of(ctx, args.project_id)
    store = atools._store(ctx)
    m, params, plan = library.plan_for(args.mech_id, args.params)
    card = library.card_for(m, params, plan)
    asm = store.get(pid)
    attach = None
    if args.attach_to:
        attach = atools._instance_or_404(asm, args.attach_to)
    gid = uuid.uuid4().hex[:8]
    parts_root = Path(ctx.workdir) / "parts"
    built = await _build_parts(ctx, pid, m.id, gid, plan, parts_root)
    part_ids = {k: v["part_id"] for k, v in built.items()}
    inst_ids = {i.key: uuid.uuid4().hex[:12] for i in plan.instances}
    root = root_transform(args.at.model_dump()) if attach is None else None
    ins, js, lps = to_models(plan, inst_ids, part_ids, root, fixed_root=attach is None)
    if attach is not None:
        js.insert(0, Joint(id=f"{inst_ids[plan.root][:6]}_attach", kind="fixed",
                           a=JointRef(instance_id=attach.id,
                                      frame=Frame(origin=args.at.origin, axis=args.at.axis)),
                           b=JointRef(instance_id=inst_ids[plan.root],
                                      frame=Frame(origin=[0, 0, 0], axis=[0, 0, 1]))))
    trial = asm.model_copy(update={"instances": [*asm.instances, *ins],
                                   "joints": [*asm.joints, *js], "loops": [*asm.loops, *lps]})
    res = solve(trial, resolve_face=face_resolver(parts_root))
    for i in ins:
        store.add_instance(pid, i)
    for j in js:
        store.add_joint(pid, j)
    for lp in lps:
        store.add_loop(pid, lp)
    atools.store_solved(ctx, pid, res)
    label = args.name or m.title
    group_id = _register(ctx, pid, plan, built, label, gid)
    jmap = joint_ids(plan, inst_ids)
    lmap = {lp.key: lo.id for lp, lo in zip(plan.loops, lps)}
    record = {"mech_id": m.id, "title": label, "params": params, "at": args.at.model_dump(),
              "attach_to": args.attach_to, "instances": inst_ids, "joints": jmap,
              "loops": lmap, "parts": part_ids, "group_part": group_id,
              "inputs": [jmap[k] for k in plan.inputs if k in jmap],
              "contacts": [[inst_ids[a], inst_ids[b]] for a, b in plan.contacts
                           if a in inst_ids and b in inst_ids]}
    store.save_mech(pid, gid, record)
    by = "agent"
    for k, out in built.items():
        if not out.get("existing"):
            ctx.events.publish(Event(type="part_changed", run_id=getattr(ctx, "run_id", None),
                                     data={"part_id": out["part_id"], "version": 1, "by": by,
                                           "run_id": getattr(ctx, "run_id", None),
                                           "summary": f"built for mechanism {m.id}"}))
    atools._changed(ctx, pid)
    cd = card.model_dump()
    # the card's keys become this build's ids, so the agent can drive and check them
    cd["instance_ids"] = inst_ids
    cd["joint_ids"] = jmap
    cd["loop_ids"] = lmap
    cd["inputs"] = record["inputs"]
    cd["ports"] = [{**pp, "instance_id": inst_ids.get(pp["instance"])} for pp in cd["ports"]]
    warns = list(card.warnings)
    if res.loop_errors:
        warns.append("a loop does not close at the inserted pose: " + "; ".join(
            e["reason"] for e in res.loop_errors))
    return ToolOutput(summary=f"inserted {m.title} as group {gid}: {len(ins)} instance(s), "
                              f"{len(js)} joint(s), {len(lps)} loop(s), {len(built)} part(s); "
                              f"inputs {', '.join(record['inputs'])}. Run mech_check "
                              f"(group {gid}).",
                      data={"group": gid, "project_id": pid, "card": cd,
                            "parts": part_ids}, warnings=warns, step_kind=StepKind.DIFF)


# ---------------------------------------------------------------- mech_check

class CheckArgs(BaseModel):
    project_id: str | None = Field(default=None, description="Default: this run's build.")
    group: str | None = Field(default=None, description="A mech_insert group; default every "
                              "joint of the build (design rules only).")
    sweep: bool = Field(default=True, description="Sweep each input through its range and "
                        "check for collisions (needs built parts).")
    steps: int = Field(default=SWEEP_STEPS, ge=3, le=21)


def _check(name: str, status: Literal["pass", "warn", "fail"], detail: str, **extra) -> dict:
    return {"check": name, "status": status, "detail": detail, **extra}


def design_checks(asm, joint_filter: set[str] | None = None,
                  printed_parts: set[str] | None = None) -> list[dict]:
    """E27 joint design rules over an assembly's joints: every revolute carried by a bearing
    or bushing, shafts retained, printed clearances in the FDM band, a fastener size on pins,
    range-of-motion limits on driven joints, couplings pointing at real joints."""
    out: list[dict] = []
    ids = {j.id for j in asm.joints}
    for j in asm.joints:
        if joint_filter is not None and j.id not in joint_filter:
            continue
        d = j.design
        if j.kind in ("revolute", "cylindrical"):
            if d is None or not d.bearing:
                out.append(_check("bearing", "fail", f"{j.id}: a {j.kind} joint with no bearing "
                                  "or bushing (E27: every revolute runs on a bearing or "
                                  "bushing)", joint_id=j.id))
            elif d.bearing in ("none",) and (d.load_n or 0) > 0:
                out.append(_check("bearing", "warn", f"{j.id}: a loaded joint with no bearing",
                                  joint_id=j.id))
            if d is not None and d.bearing and not d.retention:
                out.append(_check("retention", "fail", f"{j.id}: the shaft or pin is not "
                                  "retained (snap ring, nut, shoulder screw, collar...)",
                                  joint_id=j.id))
            if d is not None and d.bearing in ("bushing", "printed", "printed bushing"):
                c = d.clearance_mm
                lo, hi = PRINTED_CLEARANCE
                if c is None:
                    out.append(_check("clearance", "warn", f"{j.id}: printed bushing without a "
                                      "stated clearance (FDM 0.2-0.4 mm)", joint_id=j.id))
                elif not lo <= c <= hi:
                    out.append(_check("clearance", "warn", f"{j.id}: printed clearance {c:g} mm "
                                      f"outside {lo}-{hi} mm", joint_id=j.id))
            if d is not None and d.shaft and not d.fastener and d.bearing in ("bushing",
                                                                              "printed"):
                out.append(_check("fastener", "warn", f"{j.id}: pin size not stated (e.g. "
                                  "M3 shoulder screw)", joint_id=j.id))
        if j.kind in ("revolute", "slider", "ball") and j.limits is None and j.coupling is None:
            if d is None or d.rom_deg is None:
                out.append(_check("rom_limits", "warn", f"{j.id}: no limits (fine for a "
                                  "continuous input; otherwise set the ROM)", joint_id=j.id))
        if j.coupling is not None and j.coupling.partner_joint not in ids:
            out.append(_check("coupling", "fail", f"{j.id}: coupled to a missing joint "
                              f"{j.coupling.partner_joint}", joint_id=j.id))
    if not out:
        out.append(_check("joint_design", "pass", "every joint has a bearing/bushing, "
                          "retention and clearances in range"))
    return out


async def _rom_sweep(ctx: ToolContext, pid: str, asm, rec: dict, steps: int) -> list[dict]:
    """Drive each input through its limits (or its card ROM) and check interference among the
    group's instances at every step, one sandboxed worker for all poses."""
    from ..assembly import motion
    from ..assembly.geometry import face_resolver, run_geometry
    from ..assembly.solve import solve, solve_from
    resolver = face_resolver(Path(ctx.workdir) / "parts")
    group = set(rec["instances"].values())
    contacts = {frozenset(c) for c in rec.get("contacts", [])}
    out = []
    joints = {j.id: j for j in asm.joints}
    for jid in rec.get("inputs", []):
        j = joints.get(jid)
        if j is None or isinstance(j.value, (list, tuple)):
            continue
        lo, hi = (j.limits if j.limits is not None else
                  ((float(j.value) - 180.0, float(j.value) + 180.0) if j.kind == "revolute"
                   else (float(j.value), float(j.value))))
        if lo == hi:
            continue
        values, poses = [], []
        prev = {x.id: x.value for x in asm.joints}
        for k in range(steps):
            v = lo + (hi - lo) * k / (steps - 1)
            moved = motion.with_values(asm, {**{x: y for x, y in prev.items()}, jid: v})
            res = solve_from(moved, prev, resolve_face=resolver)
            prev = {**{x.id: x.value for x in moved.joints}, **res.solved, jid: v}
            values.append(round(v, 3))
            poses.append(res.transforms)
        try:
            geo = await run_geometry(Path(ctx.workdir) / "parts", asm,
                                     solve(asm, resolve_face=resolver), poses=poses,
                                     timeout_s=240)
        except MisesError as e:
            out.append(_check("rom_sweep", "warn", f"{jid}: sweep not run ({e.message})"))
            continue
        hits = []
        for v, pos in zip(values, geo.positions):
            for h in pos.get("interferences") or []:
                pair = frozenset((h["a"], h["b"]))
                if not pair & group:
                    continue
                vol = float(h.get("volume_mm3") or 0.0)
                if pair in contacts and vol <= CONTACT_TOL_MM3:
                    continue
                hits.append({"value": v, "a": h["a"], "b": h["b"], "volume_mm3": round(vol, 3),
                             "designed_contact": pair in contacts})
        if hits:
            worst = max(hits, key=lambda h: h["volume_mm3"])
            out.append(_check("rom_sweep", "fail" if any(not h["designed_contact"]
                                                          for h in hits) else "warn",
                              f"{jid}: {len(hits)} collision(s) across {lo:g}..{hi:g} (worst "
                              f"{worst['volume_mm3']} mm3 between {worst['a']} and "
                              f"{worst['b']} at {worst['value']})", joint_id=jid,
                              collisions=hits[:20]))
        else:
            out.append(_check("rom_sweep", "pass", f"{jid}: no collision across {lo:g}..{hi:g} "
                              f"({steps} poses)", joint_id=jid))
    return out


@tool("mech_check", safety=SafetyClass.READ, scope_arg="project_id", timeout_s=420,
      description="Check a mechanism (a mech_insert group) or every joint of the build: "
                  "mobility vs intended DOF, its sizing results (contact ratio, Lewis bending "
                  "SF, Grashof and transmission angle, belt teeth in mesh, screw buckling and "
                  "back-drive, bearing L10...), the E27 joint design rules (bearing or bushing "
                  "at every revolute, shaft retention, FDM clearance 0.2-0.4 mm, fastener "
                  "size, ROM limits), loop closure, and with sweep a collision check through "
                  "each input's range of motion. Each result is pass / warn / fail.")
async def mech_check(ctx: ToolContext, args: CheckArgs) -> ToolOutput:
    from ..assembly import tools as atools
    from ..assembly.geometry import face_resolver
    from ..assembly.solve import solve
    pid = atools._build_of(ctx, args.project_id)
    store = atools._store(ctx)
    asm = store.get(pid)
    results: list[dict] = []
    rec = None
    if args.group:
        try:
            rec = store.get_mech(pid, args.group)
        except KeyError:
            raise MisesError(ErrorCode.NOT_FOUND, f"no mechanism group {args.group!r}",
                             hint="mech_insert returns the group id") from None
        m, params, plan = library.plan_for(rec["mech_id"], rec["params"])
        card = library.card_for(m, params, plan)
        results.append(_check("mobility", "pass" if card.dof == card.dof_intended else "fail",
                              f"mobility {card.dof} (intended {card.dof_intended}): "
                              f"{card.mobility_formula}"))
        for w in card.warnings:
            results.append(_check("sizing", "warn", w))
        results.append(_check("sizing", "pass", "sizing: " + json.dumps(card.sizing)[:600],
                              sizing=card.sizing))
        jf = set(rec["joints"].values())
    else:
        jf = None
    res = solve(asm, resolve_face=face_resolver(Path(ctx.workdir) / "parts"))
    loops = [e for e in res.loop_errors
             if rec is None or e["loop_id"] in set(rec.get("loops", {}).values())]
    results.append(_check("loop_closure", "fail" if loops else "pass",
                          "; ".join(f"{e['loop_id']}: {e['reason']}" for e in loops)
                          if loops else "every loop closes at the current pose"))
    results += design_checks(asm, jf)
    if args.sweep and rec is not None:
        registry = getattr(ctx, "registry", None) or (getattr(ctx, "services", {}) or {}).get(
            "registry")
        if registry is not None and getattr(registry, "get", None) and _has(registry,
                                                                            "dyn_rom_sweep"):
            results.append(_check("rom_sweep", "warn", "dyn_rom_sweep (dynamics) is available: "
                                  "run it for a fast multi-joint sweep; the kinematic sweep "
                                  "below covers each input alone"))
        results += await _rom_sweep(ctx, pid, asm, rec, args.steps)
    n = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "warn", "fail")}
    return ToolOutput(summary=f"mech_check: {n['fail']} fail, {n['warn']} warn, "
                              f"{n['pass']} pass" + (f" (group {args.group})" if args.group
                                                     else " (all joints)"),
                      data={"results": results, "counts": n}, step_kind=StepKind.MEASURE)


def _has(registry, name: str) -> bool:
    try:
        return registry.get(name) is not None
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- drivetrain_plan

class PlanArgs(BaseModel):
    torque_nm: float = Field(gt=0, le=10_000, description="Peak torque the joint needs.")
    speed_rpm: float = Field(default=30.0, gt=0, le=100_000, description="Speed at that load.")
    margin: float = Field(default=1.5, ge=1.0, le=5.0, description="Torque margin (>= 1.5).")
    max_mass_g: float | None = Field(default=None, gt=0)
    max_diameter_mm: float | None = Field(default=None, gt=0)
    self_locking: bool = Field(default=False, description="Must hold position unpowered.")
    limit: int = Field(default=8, ge=1, le=40)


_REDUCERS = [("direct", 1.0, 1.0, 0.0, None), ("belt_drive_gt2", 2.0, 0.95, 30.0, "belt"),
             ("belt_drive_gt2", 3.0, 0.95, 40.0, "belt"), ("planetary", 4.0, 0.95, 80.0, "planetary"),
             ("planetary", 6.0, 0.93, 90.0, "planetary"),
             ("cycloidal_reducer", 11.0, 0.85, 120.0, "cycloid"),
             ("cycloidal_reducer", 20.0, 0.85, 150.0, "cycloid"),
             ("planetary_compound", 45.0, 0.8, 120.0, "compound planetary"),
             ("worm_drive", 30.0, 0.55, 60.0, "worm (self-locking)")]


def _actuators() -> list[dict]:
    from ..catalog.builtin import builtin_items
    return [i for i in builtin_items() if isinstance(i.get("perf"), dict)
            and i["perf"].get("stall_torque_nm")]


def plan_options(a: PlanArgs) -> list[dict]:
    need = a.torque_nm * a.margin
    rows = []
    for it in _actuators():
        perf = it["perf"]
        stall, rpm0 = float(perf["stall_torque_nm"]), float(perf.get("no_load_rpm") or 0)
        mass = float((it.get("dims") or {}).get("mass_g") or 0)
        dia = max(float((it.get("dims") or {}).get(k) or 0) for k in
                  ("diameter_mm", "length_mm", "face_mm", "width_mm"))
        for mech, ratio, eff, extra_g, kind in _REDUCERS:
            if a.self_locking and mech != "worm_drive":
                continue                 # only a worm holds its load unpowered
            t_out = stall * ratio * eff
            if t_out < need:
                continue
            # linear torque-speed line: speed at the required torque
            frac = 1 - a.torque_nm / t_out
            w_out = rpm0 / ratio * max(0.0, frac)
            if w_out < a.speed_rpm:
                continue
            total = mass + extra_g
            if a.max_mass_g is not None and total > a.max_mass_g:
                continue
            if a.max_diameter_mm is not None and dia > a.max_diameter_mm:
                continue
            price = (perf.get("price_usd") or 0)
            rows.append({"actuator": it["id"], "actuator_name": it["name"],
                         "reduction": kind or "direct", "mechanism": None if mech == "direct"
                         else mech, "ratio": ratio, "efficiency": eff,
                         "peak_output_nm": round(t_out, 3), "margin": round(t_out / a.torque_nm, 2),
                         "speed_at_load_rpm": round(w_out, 1), "mass_g": round(total, 1),
                         "price_usd": price, "envelope_mm": dia,
                         "score": round(total + 2 * price - 20 * min(3.0, t_out / need), 2)})
    rows.sort(key=lambda r: r["score"])
    return rows[:a.limit]


@tool("drivetrain_plan", safety=SafetyClass.READ,
      description="Actuator + reduction options for a joint: given its peak torque and speed "
                  "(from dyn_torque or a static estimate), the catalog actuators (servos, "
                  "steppers, Dynamixel, BLDC actuators, gearmotors; torque-speed lines) "
                  "combined with direct drive, a GT2 belt, a planetary, a cycloidal, a "
                  "compound planetary or a worm reduction, filtered by margin, speed, mass and "
                  "size, lightest and cheapest first. Insert the chosen reduction with "
                  "mech_insert.")
async def drivetrain_plan(ctx: ToolContext, args: PlanArgs) -> ToolOutput:
    rows = plan_options(args)
    if not rows:
        return ToolOutput(summary=f"no catalog actuator + reduction reaches {args.torque_nm:g} "
                                  f"N*m x {args.margin:g} at {args.speed_rpm:g} rpm",
                          data={"options": []},
                          warnings=["lower the load (shorter lever, lighter links), split it "
                                    "over two actuators, or add a custom actuator"])
    best = rows[0]
    return ToolOutput(summary=f"{len(rows)} option(s); best: {best['actuator_name']} + "
                              f"{best['reduction']} {best['ratio']:g}:1 -> "
                              f"{best['peak_output_nm']} N*m peak ({best['margin']}x), "
                              f"{best['speed_at_load_rpm']} rpm at load, {best['mass_g']} g",
                      data={"options": rows, "required": args.model_dump()},
                      step_kind=StepKind.TOOL_RESULT)


# ---------------------------------------------------------------- material_pick

class MaterialArgs(BaseModel):
    part_id: str | None = Field(default=None, description="The part to pick for (with apply, "
                                "its material is set).")
    requirements: dict = Field(default_factory=dict, description="priority (stiffness | "
                               "strength | mass | cost | balanced), max_temp_c, printable, "
                               "processes [fdm, sls_mjf, machined, sheet, laminate], flexible, "
                               "min_yield_mpa, min_e_mpa, conductive (false for RF windows), "
                               "low_friction, form (plate | shaft | block | shell).")
    apply: bool = Field(default=False, description="Set the top pick as the part's material.")
    limit: int = Field(default=6, ge=1, le=30)


@tool("material_pick", safety=SafetyClass.READ, scope_arg="part_id",
      description="Rank materials for a part from the one materials table (PLA, PETG, ABS, "
                  "ASA, PC, PA12, PA6-CF, PA-CF, TPU 95A, POM, Al 6061-T6 / 7075-T6, steels "
                  "1018 / 4140, 304 stainless, brass C360, CF plate, plus custom ones) by "
                  "specific stiffness / strength, service temperature, process, cost and "
                  "failure mode, with the reasons. apply=true sets the top pick on the part "
                  "(mass, studies and the BOM follow).")
async def material_pick(ctx: ToolContext, args: MaterialArgs) -> ToolOutput:
    from ..sim.materials import rank_materials
    home = Path(ctx.workdir) if getattr(ctx, "workdir", None) else None
    rows = rank_materials(args.requirements, home, args.limit)
    ok = [r for r in rows if not r["rejected"]]
    data = {"ranked": rows}
    summary = (f"top pick {ok[0]['name']} ({ok[0]['process']}; " + "; ".join(ok[0]["reasons"])
               + ")") if ok else "no material meets every requirement"
    if args.apply and args.part_id and ok:
        policy = getattr(ctx, "policy", None)
        if policy is not None and SafetyClass.LOCAL_MUTATE not in policy.allowed_classes:
            raise MisesError(ErrorCode.OUT_OF_SCOPE, "setting a material changes the part; "
                             "this run may only read", hint="call without apply")
        from ..mass import SetMaterialArgs, part_set_material
        out = await part_set_material.handler(ctx, SetMaterialArgs(part_id=args.part_id,
                                                                    material=ok[0]["name"]))
        data["applied"] = out.data
        summary += f"; applied to {args.part_id}"
    return ToolOutput(summary=summary, data=data, step_kind=StepKind.TOOL_RESULT)


MECH_TOOLS = [mech_list, mech_insert, mech_check, drivetrain_plan, material_pick]


def register_mech_tools(registry: ToolRegistry) -> None:
    for t in MECH_TOOLS:
        registry.register(t)
