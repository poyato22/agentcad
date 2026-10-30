// What every pane treats as "the current run" and "the current part", so the roster, the step
// log, the command line, the left pane and the viewer always agree. Only an explicit choice
// (selectedRunId / selectedPartId) renders as selected; a fallback is shown but never blue.
import { computed, type ReadonlySignal } from "@preact/signals";
import type { AgentRun, PartSummary } from "../api/types";
import { activeBuildId } from "./builds";
import { approvals, parts, questions, retiredApprovals, runs, selectedPartId, selectedRunId } from "./store";

/** Roster order: running first, then newest first. */
export function rosterOrder(list: Iterable<AgentRun>): AgentRun[] {
  return [...list].sort((a, b) => Number(b.status === "running") - Number(a.status === "running") || b.created_at - a.created_at);
}

/**
 * The selected run (even one not in `runs` yet, e.g. just created and not listed), else the
 * newest running run, else the newest run.
 */
export const effectiveRunId: ReadonlySignal<string | null> = computed(() => {
  const sel = selectedRunId.value;
  if (sel) return sel;
  // the fallback is a run of the active build (Plan 5: each build has its own runs); a run from a
  // core without builds (no project_id) belongs to every build
  const build = activeBuildId.value;
  const list = [...runs.value.values()].filter((r) => !build || !r.project_id || r.project_id === build);
  return rosterOrder(list)[0]?.id ?? null;
});

export const effectiveRun: ReadonlySignal<AgentRun | null> = computed(() => {
  const id = effectiveRunId.value;
  return id ? runs.value.get(id) ?? null : null;
});

/** What needs the human: pending (not retired) approvals plus open questions, and their runs. */
export const needsYou: ReadonlySignal<{ count: number; runIds: ReadonlySet<string> }> = computed(() => {
  const retired = retiredApprovals.value;
  const runIds = new Set<string>();
  let count = 0;
  for (const a of approvals.value.values()) {
    if (a.status !== "pending" || retired.has(a.id)) continue;
    count++;
    runIds.add(a.run_id);
  }
  for (const q of questions.value.values()) { count++; runIds.add(q.run_id); }
  return { count, runIds };
});

/** The selected part, else the first part (shown, but not rendered as selected). */
export const activePart: ReadonlySignal<PartSummary | null> = computed(() => {
  const list = parts.value;
  const sel = selectedPartId.value;
  return (sel !== null ? list.find((p) => p.part_id === sel) : undefined) ?? list[0] ?? null;
});
