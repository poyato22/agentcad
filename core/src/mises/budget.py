"""The per-run dollar cap (Caps.usd_run), held across a run and all its descendants.

Like the Onshape family cap (onshape/client.py): a child's policy gets the parent's cap
narrowed to what the family has left (orchestrator.child_policy), but children run in
parallel, so only the family sum against the ROOT run's cap keeps them within it together.

Spend is runs.usd: Claude Code runs add the deltas of stream-json `total_cost_usd`; a provider
that reports no cost (the native loop's providers today, a Claude Code result without the
field) adds $0, and the run's cost view says so (`usd_note`).
"""
from __future__ import annotations

from .store import Store
from .types import ErrorCode, MisesError

# providers whose runs report real dollar cost; any other run's usd is "unknown, counted as $0"
COST_REPORTING = frozenset({"claude-code"})
UNKNOWN_NOTE = "this provider does not report cost; counted as $0 toward the dollar cap"


def family_spend(store: Store, run_id: str) -> tuple[str, list[str], float]:
    """(root run id, family run ids, dollars spent by the whole family)."""
    root, family = store.run_family(run_id)
    return root, family, store.runs_usd(family)


def _cap(store: Store, run_id: str) -> float | None:
    try:
        return float(store.current_policy(run_id).caps.usd_run)
    except KeyError:
        return None


def remaining_usd(store: Store, run_id: str) -> float:
    """Dollars a new child of `run_id` may still spend: the smaller of the parent's own cap
    minus its spend and the root's cap minus the family's spend (never below 0)."""
    own_cap = _cap(store, run_id)
    try:
        own_used = store.get_run(run_id).usd
    except KeyError:
        own_used = 0.0
    root, _family, used = family_spend(store, run_id)
    root_cap = _cap(store, root)
    left = []
    if own_cap is not None:
        left.append(own_cap - own_used)
    if root_cap is not None:
        left.append(root_cap - used)
    return max(0.0, min(left)) if left else 0.0


def usd_exhausted(store: Store, run_id: str) -> MisesError | None:
    """BUDGET_EXHAUSTED when the run's own dollar cap, or its root's cap over the whole family,
    is spent; None otherwise. Nothing spent is never "exhausted" (an unknown-cost run counts
    $0, so it is stopped only by what cost-reporting relatives spent)."""
    try:
        run = store.get_run(run_id)
    except KeyError:
        return None
    cap = _cap(store, run_id)
    if cap is not None and run.usd > 0 and run.usd >= cap:
        return MisesError(ErrorCode.BUDGET_EXHAUSTED,
                          f"dollar cap reached: ${run.usd:.2f} of ${cap:.2f} for this run",
                          hint="ask the user to raise the run's dollar cap, or start a new run")
    root, family, used = family_spend(store, run_id)
    root_cap = _cap(store, root)
    if len(family) > 1 and root_cap is not None and used > 0 and used >= root_cap:
        return MisesError(ErrorCode.BUDGET_EXHAUSTED,
                          f"dollar cap of the root run reached: ${used:.2f} of ${root_cap:.2f}"
                          " across it and its subagents",
                          hint="ask the user to raise the run's dollar cap, or start a new run")
    return None


def check_usd(store: Store, run_id: str) -> None:
    err = usd_exhausted(store, run_id)
    if err is not None:
        raise err
