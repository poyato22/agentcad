"""topopt_accept: promote a variant to a part (a new part, or a new version of the source).

The organic body is a mesh-derived B-rep, not a parametric script: it is stored next to the
part's script as topopt_body.step and the script imports it, so the part builds, measures,
studies, prints and takes `# mises-op` features like any other. Replacing the source part is a
whole-script edit and goes through the same version check (cad/tools._guard_source); an
agent's replacement of a part the user edited asks the user first (tools.py needs_approval).
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..cad import safe_io
from ..cad.tools import (_accept, _after_build, _build_guarded, _check_part_id,
                         _existing, _finish_edit, _guard_source, _inline, _keep_ops, _lock,
                         _part_dir, _prepare, _snapshot, _summary)
from ..cad.runner import run_build
from ..events import Event
from ..types import ErrorCode, MisesError, StepKind, ToolOutput
from . import jobs
from .spec import AcceptArgs

BODY = "topopt_body.step"
BUILD_TIMEOUT_S = 300.0


def script(topopt_id: str, vid: str, source: str, name: str) -> str:
    """The accepted part's script. Nothing free-form is ever spliced into source text: the run's
    ids and the variant's label go in as repr() literals of one data assignment (untrusted text
    stays data)."""
    meta = {"topopt_id": str(topopt_id), "variant": str(vid), "source": str(source),
            "name": str(name)}
    return ('"""Generative design: a topology-optimized body (see GENERATIVE below).\n\n'
            f"The organic body is a mesh-derived solid stored next to this script ({BODY}); its\n"
            "mounting faces and bores are exact. Add features on top (holes, fillets on the exact\n"
            'faces) or run generative design again to change the body itself."""\n'
            f"GENERATIVE = {meta!r}\n"
            "VARS = {}\n\n\n"
            "def build(v: dict):\n"
            "    import os\n"
            f"    return import_step(os.path.join(os.path.dirname(__file__), {BODY!r}))\n")


def _free_id(parts_root: Path, base: str) -> str:
    """<source>_generative, then _generative_b, _c, ... (part names never end in a number)."""
    stem = f"{base[:28]}_generative"
    for suffix in ["", *(f"_{c}" for c in "bcdefghijklmnopqrstuvwxyz")]:
        if not (parts_root / (stem + suffix)).exists():
            return stem + suffix
    raise MisesError(ErrorCode.INVALID_INPUT, "no free part id", hint="pass new_part_id")


def _variant(rec: dict, vid: str | None) -> dict:
    vid = vid or rec.get("recommended")
    if vid is None:
        raise MisesError(ErrorCode.INVALID_INPUT, "this run has no recommended variant",
                         hint="pass variant (e.g. \"v0\"); see topopt_result")
    v = next((x for x in rec.get("variants") or [] if x["id"] == vid), None)
    if v is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"no variant {vid} in {rec['topopt_id']}")
    if v.get("status") != "done":
        raise MisesError(ErrorCode.INVALID_INPUT, f"variant {vid} is {v.get('status')}",
                         hint="accept a finished variant")
    return v


