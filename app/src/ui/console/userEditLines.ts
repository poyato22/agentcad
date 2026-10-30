// "You · changed thickness 4 → 5 mm on quad arm": the user's own part edits (vars, code, feature
// ops) woven into the shown run's thread by time, so the conversation reads as one session with
// the agent. They are local lines (the edits run on the app's ui run, which the thread never shows);
// the agent sees the same edits as data in its context (C6).
import type { AgentRun } from "../../api/types";
import type { UserEditLine } from "../../state/partEdits";
import type { ThreadItem } from "./threadModel";

export type ThreadRow = { kind: "item"; item: ThreadItem } | { kind: "you"; line: UserEditLine };

function tsOf(it: ThreadItem): number | null {
  return "ts" in it && typeof it.ts === "number" ? it.ts : null;
}

/** Edits made since the run started, in the run's build (a run without one takes any). */
export function editsForRun(edits: readonly UserEditLine[], run: AgentRun | null): UserEditLine[] {
  if (!run) return [];
  return edits.filter((l) => l.ts >= run.created_at && (!run.project_id || !l.buildId || l.buildId === run.project_id));
}

/** The thread with each edit placed before the first item that came after it. */
export function weave(items: readonly ThreadItem[], edits: readonly UserEditLine[]): ThreadRow[] {
  const sorted = [...edits].sort((a, b) => a.ts - b.ts);
  const out: ThreadRow[] = [];
  let i = 0;
  for (const it of items) {
    const ts = tsOf(it);
    if (ts !== null) while (i < sorted.length && sorted[i].ts <= ts) out.push({ kind: "you", line: sorted[i++] });
    out.push({ kind: "item", item: it });
  }
  while (i < sorted.length) out.push({ kind: "you", line: sorted[i++] });
  return out;
}
