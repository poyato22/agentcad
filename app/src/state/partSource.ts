// GET /parts/{id}/source, cached per part at the version it was read. The Variables panel needs
// its var schema (min / max / step / unit / doc / inferred); the code editor always re-reads it
// fresh before an edit (readSource), as the C6 contract asks.
import { signal, type Signal } from "@preact/signals";
import type { PartSource, VarSpec } from "../api/builds";
import { CoreError } from "../api/client";
import { actions } from "./store";

export type SourceEntry =
  | { state: "loading"; version: number }
  | { state: "ready"; version: number; source: PartSource }
  | { state: "error"; version: number; message: string };

export const sources: Signal<ReadonlyMap<string, SourceEntry>> = signal<ReadonlyMap<string, SourceEntry>>(new Map());

function put(id: string, e: SourceEntry): void {
  const m = new Map(sources.peek());
  m.set(id, e);
  sources.value = m;
}

/** Fresh GET /parts/{id}/source (the editor's read before it saves). */
export async function readSource(id: string): Promise<PartSource> {
  const s = await actions.client().partSource(id);
  put(id, { state: "ready", version: s.version, source: s });
  return s;
}

/** Make sure the cached source is at least `version` (re-reads when the part changed). */
export function ensureSource(id: string, version: number): void {
  const cur = sources.peek().get(id);
  if (cur && cur.version >= version && cur.state !== "error") return;
  if (cur?.state === "loading") return;
  if (cur?.state === "error" && cur.version >= version) return;
  put(id, { state: "loading", version });
  readSource(id).catch((e: unknown) => {
    put(id, { state: "error", version, message: e instanceof CoreError || e instanceof Error ? e.message : String(e) });
  });
}

/** The schema to show: the core's, else (no source yet / an older core) a guessed range flagged
 *  inferred, like the core's own guess (value/4 .. value*4; 0..10 for a zero). */
export function varSpecs(vars: Record<string, number>, entry: SourceEntry | undefined): VarSpec[] {
  const known = entry?.state === "ready" ? new Map(entry.source.vars.map((v) => [v.name, v])) : new Map<string, VarSpec>();
  return Object.entries(vars).map(([name, value]) => {
    const k = known.get(name);
    if (k) return { ...k, value };
    const [min, max] = value === 0 ? [0, 10] : [Math.min(value / 4, value * 4), Math.max(value / 4, value * 4)];
    const unit = MM_WORDS.some((w) => name.toLowerCase().includes(w)) ? "mm" : null;
    return { name, value, min, max, step: null, unit, doc: null, inferred: true };
  });
}

/** Same words as core edits._MM_WORDS (length-like names get "mm" in a guessed schema). */
const MM_WORDS = ["thickness", "wall", "width", "height", "length", "diameter", "radius", "depth"];

export function resetSources(): void { sources.value = new Map(); }
