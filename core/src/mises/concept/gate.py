"""The concept gate (wave-3 shared contract) and the build's design class.

`check_call` runs in the Gate on every tool call (a `call_checks` service) before approval and
before the handler: for a build whose design class is `design`, `mirror_part_create` of a NEW
part that is not a massing/skeleton part is refused with "needs concept approval" until the
user approved a sketch or chose "Skip sketch". Rebuilding an existing part is never refused
(the gate is about starting parts before the look is agreed, not about editing them).

The design class comes from G-B's classifier (`design_intensity`), stored on the build's lead
run as `design_class`. Before that lands (or when it is missing) the class is read as `mixed`:
a sketch is recommended, nothing is refused.
"""
from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path
from typing import Any

from ..cad import safe_io
from ..types import ErrorCode, MisesError
from . import OPEN

log = logging.getLogger(__name__)

CLASSES = ("engineering", "mixed", "design")
FALLBACK_CLASS = "mixed"
GATED_TOOLS = ("mirror_part_create",)
# a part named like this is concept-stage geometry (rough massing, the layout skeleton): never
# refused, it is what the concept stage builds on
_MASSING_RE = re.compile(r"^(?:massing|skeleton|concept)(?:_|$)")


def is_massing_part(part_id: str) -> bool:
    return bool(_MASSING_RE.match(part_id or ""))


def _as_class(raw: Any) -> dict | None:
    """G-B's payload ({score, cls, per_part, reasons}) or a bare class name, normalised; None
    for anything else."""
    if isinstance(raw, str):
        raw = {"cls": raw}
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


def design_class(store, project_id: str | None, run_id: str | None = None) -> dict:
    """{cls, score?, reasons?, source}: the build lead's stored class, else the classifier on
    its task, else `mixed` (no classifier)."""
    from ..orchestrator.lead import find_lead
    run = None
    try:
        run = find_lead(store, project_id) if project_id else None
        if run is None and run_id is not None:
            run = store.get_run(run_id)
    except Exception:  # noqa: BLE001
        run = None
    if run is not None:
        stored = _as_class(getattr(run, "design_class", None))
        if stored is not None:
            return {**stored, "source": "run"}
        guessed = _classify(run.task or "")
        if guessed is not None:
            return {**guessed, "source": "classifier"}
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
    """The Gate's call check: raise NEEDS_APPROVAL for a new non-massing part in a design-class
    build whose concept is not approved or skipped."""
    if name not in GATED_TOOLS:
        return
    part_id = getattr(args, "part_id", "") or ""
    if is_massing_part(part_id):
        return
    if safe_io.exists(Path(ctx.workdir) / "parts" / part_id, "metrics.json"):
        return                                   # rebuilding a part that exists
    concepts = (ctx.services or {}).get("concept")
    store = getattr(ctx, "store", None)
    if concepts is None or store is None:
        return
    project_id = _run_project(store, ctx.run_id)
    if project_id is None:
        return
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
             "wait for the user's Approve (concept_status shows where it stands); massing_* "
             "and skeleton parts are allowed meanwhile")
