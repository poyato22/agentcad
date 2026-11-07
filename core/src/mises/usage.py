"""How much usage a turn (one user message's agent run, sub-agents included) and a build took.

Store.add_usage records a usage event per model call or Claude Code result, tagged with its
root run and the root's turn (a lead's turn_started_at; any other root's created_at). This
module only aggregates them. Dollars are what the runtime reported (Claude Code's stream-json
total_cost_usd deltas: an API-price equivalent, not a charge on a subscription) or, for an
API provider that reports none, an estimate from PRICES; anything else is "unknown".
Runs from before usage events have only their row totals: a build reports those as
`unrecorded`, never spread over turns.
"""
from __future__ import annotations

from .store import Store

# USD per million tokens: (input, output, cache read, cache write). Matched by model-id prefix,
# first match wins, so the most specific prefixes come first. Anthropic first-party API list
# prices (Oct 2026 model table); cache write is the 5-minute rate, 1.25x input. Edit here when
# prices change.
PRICES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("claude-fable-5", (10.0, 50.0, 0.25, 12.5)),     # Fable 5 / 5.1
    ("claude-mythos-5", (10.0, 50.0, 0.25, 12.5)),
    ("claude-opus-5-5", (4.0, 20.0, 0.2, 5.0)),
    ("claude-opus-5", (5.0, 25.0, 0.5, 6.25)),
    ("claude-opus-4-0", (15.0, 75.0, 1.5, 18.75)),
    ("claude-opus-4-1", (15.0, 75.0, 1.5, 18.75)),
    ("claude-opus-4-2", (15.0, 75.0, 1.5, 18.75)),   # "claude-opus-4-20250514"
    ("claude-opus-4", (5.0, 25.0, 0.5, 6.25)),        # Opus 4.5 - 4.8
    ("claude-sonnet-5", (2.0, 10.0, 0.2, 2.5)),       # Sonnet 5 / 5.5
    ("claude-sonnet-4", (3.0, 15.0, 0.3, 3.75)),
    ("claude-haiku-5-5", (0.1, 0.5, 0.01, 0.125)),    # prompts up to 100K tokens
    ("claude-haiku-4", (1.0, 5.0, 0.1, 1.25)),
    ("claude-3-5-haiku", (0.8, 4.0, 0.08, 1.0)),
)
PRICED_PROVIDERS = frozenset({"anthropic"})

SUBSCRIPTION_NOTE = ("API-price equivalent: on a Claude subscription this is what the tokens "
                     "would cost on the API, not a charge")


def price(provider: str, model: str, input_tokens: int, output_tokens: int,
          cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
    """The API list price of these tokens, or None for a provider or model with no price."""
    if provider not in PRICED_PROVIDERS:
        return None
    m = model.rsplit("/", 1)[-1].lower()
    for prefix, (pi, po, pr, pw) in PRICES:
        if m.startswith(prefix):
            return (input_tokens * pi + output_tokens * po + cache_read_tokens * pr
                    + cache_write_tokens * pw) / 1_000_000
    return None


_TOK = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _empty() -> dict:
    return {**{k: 0 for k in _TOK}, "tokens": 0, "usd": 0.0, "_src": set()}


def _add(acc: dict, ev: dict) -> None:
    for k in _TOK:
        acc[k] += int(ev.get(k) or 0)
    acc["tokens"] = sum(acc[k] for k in _TOK)
    acc["usd"] += float(ev.get("usd") or 0.0)
    acc["_src"].add(ev.get("usd_source") or "unknown")


def _source(srcs: set) -> str:
    if len(srcs) == 1:
        return next(iter(srcs))
    return "partial" if "unknown" in srcs else "priced" if "priced" in srcs else "reported"


def _done(acc: dict) -> dict:
    out = {k: v for k, v in acc.items() if k != "_src"}
    out["usd"] = round(out["usd"], 6)
    out["usd_source"] = _source(acc["_src"])
    return out


def _agent_label(store: Store, run_id: str) -> dict:
    try:
        r = store.get_run(run_id)
    except KeyError:
        return {"agent": "deleted agent", "title": None, "role": None}
    return {"agent": r.agent, "title": r.title, "role": r.role or ("sub" if r.parent_id else None)}


def _turns(store: Store, events: list[dict]) -> list[dict]:
    turns: dict[tuple, dict] = {}
    agents: dict[tuple, dict] = {}
    for ev in events:
        key = (ev["root_id"], ev["turn_ts"])
        turns.setdefault(key, _empty())
        _add(turns[key], ev)
        akey = (*key, ev["run_id"], ev["model"])
        if akey not in agents:
            agents[akey] = {**_empty(), "run_id": ev["run_id"], "model": ev["model"],
                            "provider": ev["provider"]}
        _add(agents[akey], ev)
    labels: dict[str, dict] = {}
    out = []
    for (root, ts), acc in sorted(turns.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows = []
        for akey, a in agents.items():
            if akey[:2] != (root, ts):
                continue
            rid = a["run_id"]
            labels.setdefault(rid, _agent_label(store, rid))
            rows.append({**_done(a), **labels[rid]})
        rows.sort(key=lambda a: (a["run_id"] != root, -a["usd"], -a["tokens"]))
        out.append({"root_id": root, "turn_ts": ts, **_done(acc), "agents": rows})
    return out


def run_turns(store: Store, run_id: str) -> list[dict]:
    """The turns of `run_id`'s root (the build's lead for a sub-agent), oldest first, each
    with its total and a breakdown by agent and model."""
    root, _family = store.run_family(run_id)
    return _turns(store, store.usage_events(root_id=root))


def build_usage(store: Store, project_id: str) -> dict:
    """A build's total, its turns, and what its older runs used before usage events
    (`unrecorded`: their row totals not covered by events; None when there is none)."""
    events = store.usage_events(project_id=project_id)
    turns = _turns(store, events)
    total = _empty()
    for ev in events:
        _add(total, ev)
    covered: dict[str, list[float]] = {}
    for ev in events:
        c = covered.setdefault(ev["run_id"], [0, 0.0])
        c[0] += sum(int(ev.get(k) or 0) for k in _TOK)
        c[1] += float(ev["usd"] or 0.0) if ev.get("usd_source") == "reported" else 0.0
    un_runs, un_tok, un_usd = 0, 0, 0.0
    for r in store.runs_in_project(project_id):
        tok, usd = covered.get(r.id, [0, 0.0])
        extra_tok = max(0, r.input_tokens + r.output_tokens - tok)
        extra_usd = max(0.0, r.usd - usd)
        if extra_tok or extra_usd > 1e-9:
            un_runs, un_tok, un_usd = un_runs + 1, un_tok + extra_tok, un_usd + extra_usd
    return {"project_id": project_id, **_done(total), "turns": turns,
            "recorded": bool(events),
            "unrecorded": ({"runs": un_runs, "tokens": un_tok, "usd": round(un_usd, 6)}
                           if un_runs else None),
            "note": SUBSCRIPTION_NOTE}


def build_summary(store: Store, project_id: str) -> dict:
    """The Home tile's figure: the build's recorded total (no per-turn detail)."""
    row = store._one("SELECT COUNT(*), COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens"
                     "+cache_write_tokens),0), COALESCE(SUM(usd),0) FROM usage_events"
                     " WHERE project_id=?", (project_id,))
    return {"recorded": bool(row[0]), "tokens": int(row[1]), "usd": round(float(row[2]), 6)}
