import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KeyStatus, ModelsInfo, Profile, SetupState } from "../../api/types";
import { apiKeyVendors, availableModels, localTestText, onshapeTestText, pollUntil, resumeStep } from "./logic";

const st = (p: Partial<SetupState>): SetupState => ({ complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"], ...p });
const none = { modelSkipped: false, onshapeSkipped: false, reopened: false, profileNamed: false };
const named = { ...none, profileNamed: true };

describe("resumeStep", () => {
  // the core keeps "profile" in `missing` until POST /setup/complete; the wizard's own step order
  // uses whether a display name is saved (profileNamed)
  it.each([
    ["nothing saved → welcome", st({}), none, "welcome"],
    ["name saved, no model → model", st({}), named, "model"],
    ["model saved → onshape", st({ has_model: true, missing: ["onshape", "profile"] }), none, "onshape"],
    ["onshape skipped → profile", st({ has_model: true, missing: ["onshape", "profile"] }), { ...none, onshapeSkipped: true }, "profile"],
    ["model + onshape → profile", st({ has_model: true, has_onshape: true, missing: ["profile"] }), none, "profile"],
    ["model + onshape + name → done", st({ has_model: true, has_onshape: true, missing: ["profile"] }), named, "done"],
    ["onshape skipped, name saved → done", st({ has_model: true, missing: ["onshape", "profile"] }), { ...named, onshapeSkipped: true }, "done"],
    ["local-only skip, nothing else → onshape", st({ missing: ["model", "onshape", "profile"] }), { ...none, modelSkipped: true }, "onshape"],
    ["re-opened from Profile → welcome", st({ complete: true, has_model: true, missing: [] }), { ...named, reopened: true }, "welcome"],
  ] as const)("%s", (_name, state, opts, want) => {
    expect(resumeStep(state, opts)).toBe(want);
  });
});

describe("apiKeyVendors", () => {
  it("is_an_allow_list_of_model_vendors_that_take_a_key", () => {
    const keys: KeyStatus[] = ["anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio", "onshape", "claude-code", "someday"]
      .map((vendor) => ({ vendor, present: false, source: "none" }));
    expect(apiKeyVendors(keys).map((k) => k.vendor)).toEqual(["anthropic", "openai", "gemini", "openrouter"]);
  });
});

describe("onshapeTestText", () => {
  it.each([
    [{ ok: true, user_name: "Hartigan Deely" }, "connected as Hartigan Deely"],
    [{ ok: true }, "connected"],
    [{ ok: false, error: "denied" }, "Onshape refused these keys"],
    [{ ok: false, error: "not_configured" }, "No Onshape keys are saved yet"],
    [{ ok: false, error: "unreachable" }, "Onshape did not answer"],
    [{ ok: false, error: "budget" }, "Onshape call budget"],
    [{ ok: false, error: "failed" }, "The Onshape test failed"],
    [{ ok: false, error: "something new" }, "The Onshape test failed"],
  ] as const)("%j", (r, want) => {
    const text = onshapeTestText(r);
    expect(text).toContain(want);
    expect(text).toMatch(/^[A-Zc]/); // a sentence (or "connected…"), never a bare code
    if (!r.ok) expect(text).not.toBe(r.error);
  });
});

describe("localTestText", () => {
  it("names_the_url_and_the_app", () => {
    expect(localTestText({ ok: true }, "http://localhost:11434/v1", "Ollama")).toBe("answering");
    expect(localTestText({ ok: false, error: "unreachable" }, "http://localhost:11434/v1", "Ollama"))
      .toBe("no answer from http://localhost:11434/v1 — is Ollama running?");
  });
});

describe("availableModels", () => {
  const models: ModelsInfo = {
    default: "anthropic/claude-sonnet-5-5",
    vendors: [
      { vendor: "anthropic", key: "keychain" }, { vendor: "openai", key: "none" }, { vendor: "ollama", key: "not_needed" },
      { vendor: "lmstudio", key: "not_needed", models: ["qwen3"] },
      { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] },
    ],
  };
  const profile = { local_endpoints: { lmstudio: "http://localhost:1234/v1" } } as unknown as Profile;

  it("lists_only_vendors_that_work_with_their_known_models", () => {
    expect(availableModels(models, profile)).toEqual([
      { ref: "anthropic/claude-sonnet-5-5", label: "anthropic · claude-sonnet-5-5" },
      { ref: "lmstudio/qwen3", label: "lmstudio · qwen3" },
      { ref: "claude-code/sonnet", label: "Claude Code (your account) · sonnet" },
      { ref: "claude-code/opus", label: "Claude Code (your account) · opus" },
    ]);
  });

  it("local_vendors_the_core_found_answering_at_their_default_url_count_too", () => {
    const m: ModelsInfo = { default: "ollama/qwen3", vendors: [{ vendor: "ollama", key: "not_needed" }, { vendor: "lmstudio", key: "not_needed" }] };
    const p = { local_endpoints: {}, default_model: null } as unknown as Profile;
    expect(availableModels(m, p)).toEqual([]);
    expect(availableModels(m, p, ["ollama"])).toEqual([{ ref: "ollama/qwen3", label: "ollama · qwen3" }]);
    // a saved default model of that vendor is offered as well
    const withDefault = { ...p, default_model: "lmstudio/gemma" } as Profile;
    expect(availableModels(m, withDefault, ["lmstudio"])).toEqual([{ ref: "lmstudio/gemma", label: "lmstudio · gemma" }]);
  });

  it("signed_out_or_missing_claude_code_is_not_offered", () => {
    const m: ModelsInfo = { default: "a/b", vendors: [{ vendor: "claude-code", key: "signed_out", models: ["sonnet"] }, { vendor: "claude-code", key: "not_installed", models: ["opus"] }] };
    expect(availableModels(m, profile)).toEqual([]);
  });
});

describe("pollUntil", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("polls_every_interval_and_stops_on_success", async () => {
    let n = 0;
    const check = vi.fn(async () => ++n);
    const p = pollUntil(check, (v) => v >= 3, { intervalMs: 2000, timeoutMs: 120_000 });
    await vi.advanceTimersByTimeAsync(1999);
    expect(check).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(check).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4000);
    await expect(p.result).resolves.toEqual({ outcome: "done", value: 3 });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(check).toHaveBeenCalledTimes(3);
  });

  it("times_out_and_stops", async () => {
    const check = vi.fn(async () => false);
    const p = pollUntil(check, (v) => v, { intervalMs: 2000, timeoutMs: 120_000 });
    await vi.advanceTimersByTimeAsync(120_000);
    await expect(p.result).resolves.toEqual({ outcome: "timeout" });
    const calls = check.mock.calls.length;
    expect(calls).toBe(60);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(check).toHaveBeenCalledTimes(calls);
  });

  it("a_check_that_hangs_is_aborted_after_its_timeout_and_polling_goes_on", async () => {
    const signals: AbortSignal[] = [];
    let n = 0;
    const check = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      n++;
      if (n === 1) return new Promise<boolean>(() => { /* hangs forever */ });
      return Promise.resolve(true);
    });
    const p = pollUntil(check, (v) => v, { intervalMs: 2000, timeoutMs: 120_000, checkTimeoutMs: 5000 });
    await vi.advanceTimersByTimeAsync(2000);
    expect(check).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4999);
    expect(signals[0].aborted).toBe(false);
    expect(check).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(signals[0].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    await expect(p.result).resolves.toEqual({ outcome: "done", value: true });
    expect(check).toHaveBeenCalledTimes(2);
  });

  it("stop_aborts_the_check_in_flight", async () => {
    let sig: AbortSignal | null = null;
    const p = pollUntil((s) => { sig = s; return new Promise<boolean>(() => {}); }, (v) => v, { intervalMs: 1000, timeoutMs: 60_000 });
    await vi.advanceTimersByTimeAsync(1000);
    p.stop();
    expect((sig as AbortSignal | null)?.aborted).toBe(true);
    await expect(p.result).resolves.toEqual({ outcome: "stopped" });
  });

  it("stop_cancels_pending_polls_and_failed_checks_keep_polling", async () => {
    const check = vi.fn().mockRejectedValueOnce(new Error("blip")).mockResolvedValue(false);
    const p = pollUntil(check, (v: boolean) => v, { intervalMs: 2000, timeoutMs: 120_000 });
    await vi.advanceTimersByTimeAsync(4000);
    expect(check).toHaveBeenCalledTimes(2);
    p.stop();
    await expect(p.result).resolves.toEqual({ outcome: "stopped" });
    await vi.advanceTimersByTimeAsync(20_000);
    expect(check).toHaveBeenCalledTimes(2);
  });
});
