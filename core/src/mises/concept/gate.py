"""The concept gate (wave-3 shared contract) and the build's design class.

`check_call` runs in the Gate on every tool call (a `call_checks` service) before approval and
before the handler: for a build whose design class is `design`, every `mirror_part_create` of
a part this build does not have yet, and every `onshape_import_element`, is refused with "needs
concept approval" until the user approved a sketch or chose "Skip sketch". No part name is
exempt: the concept massing is built by `concept_sketch` under `<home>/concepts/`, never as a
build part, and the skeleton goes through `skeleton_set` (not gated). Rebuilding a part that
already belongs to THIS build is never refused (the gate is about starting parts before the
look is agreed, not about editing them); a part id that exists in another build is not a
rebuild. Catalog inserts (bought mechanism parts: servos, bearings) are not gated (ruling).

The design class is the wave-3 contract's field `design_class` (G-B's `design_intensity`
payload {score, cls, ...} or a bare class name): on the build's lead run (model attribute or the
`runs.design_class` column), else on the build (`projects.design_class` column), else G-B's
classifier on the lead's task. Missing everywhere, the class is read as `mixed` (a warning is
logged once): a sketch is recommended, nothing is refused.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from ..types import ErrorCode, MisesError
from . import OPEN

log = logging.getLogger(__name__)

CLASSES = ("engineering", "mixed", "design")
FALLBACK_CLASS = "mixed"
GATED_TOOLS = ("mirror_part_create", "onshape_import_element")
_warned = False


def _as_class(raw: Any) -> dict | None:
    """G-B's payload ({score, cls, per_part, reasons}), its JSON text, or a bare class name,
    normalised; None for anything else."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        raw = parsed if isinstance(parsed, dict) else {"cls": raw}
    if not isinstance(raw, dict):
        return None
    cls = raw.get("cls")
    if cls not in CLASSES:
        return None
    out = {"cls": cls}
    score = raw.get("score")
    if isinstance(score, (int, float)):
        out["score"] = float(score)
    if isinstance(raw.get("reasons"), list):
        out["reasons"] = [str(r)[:200] for r in raw["reasons"][:8]]
    return out


def _classify(task: str) -> dict | None:
    """G-B's keyword-prior classifier when it is installed and synchronous; None otherwise."""
    try:
        from .. import design_intensity as di  # type: ignore[attr-defined]
    except ImportError:
        return None
    fn = getattr(di, "design_intensity", None)
    if fn is None or inspect.iscoroutinefunction(fn):
        return None
    try:
        return _as_class(fn(task))
    except Exception:  # noqa: BLE001 - a classifier failure never blocks a build
        log.debug("design_intensity failed", exc_info=True)
        return None


def _column(store, table: str, row_id: str | None) -> Any:
    """The raw `design_class` column of a runs / projects row; None when the column (G-B's
    migration) or the row is not there."""
    if store is None or row_id is None or not hasattr(store, "_one"):
        return None
    try:
        row = store._one(f"SELECT design_class FROM {table} WHERE id=?", (row_id,))
    except Exception:  # noqa: BLE001 - no such column before G-B's migration
        return None
    return row[0] if row else None


def design_class(store, project_id: str | None, run_id: str | None = None) -> dict:
    """{cls, score?, reasons?, source}: the `design_class` of the build's lead run, else of the
    build, else the classifier on the lead's task, else `mixed`."""
    global _warned
    from ..orchestrator.lead import find_lead
    # Plan 9 G-B: the class the user set on the build (PATCH /projects/{id} {design_class})
    # wins over everything else
    user_cls = None
    if project_id and hasattr(store, "project_design_class"):
        try:
            user_cls = store.project_design_class(project_id)
        except Exception:  # noqa: BLE001
            user_cls = None
    if user_cls in CLASSES:
        return {"cls": user_cls, "source": "user"}
    run = None
    try:
        run = find_lead(store, project_id) if project_id else None
        if run is None and run_id is not None:
            run = store.get_run(run_id)
    except Exception:  # noqa: BLE001
        run = None
    if run is not None:
        stored = (_as_class(getattr(run, "design_class", None))
                  or _as_class(_column(store, "runs", getattr(run, "id", None))))
        if stored is not None:
            return {**stored, "source": "run"}
    build = _as_class(_column(store, "projects", project_id))
    if build is not None:
        return {**build, "source": "build"}
    if run is not None:
        guessed = _classify(run.task or "")
        if guessed is not None:
            return {**guessed, "source": "classifier"}
    if not _warned:
        _warned = True
        log.warning("no design_class on the build or its lead (G-B not merged?): the concept "
                    "gate reads it as %r and refuses nothing", FALLBACK_CLASS)
    return {"cls": FALLBACK_CLASS, "source": "fallback"}


def required(cls: str) -> str:
    """What the concept stage asks of a class: "pitch" (design: pitch, then sketch, approval
    required), "sketch" (mixed: one sketch recommended) or "off" (engineering)."""
    return {"design": "pitch", "mixed": "sketch"}.get(cls, "off")


def _run_project(store, run_id: str) -> str | None:
    try:
        return store.get_run(run_id).project_id
    except Exception:  # noqa: BLE001
        return None


def check_call(ctx, name: str, args) -> None:
    """The Gate's call check: raise NEEDS_APPROVAL for a new part (or an Onshape import) in a
    design-class build whose concept is not approved or skipped."""
    if name not in GATED_TOOLS:
        return
    concepts = (ctx.services or {}).get("concept")
    store = getattr(ctx, "store", None)
    if concepts is None or store is None:
        return
    project_id = _run_project(store, ctx.run_id)
    if project_id is None:
        return
    if name == "mirror_part_create":
        try:
            row = store.get_part(getattr(args, "part_id", "") or "")
        except Exception:  # noqa: BLE001
            row = None
        if row is not None and row.project_id == project_id:
            return                               # rebuilding a part of this build
    cls = design_class(store, project_id, ctx.run_id)["cls"]
    if cls != "design":
        return
    status = concepts.get(project_id)["status"]
    if status in OPEN:
        return
    raise MisesError(
        ErrorCode.NEEDS_APPROVAL,
        f"needs concept approval: this is a design-heavy build and its concept is "
        f"{status!r}; parts start after the user approves a sketch (or skips the sketch)",
        hint="pitch 2-3 directions with pitch_options, then concept_sketch the chosen one and "
             "wait for the user's Approve (concept_status shows where it stands); the layout "
             "skeleton (skeleton_set) may be set meanwhile")
