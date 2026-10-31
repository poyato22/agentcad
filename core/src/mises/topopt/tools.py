"""topopt_* tools: generative design / topology optimization of a part (D14).

- topopt_run: one optimized variant of a part (design space + keep-ins / keep-outs + fixtures
  and load cases -> an organic solid), verified with gmsh + CalculiX. min_mass runs a volume
  sweep and keeps the lightest verified variant within max_deflection_mm.
- topopt_generate: several variants (explicit deltas or a sweep grid), Pareto-ranked on mass
  vs deflection, filtered by the safety factor.
- topopt_status / topopt_wait / topopt_result / topopt_cancel: follow a run.
- topopt_accept: promote a variant to a new part, or to a new version of the source part.

Runs are background jobs (wait=false by default), like B3's studies: the agent keeps talking
while the variants run; progress and the finish arrive as `topopt_progress` /
`topopt_finished` events on the "sim" stream. Every number on a card comes from CalculiX on the
final solid, not from the voxel optimizer: a GUIDE ONLY, like every study.
"""
from __future__ import annotations

from ..tools.registry import ToolContext, ToolRegistry, tool
from ..types import ErrorCode, MisesError, SafetyClass, StepKind, ToolOutput
from . import jobs
from .spec import AcceptArgs, IdArgs, TopoptGenerateArgs, TopoptRunArgs, WaitArgs

RUN_TIMEOUT_S = 4 * 3600.0
GUIDE = "guide only, not a guarantee"
APPROVAL_REASON = ("replaces the body of a part the user edited with a generative design "
                   "(its script becomes an import of the optimized solid)")


def _compact_variant(v: dict) -> dict:
    ver = v.get("verify") or {}
    out = {k: v.get(k) for k in ("id", "name", "status", "phase", "pct", "it", "max_iter",
                                  "volume_fraction", "filter_radius_mm", "resolution_mm",
                                  "mass_g", "faces", "step_bytes", "thin_member_pct",
                                  "overhang_area_pct", "verified", "passes", "pareto", "rank",
                                  "compliance_error_pct", "error")}
    if ver:
        out["verify"] = {k: ver.get(k) for k in ("compliance", "max_deflection_mm",
                                                  "max_von_mises_mpa", "safety_factor",
                                                  "allowable_mpa", "nodes", "error")}
    if v.get("warnings"):
        out["warnings"] = v["warnings"][:4]
    return out


def compact(rec: dict) -> dict:
    return {"topopt_id": rec["topopt_id"], "part_id": rec["part_id"],
            "status": rec["status"], "stream": jobs.STREAM, "objective": rec.get("objective"),
            "recommended": rec.get("recommended"), "pareto": rec.get("pareto") or [],
            "accepted": rec.get("accepted") or [], "label": GUIDE,
            "variants": [_compact_variant(v) for v in rec.get("variants") or []]}


def _scoped(ctx: ToolContext, rec: dict, write: bool = False) -> dict:
    if not jobs._in_scope(ctx, rec["part_id"], write=write):
        raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{rec['topopt_id']} belongs to "
                         f"{rec['part_id']}, outside this run's scope")
    return rec


def _load(ctx: ToolContext, topopt_id: str, write: bool = False) -> dict:
    return _scoped(ctx, jobs.read_record(jobs.home_of(ctx), topopt_id), write)


async def _start(ctx: ToolContext, args: TopoptRunArgs, name: str) -> ToolOutput:
    rec = await jobs.start(ctx, args, name)
    tid = rec["topopt_id"]
    if args.wait:
        rec, _ = await jobs.wait(jobs.home_of(ctx), tid, RUN_TIMEOUT_S)
        return ToolOutput(summary=jobs.summary(rec), data=compact(rec),
                          step_kind=StepKind.SIM_RUN)
    names = ", ".join(f"{v['id']} {v['name']}" for v in rec["variants"])
    return ToolOutput(
        summary=(f"started generative design {tid} of {args.part_id} in the background: "
                 f"{len(rec['variants'])} variant(s) ({names}). Keep working; it reports on "
                 "the Simulations stream. Check with topopt_status / topopt_wait, then "
                 "topopt_result and topopt_accept."),
        data={**compact(rec), "status": "running"}, step_kind=StepKind.SIM_RUN)


@tool("topopt_run", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=RUN_TIMEOUT_S,
      description="Generative design / topology optimization of a part: the part's solid (or "
                  "its bounding box) is the design space; keep regions stay solid (faces "
                  "band, box, cylinder, another part), keep_out regions stay empty (bolt "
                  "clearances, envelopes); fixtures (fixed faces) and load cases (force on a "
                  "face, presets or face ids like sim_run) define the job. Minimizes "
                  "compliance at volume_fraction (or, objective min_mass, the lightest "
                  "variant within max_deflection_mm). Manufacturing: symmetry planes, "
                  "process print_3d (45 degree self-supporting overhang along build_dir) or "
                  "machined (2.5-D along z), min_member_mm. The result is an organic solid "
                  "whose fixture / load faces and bores are exact, verified with gmsh + "
                  "CalculiX (mass, deflection, peak stress, safety factor). Runs in the "
                  "background (wait=false): returns topopt_id at once. Nothing changes any "
                  "part until topopt_accept. Results are a GUIDE ONLY.")
