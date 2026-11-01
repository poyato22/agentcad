"""E33: what the lead sees of the whole assembly after it changed it.

- `assembly_report`: the bbox health (health.py) of a build's assembly, with the bbox overlap
  candidates replaced by the real OCCT interference volumes (the assembly_check worker, boolean
  common) whenever any boxes overlap. bbox stays the prefilter: no candidate, no worker run.
- `overview_images`: a whole-assembly render (every instance, strays included, so the camera
  frames all of them), overlapping parts tinted red, strays orange, floating parts amber, as at
  most 2 ToolImages at 1024 px, labelled with what is wrong.
- `claims_done` / `fix_note`: the turn-end finish check (Runtime._after_turn) warns a lead that
  reports completion while the health has errors.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path

from ..types import MisesError, ToolImage
from .geometry import run_geometry
from .health import finalize, health
from .interference import TOL_MM3, allowed_contacts, classify, problems


log = logging.getLogger(__name__)

OCCT_TIMEOUT_S = 60.0
OVERVIEW_VIEWS = ("iso", "top")
OVERVIEW_SIZE = 1024
_OK = (0.62, 0.68, 0.76)
_RED = (0.92, 0.16, 0.16)
_ORANGE = (1.0, 0.55, 0.05)
_AMBER = (0.95, 0.80, 0.10)


def _bboxes(root: Path, asm) -> dict[str, dict]:
    from ..cad import safe_io
    out: dict[str, dict] = {}
    for inst in asm.instances:
        if inst.part_id in out:
            continue
        try:
            m = safe_io.read_json(root / inst.part_id, "metrics.json")
        except (OSError, ValueError, safe_io.UnsafePath):
            continue
        bbox = m.get("bbox") if isinstance(m, dict) else None
        if isinstance(bbox, dict) and "min" in bbox and "max" in bbox:
            out[inst.part_id] = bbox
    return out


def bbox_report(asm_store, root: Path, project_id: str) -> tuple[dict, object, object]:
    """(bbox health report, assembly, solve result) of a build's assembly."""
    from .pose import solve_assembly                      # the one pose (P11 F4)
    asm = asm_store.get(project_id)
    res = solve_assembly(asm, root)
    # the prefilter keeps every box overlap that could hold a listed one (G2: common <= box)
    return (health(asm.instances, res.transforms, _bboxes(root, asm), joints=asm.joints,
                   overlap_tol_mm3=TOL_MM3), asm, res)


async def assembly_report(asm_store, root: Path, project_id: str, *,
                          timeout_s: float = OCCT_TIMEOUT_S) -> dict:
    """The health report with real interference volumes for bbox-overlapping pairs (E33): the
    same list the app draws (G2, assembly/interference.py), declared intended contacts kept
    apart under `intended` and never counted as problems."""
    rep, asm, res = bbox_report(asm_store, root, project_id)
    if not rep["overlaps"]:
        return {**rep, "intended": []}
    allowed = allowed_contacts(asm_store, asm, project_id)
    try:
        geo = await run_geometry(root, asm, res, want="interference", timeout_s=timeout_s)
    except MisesError as e:
        # no OCCT verdict (a part not built as STEP, a timeout): keep the bbox candidates,
        # marked as approximate, rather than claim the assembly is clear
        log.info("assembly interference check unavailable: %s", e.message)
        cand = [o for o in rep["overlaps"]
                if tuple(sorted((o["a_id"], o["b_id"]))) not in allowed]
        return finalize({**rep, "overlaps": cand, "intended": [], "overlap_method": "bbox",
                         "overlap_note": e.message})
    items = classify(geo.interferences, asm, allowed)
    return finalize({**rep, "overlaps": problems(items),
                     "intended": [i for i in items if i.get("intended")],
                     "overlap_method": "occt"})


def record_health(rt, project_id: str, report: dict) -> None:
    """Write the health verdict into C3's engineering pipeline (check `assembly_health`), with
    the assembly signature it saw, so the done gate reads it and a later change makes it stale.
    Never raises."""
    try:
        from ..engineering import pipeline
        asm = rt.assembly_store.get(project_id)
        pipeline.record(rt.home, project_id, "assembly_health",
                        status="pass" if report["ok"] else "fail", summary=report["summary"],
                        sig=pipeline.signature(asm, rt.home, store=rt.store,
                                               project_id=project_id),
                        extra={"overlap_method": report.get("overlap_method")})
    except Exception:  # noqa: BLE001
        log.debug("could not record assembly_health for %s", project_id, exc_info=True)


