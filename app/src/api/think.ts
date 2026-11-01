// Plan 11 F8 (core effort.py, server/effort_api.py): the composer's Think control and the effort
// each turn ran at. A message's choice rides on POST /projects/{id}/chat as `effort`; the build's
// default lives at GET/PUT /projects/{id}/effort. Each turn's decision is a stored decision step
// {effort: {level, choice, source, reasons, label, control}} just before that turn's user message.
import { z } from "zod";

export const THINK_CHOICES = ["auto", "normal", "hard", "max"] as const;
export type ThinkChoice = (typeof THINK_CHOICES)[number];
export const EFFORT_LEVELS = ["low", "medium", "high", "max"] as const;
export type EffortLevel = (typeof EFFORT_LEVELS)[number];

export const THINK_LABEL: Record<ThinkChoice, string> = { auto: "Auto", normal: "Normal", hard: "Hard", max: "Max" };

export const effortDefaultSchema = z.object({
  default: z.enum(THINK_CHOICES),
  choices: z.array(z.string()),
  levels: z.record(z.string(), z.string().nullable()),
});
export type EffortDefault = z.infer<typeof effortDefaultSchema>;

export const effortDecisionSchema = z.object({
  level: z.enum(EFFORT_LEVELS),
  choice: z.string(),
  source: z.enum(["user", "build", "auto"]),
  reasons: z.array(z.string()).default([]),
  label: z.string(),
  control: z.object({
    runtime: z.string().optional(), param: z.string().optional(), value: z.string().nullable().optional(),
    supported: z.boolean().optional(), note: z.string().optional(),
  }).partial().default({}),
});
export type EffortDecision = z.infer<typeof effortDecisionSchema>;

/** A step's effort decision (a decision step's `effort` payload), or null. */
export function effortOf(step: { kind: string; payload: Record<string, unknown> }): EffortDecision | null {
  if (step.kind !== "decision" || !("effort" in step.payload)) return null;
  const r = effortDecisionSchema.safeParse(step.payload.effort);
  return r.success ? r.data : null;
}

/** The chip's words, e.g. "Thinking: Max · open-ended: an egg-drop challenge". */
export function effortChipText(e: EffortDecision): string {
  const why = e.source === "user" ? "your pick" : e.source === "build" ? "this build's default" : (e.reasons[0] ?? "auto");
  return `Thinking: ${e.label} · ${why}`;
}

/** The chip's tooltip: every reason and what the runtime was sent. */
export function effortChipTitle(e: EffortDecision): string {
  const c = e.control;
  const sent = c.supported === false ? (c.note ?? "this runtime has no effort control")
    : c.param ? `${c.runtime ?? "runtime"}: ${c.param} = ${c.value ?? e.level}` : "";
  return [`Effort ${e.level} (${e.source})`, ...e.reasons, sent].filter(Boolean).join("\n");
}
