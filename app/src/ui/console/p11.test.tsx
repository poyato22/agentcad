// P11 against the real core's steps (core/scripts/dump_p11_fixtures.py → core/tests/fixtures/ui/p11):
// F9 recovery notes and the end-of-budget card (Continue, never your message again), F7 a message's images from the
// build's store, F4 the pin's pose fields read leniently.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { feedbackPinSchema } from "../../api/measure";
import { stepSchema } from "../../api/schemas";
import type { AgentRun, Step } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { bindClient, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { resetChatImages } from "./ChatImages";
import { ConsolePane } from "./ConsolePane";
import { resetOffListRuns } from "./derived";
import { message, run, step } from "./testSteps";
import { resetConsoleView } from "./view";

const P11 = resolve(__dirname, "../../../../core/tests/fixtures/ui/p11");
const read = (f: string) => JSON.parse(readFileSync(resolve(P11, f), "utf8")) as unknown;

async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

function mount(list: Step[], projectId: string | null, extra: Record<string, unknown> = {}) {
  const c = {
    agents: vi.fn().mockResolvedValue([]), models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }),
    localStatus: vi.fn().mockRejectedValue(new Error("404")), getProfile: vi.fn().mockResolvedValue({}),
    cancel: vi.fn().mockResolvedValue({ ok: true }), message: vi.fn().mockResolvedValue({ ok: true }),
    chat: vi.fn().mockResolvedValue({ run_id: list[0].run_id, routed: { agent: "engineer", reason: "", confidence: 1 }, new_lead: false, queued: false }),
    continueRun: vi.fn().mockResolvedValue(undefined),
    ...extra,
  };
  bindClient(c as unknown as CoreClient);
  const rid = list[0].run_id;
  const r: AgentRun = run(rid, { role: "lead", project_id: projectId, status: "failed" });
  runs.value = new Map([[rid, r]]);
  steps.value = new Map([[rid, list]]);
  selectedRunId.value = rid;
  activeBuildId.value = projectId;
  return { c, ...render(<ConsolePane />) };
}

afterEach(() => { cleanup(); resetStore(); resetConsoleView(); resetOffListRuns(); resetChatImages(); activeBuildId.value = null; });

describe("F9: a run that recovers by itself", () => {
  it("shows each stop and recovery as a quiet line, and one card with Continue at the end", async () => {
    const list = (read("recovery_steps.json") as unknown[]).map((s) => stepSchema.parse(s));
    const { c } = mount(list, "b1");
    await frame();
    const log = screen.getByRole("log", { name: "Conversation" });
    expect(log.textContent).toContain("Recovered: resumed the session after Claude Code exited (code 3)");
    expect(log.textContent).toContain("Stopped: Claude Code exited (code 3) before finishing the turn");
    expect(log.textContent).toContain("Stuck (mirror_part_set_vars failing repeatedly)");
    // one card, not one per failure
    const cards = log.querySelectorAll("[data-error]");
    expect(cards).toHaveLength(1);
    const card = cards[0] as HTMLElement;
    expect(card.hasAttribute("data-recovery-exhausted")).toBe(true);
    expect(card.textContent).toContain("restarting the session 3 times");
    // no way to send the same message again: Continue carries the run on internally
    expect(within(card).queryByRole("button", { name: "Retry" })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(c.continueRun).toHaveBeenCalledWith(list[0].run_id, expect.stringContaining("Stopped after 3 recoveries")));
    expect(c.chat).not.toHaveBeenCalled();
    expect(c.message).not.toHaveBeenCalled();
  });
});

describe("AgentCAD continues a stopped run by itself", () => {
  it("shows its internal continuation as a small note, never as your message", async () => {
    const internal = "AgentCAD note: your last turn stopped (the app restarted before the turn finished). Carry on.";
    const list = [
      message("c1", "user", "build the arm", 1),
      step("c1", "error", { stop_reason: "interrupted", error: "cancelled", message: "the process driving this run exited before it finished" }, 2),
      step("c1", "decision", { continuation: { cause: "the app restarted before the turn finished", auto: true, text: "AgentCAD continued after: the app restarted before the turn finished" } }, 3),
      // a native run stores the internal message as a system note: no bubble for it
      step("c1", "message", { role: "user", text: internal, system_note: { kind: "continuation" } }, 3.5),
      message("c1", "assistant", "Picking up the elbow joint.", 4),
    ];
    mount(list, "b1");
    runs.value = new Map([["c1", run("c1", { role: "lead", project_id: "b1", status: "done" })]]);
    await frame();
    const log = screen.getByRole("log", { name: "Conversation" });
    const notes = [...log.querySelectorAll("[data-kind=note]")].map((n) => n.textContent);
    expect(notes).toContain("AgentCAD continued after: the app restarted before the turn finished");
    const mine = [...log.querySelectorAll("[data-msg=user]")].map((n) => n.textContent ?? "");
    expect(mine).toHaveLength(1);
    expect(mine[0]).toContain("build the arm");
    expect(log.textContent).not.toContain("Carry on.");
    // the run got past the interruption: nothing to press
    expect(within(log.querySelector("[data-error]") as HTMLElement).queryByRole("button")).toBeNull();
  });
});

describe("F7: a message's images", () => {
  it("shows the attached image from the build's store", async () => {
    const fx = read("chat_image_step.json") as { project_id: string; step: unknown };
    const step = stepSchema.parse(fx.step);
    const bytes = readFileSync(resolve(P11, "chat_image.png"));
    const chatImage = vi.fn().mockResolvedValue(new Blob([bytes], { type: "image/png" }));
    const created = vi.fn(() => "blob:img");
    const revoked = vi.fn();
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
    const { unmount } = mount([step], fx.project_id, { chatImage });
    await frame();
    const ref = (step.payload as { images: { id: string; media_type: string }[] }).images[0];
    await waitFor(() => expect(document.querySelector(`[data-chat-image="${ref.id}"] img`)?.getAttribute("src")).toBe("blob:img"));
    expect(chatImage).toHaveBeenCalledWith(fx.project_id, ref.id, "image/png");
    expect(screen.getByRole("log", { name: "Conversation" }).textContent).toContain("why does the lid look offset here?");
    unmount();                                        // fix round 1: its object URL is released
    expect(revoked).toHaveBeenCalledWith("blob:img");
  });
});

describe("F4: the pin carries the drawn and the model's box", () => {
  it("parses the core's pose fields", () => {
    const step = read("feedback_pose.json") as { payload: { feedback: unknown } };
    const pin = feedbackPinSchema.parse(step.payload.feedback);
    expect(pin.pose_check?.agrees).toBe(false);
    expect(pin.pose_check?.offset_mm).toBe(360);
    expect(pin.model_bbox?.min).toEqual([-31.6, -31.6, 360]);
    expect(pin.drawn_bbox?.max).toEqual([31.6, -328.4, 120]);
  });
});
