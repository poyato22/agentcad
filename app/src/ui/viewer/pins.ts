// Feedback pins (Plan 7 U2a + U3c): a comment pinned on the model goes to the build's lead. The
// core stores the pin as structured `feedback` (api/measure.ts pinOf) on a decision step, the one
// POST /projects/{id}/feedback answers with, so it is the pin's identity: one decision step, one
// pin. (The user's message step and a queued message carry the same `feedback` for the thread;
// they are never counted.) The pins on the sheet are read back from those steps, so they survive
// a reload; a pin just sent shows at once until its step arrives.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { pinOf, type FeedbackPin } from "../../api/measure";
import type { Step, Vec3 } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { runs, steps } from "../../state/store";

export type { FeedbackPin };

export interface LocalPin {
  buildId: string | null; runId: string; stepId: number | null; partId: string; point: Vec3; faceId: string | null; text: string; ts: number;
  /** Plan 10 E35: pinned in the assembly view: on this instance (`point` is still in the part's
   *  own frame, so the pin follows the instance's pose and the explode). */
  instanceId?: string | null;
}
export interface Pin extends LocalPin { n: number; key: string }

/** Pins sent from this window whose step may not be in yet. */
const localPins: Signal<readonly LocalPin[]> = signal<readonly LocalPin[]>([]);

export function addLocalPin(p: LocalPin): void { localPins.value = [...localPins.peek(), p]; }
export function resetPins(): void { localPins.value = []; }

/** The stored pin of a decision step (the pin's identity), else null. */
export function pinStep(s: Step): FeedbackPin | null {
  return s.kind === "decision" ? pinOf(s.payload) : null;
}

const same = (a: Vec3, b: Vec3) => a.every((c, i) => Math.abs(c - b[i]) < 1e-3);

/** The active build's pins, oldest first, numbered 1.. (a run without a build belongs to all). */
export const buildPins: ReadonlySignal<Pin[]> = computed(() => {
  const build = activeBuildId.value;
  const out: Pin[] = [];
  for (const [runId, list] of steps.value) {
    const run = runs.value.get(runId);
    if (build && run?.project_id && run.project_id !== build) continue;
    for (const s of list) {
      const f = pinStep(s);
      if (!f) continue;
      out.push({ buildId: run?.project_id ?? null, runId, stepId: s.id, partId: f.part_id, point: f.point, faceId: f.face_id ?? null,
        text: f.text, ts: s.ts, n: 0, key: `s${s.id}`, instanceId: f.instance_id ?? null });
    }
  }
  localPins.value.forEach((p, i) => {
    if (build && p.buildId && p.buildId !== build) return;
    const seen = out.some((q) => (p.stepId !== null ? q.stepId === p.stepId : q.runId === p.runId && q.text === p.text && same(q.point, p.point)));
    if (!seen) out.push({ ...p, n: 0, key: `l${i}` });
  });
  out.sort((a, b) => a.ts - b.ts);
  return out.map((p, i) => ({ ...p, n: i + 1 }));
});

/** The number of the pin a thread message carries (matched on run, part, point and words). */
export function pinNumber(runId: string | null, f: FeedbackPin): number | null {
  const hit = buildPins.value.find((p) => (runId === null || p.runId === runId) && p.partId === f.part_id && p.text === f.text && same(p.point, f.point));
  return hit?.n ?? null;
}
