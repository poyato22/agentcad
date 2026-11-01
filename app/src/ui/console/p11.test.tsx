// P11 against the real core's steps (core/scripts/dump_p11_fixtures.py → core/tests/fixtures/ui/p11):
// F9 recovery notes and the end-of-budget card (Retry / Continue), F7 a message's images from the
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
import { run } from "./testSteps";
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
  it("shows each stop and recovery as a quiet line, and one card with Retry / Continue at the end", async () => {
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
    fireEvent.click(within(card).getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "Continue from where you stopped."));
    expect(within(card).getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

describe("F7: a message's images", () => {
  it("shows the attached image from the build's store", async () => {
    const fx = read("chat_image_step.json") as { project_id: string; step: unknown };
    const step = stepSchema.parse(fx.step);
    const bytes = readFileSync(resolve(P11, "chat_image.png"));
    const chatImage = vi.fn().mockResolvedValue(new Blob([bytes], { type: "image/png" }));
    const created = vi.fn(() => "blob:img");
    Object.assign(URL, { createObjectURL: created });
    mount([step], fx.project_id, { chatImage });
    await frame();
    const ref = (step.payload as { images: { id: string; media_type: string }[] }).images[0];
    await waitFor(() => expect(document.querySelector(`[data-chat-image="${ref.id}"] img`)?.getAttribute("src")).toBe("blob:img"));
    expect(chatImage).toHaveBeenCalledWith(fx.project_id, ref.id, "image/png");
    expect(screen.getByRole("log", { name: "Conversation" }).textContent).toContain("why does the lid look offset here?");
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
