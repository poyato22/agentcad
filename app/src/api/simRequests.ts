// Agent-built simulators (plan 2026-11-06 S1-S3, S6; core sim/requests.py, server/
// sim_requests_api.py): an agent asks for a simulator no study covers; the request carries a
// cost estimate (low / likely / high, with its basis) and waits for the user's one-time
// decision. Every string here was written by an agent: rendered as text, never as markup.
import { z } from "zod";

const band = z.object({ low: z.number(), likely: z.number(), high: z.number() });

export const simEstimateSchema = z.object({
  method: z.string(), basis: z.string(), confidence: z.string(),
  usd: band, tokens: band, wall_minutes: band,
  machine_load: z.string(),
  dependencies: z.array(z.object({ name: z.string(), manager: z.string(), licence: z.string(), size_mb: z.number().nullable() })),
  download_mb: z.number().nullable(), download_note: z.string().nullable(),
  budget_pause_usd: z.number(), weekly_share_pct: band.nullable(), note: z.string(),
});

const quantity = z.object({ name: z.string(), unit: z.string() });

export const simRequestSchema = z.object({
  id: z.string(), name: z.string(), status: z.string(),
  project_id: z.string().nullable(), run_id: z.string().nullable(),
  request: z.looseObject({
    computes: z.string(), why: z.string(), method: z.string(), fidelity_class: z.string(), complexity: z.string(),
    inputs: z.array(quantity), outputs: z.array(quantity),
    validation_cases: z.array(z.looseObject({ name: z.string(), source: z.string() })),
  }),
  estimate: simEstimateSchema,
  decision: z.looseObject({ action: z.string(), reason: z.string().nullable() }).nullable(),
  budget: z.looseObject({ limit_usd: z.number(), paused_spend: z.object({ usd: z.number(), tokens: z.number() }).optional() }).nullable(),
  created_at: z.number(),
});
export type SimRequest = z.infer<typeof simRequestSchema>;

export const simRequestListSchema = z.object({ requests: z.array(simRequestSchema) });
export const simRequestDecidedSchema = z.object({ request: simRequestSchema }).loose();

export type SimDecision = "accept" | "reject" | "changes";
export type SimBudgetDecision = "continue" | "stop";

/** "$1.20" — two decimals under $100, whole dollars above. */
export function usd(v: number): string { return v >= 100 ? `$${Math.round(v)}` : `$${v.toFixed(2)}`; }

/** "1.2M" / "340k" / "900". */
export function tokens(v: number): string {
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}k`;
  return String(Math.round(v));
}
