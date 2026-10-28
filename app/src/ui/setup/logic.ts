// Pure parts of the setup wizard (also used by Profile): where to resume, which models and key
// vendors are usable, the sentences for test results, sign-in polling.
import type { KeyStatus, LocalTestResult, ModelsInfo, OnshapeTestResult, Profile, SetupState } from "../../api/types";
import { splitModelRef } from "../dialogs/modelRef";

export type StepId = "welcome" | "model" | "onshape" | "profile" | "done";
/** Plan 4 order: the profile is what "signing in" to Mises sets up, so it comes first. */
export const STEPS: readonly { id: StepId; label: string }[] = [
  { id: "welcome", label: "Welcome" },
  { id: "profile", label: "Profile" },
  { id: "model", label: "Model" },
  { id: "onshape", label: "Onshape" },
  { id: "done", label: "Done" },
];

export interface ResumeOpts {
  /** "Skip — local models only" was chosen on the model step. */
  modelSkipped: boolean;
  /** "Skip for now" was chosen on the Onshape step. */
  onshapeSkipped: boolean;
  /** Opened again on purpose (from Profile): start from the top. */
  reopened: boolean;
  /** A display name is saved (the Profile basics step was done). The core keeps "profile" in
   *  `missing` until setup is marked complete, so the step order uses this instead. */
  profileNamed: boolean;
}

/** The first step still missing. What's saved stays saved, so a reload resumes here. */
export function resumeStep(s: SetupState, o: ResumeOpts): StepId {
  if (o.reopened) return "welcome";
  const missing = new Set(s.missing);
  const modelDone = s.has_model || o.modelSkipped;
  const started = modelDone || !missing.has("onshape") || o.onshapeSkipped;
  if (!o.profileNamed) return started ? "profile" : "welcome";
  if (!modelDone) return "model";
  if (missing.has("onshape") && !o.onshapeSkipped) return "onshape";
  return "done";
}

export const CLAUDE_CODE = "claude-code";
/** How the user's own Claude Code is named as a provider (never as a Mises feature). */
export const CLAUDE_CODE_LABEL = "Claude Code (your account)";
const WORKS = new Set(["env", "keychain", "signed_in"]);

/** Model vendors that take an API key (core providers/registry VENDORS with an env_key). An
 *  allow-list: local servers, Onshape, the user's Claude Code (signs in, no key) and anything a
 *  newer core adds stay out of the API-key pickers. */
export const API_KEY_VENDORS: readonly string[] = ["anthropic", "openai", "gemini", "openrouter"];

export function apiKeyVendors(keys: readonly KeyStatus[]): KeyStatus[] {
  return keys.filter((k) => API_KEY_VENDORS.includes(k.vendor));
}

export interface ModelOption { ref: string; label: string }

/**
 * Models that can run now: a saved key, a signed-in Claude Code, or a local server with a saved
 * endpoint or found answering at its default URL (`detected`, from POST /local/test). Ids come
 * from the vendor's list, else the core's default and the profile's default model for that vendor.
 */
export function availableModels(models: ModelsInfo, profile: Profile, detected: readonly string[] = []): ModelOption[] {
  const known = [splitModelRef(models.default), ...(profile.default_model ? [splitModelRef(profile.default_model)] : [])];
  const out: ModelOption[] = [];
  for (const v of models.vendors) {
    const local = v.key === "not_needed" && (v.vendor in profile.local_endpoints || detected.includes(v.vendor));
    if (!WORKS.has(v.key) && !local) continue;
    const ids = v.models ?? [...new Set(known.filter((k) => k.vendor === v.vendor && k.model).map((k) => k.model))];
    const name = v.vendor === CLAUDE_CODE ? CLAUDE_CODE_LABEL : v.vendor;
    for (const id of ids) out.push({ ref: `${v.vendor}/${id}`, label: `${name} · ${id}` });
  }
  return out;
}

/** POST /onshape/test answer → one sentence (the core's codes: denied, not_configured, unreachable, budget, failed). */
export function onshapeTestText(r: OnshapeTestResult): string {
  if (r.ok) return r.user_name ? `connected as ${r.user_name}` : "connected";
  switch (r.error) {
    case "denied":
      return "Onshape refused these keys (denied). Check the access key and secret key in Onshape's developer portal, then save them again.";
    case "not_configured":
      return "No Onshape keys are saved yet. Save both keys first, then test.";
    case "unreachable":
      return "Onshape did not answer. Check this Mac's internet connection, then test again.";
    case "budget":
      return "The Onshape call budget is used up. Raise the yearly call cap in Profile, then test again.";
    default:
      return "The Onshape test failed. Try again; if it keeps failing, replace the keys.";
  }
}

/** POST /local/test answer → status text. */
export function localTestText(r: LocalTestResult, url: string, app: string): string {
  return r.ok ? "answering" : `no answer from ${url} — is ${app} running?`;
}

export type PollResult<T> = { outcome: "done"; value: T } | { outcome: "timeout" } | { outcome: "stopped" };

/**
 * Calls `check` every `intervalMs` (first call after one interval) until `done(value)`, the
 * deadline, or stop(). One call at a time; a failed check counts as "not yet". A check that takes
 * longer than `checkTimeoutMs` (default: the interval) is aborted through its signal and also
 * counts as "not yet", so one hung request never stalls the poll.
 */
export function pollUntil<T>(check: (signal: AbortSignal) => Promise<T>, done: (v: T) => boolean,
  o: { intervalMs: number; timeoutMs: number; checkTimeoutMs?: number }): { result: Promise<PollResult<T>>; stop: () => void } {
  const deadline = Date.now() + o.timeoutMs;
  const checkTimeout = o.checkTimeoutMs ?? o.intervalMs;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight: AbortController | null = null;
  let stopped = false;
  let finish: (r: PollResult<T>) => void = () => {};
  const result = new Promise<PollResult<T>>((resolve) => { finish = resolve; });

  const attempt = (ctl: AbortController): Promise<{ ok: true; value: T } | { ok: false }> => new Promise((resolve) => {
    const t = setTimeout(() => { ctl.abort(); resolve({ ok: false }); }, checkTimeout);
    let p: Promise<T>;
    try { p = check(ctl.signal); } catch { clearTimeout(t); resolve({ ok: false }); return; }
    p.then((value) => { clearTimeout(t); resolve({ ok: true, value }); }, () => { clearTimeout(t); resolve({ ok: false }); });
  });

  const tick = async (): Promise<void> => {
    timer = null;
    if (stopped) return;
    const ctl = inFlight = new AbortController();
    const r = await attempt(ctl);
    if (inFlight === ctl) inFlight = null;
    if (stopped) return;
    if (r.ok && done(r.value)) { stopped = true; finish({ outcome: "done", value: r.value }); return; }
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
      inFlight?.abort();
      inFlight = null;
      finish({ outcome: "stopped" });
    },
  };
}
