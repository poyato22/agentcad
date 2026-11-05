"""Generative-design jobs: plan, run variants in the background, verify, rank, accept.

A job is one topopt_run (one variant, or a mass sweep for min_mass) or one topopt_generate
(several variants: explicit deltas or a sweep grid). Records live at
<home>/topopt/<topopt_id>/record.json, each variant's sandboxed worker dir at
<home>/topopt/<topopt_id>/<variant>/ (spec.json + model.step in, artifacts out).

Like B3's background studies, a job never holds up the agent's turn: the tool returns
{topopt_id, status: "running", stream: "sim"} at once (wait=false, the default), the variants
run as asyncio tasks (each worker / mesher / solver in a heavy-worker slot), and the job
publishes `topopt_progress` (every few iterations, with a live density preview) and finally
`topopt_finished {topopt_id, run_id, build_id, status, summary}`, all with stream "sim".

Ranking (research §4): every badge comes from CalculiX, never from the optimizer. Pareto on
(mass, deflection) among the verified variants that pass the safety factor (and the
deflection target); an unverified or failing variant is marked and never ranked first.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import math
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..cad import safe_io
from ..events import Event
from ..sim import studies
from ..sim.materials import get_material
from ..sim.mesh import FACE_ID_RE, FACE_TAG_RE, size_floor
from ..sim.proc import run_sandboxed
from ..sim.types import SimError
from ..types import ErrorCode, MisesError
from .spec import TOPOPT_ID_RE, VARIANT_ID_RE, TopoptGenerateArgs, TopoptRunArgs

ROOT = "topopt"
RECORD = "record.json"
WORKER_MODULE = "mises.topopt.worker"
TARGET_ELEMENTS = 50_000
MAX_FILTER_VOXELS = 6.0
#: a variant's peak memory, measured (DOG demo: 160k elements used 4.2-4.6 GB and every
#: variant was stopped at the 4096 MB budget; 110k still reached 4.1 GB): ~1 GB + ~30 kB/element
BASE_MB, KB_PER_ELEMENT = 1000.0, 30.0
MAX_ELEMENTS = 80_000
LOW_VF = 0.15
MAX_PARALLEL = 3
VARIANT_TIMEOUT_S = 3600.0
MEMORY_MB = 4096.0
POLL_S = 0.5
SAVE_EVERY_S = 2.0
DEFAULT_SWEEP = (0.15, 0.25, 0.35)
STREAM = "sim"
_FILES = {"preview.glb": "model/gltf-binary", "result.glb": "model/gltf-binary",
          "stress.glb": "model/gltf-binary", "render.png": "image/png",
          "part.step": "application/step", "part.stl": "model/stl"}

_active: dict[str, asyncio.Task] = {}
_cancel_requested: set[str] = set()


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def home_of(ctx) -> Path:
    return studies._home(ctx) or Path(ctx.workdir)


def root(home: Path) -> Path:
    return Path(home) / ROOT


def job_dir(home: Path, topopt_id: str) -> Path:
    if not TOPOPT_ID_RE.fullmatch(topopt_id or ""):
        raise _bad(f"bad topopt id {topopt_id!r}")
    return root(home) / topopt_id


def write_record(d: Path, rec: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (RECORD + ".tmp")
    tmp.write_text(json.dumps(rec, default=float))
    os.replace(tmp, d / RECORD)


def read_record(home: Path, topopt_id: str) -> dict:
    d = job_dir(home, topopt_id)
    try:
        rec = json.loads((d / RECORD).read_text())
    except (OSError, ValueError):
        raise MisesError(ErrorCode.NOT_FOUND, f"no generative design run {topopt_id}",
                         hint="start one with topopt_run or topopt_generate") from None
    if rec.get("status") in ("queued", "running") and topopt_id not in _active:
        rec["status"] = "interrupted"     # Mises restarted mid-run
    return rec


def list_records(home: Path, *, part_id: str | None = None, project_id: str | None = None,
                 run_id: str | None = None) -> list[dict]:
    out = []
    r = root(home)
    if not r.is_dir():
        return out
    for d in r.iterdir():
        if not TOPOPT_ID_RE.fullmatch(d.name):
            continue
        try:
            rec = read_record(home, d.name)
        except MisesError:
            continue
        if part_id and rec.get("part_id") != part_id:
            continue
        if project_id and rec.get("project_id") != project_id:
            continue
        if run_id and rec.get("run_id") != run_id:
            continue
        out.append(public(rec))
    return sorted(out, key=lambda r: r.get("created_at") or 0, reverse=True)


def urls(topopt_id: str, vid: str) -> dict:
    base = f"/topopt/{topopt_id}/variants/{vid}"
    return {"preview": f"{base}/preview.glb", "result": f"{base}/result.glb",
            "stress": f"{base}/stress.glb", "render": f"{base}/render.png",
            "step": f"{base}/part.step", "stl": f"{base}/part.stl"}


def public(rec: dict) -> dict:
    """The record as the app and agents see it (URLs, no internals)."""
    out = {k: v for k, v in rec.items() if k not in ("spec",)}
    out["stream"] = STREAM
    out["variants"] = []
    for v in rec.get("variants") or []:
        pv = {k: x for k, x in v.items() if k != "spec"}
        pv["urls"] = urls(rec["topopt_id"], v["id"])
        # the density's evolution (replay / stills): one preview mesh per kept iteration
        pv["urls"]["evolution"] = [f"/topopt/{rec['topopt_id']}/variants/{v['id']}/"
                                   f"evo_{int(i):03d}.glb" for i in v.get("evolution") or []]
        out["variants"].append(pv)
    return out


# ---- planning

def _metrics(ctx, part_id: str) -> tuple[Path, dict]:
    from ..cad.tools import _existing
    d = _existing(ctx, part_id)
    m = safe_io.read_json(d, "metrics.json")
    if not m or not safe_io.exists(d, "model.step"):
        raise MisesError(ErrorCode.NOT_FOUND, f"{part_id} has no built model",
                         hint="rebuild the part")
    return d, m


def _check_faces(specs: list[str], metrics: dict) -> None:
    known = {f.get("id") for f in metrics.get("face_list") or []}
    tags = metrics.get("face_tags") or {}
    for f in specs:
        if FACE_ID_RE.match(f) and f not in known:
            raise _bad(f"no face {f} on this part",
                       hint=f"face ids run f0..f{len(known) - 1}; see mirror_faces")
        if FACE_TAG_RE.match(f) and f[4:] not in tags:
            raise _bad(f"this part has no face tagged {f[4:]!r}")


def _in_scope(ctx, key: str, write: bool = False) -> bool:
    pol = getattr(ctx, "policy", None)
    if pol is None:
        return True
    allowed = set(pol.edit_scope) | (set() if write else set(pol.read_scope))
    return "*" in allowed or key in allowed


def expand(args: TopoptRunArgs) -> list[dict]:
    """The variant deltas of a job (at least one, the base itself = {})."""
    deltas: list[dict] = []
    if isinstance(args, TopoptGenerateArgs):
        deltas = [dict(v) for v in args.variants]
        if not deltas and args.sweep:
            keys = sorted(args.sweep)
            for combo in itertools.product(*(args.sweep[k] for k in keys)):
                deltas.append(dict(zip(keys, combo)))
        cap = args.max_variants
    else:
        cap = 12
    if not deltas and args.objective == "min_mass":
        deltas = [{"volume_fraction": vf} for vf in DEFAULT_SWEEP]
    if not deltas:
        deltas = [{}]
    seen, out = set(), []
    for d in deltas:
        key = json.dumps(d, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out[:cap]


def _voxel(space_volume: float, bbox: dict, vf: float, res: float | None,
           warnings: list[str]) -> float:
    if res:
        h = float(res)
    else:
        h = max(0.25, round((space_volume / TARGET_ELEMENTS) ** (1 / 3), 2))
        if vf < LOW_VF:
            h = max(0.25, round(0.75 * h, 2))
            warnings.append(f"volume fraction {vf:g} is low: refined to {h:g} mm voxels so "
                            "members stay at least 3 voxels thick")
    size = bbox["size"]

    def cells(hh: float) -> int:
        return math.prod(4 * math.ceil(math.ceil(s / hh - 1e-6) / 4) for s in size)

    if cells(h) > MAX_ELEMENTS:
        h0 = h
        while cells(h) > MAX_ELEMENTS:
            h = round(h * 1.05 + 0.01, 2)
        warnings.append(f"{h0:g} mm voxels would exceed {MAX_ELEMENTS} elements: using {h:g} mm")
    return h


def _variant_name(delta: dict, vf: float, r_mm: float, mf: dict) -> str:
    if delta.get("name"):
        return str(delta["name"])[:40]
    bits = [f"vf {vf:.2f}", f"r {r_mm:.1f} mm"]
    if mf.get("overhang"):
        bits.append("printable")
    if mf.get("extrusion_axis"):
        bits.append("2.5-D")
    if mf.get("symmetry"):
        bits.append("sym " + "".join(mf["symmetry"]))
    return " · ".join(bits)


def plan(ctx, args: TopoptRunArgs) -> dict:
    """Validate everything that can be checked before anything runs; returns the job plan:
    material, base metrics and one resolved worker spec per variant."""
    part_dir, metrics = _metrics(ctx, args.part_id)
    faces = [f.face for f in args.fixtures] + [ld.face for c in args.load_cases for ld in c.loads]
    faces += [f for r in args.keep for f in r.faces]
    _check_faces(faces, metrics)
    home = home_of(ctx)
    try:
        # Plan 9 B2: the part's own material, else the profile's; one without simulation
        # data is refused with what to do (never swapped for another)
        mat = (get_material(args.material, home) if args.material else
               studies._default_sim_material(studies.default_material(ctx, args.part_id), home))
    except SimError as e:
        raise _bad(e.message, e.hint) from None
    bbox = metrics["bbox"]
    space_volume = (float(metrics.get("volume_mm3") or 0) if args.design_space == "part"
                    else math.prod(bbox["size"]))
    if space_volume <= 0:
        raise _bad(f"{args.part_id} has no volume to optimize")
    others = {}
    for r in [*args.keep, *args.keep_out]:
        if r.kind == "part":
            if not _in_scope(ctx, r.part_id):
                raise MisesError(ErrorCode.OUT_OF_SCOPE, f"{r.part_id} is outside this run's "
                                 "scope")
            others[r.part_id] = _metrics(ctx, r.part_id)[0]
    variants = []
    for i, delta in enumerate(expand(args)):
        warnings: list[str] = []
        vf = float(delta.get("volume_fraction", args.volume_fraction))
        if not 0.03 < vf < 0.8:
            raise _bad(f"variant volume_fraction {vf} must be between 0.03 and 0.8")
        mf_in = {**args.manufacturing.model_dump(), **(delta.get("manufacturing") or {})}
        from .spec import Manufacturing
        mf = Manufacturing.model_validate(mf_in).resolved()
        h = _voxel(space_volume, bbox, vf, delta.get("resolution_mm", args.resolution_mm),
                   warnings)
        r_mm = delta.get("filter_radius_mm", args.filter_radius_mm)
        if r_mm is None:
            r_mm = max(2.0 * h, (mf.min_member_mm or 0) / 2)
        if float(r_mm) / h > MAX_FILTER_VOXELS:
            # the filter's non-zeros grow with r^3 (r 6: ~0.8 GB on a 48k grid; r 10: ~4 GB):
            # a wide filter runs on coarser voxels instead
            h0, h = h, math.ceil(100 * float(r_mm) / MAX_FILTER_VOXELS) / 100
            warnings.append(f"a {float(r_mm):g} mm filter radius is over {MAX_FILTER_VOXELS} voxels "
                            f"of {h0:g} mm: using {h:g} mm voxels")
        r_el = min(MAX_FILTER_VOXELS, max(1.2, float(r_mm) / h))
        reg = []
        for k, r in enumerate(args.keep):
            reg.append({**r.model_dump(exclude_none=True),
                        **({"step": f"keep_{k}.step"} if r.kind == "part" else {})})
        kout = []
        for k, r in enumerate(args.keep_out):
            kout.append({**r.model_dump(exclude_none=True),
                         **({"step": f"keepout_{k}.step"} if r.kind == "part" else {})})
        mfd = mf.model_dump()
        spec = {
            "design_space": args.design_space,
            "bbox": {"min": bbox["min"], "max": bbox["max"]},
            "h": h, "filter_radius": r_el,
            "material": {"E": mat.e_eff_mpa, "nu": mat.nu},
            "volume_fraction": vf, "max_iter": int(delta.get("max_iter", args.max_iter)),
            "mirror_axes": ["xyz".index(a) for a in mf.symmetry],
            "extrusion_axis": "xyz".index(mf.extrusion_axis) if mf.extrusion_axis else None,
            "overhang_dir": mf.overhang.build_dir if mf.overhang else None,
            "fixtures": [f.model_dump() for f in args.fixtures],
            "load_cases": [{"name": c.name, "weight": c.weight,
                            "loads": [{"face": ld.face, "newtons": ld.newtons,
                                       "direction": _dir(ld.direction)} for ld in c.loads]}
                           for c in args.load_cases],
            "keep": reg, "keep_out": kout,
            "face_list": metrics.get("face_list") or [], "face_tags": metrics.get("face_tags"),
            "preview_every": 5}
        variants.append({
            "id": f"v{i}", "name": _variant_name(delta, vf, r_el * h, mfd), "delta": delta,
            "status": "queued", "phase": "queued", "pct": 0.0, "it": 0,
            "max_iter": spec["max_iter"], "preview": None, "volume_fraction": vf,
            "resolution_mm": h, "filter_radius_mm": round(r_el * h, 3),
            "manufacturing": mfd, "warnings": warnings, "error": None, "verified": False,
            "passes": None, "rank": None, "pareto": False, "spec": spec})
    return {"part_dir": part_dir, "metrics": metrics, "material": mat, "variants": variants,
            "others": others}


def _dir(name: str) -> list[float]:
    v = [0.0, 0.0, 0.0]
    v["xyz".index(name[1])] = 1.0 if name[0] == "+" else -1.0
    return v


# ---- ranking

def _dominates(a: dict, b: dict) -> bool:
    return (a["mass_g"] <= b["mass_g"] and a["max_deflection_mm"] <= b["max_deflection_mm"]
            and (a["mass_g"] < b["mass_g"] or a["max_deflection_mm"] < b["max_deflection_mm"]))


def rank(variants: list[dict], *, min_safety_factor: float,
         max_deflection_mm: float | None) -> tuple[list[str], str | None]:
    """Mark each variant's verified / passes / pareto / rank in place; returns (Pareto ids,
    recommended id). Order: the recommended variant, the rest of the passing Pareto front (by
    mass), other passing (by mass), verified but failing (best safety factor first), unverified,
    failed."""
    for v in variants:
        ver = v.get("verify") or {}
        v["verified"] = v.get("status") == "done" and bool(ver) and ver.get("error") is None
        if v["verified"]:
            sf = ver.get("safety_factor")
            ok = sf is not None and sf >= min_safety_factor
            if max_deflection_mm is not None:
                ok = ok and ver["max_deflection_mm"] <= max_deflection_mm
            v["passes"] = bool(ok)
            v["max_deflection_mm"] = ver["max_deflection_mm"]
        else:
            v["passes"] = None if v.get("status") == "done" else False
        v["pareto"] = False
    good = [v for v in variants if v["verified"] and v["passes"]]
    for v in good:
        v["pareto"] = not any(_dominates(o, v) for o in good if o is not v)
    pareto = sorted((v for v in good if v["pareto"]), key=lambda v: v["mass_g"])
    rest = sorted((v for v in good if not v["pareto"]), key=lambda v: v["mass_g"])
    failing = sorted((v for v in variants if v["verified"] and not v["passes"]),
                     key=lambda v: -((v.get("verify") or {}).get("safety_factor") or 0))
    unverified = [v for v in variants if v.get("status") == "done" and not v["verified"]]
    broken = [v for v in variants if v.get("status") != "done"]
    rec = None
    if pareto:
        if max_deflection_mm is not None:
            rec = pareto[0]
        else:
            # the stiffest per gram (lowest mass x deflection), printability breaks ties
            rec = min(pareto, key=lambda v: (round(v["mass_g"] * v["max_deflection_mm"], 6),
                                             v.get("overhang_area_pct") or 0))
    ids = [v["id"] for v in pareto]
    if rec is not None:          # the recommendation is rank 1, the rest of the front by mass
        pareto = [rec, *(v for v in pareto if v is not rec)]
    for i, v in enumerate([*pareto, *rest, *failing, *unverified, *broken], 1):
        v["rank"] = i
    return ids, (rec["id"] if rec else None)


def summary(rec: dict) -> str:
    vs = rec.get("variants") or []
    done = [v for v in vs if v.get("status") == "done"]
    head = (f"generative design {rec['topopt_id']} of {rec['part_id']}: {rec['status']}, "
            f"{len(done)}/{len(vs)} variants")
    best = next((v for v in vs if v["id"] == rec.get("recommended")), None)
    if best is not None:
        ver = best.get("verify") or {}
        peak = ver.get("max_von_mises_incl_fixture_mpa", ver.get("max_von_mises_mpa", 0))
        head += (f"; recommended {best['id']} ({best['name']}): {best['mass_g']:.1f} g, "
                 f"deflection {ver.get('max_deflection_mm', 0):.3g} mm, peak "
                 f"{peak:.3g} MPa incl. the fixture zone "
                 f"({ver.get('max_von_mises_mpa', 0):.3g} MPa away from it), SF "
                 f"{ver.get('safety_factor') or 0:.2f} (CalculiX, guide only)")
    elif rec.get("status") == "done":
        head += "; no variant passed verification"
        fails = [v for v in vs if v.get("error")]
        if fails:
            head += f" ({fails[0]['id']}: {fails[0]['error']})"
    return head


# ---- running

def _publish(events, rec: dict, kind: str, data: dict) -> None:
    if events is None:
        return
    events.publish(Event(type=kind, run_id=rec.get("run_id"), data={
        "topopt_id": rec["topopt_id"], "part_id": rec["part_id"],
        "project_id": rec.get("project_id"), "build_id": rec.get("project_id"),
        "stream": STREAM, **data}))


def _copy_inputs(part_dir: Path, others: dict, args: TopoptRunArgs, vdir: Path) -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    if safe_io.copy_out(part_dir, "model.step", vdir) is None:
        raise MisesError(ErrorCode.NOT_FOUND, "the part has no model.step", hint="rebuild it")
    for kind, regions in (("keep", args.keep), ("keepout", args.keep_out)):
        for k, r in enumerate(regions):
            if r.kind == "part":
                data = safe_io.read_bytes(others[r.part_id], "model.step")
                if data is None:
                    raise MisesError(ErrorCode.NOT_FOUND, f"{r.part_id} has no model.step")
                (vdir / f"{kind}_{k}.step").write_bytes(data)


async def _poll(vdir: Path, on: Any) -> None:
    last = None
    while True:
        await asyncio.sleep(POLL_S)
        try:
            d = safe_io.read_json(vdir, "progress.json")
        except (OSError, ValueError, safe_io.UnsafePath):
            continue
        if d and d != last:
            last = d
            on(d)


def _render(vdir: Path) -> None:
    from ..cad.render import Mesh, load_stl, render_views
    data = safe_io.read_bytes(vdir, "part.stl")
    if not data:
        return
    png = render_views(Mesh(load_stl(data), color=(0.62, 0.66, 0.72)), views=("iso",),
                       size=512)["iso"]
    (vdir / "render.png").write_bytes(png)


async def _run_variant(ctx_events, rec: dict, v: dict, d: Path, args: TopoptRunArgs,
                       plan_: dict, ccx: str | None, save) -> None:
    vdir = d / v["id"]
    tid = rec["topopt_id"]

    def emit() -> None:
        _publish(ctx_events, rec, "topopt_progress", {
            "variant": v["id"], "status": v["status"], "phase": v["phase"], "pct": v["pct"],
            "it": v.get("it"), "max_iter": v.get("max_iter"), "compliance": v.get("compliance"),
            "vf": v.get("vf"), "preview": v.get("preview"),
            "preview_url": (f"/topopt/{tid}/variants/{v['id']}/preview.glb?v={v['preview']}"
                            if v.get("preview") else None)})

    saved = [0.0]

    def on_progress(p: dict) -> None:
        v["phase"] = p.get("phase") or v["phase"]
        v["pct"] = float(p.get("pct") or v["pct"])
        for k in ("it", "max_iter", "preview"):
            if p.get(k) is not None:
                v[k] = p[k]
        if p.get("c") is not None:
            v["compliance"], v["vf"] = p["c"], p.get("vf")
        emit()
        # the record on disk follows too (GET /topopt/{id} polls), at most every SAVE_EVERY_S
        now = time.monotonic()
        if now - saved[0] >= SAVE_EVERY_S:
            saved[0] = now
            save()

    v.update(status="running", phase="setup", pct=0.5, started_at=time.time())
    save()
    emit()
    await asyncio.to_thread(_copy_inputs, plan_["part_dir"], plan_["others"], args, vdir)
    (vdir / "spec.json").write_text(json.dumps(v["spec"]))
    poll = asyncio.create_task(_poll(vdir, on_progress))
    try:
        rc, _, err = await run_sandboxed(
            [sys.executable, "-I", "-m", WORKER_MODULE, str(vdir.resolve())], vdir,
            timeout_s=VARIANT_TIMEOUT_S, what="the topology optimizer",
            memory_limit_mb=MEMORY_MB,
            env={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                 "VECLIB_MAXIMUM_THREADS": "1"})
    finally:
        poll.cancel()
    rep = safe_io.read_json(vdir, "report.json") if rc == 0 else None
    if not rep:
        msg = next((ln for ln in err.splitlines() if ln.startswith("TOPOPT_ERROR")), err.strip())
        msg = msg.replace("TOPOPT_ERROR: ", "").replace("TopoptError: ", "")
        raise SimError("topopt_failed", msg[-500:] or f"the optimizer exited {rc}")
    mat = plan_["material"]
    v.update(report={k: rep.get(k) for k in (
        "grid", "h_mm", "elements", "space_elements", "iters", "converged", "levels",
        "fallback_at", "compliance_gray", "compliance_bw", "deflection_bw", "vf_bw",
        "mesh", "faces", "final_vf", "islands_dropped_pct", "times_s", "total_s")},
             history=[[r["it"], r["c"], r["vf"]] for r in rep.get("history") or []],
             evolution=[int(i) for i in rep.get("evolution") or []],
             volume_mm3=rep["volume_mm3"],
             mass_g=rep["volume_mm3"] / 1000.0 * mat.density_g_cm3,
             faces=rep["faces"], step_bytes=rep["step_bytes"],
             thin_member_pct=rep.get("thin_member_pct"),
             overhang_area_pct=(rep.get("printability") or {}).get("overhang_area_pct"),
             compliance=rep["compliance_bw"], pct=92.0, phase="render")
    if (rep.get("thin_member_pct") or 0) > 25:
        v["warnings"].append(f"{rep['thin_member_pct']:.0f}% of the material is in members "
                             "under 3 voxels thick: the voxel model is optimistic there; "
                             "refine resolution_mm or raise filter_radius_mm")
    if rep.get("islands_dropped_pct"):
        v["warnings"].append(split_warning(rep))
    if not rep.get("converged"):
        v["warnings"].append(f"stopped at max_iter {v['max_iter']} before converging")
    save()
    emit()
    try:
        await asyncio.to_thread(_render, vdir)
    except Exception as e:  # noqa: BLE001 - a missing thumbnail never fails a variant
        v["warnings"].append(f"no render: {type(e).__name__}")
    if args.verify:
        if ccx is None:
            v["verify"] = {"error": "CalculiX (ccx) is not installed"}
        else:
            from . import verify as verify_mod
            v.update(phase="verify", pct=94.0)
            emit()
            size = max(1.5 * v["resolution_mm"], size_floor(v["volume_mm3"]))
            try:
                v["verify"] = await verify_mod.verify(
                    vdir, mat, ccx, size_mm=round(size, 3),
                    progress=lambda *a: (v.update(phase="verify"), emit()))
                c_ref = rep["compliance_bw"]
                v["compliance_error_pct"] = (round(100 * (v["verify"]["compliance"] - c_ref)
                                                   / c_ref, 2) if c_ref else None)
            except SimError as e:
                v["verify"] = {"error": e.message, "hint": verify_hint(e, rep)}
    v.update(status="done", phase="done", pct=100.0, finished_at=time.time())


SPLIT_PCT = 5.0          # dropped share over which a split result is a setup problem


def split_warning(rep: dict) -> str:
    """The variant's islands warning. A big share in other bodies (the drone frame: four arms,
    each held by its own stack-hole fixture and tied to nothing else) is a setup problem, not
    specks: say what happened and how to join them."""
    pct, n = float(rep.get("islands_dropped_pct") or 0.0), int(rep.get("bodies") or 0)
    if pct < SPLIT_PCT:
        return f"{pct:.1f}% of the volume was in disconnected islands and was removed"
    return (f"the optimized material split into {n if n > 1 else 'several'} separate bodies "
            f"and only the largest was kept ({pct:.1f}% of the volume dropped): each fixture "
            "region held its own piece, so nothing had to tie them together. If this must be "
            "one part, add a keep region joining them (e.g. a box over the hub between the "
            "fixtures), or fix one region and load the others")


def verify_hint(e: SimError, rep: dict) -> str | None:
    """A verification error's hint; a missing load / fixture face on a split result points at
    the split (the face went with a dropped body), not at the design space."""
    if ("is not on the variant" in (e.message or "")
            and float(rep.get("islands_dropped_pct") or 0.0) >= SPLIT_PCT):
        return ("that face was on a body dropped when the result split apart; join the bodies "
                "with a keep region (see the variant's warning) and run it again")
    return e.hint


_note_tasks: set[asyncio.Task] = set()


def note_lead(rt, rec: dict) -> bool:
    """Tell the agent that started the run that it finished, the way B3 tells it a study did
    (orchestrator.lead.deliver_note: sent now when idle, queued behind a busy turn, never a
    new user turn), so it reacts without polling. No note for a run the agent already waited
    on or read (topopt_wait), or one stopped with its agent (Stop); a run the user stopped on
    its own rides along with the next note. Returns whether a note was sent."""
    from .. import jobs as bg
    job = bg.get(rec["topopt_id"])
    if job is not None:
        job.status, job.summary = rec["status"], rec.get("summary") or ""
        job.finished_at = time.time()
        if job.stopped or job.consumed or job.waiters:
            job.consumed = job.consumed or bool(job.waiters)
            return False
    if rt is None or not rec.get("run_id"):
        return False
    if rec["status"] == "cancelled" and (job is None or not job.defer):
        return False
    from ..orchestrator.lead import deliver_note
    label = f"generative design of {rec.get('part_id')}"
    verb = {"done": "finished", "cancelled": "was cancelled by the user"}.get(rec["status"],
                                                                             "failed")
    summary = " ".join(str(rec.get("summary") or "").split())[:400].replace('"', "'")
    text = (f"[AgentCAD system note, not from the user] {label} ({rec['topopt_id']}) {verb}. "
            f"Result summary (data): \"{summary}\". Read it with topopt_result "
            f"{rec['topopt_id']}.")
    meta = {"kind": "sim_finished", "job": "topopt", "id": rec["topopt_id"],
            "topopt_id": rec["topopt_id"], "status": rec["status"]}
    t = asyncio.ensure_future(deliver_note(rt, rec["run_id"], text, meta,
                                           defer=bool(job is not None and job.defer)))
    _note_tasks.add(t)
    t.add_done_callback(_note_tasks.discard)
    return True


def parallel_variants() -> int:
    """Variants run at once: at most MAX_PARALLEL, one per MEMORY_MB of usable memory (each
    worker's watchdog budget) and half the heavy-worker slots (a variant's worker, then its
    mesher and solver, hold a slot for minutes: renders and studies keep the rest)."""
    from .. import hardware
    from ..heavy import heavy_limit
    by_mem = int(hardware.usable_memory_gb() * 1024 // MEMORY_MB)
    return max(1, min(MAX_PARALLEL, by_mem, heavy_limit() // 2))


async def _run_job(events, rec: dict, d: Path, args: TopoptRunArgs, plan_: dict, save) -> None:
    from ..sim.ccx import find_ccx
    ccx = find_ccx() if args.verify else None
    sem = asyncio.Semaphore(min(parallel_variants(), len(rec["variants"])))

    async def one(v: dict) -> None:
        async with sem:
            if rec["status"] == "cancelled":
                return
            try:
                await _run_variant(events, rec, v, d, args, plan_, ccx, save)
            except asyncio.CancelledError:
                v.update(status="cancelled", phase="cancelled", error="cancelled")
                raise
            except SimError as e:
                v.update(status="failed", phase="failed", error=e.message)
            except MisesError as e:
                v.update(status="failed", phase="failed", error=e.message)
            except Exception as e:  # noqa: BLE001
                v.update(status="failed", phase="failed", error=f"{type(e).__name__}: {e}")
            finally:
                # this variant's verified / passes marks right away (the order is final at the end)
                rank(rec["variants"], min_safety_factor=args.min_safety_factor,
                     max_deflection_mm=args.max_deflection_mm)
                save()

    rec["status"] = "running"
    save()
    try:
        await asyncio.gather(*(one(v) for v in rec["variants"]))
        rec["status"] = ("done" if any(v["status"] == "done" for v in rec["variants"])
                         else "failed")
    except asyncio.CancelledError:
        rec["status"] = "cancelled"
        for v in rec["variants"]:
            if v["status"] in ("queued", "running"):
                v.update(status="cancelled", phase="cancelled")
        raise
    finally:
        pareto, best = rank(rec["variants"], min_safety_factor=args.min_safety_factor,
                            max_deflection_mm=args.max_deflection_mm)
        rec.update(pareto=pareto, recommended=best, finished_at=time.time())
        rec["summary"] = summary(rec)
        save()
        # mark the background Job finished FIRST, so an exception in _publish cannot leave it
        # listed as running in GET /jobs for the life of the process (FW-core M-8)
        from .. import jobs as bg
        job = bg.get(rec["topopt_id"])
        if job is not None and job.status == "running":
            job.status = rec["status"]
            job.summary = rec.get("summary") or ""
            job.finished_at = time.time()
        _publish(events, rec, "topopt_finished", {
            "run_id": rec.get("run_id"), "status": rec["status"], "summary": rec["summary"],
            "recommended": best, "pareto": pareto})
        # a user's cancel starts no note turn (deferred); a Stop sends none (note_lead)
        note_lead(plan_.get("runtime"), rec)


async def start(ctx, args: TopoptRunArgs, tool: str) -> dict:
    """Plan (refusing bad input before anything exists), write the record and start the job in
    the background. Returns the record."""
    from .. import jobs as bg
    bg.check_room(ctx)                 # a 4-hour run counts against the family's job budget (M-8)
    plan_ = await asyncio.to_thread(plan, ctx, args)
    plan_["runtime"] = (getattr(ctx, "services", None) or {}).get("runtime")
    home = home_of(ctx)
    tid = "to_" + secrets.token_hex(6)
    d = job_dir(home, tid)
    project_id = None
    store = getattr(ctx, "store", None)
    if store is not None:
        try:
            row = store.get_part(args.part_id)
            project_id = row.project_id if row is not None else store.get_run(
                ctx.run_id).project_id
        except Exception:  # noqa: BLE001 - a bare test context
            project_id = None
    rec = {"topopt_id": tid, "tool": tool, "part_id": args.part_id, "project_id": project_id,
           "run_id": getattr(ctx, "run_id", None), "call_id": getattr(ctx, "call_id", None),
           "status": "queued", "created_at": time.time(), "finished_at": None,
           "reason": args.reason, "objective": args.objective,
           "min_safety_factor": args.min_safety_factor,
           "max_deflection_mm": args.max_deflection_mm,
           "material": plan_["material"].to_dict(),
           "args": args.model_dump(mode="json", exclude={"wait"}),
           "variants": plan_["variants"], "pareto": [], "recommended": None,
           "accepted": [], "summary": None}
    def save() -> None:
        write_record(d, rec)

    save()
    events = getattr(ctx, "events", None)

    async def body() -> None:
        try:
            await _run_job(events, rec, d, args, plan_, save)
        finally:
            _active.pop(tid, None)
            _cancel_requested.discard(tid)

    _active[tid] = asyncio.ensure_future(body())
    # B3: a background job like a study's — Stop / Stop all / POST /jobs/{id}/cancel reach it
    from .. import jobs as bg
    bg.register(bg.Job(id=tid, kind="topopt", run_id=rec["run_id"],
                       label=f"generative design of {args.part_id}", task=_active[tid],
                       cancel=lambda: cancel(tid), build_id=project_id, part_id=args.part_id,
                       scope=[args.part_id]))
    # a job-list refresh on start, the counterpart to topopt_finished, so the Simulations stream
    # shows the new generative run at once (B3 follow-up, core side)
    _publish(events, rec, "topopt_progress", {"status": rec["status"], "started": True})
    return rec


def cancel(topopt_id: str) -> bool:
    t = _active.get(topopt_id)
    if t is None or t.done():
        return False
    _cancel_requested.add(topopt_id)
    t.cancel()
    return True


async def wait(home: Path, topopt_id: str, timeout_s: float) -> tuple[dict, bool]:
    """(record, finished) after at most timeout_s; never cancels the job itself."""
    from .. import jobs as bg
    job = bg.get(topopt_id)
    t = _active.get(topopt_id)
    if t is not None and not t.done():
        if job is not None:
            job.waiters += 1         # an agent waiting on it reads the result: no note
        try:
            done, _ = await asyncio.wait({t}, timeout=timeout_s)
        finally:
            if job is not None:
                job.waiters -= 1
        if not done:
            return read_record(home, topopt_id), False
    if job is not None:
        job.consumed = True
    return read_record(home, topopt_id), True


def active_ids() -> list[str]:
    return [k for k, t in _active.items() if not t.done()]


def variant_file(home: Path, topopt_id: str, vid: str, name: str) -> tuple[bytes, str]:
    evo = re.fullmatch(r"evo_\d{3}\.glb", name or "") is not None
    if not VARIANT_ID_RE.fullmatch(vid or "") or (name not in _FILES and not evo):
        raise MisesError(ErrorCode.NOT_FOUND, f"no file {name!r}")
    vdir = job_dir(home, topopt_id) / vid
    try:
        data = safe_io.read_bytes(vdir, name)
    except safe_io.UnsafePath:
        data = None
    if data is None:
        raise MisesError(ErrorCode.NOT_FOUND, f"{topopt_id}/{vid} has no {name} yet")
    return data, _FILES.get(name, "model/gltf-binary")