async def topopt_run(ctx: ToolContext, args: TopoptRunArgs) -> ToolOutput:
    return await _start(ctx, args, "topopt_run")


@tool("topopt_generate", safety=SafetyClass.LOCAL_MUTATE, scope_arg="part_id",
      timeout_s=RUN_TIMEOUT_S,
      description="Generative design study: several topology-optimized variants of a part "
                  "(same inputs as topopt_run plus `variants` deltas or a `sweep` grid over "
                  "volume_fraction / filter_radius_mm / resolution_mm, up to max_variants), "
                  "each verified with CalculiX, Pareto-ranked on mass vs deflection and "
                  "filtered by min_safety_factor; unverified or failing variants are marked "
                  "and never ranked first. Background job (wait=false): returns topopt_id at "
                  "once; compare the variant cards with topopt_result, then topopt_accept the "
                  "one to keep. Results are a GUIDE ONLY.")
async def topopt_generate(ctx: ToolContext, args: TopoptGenerateArgs) -> ToolOutput:
    return await _start(ctx, args, "topopt_generate")


@tool("topopt_status", safety=SafetyClass.READ,
      description="Progress of a generative design run: status, and per variant its phase "
                  "(optimize / geometry / verify), iteration and live preview.")
async def topopt_status(ctx: ToolContext, args: IdArgs) -> ToolOutput:
    rec = _load(ctx, args.topopt_id)
    parts = [f"{v['id']} {v['status']}" + (f" {v['phase']} it {v.get('it')}/{v.get('max_iter')}"
                                            if v["status"] == "running" else "")
             for v in rec["variants"]]
    return ToolOutput(summary=f"{args.topopt_id}: {rec['status']} ({'; '.join(parts)})",
                      data=compact(rec))


@tool("topopt_wait", safety=SafetyClass.READ, timeout_s=660,
      description="Wait up to timeout_s (<= 600) for a generative design run to finish; "
                  "returns its result (or its progress if still running). Prefer doing other "
                  "work and checking topopt_status.")
async def topopt_wait(ctx: ToolContext, args: WaitArgs) -> ToolOutput:
    _load(ctx, args.topopt_id)
    rec, done = await jobs.wait(jobs.home_of(ctx), args.topopt_id, args.timeout_s)
    summary = jobs.summary(rec) if done else f"{args.topopt_id} still {rec['status']}"
    return ToolOutput(summary=summary, data=compact(rec), step_kind=StepKind.SIM_RUN)


@tool("topopt_result", safety=SafetyClass.READ,
      description="The full result of a generative design run: every variant's mass, "
                  "CalculiX deflection / peak stress / safety factor, verified and Pareto "
                  "flags, rank, printability and the recommended variant.")
async def topopt_result(ctx: ToolContext, args: IdArgs) -> ToolOutput:
    rec = _load(ctx, args.topopt_id)
    return ToolOutput(summary=jobs.summary(rec), data=compact(rec), step_kind=StepKind.SIM_RUN)


@tool("topopt_cancel", safety=SafetyClass.LOCAL_MUTATE,
      description="Cancel a running generative design run (kills its workers).")
async def topopt_cancel(ctx: ToolContext, args: IdArgs) -> ToolOutput:
    rec = _load(ctx, args.topopt_id, write=True)
    if not jobs.cancel(args.topopt_id):
        return ToolOutput(ok=False, summary=f"{args.topopt_id} is not running "
                          f"(status {rec['status']})", data={"status": rec["status"]})
    return ToolOutput(summary=f"cancelling {args.topopt_id}",
                      data={"topopt_id": args.topopt_id, "stream": jobs.STREAM})


def _accept_needs_approval(ctx: ToolContext, args: AcceptArgs) -> str | None:
    if args.mode != "replace":
        return None
    from ..catalog.tools import ui_call_meta
    if ui_call_meta(ctx) is not None:
        return None     # the user's own Accept click
    from .. import edits
    try:
        edited = edits.recent_user_edits(jobs.home_of(ctx), part_id=args.part_id)
    except OSError:
        edited = []
    return APPROVAL_REASON if edited else None


@tool("topopt_accept", safety=SafetyClass.LOCAL_MUTATE,
      scope_keys=lambda a: [a.part_id], needs_approval=_accept_needs_approval, timeout_s=600,
      description="Keep a generative design variant: mode new (default) makes a new part "
                  "beside the source (new_part_id, default <part>_generative); mode replace makes it "
                  "a new version of the source part (base_version from mirror_part_get_source; "
                  "asks the user first when they edited that part). variant defaults to the "
                  "recommended one. The part's body is the optimized solid; features can be "
                  "added on top with mirror_part_op.")
async def topopt_accept(ctx: ToolContext, args: AcceptArgs) -> ToolOutput:
    from .accept import accept
    return await accept(ctx, args)


def register_topopt_tools(registry: ToolRegistry) -> None:
    """Idempotent."""
    for t in (topopt_run, topopt_generate, topopt_status, topopt_wait, topopt_result,
              topopt_cancel, topopt_accept):
        if not registry.list([t.name]):
            registry.register(t)
