// Pure parts of the setup wizard: where to resume, which models are usable, sign-in polling.
import type { ModelsInfo, Profile, SetupState } from "../../api/types";
import { splitModelRef } from "../dialogs/modelRef";

export type StepId = "welcome" | "model" | "onshape" | "profile" | "done";
export const STEPS: readonly { id: StepId; label: string }[] = [
  { id: "welcome", label: "Welcome" },
  { id: "model", label: "Model" },
  { id: "onshape", label: "Onshape" },
  { id: "profile", label: "Profile" },
  { id: "done", label: "Done" },
];

export interface ResumeOpts {
  /** "Skip — local models only" was chosen on the model step. */
  modelSkipped: boolean;
  /** "Skip for now" was chosen on the Onshape step. */
  onshapeSkipped: boolean;
  /** Opened again on purpose (from Profile): start from the top. */
  reopened: boolean;
}

/** The first step still missing. What's saved stays saved, so a reload resumes here. */
export function resumeStep(s: SetupState, o: ResumeOpts): StepId {
  if (o.reopened) return "welcome";
  const missing = new Set(s.missing);
  const modelDone = s.has_model || o.modelSkipped;
  if (!modelDone && missing.has("onshape") && missing.has("profile")) return "welcome";
  if (!modelDone) return "model";
  if (missing.has("onshape") && !o.onshapeSkipped) return "onshape";
  if (missing.has("profile")) return "profile";
  return "done";
}

export const CLAUDE_CODE = "claude-code";
const WORKS = new Set(["env", "keychain", "signed_in"]);

export interface ModelOption { ref: string; label: string }

/** Models that can run now: a saved key, a signed-in Claude Code, or a configured local endpoint. */
export function availableModels(models: ModelsInfo, profile: Profile): ModelOption[] {
  const dflt = splitModelRef(models.default);
  const out: ModelOption[] = [];
  for (const v of models.vendors) {
    const ok = WORKS.has(v.key) || (v.key === "not_needed" && v.vendor in profile.local_endpoints);
    if (!ok) continue;
    const ids = v.models ?? (dflt.vendor === v.vendor && dflt.model ? [dflt.model] : []);
    const name = v.vendor === CLAUDE_CODE ? "Claude Code (your account)" : v.vendor;
    for (const id of ids) out.push({ ref: `${v.vendor}/${id}`, label: `${name} · ${id}` });
  }
  return out;
}

export type PollResult<T> = { outcome: "done"; value: T } | { outcome: "timeout" } | { outcome: "stopped" };

/**
 * Calls `check` every `intervalMs` (first call after one interval) until `done(value)`, the
 * deadline, or stop(). One call at a time; a failed check counts as "not yet".
 */
export function pollUntil<T>(check: () => Promise<T>, done: (v: T) => boolean, o: { intervalMs: number; timeoutMs: number }):
  { result: Promise<PollResult<T>>; stop: () => void } {
  const deadline = Date.now() + o.timeoutMs;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let finish: (r: PollResult<T>) => void = () => {};
  const result = new Promise<PollResult<T>>((resolve) => { finish = resolve; });

  const tick = async (): Promise<void> => {
    timer = null;
    if (stopped) return;
    let value: T | undefined;
    let got = false;
    try {
      value = await check();
      got = true;
    } catch { /* not yet */ }
    if (stopped) return;
    if (got && done(value as T)) { stopped = true; finish({ outcome: "done", value: value as T }); return; }
    schedule();
  };
  const schedule = (): void => {
    if (Date.now() + o.intervalMs > deadline) { stopped = true; finish({ outcome: "timeout" }); return; }
    timer = setTimeout(() => { void tick(); }, o.intervalMs);
  };
  schedule();
  return {
    result,
    stop: () => {
      if (stopped) return;
      stopped = true;
      if (timer) clearTimeout(timer);
      finish({ outcome: "stopped" });
    },
  };
}
