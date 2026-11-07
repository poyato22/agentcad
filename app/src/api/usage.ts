// Usage per turn (one user message's agent run, sub-agents included) and per build: GET
// /runs/{id}/usage and /projects/{id}/usage (core mises/usage.py). Dollars are the runtime's
// reported cost (Claude Code: an API-price equivalent, not a charge on a subscription), a
// price-table estimate ("priced"), or unknown.
import { z } from "zod";

export type UsdSource = "reported" | "priced" | "unknown" | "partial";
const src = z.enum(["reported", "priced", "unknown", "partial"]).catch("unknown");
const n = z.number().catch(0);

const counts = {
  input_tokens: n, output_tokens: n, cache_read_tokens: n, cache_write_tokens: n,
  tokens: n, usd: n, usd_source: src,
};
export const agentUsageSchema = z.object({
  ...counts, run_id: z.string(), model: z.string().nullable().catch(null),
  provider: z.string().nullable().catch(null), agent: z.string().nullable().catch(null),
  title: z.string().nullable().catch(null), role: z.string().nullable().catch(null),
});
export const turnUsageSchema = z.object({
  ...counts, root_id: z.string(), turn_ts: z.number(), agents: z.array(agentUsageSchema).catch([]),
});
export const runUsageSchema = z.object({
  run_id: z.string(), root_id: z.string(), turns: z.array(turnUsageSchema), note: z.string().catch(""),
});
export const buildUsageSchema = z.object({
  ...counts, project_id: z.string(), turns: z.array(turnUsageSchema), recorded: z.boolean().catch(false),
  unrecorded: z.object({ runs: n, tokens: n, usd: n }).nullable().catch(null), note: z.string().catch(""),
});
export const buildUsageSummarySchema = z.object({ recorded: z.boolean(), tokens: n, usd: n });

export type AgentUsage = z.infer<typeof agentUsageSchema>;
export type TurnUsage = z.infer<typeof turnUsageSchema>;
export type RunUsage = z.infer<typeof runUsageSchema>;
export type BuildUsage = z.infer<typeof buildUsageSchema>;
export type BuildUsageSummary = z.infer<typeof buildUsageSummarySchema>;
