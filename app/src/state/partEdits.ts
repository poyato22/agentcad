// Editing alongside the agent (Plan 5 C6 contract). Every user edit (vars, code, feature ops) and
// every agent edit bumps the part's version and arrives as `part_changed {part_id, version, by,
// run_id, summary}`. This module keeps:
//   · the newest version seen per part (the part list can lag behind an edit's answer);
//   · the user's own edit lines ("You · changed thickness 4 → 5 mm") that the conversation shows;
//   · a short flash per part an agent just changed (the part tree highlights it).
// Edits made by this window are recorded with their exact wording and the matching
// `part_changed` echo is not shown twice. The core publishes that echo *inside* the edit request,
// so it usually arrives before the HTTP answer: while an edit of a part is in flight
// (`trackEdit`), the part's `by:"user"` events are held, and once the answer names the new
// version the echo of that version is dropped and anything else held is shown.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { partChangedSchema, type PartChanged } from "../api/builds";
import { onCoreEvent } from "../api/events";
import type { MisesEvent, PartSummary } from "../api/types";
import { partName } from "../lib/names";
import { activeBuildId } from "./builds";
import { parts } from "./store";

export interface UserEditLine { key: string; ts: number; partId: string; buildId: string | null; text: string }

/** The user's edits this session, oldest first (capped). */
export const userEdits: Signal<readonly UserEditLine[]> = signal<readonly UserEditLine[]>([]);
/** Parts an agent changed in the last FLASH_MS, part id → when (ms). */
export const agentFlash: Signal<ReadonlyMap<string, number>> = signal<ReadonlyMap<string, number>>(new Map());
/** The newest version seen per part (edit answers and part_changed events). */
export const knownVersions: Signal<ReadonlyMap<string, number>> = signal<ReadonlyMap<string, number>>(new Map());
/** The last agent change per part (who and what), for the vars panel's conflict line. */
export const lastAgentChange: Signal<ReadonlyMap<string, PartChanged>> = signal<ReadonlyMap<string, PartChanged>>(new Map());

export const FLASH_MS = 1600;
const MAX_LINES = 200;
const mine = new Set<string>();
/** Parts with an edit request in flight from this window, part id → how many. */
const inFlight = new Map<string, number>();
/** `by:"user"` part_changed events held while their part has an edit in flight. */
const held = new Map<string, { d: PartChanged; ts: number }[]>();
/** Echoes dropped while held (so `noteMyEdit` doesn't wait for them again). */
const echoed = new Set<string>();
/** Every version this window's own edits made (kept; `mine` only skips the echo once). */
const myVersions = new Set<string>();

/** Did this window's own edit make that version? */
export function isMyVersion(partId: string, version: number): boolean { return myVersions.has(`${partId}@${version}`); }
let seq = 0;

/** The version an edit of `p` is based on: the newest one seen. */
export function versionOf(p: Pick<PartSummary, "part_id" | "version">): number {
  return Math.max(p.version ?? 0, knownVersions.peek().get(p.part_id) ?? 0);
}

export function noteVersion(partId: string, version: number): void {
  const cur = knownVersions.peek().get(partId) ?? 0;
  if (version <= cur) return;
  const m = new Map(knownVersions.peek());
  m.set(partId, version);
  knownVersions.value = m;
}

function pushLine(partId: string, text: string, ts: number): void {
  const line: UserEditLine = { key: `you-${++seq}`, ts, partId, buildId: activeBuildId.peek(), text };
  const next = [...userEdits.peek(), line];
  userEdits.value = next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
}

/** An edit this window made and the core accepted (its echo event is then skipped). */
export function noteMyEdit(partId: string, version: number, text: string, ts = Date.now() / 1000): void {
  if (!echoed.delete(`${partId}@${version}`)) mine.add(`${partId}@${version}`);
  myVersions.add(`${partId}@${version}`);
  noteVersion(partId, version);
  pushLine(partId, text, ts);
}

/** Runs one edit request of `partId` from this window. Until it answers, the part's `by:"user"`
 *  events are held; then the echo of the answered version is dropped and the rest are shown
 *  (all of them if the request failed). Call `noteMyEdit` with the answer as before. */
