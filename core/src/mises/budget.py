"""The per-run dollar cap (Caps.usd_run), held across a run and all its descendants.

Like the Onshape family cap (onshape/client.py): a child's policy gets the parent's cap
narrowed to what the family has left (orchestrator.child_policy), but children run in
parallel, so only the family sum against the ROOT run's cap keeps them within it together.

Spend is runs.usd: Claude Code runs add the deltas of stream-json `total_cost_usd`; a provider
that reports no cost (the native loop's providers today, a Claude Code result without the
field) adds $0, and the run's cost view says so (`usd_note`).

A build's lead (Plan 7) is one long-lived run that gets every message of the build, so its
per-run caps (dollars, Onshape calls, web fetches, spawned sub-agents) count per user message:
`start_turn` opens a new window at each message (Runtime.send). Sub-agents spawned during the
turn count within it; a run without a window (every other run) counts over its whole life.
"""
from __future__ import annotations

import time
from pathlib import Path

from .store import AgentRun, Store
from .types import ErrorCode, MisesError

# providers whose runs report real dollar cost; any other run's usd is "unknown, counted as $0"
COST_REPORTING = frozenset({"claude-code"})
UNKNOWN_NOTE = "this provider does not report cost; counted as $0 toward the dollar cap"
TURN_HINT = "ask the user to raise the dollar cap; their next message starts a fresh budget"


def start_turn(store: Store, run_id: str) -> None:
    """A new user message to a build's lead: its caps count from now."""
    _root, family = store.run_family(run_id)
    store.start_turn(run_id, time.time(), store.runs_usd(family))


def _windowed(run: AgentRun) -> bool:
    """A root run (a lead) whose caps count per user message."""
    return run.turn_started_at is not None and run.parent_id is None


def turn_since(store: Store, run_id: str) -> float:
    """When the run's current cap window began: its turn start for a lead, else 0 (all of
    its life)."""
    try:
        run = store.get_run(run_id)
    except KeyError:
        return 0.0
    return float(run.turn_started_at) if _windowed(run) else 0.0


def onshape_used(store: Store, user: str, run_id: str) -> int:
    """Onshape calls the run made in its cap window (counted + reserved for a lead's turn)."""
    since = turn_since(store, run_id)
    if since:
        return store.ledger_used(user, since=since, run_id=run_id)
    try:
        return store.get_run(run_id).onshape_calls
    except KeyError:
        return 0


def family_spend(store: Store, run_id: str) -> tuple[str, list[str], float]:
    """(root run id, family run ids, dollars spent by the whole family in the root's window)."""
    root, family = store.run_family(run_id)
    used = store.runs_usd(family)
    try:
        r = store.get_run(root)
    except KeyError:
        return root, family, used
    base = float(r.turn_usd0 or 0.0) if _windowed(r) else 0.0
    return root, family, max(0.0, used - base)


def spawned_this_turn(store: Store, run_id: str) -> int:
    """Sub-agents the run's family started in the root's window (its whole life if none)."""
    root, family = store.run_family(run_id)
    return store.runs_created_since([f for f in family if f != root], turn_since(store, root))


def _cap(store: Store, run_id: str) -> float | None:
    try:
        return float(store.current_policy(run_id).caps.usd_run)
    except KeyError:
        return None


def remaining_usd(store: Store, run_id: str) -> float:
    """Dollars a new child of `run_id` may still spend: the smaller of the parent's own cap
    minus its spend and the root's cap minus the family's spend (never below 0). A lead's own
    spend is its family's this turn (it is the root)."""
    own_cap = _cap(store, run_id)
    try:
        run = store.get_run(run_id)
    except KeyError:
        run = None
    root, _family, used = family_spend(store, run_id)
    own_used = used if run is not None and _windowed(run) else (run.usd if run else 0.0)
    root_cap = _cap(store, root)
    left = []
    if own_cap is not None:
        left.append(own_cap - own_used)
    if root_cap is not None:
        left.append(root_cap - used)
    return max(0.0, min(left)) if left else 0.0


#: providers that are a subscription, never metered dollars: no dollar cap ever applies (G3)
SUBSCRIPTION = frozenset({"claude-code"})


def caps_enabled(store: Store, run: AgentRun) -> bool:
    """G3: whether dollar caps apply to `run`: only when the profile turns them on
    (`dollar_caps`, off by default), and never for a run of its family on a subscription
    runtime (Claude Code)."""
    if run.provider in SUBSCRIPTION:
        return False
    try:
        root, _family = store.run_family(run.id)
        if root != run.id and store.get_run(root).provider in SUBSCRIPTION:
            return False
    except KeyError:
        pass
    return profile_caps_on(store)


def profile_caps_on(store: Store) -> bool:
    """The profile's `dollar_caps` switch (off by default)."""
    db = getattr(store, "path", None)          # home/mises.db: the profile sits beside it
    if db is None or str(db) == ":memory:":
        return False
    try:
        from .profile import load_profile
        return bool(load_profile(Path(db).parent).dollar_caps)
    except Exception:  # noqa: BLE001 - an unreadable profile never stops an agent
        return False


def usd_exhausted(store: Store, run_id: str) -> MisesError | None:
    """BUDGET_EXHAUSTED when the run's own dollar cap, or its root's cap over the whole family,
    is spent; None otherwise. Nothing spent is never "exhausted" (an unknown-cost run counts
    $0, so it is stopped only by what cost-reporting relatives spent). A lead counts only
    this user message's spend (its own and its sub-agents'). G3: None whenever dollar caps
    are off (the default) or the run is on Claude Code."""
    try:
        run = store.get_run(run_id)
    except KeyError:
        return None
    if not caps_enabled(store, run):
        return None
    windowed = _windowed(run)
    cap = _cap(store, run_id)
    if not windowed and cap is not None and run.usd > 0 and run.usd >= cap:
        return MisesError(ErrorCode.BUDGET_EXHAUSTED,
                          f"dollar cap reached: ${run.usd:.2f} of ${cap:.2f} for this run",
                          hint="ask the user to raise the run's dollar cap, or start a new run")
    root, family, used = family_spend(store, run_id)
    root_cap = _cap(store, root)
    if root_cap is None or used <= 0 or used < root_cap:
        return None
    if windowed:
        return MisesError(ErrorCode.BUDGET_EXHAUSTED,
                          f"dollar cap reached: ${used:.2f} of ${root_cap:.2f} for this request"
                          " (the lead and its sub-agents)", hint=TURN_HINT)
    if len(family) > 1:
        root_run = store.get_run(root) if root != run_id else run
        return MisesError(ErrorCode.BUDGET_EXHAUSTED,
                          f"dollar cap of the root run reached: ${used:.2f} of ${root_cap:.2f}"
                          " across it and its subagents",
                          hint=TURN_HINT if _windowed(root_run) else
                          "ask the user to raise the run's dollar cap, or start a new run")
    return None


def check_usd(store: Store, run_id: str) -> None:
    err = usd_exhausted(store, run_id)
    if err is not None:
        raise err