def _tints(report: dict) -> tuple[dict[str, tuple], list[str]]:
    tint: dict[str, tuple] = {}
    notes: list[str] = []
    for f in report.get("floating") or []:
        tint[f["instance_id"]] = _AMBER
    if report.get("floating"):
        notes.append("amber = no joint: " + ", ".join(f["instance"] for f in report["floating"]))
    for s in report.get("stray") or []:
        tint[s["instance_id"]] = _ORANGE
    if report.get("stray"):
        notes.append("orange = stray: " + ", ".join(
            f"{s['instance']} ({s['distance_mm']:g} mm away)" for s in report["stray"]))
    for o in report.get("overlaps") or []:
        tint[o["a_id"]] = tint[o["b_id"]] = _RED
    if report.get("overlaps"):
        notes.append("red = overlap: " + ", ".join(
            f"{o['a']}/{o['b']} ({o['volume_mm3']:g} mm^3)" for o in report["overlaps"]))
    return tint, notes


async def overview_images(home: Path, asm_store, project_id: str, report: dict, *,
                          size: int = OVERVIEW_SIZE, limit: int = 2) -> list[ToolImage]:
    """A whole-assembly render for the lead: every built instance at its solved pose (the
    renderer frames the bbox of all meshes, so a stray part is in frame), problem parts tinted.
    [] when nothing is built or the render fails (never raises)."""
    from ..cad import render as render_mod
    from ..cad import safe_io
    from ..cad.render_job import MeshIn, run_render_job
    from ..cad.views import renders_dir
    root = Path(home) / "parts"
    try:
        from .pose import world_poses
        asm = asm_store.get(project_id)
        tf, _err = world_poses(asm, root)
    except Exception:  # noqa: BLE001
        return []
    tint, notes = _tints(report)
    meshes = [MeshIn(root / i.part_id, i.part_id, tint.get(i.id, _OK), 1.0,
                     tf.get(i.id) or i.transform)
              for i in asm.instances if safe_io.exists(root / i.part_id, "model.stl")]
    if not meshes:
        return []
    views = list(OVERVIEW_VIEWS)[:limit]
    try:
        out = await run_render_job(renders_dir(Path(home)) / "jobs", meshes,
                                   renders=[{"key": "asm", "views": views, "size": size}])
    except Exception:  # noqa: BLE001 - the report still goes out as text
        log.warning("whole-assembly render failed", exc_info=True)
        return []
    pngs = out.pngs.get("asm") or {}
    legend = "; ".join(notes) or "assembly clean"

    def fit() -> list[ToolImage]:
        return [ToolImage(media_type="image/png",
                          data_b64=base64.b64encode(render_mod.fit_png(png, size)).decode(),
                          label=f"whole assembly ({view}): {legend}")
                for view, png in list(pngs.items())[:limit]]
    return await asyncio.to_thread(fit)


_DONE = re.compile(r"\b(done|complete[d]?|finished|ready|all set|built|assembled|"
                   r"that'?s it|here(?:'s| is) the (?:final|finished))\b", re.I)


def claims_done(text: str | None) -> bool:
    """Whether a lead's final message reports the work as complete (the finish check)."""
    return bool(_DONE.search(text or ""))


def overlap_signature(report: dict) -> tuple:
    """The overlaps a note told the lead about (pairs and volumes rounded to 0.1 mm^3)."""
    return tuple(sorted((*sorted((o["a_id"], o["b_id"])), round(o["volume_mm3"], 1))
                        for o in report.get("overlaps") or []))


def _where(o: dict) -> str:
    c = o.get("center")
    if not c:
        return ""
    return " at (" + ", ".join(f"{v:g}" for v in (round(x, 1) for x in c)) + ") mm"


def overlap_note(report: dict) -> str:
    """G2: the system note to a lead whose turn changed the assembly and left overlaps: every
    pair the user sees in red (volume, where), and what to do about each."""
    lines = "\n".join(f"- {o['a']} / {o['b']}: {o['volume_mm3']:g} mm^3{_where(o)}"
                      for o in report["overlaps"])
    return ("Assembly check after your last turn: these parts overlap, and the user sees each "
            f"pair as a red box in the assembly ({report['summary']}):\n{lines}\nResolve them "
            "before you continue: move, mate or resize the parts (then check again). An overlap "
            "that is the design (a press fit, a bearing seat, a pin or fastener in its hole, "
            "coincident mating faces) is declared with assembly_allow_contact(a, b, reason); "
            "tell the user why for each one you keep.")


def fix_note(report: dict) -> str:
    """The system note sent to a lead that reported done with an unhealthy assembly."""
    from .health import warnings_from
    lines = "\n".join(f"- {w}" for w in warnings_from(report))
    return ("Assembly check after your last turn: you reported the work as done, but the "
            f"assembly is not clean ({report['summary']}).\n{lines}\nFix these (move/mate the "
            "parts, rebuild, run assembly_check) and tell the user what changed.")
