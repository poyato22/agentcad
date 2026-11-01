"""The ideation gate (Plan 11 F8b), a Gate `call_checks` entry beside the concept gate.

In a build flagged as an open-ended challenge (ideation.build_challenge):
- `pitch_options` is refused until the build has an `ideate_concepts` round, so a pitch always
  comes out of >= 8 concepts on distinct principles, never out of the first idea;
- a NEW part (`mirror_part_create` of a part this build does not have) is refused until there
  is an ideation and a pitch, and the user answered it: picked an option, sent a message after
  it, or opened the concept gate (approved / skipped). Rebuilding a part of this build is never
  refused, and a build whose concept gate is already open is not held back.
"""
from __future__ import annotations

from ..concept import OPEN
from ..types import ErrorCode, MisesError, StepKind
from . import build_challenge

GATED = ("pitch_options", "mirror_part_create")


def _project(store, run_id: str) -> str | None:
    try:
        return store.get_run(run_id).project_id
    except Exception:  # noqa: BLE001
        return None


def _user_replied_after(store, project_id: str, ts: float) -> bool:
    """A user message in the build's lead thread after `ts` (the pitch)."""
    from ..orchestrator.lead import find_lead
    try:
        lead = find_lead(store, project_id)
    except Exception:  # noqa: BLE001
        lead = None
    if lead is None:
        return False
    for s in reversed(store.steps(lead.id, [StepKind.MESSAGE])):
        if s.ts <= ts:
            return False
        if s.payload.get("role") == "user" and "system_note" not in s.payload:
            return True
    return False


def check_call(ctx, name: str, args) -> None:
    if name not in GATED:
        return
    concepts = (ctx.services or {}).get("concept")
    store = getattr(ctx, "store", None)
    if concepts is None or store is None:
        return
    project = _project(store, ctx.run_id)
    if project is None or build_challenge(store, project) is None:
        return
    if name == "mirror_part_create":
        try:
            row = store.get_part(getattr(args, "part_id", "") or "")
        except Exception:  # noqa: BLE001
            row = None
        if row is not None and row.project_id == project:
            return                                   # rebuilding a part of this build
    rec = concepts.get(project)
    if rec["status"] in OPEN:
        return
    from .tools import IdeationStore
    ideas = getattr(concepts, "_ideation", None) or IdeationStore(concepts)
    concepts._ideation = ideas
    if ideas.latest(project) is None:
        raise MisesError(
            ErrorCode.NEEDS_APPROVAL,
            f"this build is an open-ended challenge: {name} waits for an ideation round",
            hint="call ideate_concepts with at least 8 genuinely different concepts on "
                 "distinct physical principles (fetch the rules first for a competition), "
                 "then pitch the 3 it shortlists with pitch_options")
    if name == "pitch_options":
        return
    pitches = concepts.pitches(project)
    if not pitches:
        raise MisesError(
            ErrorCode.NEEDS_APPROVAL,
            "this build is an open-ended challenge: new parts wait for the pitch",
            hint="pitch the ideation's shortlist with pitch_options and wait for the user")
    last = pitches[-1]
    if any(p.get("chosen") for p in pitches) or _user_replied_after(
            store, project, float(last.get("created_at") or 0)):
        return
    raise MisesError(
        ErrorCode.NEEDS_APPROVAL,
        "new parts wait for the user's pick of the pitched concepts",
        hint="end your turn and let the user pick, mix or comment on the pitch")
