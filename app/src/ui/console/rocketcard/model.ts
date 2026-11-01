// Plan 11 F6: the rocket tools' results as Simulations-stream cards (pure, no DOM). The core's
// rocket_analyze / rocket_flight (core mises/rocketry/tools.py) return data.card {kind, title,
// status, metrics, charts, notes, accuracy} and the tables beside it (data.terms / flutter /
// mass_table, data.flights); the result's warnings are the card's warnings. A running call has
// only its input.
import { z } from "zod";
import { studyChartSchema, type StudyChart } from "../../../api/studies";
import type { LogItem } from "../logModel";

type Call = Extract<LogItem, { kind: "call" }>;

export const ROCKET_TOOLS = new Set(["rocket_analyze", "rocket_flight"]);
const bare = (n: string): string => n.replace(/^mcp__mises__/, "");

/** Calls that render as a rocket card. */
export function isRocketCallName(name: string): boolean {
  return ROCKET_TOOLS.has(bare(name));
}

export type RocketStatus = "pass" | "warn" | "fail" | "not_checked";
const status = z.enum(["pass", "warn", "fail", "not_checked"]).catch("not_checked");

const metricSchema = z.looseObject({
  label: z.string(), value: z.union([z.number(), z.string()]).nullable(), unit: z.string().default(""),
  status: status.optional(),
});
export type RocketMetric = z.infer<typeof metricSchema>;

const cardSchema = z.looseObject({
  kind: z.string(), title: z.string(), status,
  metrics: z.array(metricSchema).default([]),
  charts: z.array(z.unknown()).default([]),
  notes: z.array(z.string()).default([]),
  accuracy: z.array(z.string()).default([]),
});

const termSchema = z.looseObject({ term: z.string(), cn_alpha: z.number(), x_mm: z.number() });
const flutterSchema = z.looseObject({
  fins: z.string(), flutter_mps: z.number(), max_v_mps: z.number().optional(), ratio: z.number().optional(), status,
});
const flightSchema = z.looseObject({
  motor: z.string(), apogee_ft: z.number(), apogee_m: z.number(), t_apogee_s: z.number(), flight_time_s: z.number(),
  max_v_mps: z.number(), payload_peak_g: z.number(), margin_cal: z.number(), descent_mps: z.number(),
  rail_exit_mps: z.number(), status,
  apogee_error_ft: z.number().optional(),
  tune: z.looseObject({ reachable: z.boolean(), ballast_g: z.number().optional(), note: z.string().optional(),
    margin_launch_cal: z.number().optional() }).optional(),
  chute_mm: z.number().optional(), chute_for_time_mm: z.number().optional(),
});
export type FlightRow = z.infer<typeof flightSchema>;
export type TermRow = z.infer<typeof termSchema>;
export type FlutterRow = z.infer<typeof flutterSchema>;

export interface RocketCallView {
  callId: string;
  tool: "rocket_analyze" | "rocket_flight";
  running: boolean;
  failed: boolean;
  /** One line: the result summary, or the failure. */
  summary: string | null;
  hint: string | null;
  title: string;
  status: RocketStatus | null;
  metrics: RocketMetric[];
  charts: StudyChart[];
  terms: TermRow[];
  flutter: FlutterRow[];
  flights: FlightRow[];
  warnings: string[];
  notes: string[];
  accuracy: string[];
  /** What the running call asked for ("F52C, G40W"). */
  motors: string[];
}

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const rows = <T>(schema: z.ZodType<T>, v: unknown): T[] =>
  (Array.isArray(v) ? v : []).map((x) => schema.safeParse(x)).filter((r) => r.success).map((r) => r.data as T);

export function rocketCallView(call: Call): RocketCallView | null {
  const tool = bare(call.name);
  if (tool !== "rocket_analyze" && tool !== "rocket_flight") return null;
  const running = call.result === null;
  const p = call.result?.payload ?? {};
  const out = rec(p.output);
  const data = rec(out.data);
  const c = cardSchema.safeParse(data.card);
  const card = c.success ? c.data : null;
  const input = call.input ?? {};
  const motors = [
    ...(Array.isArray(input.motors) ? input.motors.filter((m): m is string => typeof m === "string") : []),
    ...(typeof input.motor === "string" ? [input.motor] : []),
  ];
  return {
    callId: call.callId, tool, running, failed: call.ok === false,
    summary: call.summary, hint: call.hint,
    title: card?.title ?? (tool === "rocket_analyze" ? "Rocket stability" : "Rocket flight"),
    status: card?.status ?? null,
    metrics: card?.metrics ?? [],
    charts: rows(studyChartSchema, card?.charts),
    terms: rows(termSchema, data.terms),
    flutter: rows(flutterSchema, data.flutter),
    flights: rows(flightSchema, data.flights),
    warnings: (Array.isArray(out.warnings) ? out.warnings : []).filter((w): w is string => typeof w === "string"),
    notes: card?.notes ?? [],
    accuracy: card?.accuracy ?? [],
    motors,
  };
}

/** "1.84 cal", "750 ft", "n/a". */
export function fmtMetric(m: RocketMetric): string {
  if (m.value === null || m.value === undefined) return "n/a";
  const v = typeof m.value === "number" ? (Number.isInteger(m.value) ? String(m.value) : String(Math.round(m.value * 100) / 100)) : m.value;
  return m.unit ? `${v} ${m.unit}` : v;
}
