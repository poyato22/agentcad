// Agent announcement cards (R16) woven into the thread by time, after the user-edit weave, so each
// card lands where the agent did the thing. Pure, so the view stays thin.
import type { Announcement } from "../../api/types";
import type { ThreadItem } from "./threadModel";
import type { ThreadRow } from "./userEditLines";

export type WovenRow = ThreadRow | { kind: "announce"; announcement: Announcement };

function rowTs(r: ThreadRow): number | null {
  if (r.kind === "you") return r.line.ts;
  const it: ThreadItem = r.item;
  return "ts" in it && typeof it.ts === "number" ? it.ts : null;
}

/** Each announcement placed before the first row that came after it; later ones go at the end. */
export function weaveAnnouncements(rows: readonly ThreadRow[], announcements: readonly Announcement[]): WovenRow[] {
  const sorted = [...announcements].sort((a, b) => a.ts - b.ts);
  const out: WovenRow[] = [];
  let i = 0;
  for (const r of rows) {
    const ts = rowTs(r);
    if (ts !== null) while (i < sorted.length && sorted[i].ts <= ts) out.push({ kind: "announce", announcement: sorted[i++] });
    out.push(r);
  }
  while (i < sorted.length) out.push({ kind: "announce", announcement: sorted[i++] });
  return out;
}
