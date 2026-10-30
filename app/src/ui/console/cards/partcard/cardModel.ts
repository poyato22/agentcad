// Which tool calls in the thread are part cards, and what each card shows (pure, no DOM). The
// `part_show` tool (R4) puts a part in front of the user: image, name, price, vendor, key specs,
// and — for a catalog-backed part — an "Insert into build" action. The card is built from the
// call's input merged under the result's data (the core resolves snapshot_id → a proxied image
// path; partShownSchema then drops any non-proxy image so the card never pulls a page's host).
//
// Everything here is untrusted, page- or model-authored text, rendered as plain text by the card.
import { partShownSchema, type PartShown } from "../../../../api/browser";
import type { LogItem } from "../../logModel";

type Call = Extract<LogItem, { kind: "call" }>;

const PREFIX = /^mcp__mises__/;
export const bareToolName = (name: string): string => name.replace(PREFIX, "");

/** Calls that render as a part card (and never fold away in the thread). */
export function isPartShowName(name: string): boolean {
  return bareToolName(name) === "part_show";
}

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** The data a result step carries: output.data (a ToolOutput) or the payload itself. */
function resultData(call: Call): Record<string, unknown> {
  const p = call.result?.payload ?? {};
  const out = rec(p.output);
  if (Object.keys(rec(out.data)).length) return rec(out.data);
  return rec(p.data);
}

export interface PartCardView {
  callId: string;
  runId: string | null;
  card: PartShown;
  running: boolean;
  failed: boolean;
  summary: string | null;
  hint: string | null;
}

export function partCardView(call: Call, runId: string | null = call.runId ?? null): PartCardView | null {
  if (!isPartShowName(call.name)) return null;
  const running = call.result === null;
  const failed = call.ok === false;
  // input first, then the core's result data on top (proxied image, resolved vendor/url, …)
  const merged = { ...call.input, ...(running ? {} : resultData(call)) };
  const card = partShownSchema.parse(merged);
  return { callId: call.callId, runId, card, running, failed, summary: call.summary, hint: call.hint };
}

/** "$24.50", "€24.50", or the plain number with its currency when the symbol is unknown. */
export function priceText(price: number | null, currency: string | null): string | null {
  if (price === null) return null;
  const sym: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CAD: "$", AUD: "$" };
  const code = (currency ?? "USD").toUpperCase();
  const n = price.toLocaleString(undefined, { minimumFractionDigits: code === "JPY" ? 0 : 2, maximumFractionDigits: code === "JPY" ? 0 : 2 });
  return sym[code] ? `${sym[code]}${n}` : `${n} ${code}`;
}

/** "KV 1750", "mass 27.5 g" — the first few specs, as one line each. */
export function specText(spec: { key: string; value: string | number; unit: string | null }): string {
  return `${spec.key} ${spec.value}${spec.unit ? ` ${spec.unit}` : ""}`;
}
