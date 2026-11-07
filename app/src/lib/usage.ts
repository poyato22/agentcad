// How a turn's or a build's usage reads: "≈$0.42 · 182k tokens". Dollars on Claude Code are an
// API-price equivalent (not a charge on a subscription); a price-table figure is an estimate;
// a provider that reports no cost shows tokens only.
import type { TurnUsage, UsdSource } from "../api/usage";
import type { ThreadItem } from "../ui/console/threadModel";

export const EQUIV_NOTE = "API-price equivalent: on a Claude subscription this is what the tokens would cost on the API, not a charge.";

export function fmtTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 2 : 1).replace(/\.?0+$/, "")}M`;
}

export function fmtUsd(usd: number): string {
  if (usd <= 0) return "$0";
  if (usd < 0.01) return "<$0.01";
  return `$${usd.toFixed(2)}`;
}

/** "≈$0.42 · 182k tokens"; tokens only when no cost is known. */
export function usageText(u: { usd: number; tokens: number; usd_source: UsdSource }): string {
  const tok = `${fmtTokens(u.tokens)} tokens`;
  if (u.usd_source === "unknown") return tok;
  return `≈${fmtUsd(u.usd)}${u.usd_source === "partial" ? "+" : ""} · ${tok}`;
}

export function sourceNote(s: UsdSource): string {
  switch (s) {
    case "reported": return EQUIV_NOTE;
    case "priced": return "Estimated from API list prices; the provider reported no cost.";
    case "partial": return "Some of this usage has no known cost, so the figure is a lower bound.";
    default: return "This provider reports no cost: tokens only.";
  }
}

/** Where each turn's usage line goes: under the last row of the turn (the row before the next
 *  user message that starts another turn, or the thread's last row). A turn starts at its
 *  `turn_ts`; a user message belongs to the latest turn begun by its time (2 s slack). Returns
 *  row key -> turn. */
export function placeTurns(items: readonly ThreadItem[], turns: readonly TurnUsage[]): Map<string, TurnUsage> {
  const out = new Map<string, TurnUsage>();
  if (turns.length === 0 || items.length === 0) return out;
  const sorted = [...turns].sort((a, b) => a.turn_ts - b.turn_ts);
  const turnAt = (ts: number): TurnUsage | null => {
    let hit: TurnUsage | null = null;
    for (const t of sorted) if (t.turn_ts <= ts + 2) hit = t;
    return hit;
  };
  let cur: TurnUsage | null = null;
  let lastKey: string | null = null;
  for (const it of items) {
    if (it.kind === "user") {
      const t = turnAt(it.ts);
      if (t && t !== cur) {
        if (cur && lastKey) out.set(lastKey, cur);
        cur = out.size > 0 && [...out.values()].includes(t) ? null : t;
      }
    }
    lastKey = it.key;
  }
  if (cur && lastKey) out.set(lastKey, cur);
  return out;
}

/** A turn seen from one agent of it (a sub-agent's own thread): only that agent's rows. */
export function turnFor(t: TurnUsage, runId: string): TurnUsage | null {
  if (t.root_id === runId) return t;
  const mine = t.agents.filter((a) => a.run_id === runId);
  if (mine.length === 0) return null;
  const sum = (k: "input_tokens" | "output_tokens" | "cache_read_tokens" | "cache_write_tokens" | "tokens" | "usd") =>
    mine.reduce((s, a) => s + a[k], 0);
  const srcs = new Set(mine.map((a) => a.usd_source));
  const usd_source: UsdSource = srcs.size === 1 ? mine[0].usd_source : srcs.has("unknown") ? "partial" : "priced";
  return { ...t, input_tokens: sum("input_tokens"), output_tokens: sum("output_tokens"), cache_read_tokens: sum("cache_read_tokens"),
    cache_write_tokens: sum("cache_write_tokens"), tokens: sum("tokens"), usd: sum("usd"), usd_source, agents: mine };
}
