// view-tab: what waits on the user, for the View tab's "Needs your decision" section and its
// "N to decide" badge: the Gate's approval cards, the agents' asks (measurement asks included),
// and the concept stage's decisions (a pitch to choose from, a sketch to approve or skip). An item
// leaves the list the moment the user decides it (decisions/once), before any refetch.
import { computed, type ReadonlySignal } from "@preact/signals";
import type { Approval, Question } from "../../../api/types";
import { activeBuildId } from "../../../state/builds";
import { approvals, questions, retiredApprovals } from "../../../state/store";
import { concepts } from "../../sketch/state";
import { isExpired } from "../approvals/decide";
import { resolved } from "./once";
import type { SimRequest } from "../../../api/simRequests";
import { simKey, simRequests } from "./simRequests";

export type PendingDecision =
  | { key: string; kind: "approval"; what: string; runId: string; approval: Approval }
  | { key: string; kind: "question"; what: string; runId: string; question: Question }
  | { key: string; kind: "pitch"; what: string; runId: string | null; projectId: string; pitchId: string }
  | { key: string; kind: "sketch"; what: string; runId: string | null; projectId: string; sketchId: string }
  | { key: string; kind: "simulator"; what: string; runId: string | null; request: SimRequest };

/** Pure: the pending decisions, oldest first within each kind (approvals, asks, concept). */
export function pendingOf(input: {
  approvals: ReadonlyMap<string, Approval>; questions: ReadonlyMap<string, Question>;
  retired: ReadonlySet<string>; resolved: ReadonlyMap<string, unknown>;
  concept: { projectId: string; view: import("../../../api/concept").ConceptView } | null; nowS?: number;
  sims?: ReadonlyMap<string, SimRequest>;
}): PendingDecision[] {
  const out: PendingDecision[] = [];
  for (const a of input.approvals.values()) {
    const key = `approval:${a.id}`;
    if (a.status !== "pending" || isExpired(a, input.nowS) || input.retired.has(a.id) || input.resolved.has(key)) continue;
    out.push({ key, kind: "approval", what: `Approve: ${a.action.replace(/_/g, " ")}`, runId: a.run_id, approval: a });
  }
  for (const q of input.questions.values()) {
    const key = `question:${q.question_id}`;
    if (input.resolved.has(key)) continue;
    out.push({ key, kind: "question", what: q.image_id ? `Measure: ${q.question}` : q.question, runId: q.run_id, question: q });
  }
  const c = input.concept;
  if (c) {
    const st = c.view.concept.status;
    const pitch = c.view.pitches[c.view.pitches.length - 1];
    if (pitch && !pitch.chosen && st !== "approved" && st !== "skipped" && !input.resolved.has(`pitch:${pitch.pitch_id}`)) {
      out.push({ key: `pitch:${pitch.pitch_id}`, kind: "pitch", what: `Choose a design direction (${pitch.options.length} options)`,
        runId: pitch.run_id, projectId: c.projectId, pitchId: pitch.pitch_id });
    }
    const sk = c.view.sketches[c.view.sketches.length - 1];
    if (sk && st === "sketching" && !input.resolved.has(`sketch:${c.projectId}`)) {
      out.push({ key: `sketch:${c.projectId}`, kind: "sketch", what: `Approve concept sketch v${sk.version}`,
        runId: sk.run_id ?? null, projectId: c.projectId, sketchId: sk.sketch_id });
    }
  }
  // agent-built simulators: a request to accept / reject / change, or a build paused at its budget
  for (const r of input.sims?.values() ?? []) {
    if (r.status !== "pending" && r.status !== "paused_budget") continue;
    const key = simKey(r);
    if (input.resolved.has(key)) continue;
    out.push({ key, kind: "simulator", runId: r.run_id,
      what: r.status === "pending" ? `New simulator: ${r.name.replace(/_/g, " ")}` : `Simulator build over budget: ${r.name.replace(/_/g, " ")}`, request: r });
  }
  return out;
}

export const pendingDecisions: ReadonlySignal<PendingDecision[]> = computed(() => {
  const pid = activeBuildId.value;
  const view = pid ? concepts.value[pid] : undefined;
  return pendingOf({
    approvals: approvals.value, questions: questions.value, retired: retiredApprovals.value, resolved: resolved.value, sims: simRequests.value,
    concept: pid && view ? { projectId: pid, view } : null,
  });
});