export async function trackEdit<T extends { version: number }>(partId: string, request: Promise<T>): Promise<T> {
  inFlight.set(partId, (inFlight.get(partId) ?? 0) + 1);
  let version: number | null = null;
  try {
    const r = await request;
    version = r.version;
    return r;
  } finally {
    settle(partId, version);
  }
}

function settle(partId: string, version: number | null): void {
  const n = (inFlight.get(partId) ?? 1) - 1;
  if (n > 0) inFlight.set(partId, n); else inFlight.delete(partId);
  const list = held.get(partId) ?? [];
  const keep: typeof list = [];
  for (const h of list) {
    if (version !== null && h.d.version === version) { echoed.add(`${partId}@${version}`); continue; }
    keep.push(h);
  }
  if (n > 0) { held.set(partId, keep); return; }   // another edit still in flight: keep holding
  held.delete(partId);
  for (const h of keep) showUserEvent(h.d, h.ts);
}

function showUserEvent(d: PartChanged, ts: number): void {
  const key = `${d.part_id}@${d.version}`;
  if (mine.has(key)) { mine.delete(key); return; }
  // the user's edit from somewhere else (another window): the core's summary, as data
  pushLine(d.part_id, d.summary || `changed ${nameOf(d.part_id)}`, ts);
}

/** The part's name as the tree shows it (its label, else its id with spaces). */
export function nameOf(partId: string): string {
  const row = parts.peek().find((p) => p.part_id === partId);
  return partName(row ?? { part_id: partId });
}

/** "4", "4.5", "4.25" — never "4.000". */
export function fmtNum(v: number): string { return String(Number(v.toFixed(3))); }

/** "changed thickness 4 → 5 mm, length 60 → 80 mm on quad arm". */
export function describeVarsEdit(partId: string, changes: { name: string; prev: number | null; now: number; unit: string | null }[]): string {
  const bits = changes.map((c) => {
    const u = c.unit ? ` ${c.unit}` : "";
    return c.prev === null || c.prev === c.now ? `${c.name} → ${fmtNum(c.now)}${u}` : `${c.name} ${fmtNum(c.prev)} → ${fmtNum(c.now)}${u}`;
  });
  return `changed ${bits.join(", ")} on ${nameOf(partId)}`;
}

function flash(partId: string): void {
  const m = new Map(agentFlash.peek());
  const at = Date.now();
  m.set(partId, at);
  agentFlash.value = m;
  setTimeout(() => {
    if (agentFlash.peek().get(partId) !== at) return;
    const n = new Map(agentFlash.peek());
    n.delete(partId);
    agentFlash.value = n;
  }, FLASH_MS);
}

export function onPartChanged(ev: MisesEvent): void {
  if (ev.type !== "part_changed") return;
  const p = partChangedSchema.safeParse(ev.data);
  if (!p.success) return;
  const d = p.data;
  noteVersion(d.part_id, d.version);
  if (d.by === "agent") {
    const m = new Map(lastAgentChange.peek());
    m.set(d.part_id, d);
    lastAgentChange.value = m;
    flash(d.part_id);
    return;
  }
  if (inFlight.has(d.part_id)) {
    held.set(d.part_id, [...(held.get(d.part_id) ?? []), { d, ts: ev.ts }]);
    return;
  }
  showUserEvent(d, ev.ts);
}

/** The user's edit lines that belong to the active build. */
export const buildUserEdits: ReadonlySignal<UserEditLine[]> = computed(() => {
  const b = activeBuildId.value;
  const ids = new Set(parts.value.map((p) => p.part_id));
  return userEdits.value.filter((l) => (b === null || l.buildId === null || l.buildId === b) && (l.buildId !== null || ids.has(l.partId)));
});

/** Subscribes to part_changed (once, at boot). */
export function startPartEdits(): () => void {
  return onCoreEvent(onPartChanged);
}

/** Back to boot state (tests). */
export function resetPartEdits(): void {
  userEdits.value = [];
  agentFlash.value = new Map();
  knownVersions.value = new Map();
  lastAgentChange.value = new Map();
  mine.clear();
  myVersions.clear();
  inFlight.clear();
  held.clear();
  echoed.clear();
}
