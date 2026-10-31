// Unread counts for collapsed dock streams (Plan 9 D4): what arrived in a stream since you last
// had it open. Rows are counted by key, per run and stream, so a remount of the dock (a page
// switch) doesn't call old rows new. Kept for the session only.
import type { StreamKind } from "../../api/types";

const seen = new Map<string, ReadonlySet<string>>();
const slot = (runId: string | null, s: StreamKind) => `${runId ?? ""}|${s}`;

/** How many of `keys` were not seen. Pure. */
export function unreadCount(keys: readonly string[], seenKeys: ReadonlySet<string>): number {
  let n = 0;
  for (const k of keys) if (!seenKeys.has(k)) n++;
  return n;
}

/** The stream is open: everything in it now counts as read. */
export function markSeen(runId: string | null, s: StreamKind, keys: readonly string[]): void {
  seen.set(slot(runId, s), new Set(keys));
}

/** Unread rows of a collapsed stream. A stream never seen open (collapsed in a stored layout, or a
 *  run shown for the first time) starts with what it holds now as read. */
export function unreadOf(runId: string | null, s: StreamKind, keys: readonly string[]): number {
  const k = slot(runId, s);
  const had = seen.get(k);
  if (!had) { seen.set(k, new Set(keys)); return 0; }
  return unreadCount(keys, had);
}

/** Tests. */
export function resetUnread(): void { seen.clear(); }
