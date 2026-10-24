// Derived left-pane state (computed from the store; no new store fields). The current run and
// part are the ones every pane shares (state/selection.ts).
import { computed } from "@preact/signals";
import type { AgentRun, Step } from "../../api/types";
import { activePart, effectiveRun } from "../../state/selection";
import { inflight, runs, steps } from "../../state/store";
import { changedVars, type VarChange } from "./changedVars";

export { activePart };

export function newestFirst(m: ReadonlyMap<string, AgentRun>): AgentRun[] {
  return [...m.values()].sort((a, b) => b.created_at - a.created_at);
}

/** The run the console shows (selected, else the newest running run, else the newest). */
export const activeRun = effectiveRun;

export const activeSteps = computed<readonly Step[]>(() => {
  const r = activeRun.value;
  return r ? steps.value.get(r.id) ?? [] : [];
});

export const activeChanges = computed<Map<string, VarChange>>(() => {
  const p = activePart.value;
  return p ? changedVars(p.part_id, activeSteps.value) : new Map();
});

/** Part ids an in-flight mirror_* call is touching. */
export const changingParts = computed<ReadonlySet<string>>(() => {
  const s = new Set<string>();
  for (const c of inflight.value.values()) {
    const id = c.input["part_id"];
    if (c.name.startsWith("mirror_") && typeof id === "string") s.add(id);
  }
  return s;
});

/** 5 → "5", 5.5 → "5.5", 5.123 → "5.12". */
export function num(v: number): string { return String(Number(v.toFixed(2))); }

export const allRuns = computed<AgentRun[]>(() => [...runs.value.values()]);
