// Activity indicators in the thread (Task A2): the live status line per run driven by
// agent_activity transitions, the "Show thinking" disclosure (agent_thinking), the run switcher's
// animated dots — and approval / failed-call visibility unchanged while a run waits (Plan 4
// Review Focus 3).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentRun, MisesEvent, Step } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { approvals, bindClient, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { recordThinking, resetThinking, THINKING_MAX, thinkingByRun } from "../../state/thinking";
import { resetCatalog } from "../dialogs/catalog";
import { ConsolePane } from "./ConsolePane";
import { resetOffListRuns } from "./derived";
import { elapsedSeconds, statusText } from "./RunStatus";
import { approval, message, resetIds, run, toolCall, toolFailure } from "./testSteps";
import { resetConsoleView } from "./view";

const act_ = (state: string, detail = "", since = 100): MisesEvent =>
  ({ type: "agent_activity", run_id: "r1", ts: 0, data: { state, detail, since } });

async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

function mount(list: Step[] = [message("r1", "user", "thicken the plate", 1)]) {
  bindClient({
    agents: vi.fn().mockResolvedValue([]), models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }),
    localStatus: vi.fn().mockRejectedValue(new Error("404")), getProfile: vi.fn().mockResolvedValue({}),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
  } as unknown as CoreClient);
  const r: AgentRun = run("r1", { task: "thicken the plate" });
  runs.value = new Map([[r.id, r]]);
  steps.value = new Map([["r1", list]]);
  selectedRunId.value = "r1";
  return render(<ConsolePane />);
}

const status = () => document.querySelector("[data-run-status]") as HTMLElement | null;

beforeEach(() => { resetIds(); resetConsoleView(); });
afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); resetActivity(); resetThinking(); vi.useRealTimers(); });

describe("status line text", () => {
  it("formats each state", () => {
    const now = 112_000;           // ms: 12 s after since=100
    expect(statusText({ run_id: "r", state: "tool", detail: "measuring arm", since: 100 }, now)).toBe("measuring arm · 12 s");
    expect(statusText({ run_id: "r", state: "tool", detail: "", since: 100 }, now)).toBe("working · 12 s");
    expect(statusText({ run_id: "r", state: "thinking", detail: "", since: 100 })).toBe("thinking");
    expect(statusText({ run_id: "r", state: "writing", detail: "", since: 100 })).toBe("writing");
    expect(statusText({ run_id: "r", state: "waiting_approval", detail: "", since: 100 })).toContain("waiting for your approval");
    expect(statusText({ run_id: "r", state: "waiting_user", detail: "", since: 100 })).toContain("waiting for your answer");
    expect(statusText({ run_id: "r", state: "queued", detail: "", since: 100 })).toBe("waiting for a free slot");
    expect(elapsedSeconds(200, 100_000)).toBe(0);                     // never negative
  });
});

describe("thread status line from agent_activity transitions", () => {
  it("thinking → tool → writing → waiting → done", async () => {
    mount();
    await frame();
    expect(status()).toBeNull();                                      // no activity yet

    recordActivity(act_("thinking"));
    await frame();
    expect(status()?.dataset.runStatus).toBe("thinking");
    expect(status()?.querySelector("[data-state=thinking]")).toBeTruthy();

    recordActivity(act_("tool", "FEM study · 2/6", Date.now() / 1000 - 3));
    await frame();
    expect(status()?.dataset.runStatus).toBe("tool");
    expect(status()?.querySelector("[data-status-text]")?.textContent).toMatch(/^FEM study · 2\/6 · [34] s$/);

    recordActivity(act_("writing"));
    await frame();
    expect(status()?.querySelector("[data-state=writing]")?.textContent).toBe("▍");

    recordActivity(act_("waiting_approval"));
    await frame();
    expect(status()?.classList.contains("needs-you")).toBe(true);

    recordActivity(act_("done"));
    await frame();
    expect(status()).toBeNull();                                      // finished runs show nothing
  });

  it("the tool line counts seconds", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(100_000);
    mount();
    recordActivity(act_("tool", "meshing", 100));
    await act(async () => { await Promise.resolve(); });
    expect(status()?.querySelector("[data-status-text]")?.textContent).toBe("meshing · 0 s");
    await act(async () => { vi.setSystemTime(104_000); vi.advanceTimersByTime(1000); });   // the tick moves the clock to 105 s
    expect(status()?.querySelector("[data-status-text]")?.textContent).toBe("meshing · 5 s");
  });
});

describe("Show thinking", () => {
  it("reveals agent_thinking text in faint type", async () => {
    mount();
    recordActivity(act_("thinking"));
    recordThinking({ type: "agent_thinking", run_id: "r1", ts: 0, data: { text_delta: "The wall is 3 mm; " } });
    recordThinking({ type: "agent_thinking", run_id: "r1", ts: 0, data: { text_delta: "<b>check</b> the fillet." } });
    await frame();
    expect(document.querySelector("[data-thinking]")).toBeNull();     // closed by default
    fireEvent.click(screen.getByRole("button", { name: "Show thinking" }));
    const pre = document.querySelector("[data-thinking]") as HTMLElement;
    expect(pre.textContent).toBe("The wall is 3 mm; <b>check</b> the fillet.");
    expect(pre.querySelector("b")).toBeNull();                       // text, never HTML
    expect(pre.classList.contains("faint")).toBe(true);
  });

  it("keeps a capped buffer and drops it when the run goes idle", () => {
    recordThinking({ type: "agent_thinking", run_id: "r1", ts: 0, data: { text_delta: "x".repeat(THINKING_MAX + 50) } });
    expect(thinkingByRun.value.get("r1")?.length).toBe(THINKING_MAX);
    recordThinking(act_("idle"));
    expect(thinkingByRun.value.has("r1")).toBe(false);
  });
});

describe("run switcher dots", () => {
  it("animate by activity state", async () => {
    mount();
    recordActivity(act_("thinking"));
    await frame();
    const dot = document.querySelector("[data-run-switcher] .cv-actdot") as HTMLElement;
    expect(dot.dataset.activity).toBe("thinking");
    recordActivity(act_("tool", "measuring"));
    await frame();
    expect(dot.dataset.activity).toBe("tool");
  });
});

describe("approval and failed-call visibility unchanged (Plan 4 Review Focus 3)", () => {
  it("a waiting run still shows its approval card and its failed call", async () => {
    mount([
      message("r1", "user", "thin the wall", 1),
      toolCall("r1", "f1", "mirror_part_set_vars", { part_id: "mount_plate", vars: { wall: 0.4 } }, 2),
      toolFailure("r1", "f1", "mirror_part_set_vars", 2.5, "wall 0.4 mm is below the 0.8 mm minimum"),
    ]);
    approvals.value = new Map([["ap1", approval("ap1", "r1")]]);
    recordActivity(act_("waiting_approval"));
    await frame();
    const thread = screen.getByRole("log", { name: "Conversation" });
    expect(thread.querySelector('[data-approval="ap1"]')).toBeTruthy();
    const fail = thread.querySelector("[data-activity][data-tone=fail]") as HTMLElement;
    expect(fail.textContent).toContain("wall 0.4 mm is below the 0.8 mm minimum");
    expect(status()?.classList.contains("needs-you")).toBe(true);
  });
});
