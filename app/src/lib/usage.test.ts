import { describe, expect, it } from "vitest";
import type { TurnUsage } from "../api/usage";
import type { ThreadItem } from "../ui/console/threadModel";
import { fmtTokens, placeTurns, turnFor, usageText } from "./usage";

const turn = (ts: number, usd = 0.42, extra: Partial<TurnUsage> = {}): TurnUsage => ({
  root_id: "lead", turn_ts: ts, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
  tokens: 182_000, usd, usd_source: "reported", agents: [], ...extra,
});
const user = (key: string, ts: number): ThreadItem => ({ kind: "user", key, text: "hi", ts, stepIds: [] });
const agent = (key: string): ThreadItem => ({ kind: "agent", key, text: "ok", ts: null, live: false, stepIds: [] });

describe("usage text", () => {
  it("reads as dollars and tokens, or tokens only", () => {
    expect(usageText(turn(1))).toBe("≈$0.42 · 182k tokens");
    expect(usageText({ usd: 0, tokens: 950, usd_source: "unknown" })).toBe("950 tokens");
    expect(usageText({ usd: 0.004, tokens: 1500, usd_source: "partial" })).toBe("≈<$0.01+ · 1.5k tokens");
    expect(fmtTokens(2_500_000)).toBe("2.5M");
  });
});

describe("placeTurns", () => {
  it("puts each turn under its last row, before the next user message", () => {
    const items = [user("u1", 100), agent("a1"), agent("a2"), user("u2", 200), agent("a3")];
    const m = placeTurns(items, [turn(200, 0.1), turn(99.5, 0.3)]);
    expect([...m.entries()].map(([k, t]) => [k, t.usd])).toEqual([["a2", 0.3], ["a3", 0.1]]);
  });
  it("a message that joins a running turn shares it; a thread with no turns gets none", () => {
    const items = [user("u1", 100), agent("a1"), user("u2", 150), agent("a2")];
    expect([...placeTurns(items, [turn(100)]).keys()]).toEqual(["a2"]);
    expect(placeTurns(items, []).size).toBe(0);
  });
});

describe("turnFor", () => {
  it("a sub-agent's thread shows only its own share", () => {
    const a = (run_id: string, usd: number) => ({ run_id, model: "m", provider: "claude-code", agent: run_id, title: null, role: null,
      input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0, tokens: 2, usd, usd_source: "reported" as const });
    const t = turn(1, 0.3, { agents: [a("lead", 0.2), a("sub", 0.1)] });
    expect(turnFor(t, "lead")).toBe(t);
    expect(turnFor(t, "sub")?.usd).toBeCloseTo(0.1);
    expect(turnFor(t, "other")).toBeNull();
  });
});