async def accept(ctx, args: AcceptArgs) -> ToolOutput:
    home = jobs.home_of(ctx)
    rec = jobs.read_record(home, args.topopt_id)
    if rec["part_id"] != args.part_id:
        raise MisesError(ErrorCode.INVALID_INPUT, f"{args.topopt_id} optimized "
                         f"{rec['part_id']}, not {args.part_id}")
    v = _variant(rec, args.variant)
    body = safe_io.read_bytes(jobs.job_dir(home, args.topopt_id) / v["id"], "part.step")
    if body is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"variant {v['id']} has no part.step")
    note = "" if v.get("verified") and v.get("passes") else (
        " (NOT a passing verified variant: " + ("unverified" if not v.get("verified") else
                                                 "fails the safety factor / deflection target")
        + ")")
    src = script(args.topopt_id, v["id"], args.part_id, v.get("name") or v["id"])
    if args.mode == "new":
        pid = args.new_part_id or _free_id(Path(ctx.workdir) / "parts", args.part_id)
        if not jobs._in_scope(ctx, pid, write=True):   # like mirror_part_create's scope_arg
            raise MisesError(ErrorCode.OUT_OF_SCOPE, f"creating {pid} is outside this run's "
                             "edit scope", hint="ask the user to widen the scope, or use "
                                                "mode=replace on the source part")
        out = await _new_part(ctx, pid, src, body)
    else:
        out = await _replace(ctx, args.part_id, src, body, args.base_version)
    if not out.ok:
        return out
    entry = {"variant": v["id"], "part_id": out.data["part_id"], "mode": args.mode,
             "version": out.data.get("version"), "at": time.time()}
    rec = jobs.read_record(home, args.topopt_id)
    rec.setdefault("accepted", []).append(entry)
    jobs.write_record(jobs.job_dir(home, args.topopt_id), rec)
    events = getattr(ctx, "events", None)
    if events is not None:
        events.publish(Event(type="topopt_accepted", run_id=getattr(ctx, "run_id", None),
                             data={"topopt_id": args.topopt_id, "stream": jobs.STREAM,
                                   "build_id": rec.get("project_id"), **entry}))
    ver = v.get("verify") or {}
    return ToolOutput(
        summary=(f"accepted {v['id']} ({v.get('name')}) of {args.topopt_id} as "
                 f"{out.data['part_id']} v{out.data.get('version')}: "
                 f"{v.get('mass_g', 0):.1f} g" + (
                     f", deflection {ver['max_deflection_mm']:.3g} mm, SF "
                     f"{ver['safety_factor']:.2f} (CalculiX, guide only)"
                     if ver.get("safety_factor") else "") + note),
        data={**out.data, "topopt_id": args.topopt_id, "variant": v["id"], "mode": args.mode,
              "stream": jobs.STREAM})


async def _new_part(ctx, pid: str, src: str, body: bytes) -> ToolOutput:
    _check_part_id(pid)
    d = _part_dir(ctx, pid)
    async with _lock(d):
        if safe_io.exists(d, "metrics.json") or d.exists():
            raise MisesError(ErrorCode.INVALID_INPUT, f"part {pid!r} already exists",
                             hint="pass another new_part_id, or mode=replace")
        safe_io.ensure_dir(d)
        try:
            safe_io.write_bytes(d, BODY, body)
            safe_io.write_text(d, "part.py", src)
            res = await run_build(d, {}, timeout_s=BUILD_TIMEOUT_S)
            bad = _after_build(d)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        if bad or not res.ok:
            shutil.rmtree(d, ignore_errors=True)
            return ToolOutput(ok=False, step_kind=StepKind.ERROR,
                              summary=f"building {pid} from the variant failed: "
                                      f"{(res.error or ', '.join(bad))[-400:]}")
        _accept(d, res)
        if getattr(ctx, "store", None) is not None:
            from ..projects import register_built_part
            register_built_part(ctx.store, ctx.run_id, pid)
        version = _finish_edit(ctx, pid, d, f"created {pid} from generative design")
    return ToolOutput(summary=_summary(pid, res.metrics),
                      data={"part_id": pid, "version": version, **_inline(res.metrics)})


async def _replace(ctx, pid: str, src: str, body: bytes, base_version: int | None) -> ToolOutput:
    d = _existing(ctx, pid)
    async with _lock(d):
        _prepare(d)
        refused = _guard_source(ctx, pid, d, base_version)
        if refused is not None:
            return refused
        src, _kept, _dropped = _keep_ops(ctx, d, src, False)
        snap = _snapshot(d)
        old_body = safe_io.read_bytes(d, BODY)
        safe_io.write_bytes(d, BODY, body)
        safe_io.write_text(d, "part.py", src)
        try:
            res = await _build_guarded(d, {}, snap,
                                       timeout_s=BUILD_TIMEOUT_S)
        except BaseException:
            _restore_body(d, old_body)
            raise
        if not res.ok:
            _restore_body(d, old_body)
            return ToolOutput(ok=False, step_kind=StepKind.ERROR,
                              summary=f"rebuilding {pid} from the variant failed (the part is "
                                      f"unchanged): {(res.error or '')[-400:]}")
        _accept(d, res)
        version = _finish_edit(ctx, pid, d, f"replaced {pid}'s body with a generative design")
    return ToolOutput(summary=_summary(pid, res.metrics),
                      data={"part_id": pid, "version": version, **_inline(res.metrics)})


def _restore_body(d: Path, old: bytes | None) -> None:
    if old is None:
        safe_io.unlink(d, BODY)
    else:
        safe_io.write_bytes(d, BODY, old)
