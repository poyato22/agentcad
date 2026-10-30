// Feedback pins (Plan 7 U2a): a comment pinned on the model goes to the build's agent as a
// message whose location rides in a nonce-delimited data block (core server/feedback_api.py).
// The pins on the sheet are read back from those messages, so they survive a reload with the
// thread; a pin just sent shows at once, until its message step arrives.
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import type { Vec3 } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { runs, steps } from "../../state/store";

export interface PinLocation { partId: string; part: string; point: Vec3; faceId: string | null; nearby: string | null }
export interface Feedback { text: string; location: PinLocation }

const BLOCK_RE = /\n*\[Pinned on the model[^\]\n]*\]\n<untrusted_user_data kind="feedback_pin" nonce="([0-9a-f]{4,64})">\n([\s\S]*?)\n<\/untrusted_user_data nonce="\1">\s*$/;

const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every((c) => typeof c === "number" && Number.isFinite(c));

/** A feedback message → the user's words and the pinned location; null for any other message. */
export function parseFeedback(text: string): Feedback | null {
  const m = BLOCK_RE.exec(text);
  if (!m) return null;
  let loc: unknown;
  try { loc = JSON.parse(m[2]); } catch { return null; }
  if (typeof loc !== "object" || loc === null) return null;
  const o = loc as Record<string, unknown>;
  if (typeof o["part_id"] !== "string" || !isVec3(o["point"])) return null;
  const face = o["face"] as Record<string, unknown> | null | undefined;
  return {
    text: text.slice(0, m.index).trim(),
    location: {
      partId: o["part_id"], part: typeof o["part"] === "string" ? o["part"] : o["part_id"], point: [o["point"][0], o["point"][1], o["point"][2]],
      faceId: face && typeof face["id"] === "string" ? face["id"] : null, nearby: typeof o["nearby"] === "string" ? o["nearby"] : null,
    },
  };
}

export interface LocalPin { buildId: string | null; runId: string; partId: string; point: Vec3; faceId: string | null; text: string; ts: number }
export interface Pin extends LocalPin { n: number; key: string; stepId: number | null }

/** Pins sent from this window whose message step may not be in yet. */
const localPins: Signal<readonly LocalPin[]> = signal<readonly LocalPin[]>([]);

export function addLocalPin(p: LocalPin): void { localPins.value = [...localPins.peek(), p]; }
export function resetPins(): void { localPins.value = []; }

const same = (a: Vec3, b: Vec3) => a.every((c, i) => Math.abs(c - b[i]) < 1e-3);

/** The active build's pins, oldest first, numbered 1.. (a run without a build belongs to all). */
export const buildPins: ReadonlySignal<Pin[]> = computed(() => {
  const build = activeBuildId.value;
  const out: Pin[] = [];
  for (const [runId, list] of steps.value) {
    const run = runs.value.get(runId);
    if (build && run?.project_id && run.project_id !== build) continue;
    for (const s of list) {
      if (s.kind !== "message" || s.payload["role"] !== "user" || typeof s.payload["text"] !== "string") continue;
      const f = parseFeedback(s.payload["text"]);
      if (!f) continue;
      out.push({ buildId: run?.project_id ?? null, runId, partId: f.location.partId, point: f.location.point, faceId: f.location.faceId,
        text: f.text, ts: s.ts, n: 0, key: `s${s.id}`, stepId: s.id });
    }
  }
  localPins.value.forEach((p, i) => {
    if (build && p.buildId && p.buildId !== build) return;
    if (out.some((q) => q.runId === p.runId && q.text === p.text && same(q.point, p.point))) return;
    out.push({ ...p, n: 0, key: `l${i}`, stepId: null });
  });
  out.sort((a, b) => a.ts - b.ts);
  return out.map((p, i) => ({ ...p, n: i + 1 }));
});
