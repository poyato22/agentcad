import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ModelsInfo, Profile, SetupState } from "../../api/types";
import { availableModels, pollUntil, resumeStep } from "./logic";

const st = (p: Partial<SetupState>): SetupState => ({ complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"], ...p });
const none = { modelSkipped: false, onshapeSkipped: false, reopened: false };

describe("resumeStep", () => {
  it.each([
    ["nothing saved → welcome", st({}), none, "welcome"],
    ["profile saved, no model → model", st({ missing: ["model", "onshape"] }), none, "model"],
    ["model saved → onshape", st({ has_model: true, missing: ["onshape", "profile"] }), none, "onshape"],
    ["onshape skipped → profile", st({ has_model: true, missing: ["onshape", "profile"] }), { ...none, onshapeSkipped: true }, "profile"],
    ["model + onshape → profile", st({ has_model: true, has_onshape: true, missing: ["profile"] }), none, "profile"],
    ["all saved → done", st({ has_model: true, has_onshape: true, missing: [] }), none, "done"],
    ["onshape skipped, profile saved → done", st({ has_model: true, missing: ["onshape"] }), { ...none, onshapeSkipped: true }, "done"],
    ["local-only skip, nothing else → onshape", st({ missing: ["model", "onshape", "profile"] }), { ...none, modelSkipped: true }, "onshape"],
    ["re-opened from Profile → welcome", st({ complete: true, has_model: true, missing: [] }), { ...none, reopened: true }, "welcome"],
  ] as const)("%s", (_name, state, opts, want) => {
    expect(resumeStep(state, opts)).toBe(want);
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
