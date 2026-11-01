// P10 E24 / E9: one chat per build (the lead, created on the default model by the first message),
// a sub-agent only when asked, and the stall rule.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { CoreError } from "../../api/client";
import type { ModelsInfo } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { bindClient, resetStore, runs, selectedRunId } from "../../state/store";
import { catalog, resetCatalog } from "../dialogs/catalog";
import { defaultChatModel, editedKeys, isStalled, leadOf, markEdited, resetChat, sendChat, STALL_S } from "./chat";
import { run } from "./testSteps";

const MODELS: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [
    { vendor: "anthropic", key: "none", models: ["claude-sonnet-5-5"] },
    { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] },
  ],
};

function bind(over: Record<string, unknown> = {}) {
  const c = {
    chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: true, queued: false, notes: [] }),
    createRun: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: true, queued: false, notes: [] }),
    message: vi.fn().mockResolvedValue({ ok: true }),
    getRun: vi.fn().mockRejectedValue(new Error("not yet")),
    models: vi.fn().mockResolvedValue(MODELS), localStatus: vi.fn().mockRejectedValue(new Error("404")),
    getProfile: vi.fn().mockResolvedValue({ default_model: null }),
    ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

afterEach(() => { resetStore(); resetChat(); resetCatalog(); activeBuildId.value = null; try { localStorage.clear(); } catch { /* none */ } });

describe("one chat per build", () => {
  it("the build's lead is its newest live lead run", () => {
    const all = [run("old", { role: "lead", project_id: "b1", archived: true, created_at: 3 }), run("l1", { role: "lead", project_id: "b1", created_at: 2 }),
      run("sub", { role: "sub", project_id: "b1", created_at: 4 }), run("l2", { role: "lead", project_id: "b2", created_at: 5 })];
    expect(leadOf(all, "b1")?.id).toBe("l1");
    expect(leadOf(all, "b3")).toBeNull();
    expect(leadOf(all, null)).toBeNull();
  });

  it("the first message goes to POST /projects/{id}/chat and shows the new lead's conversation", async () => {
    const c = bind();
    activeBuildId.value = "b1";
    const out = await sendChat("make a gripper");
    expect(c.chat).toHaveBeenCalledWith("b1", "make a gripper");
    expect(c.createRun).not.toHaveBeenCalled();
    expect(out.runId).toBe("lead1");
    expect(selectedRunId.value).toBe("lead1");
  });

  it("an older core: POST /runs, the first lead on Claude Code (signed in), later messages without a model", async () => {
    const c = bind({ chat: vi.fn().mockResolvedValue(null) });
    activeBuildId.value = "b1";
    await sendChat("make a gripper");
    expect(c.createRun).toHaveBeenCalledWith({ prompt: "make a gripper", project_id: "b1", model_ref: "claude-code/sonnet" });
    runs.value = new Map([["lead1", run("lead1", { role: "lead", project_id: "b1" })]]);
    c.createRun.mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, queued: true, message_id: "m1", position: 1 });
    await sendChat("thicker fingers");
    expect(c.createRun).toHaveBeenLastCalledWith({ prompt: "thicker fingers", project_id: "b1" });
  });

  it("a sub-agent is messaged only when chosen, and falls back to the lead when its core session is gone", async () => {
    const sub = run("sub", { role: "sub", project_id: "b1" });
    const c = bind();
    await sendChat("check the hinge", sub);
    expect(c.message).toHaveBeenCalledWith("sub", "check the hinge");
    expect(c.chat).not.toHaveBeenCalled();
    c.message.mockRejectedValue(new CoreError("not_found", "unknown run sub"));
    await sendChat("again", sub);
    expect(c.chat).toHaveBeenCalledWith("b1", "again");
  });

  it("no build open: says so", async () => {
    bind();
    await expect(sendChat("hi")).rejects.toThrow("Open a build first");
  });

  it("default model: the profile's when set, else Claude Code (sonnet) when signed in", () => {
    expect(defaultChatModel({ ...catalog.value, models: MODELS })).toBe("claude-code/sonnet");
    // an explicit profile default wins even with Claude Code signed in
    expect(defaultChatModel({ ...catalog.value, models: MODELS, profileDefault: "ollama/qwen3" })).toBe("ollama/qwen3");
    // sonnet is preferred whatever order the core lists the aliases in
    const opusFirst: ModelsInfo = { ...MODELS, vendors: MODELS.vendors.map((v) => (v.vendor === "claude-code" ? { ...v, models: ["opus", "sonnet"] } : v)) };
    expect(defaultChatModel({ ...catalog.value, models: opusFirst })).toBe("claude-code/sonnet");
    const out: ModelsInfo = { ...MODELS, vendors: MODELS.vendors.map((v) => (v.vendor === "claude-code" ? { ...v, key: "signed_out" } : v)) };
    expect(defaultChatModel({ ...catalog.value, models: out, profileDefault: "ollama/qwen3" })).toBe("ollama/qwen3");
  });

  it("an edited message stays marked (remembered)", () => {
    markEdited("r1/msg/7");
    expect(editedKeys.value.has("r1/msg/7")).toBe(true);
    expect(JSON.parse(localStorage.getItem("mises.chat.edited") ?? "[]")).toEqual(["r1/msg/7"]);
  });
});

describe("stall", () => {
  const now = 1_800_000_000_000;
  const t = now / 1000;
  const r = run("r1", { status: "running", updated_at: t - STALL_S - 5 });
  it("a running run with no new step or activity for 90 s", () => {
    expect(isStalled(r, null, t - 200, now)).toBe(true);
    expect(isStalled(r, null, t - 30, now)).toBe(false);
    expect(isStalled({ ...r, status: "done" }, null, null, now)).toBe(false);
    expect(isStalled(r, { run_id: "r1", state: "thinking", detail: "", since: t - 10 }, null, now)).toBe(false);
  });
  it("not while a tool runs or the run waits for you", () => {
    expect(isStalled(r, { run_id: "r1", state: "tool", detail: "sim", since: t - 300 }, null, now)).toBe(false);
    expect(isStalled(r, { run_id: "r1", state: "waiting_approval", detail: "", since: t - 300 }, null, now)).toBe(false);
  });
});
