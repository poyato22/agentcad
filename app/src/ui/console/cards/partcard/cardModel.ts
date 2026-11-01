// Which tool calls in the thread are part cards, and which card they show (pure, no DOM). The
// `part_show` tool (R4) puts a part in front of the user. Its result carries ids only
// ({card_id, catalog_id, snapshot_id, card, untrusted_page_data}): the card's name, price,
// vendor, specs and image are page data the core resolved, fetched by id from
// GET /browser/cards/{card_id} (PartCard.tsx). A running call has no card yet.
import { partShowResultSchema } from "../../../../api/browser";
import type { LogItem } from "../../logModel";

type Call = Extract<LogItem, { kind: "call" }>;

const PREFIX = /^mcp__mises__/;
export const bareToolName = (name: string): string => name.replace(PREFIX, "");

/** Calls that render as a part card (and never fold away in the thread). */
export function isPartShowName(name: string): boolean {
  return bareToolName(name) === "part_show";
}

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** The data a result step carries: output.data (a ToolOutput) or the payload's data. */
function resultData(call: Call): Record<string, unknown> {
  const p = call.result?.payload ?? {};
  const out = rec(p.output);
  if (Object.keys(rec(out.data)).length) return rec(out.data);
  return rec(p.data);
}

export interface PartCardView {
  callId: string;
  runId: string | null;
  /** The card to fetch; null while the call runs (or when the result has no card). */
  cardId: string | null;
  running: boolean;
  failed: boolean;
  /** P10 E15 (core C1): the core inserts a shown part into the build and lists it in the parts
   *  list by itself; the result names the part / the row when it did (null: an older core). */
  partId: string | null;
  bomId: string | null;
}

const idOf = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

export function partCardView(call: Call, runId: string | null = call.runId ?? null): PartCardView | null {
  if (!isPartShowName(call.name)) return null;
  const running = call.result === null;
  const failed = call.ok === false;
  let cardId: string | null = null;
  let partId: string | null = null;
  let bomId: string | null = null;
  if (!running && !failed) {
    const d = resultData(call);
    const r = partShowResultSchema.safeParse(d);
    cardId = r.success ? r.data.card_id : partShowResultSchema.safeParse(rec(d.card)).data?.card_id ?? null;
    const card = rec(d.card);
    partId = idOf(d.part_id) ?? idOf(card.part_id);
    bomId = idOf(d.bom_id) ?? idOf(d.item_id) ?? idOf(d.bom_row_id) ?? idOf(card.bom_id);
  }
  return { callId: call.callId, runId, cardId, running, failed, partId, bomId };
}

/** "KV 1750", "mass 27.5 g": one spec as a line. */
export function specText(spec: { key: string; value: string | number; unit: string | null }): string {
  return `${spec.key} ${spec.value}${spec.unit ? ` ${spec.unit}` : ""}`;
}
