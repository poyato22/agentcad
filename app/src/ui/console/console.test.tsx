import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreClient } from "../../api/client";
import type { AgentRun, Step } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { actions, approvals, bindClient, filter, follow, newRunOpen, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { ConsolePane } from "./ConsolePane";
import { expandedFolds, labelOf, resetOffListRuns } from "./derived";
import { DiffView } from "./DiffView";
import { Filters } from "./Filters";
import { Roster } from "./Roster";
import { renderStats, StepLog } from "./StepLog";
import { approval, message, resetIds, run, step, toolCall, toolFailure, toolResult } from "./testSteps";
import { consoleView, resetConsoleView } from "./view";

const FAKE_TIMERS = { toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "cancelAnimationFrame", "Date"] } as const;

function setRuns(list: AgentRun[]) { runs.value = new Map(list.map((r) => [r.id, r])); }
function setSteps(list: Step[]) {
  const m = new Map<string, Step[]>();
  for (const s of list) m.set(s.run_id, [...(m.get(s.run_id) ?? []), s]);
  steps.value = m;
}
/** Let the log's frame scheduler and Preact flush. */
async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

// These tests cover the dense log: the console's "Details" view (the conversation view has its own tests).
beforeEach(() => { resetIds(); consoleView.value = "details"; });
afterEach(() => { cleanup(); resetStore(); expandedFolds.value = new Set(); vi.useRealTimers(); resetConsoleView(); });

describe("StepLog", () => {
  it("burst_one_render_per_frame", async () => {
    vi.useFakeTimers({ toFake: [...FAKE_TIMERS.toFake] });
    // count animation frames independently of the code under test
    const frames = { size: 0 };
    const tick = () => { frames.size++; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    const { container } = render(<StepLog />);
    await act(async () => { await vi.advanceTimersByTimeAsync(32); });
    const before = renderStats.stepLog;
    const framesBefore = frames.size;
    // 500 tool_call + tool_result pairs in 2 s: one store write every 4 ms
    let list: Step[] = [];
    for (let i = 0; i < 500; i++) {
      list = [...list, toolCall("r1", `b${i}`, "mirror_measure", { part_id: "mount_plate" }, 100 + i), toolResult("r1", `b${i}`, "mirror_measure", 100.5 + i)];
      steps.value = new Map([["r1", list]]);
      await act(async () => { await vi.advanceTimersByTimeAsync(4); });
    }
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const renders = renderStats.stepLog - before;
    const ticks = frames.size - framesBefore;
    expect(ticks).toBeGreaterThan(100);
    expect(ticks).toBeLessThan(140);
    expect(renders).toBeGreaterThan(0);
    expect(renders).toBeLessThanOrEqual(ticks);
    expect(container.querySelectorAll("[data-row]").length).toBeLessThanOrEqual(501);
    expect(container.textContent).toContain("▸ 499 more tool calls");
  });

  it("focus_step_switches_run_unfolds_stops_follow_and_highlights_the_call_row", async () => {
    setRuns([run("r1", { created_at: 1 }), run("r2", { created_at: 2 })]);
    selectedRunId.value = "r2";
    filter.value = { scope: "all", kind: "errors" };
    // r1: five ok measure calls → the first shows, four fold; the 3rd call's result is the evidence
    const list: Step[] = [message("r1", "user", "go", 99)];
    for (let i = 0; i < 5; i++) list.push(toolCall("r1", `m${i}`, "mirror_measure", {}, 100 + i), toolResult("r1", `m${i}`, "mirror_measure", 100.5 + i, { kind: "measure" }));
    const measure = list[6]; // result step of m2
    setSteps([...list, message("r2", "user", "other", 200)]);
    const scrolled: string[] = [];
    const proto = HTMLElement.prototype as unknown as { scrollIntoView?: (o?: unknown) => void };
    const orig = proto.scrollIntoView;
    proto.scrollIntoView = function (this: HTMLElement) { scrolled.push(this.getAttribute("data-key") ?? ""); };
    try {
      const { container } = render(<StepLog />);
      await frame();
      actions.focusStep(measure.id);
      for (let i = 0; i < 6; i++) await frame();
      expect(selectedRunId.value).toBe("r1");
      expect(filter.value.scope).toBe("run");
      expect(follow.value).toBe(false);
      const row = container.querySelector('[data-row][data-key="c:m2"]') as HTMLElement;
      expect(row).not.toBeNull();                       // its fold was opened
      expect(row.hasAttribute("data-focused")).toBe(true);
      expect(scrolled).toContain("c:m2");
      // the highlight goes away after ~2 s
      await act(async () => { await new Promise((r) => setTimeout(r, 2100)); });
      expect(row.hasAttribute("data-focused")).toBe(false);
    } finally {
      proto.scrollIntoView = orig;
    }
  });

  it("renders_at_most_500_rows_with_show_earlier", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    const list: Step[] = [];
    for (let i = 0; i < 700; i++) {         // failed calls never fold
      list.push(toolCall("r1", `f${i}`, "mirror_measure", {}, 100 + i), toolFailure("r1", `f${i}`, "mirror_measure", 100.5 + i));
    }
    setSteps(list);
    const { container } = render(<StepLog />);
    await frame();
    expect(container.querySelectorAll("[data-row]")).toHaveLength(500);
    expect(container.querySelector('[data-row][data-key="c:f699"]')).not.toBeNull();
    fireEvent.click(screen.getByText(/Show earlier/));
    await frame();
    expect(container.querySelectorAll("[data-row]")).toHaveLength(700);
    expect(screen.queryByText(/Show earlier/)).toBeNull();
  });

  it("untrusted_text_not_html", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    setRuns([run("r1", { task: evil })]);
    selectedRunId.value = "r1";
    setSteps([
      message("r1", "assistant", evil, 100),
      toolCall("r1", "c1", evil, { part_id: evil }, 101),
      toolResult("r1", "c1", evil, 102, { summary: evil }),
      step("r1", "error", { stop_reason: "error", error: "tool_failed", message: evil, hint: evil, retryable: false }, 103),
      toolCall("r1", "d", "mirror_part_set_vars", { part_id: "p", vars: { [evil]: 2 } }, 104),
      toolResult("r1", "d", "mirror_part_set_vars", 105, { kind: "diff", data: { part_id: evil, vars: { [evil]: 2 }, delta: {} } }),
    ]);
    const { container } = render(<ConsolePane />);
    await frame();
    fireEvent.click(container.querySelector('[data-key="c:c1"] [data-call-head]') as Element);   // details open
    await frame();
    expect(container.querySelector("img")).toBeNull();
    const text = container.textContent ?? "";
    expect(text.split(evil).length - 1).toBeGreaterThanOrEqual(6);
  });

  it("fold_toggle_expands_and_collapses", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    const list: Step[] = [];
    for (let i = 0; i < 4; i++) list.push(toolCall("r1", `k${i}`, "mirror_measure", {}, 100 + i), toolResult("r1", `k${i}`, "mirror_measure", 100.5 + i));
    setSteps(list);
    const { container } = render(<StepLog />);
    await frame();
    expect(container.querySelectorAll('[data-kind="call"]')).toHaveLength(1);
    fireEvent.click(screen.getByText(/▸ 3 more tool calls · mirror_measure/));
    await frame();
    expect(container.querySelectorAll('[data-kind="call"]')).toHaveLength(4);
    fireEvent.click(screen.getByText(/▾ Fold/));
    await frame();
    expect(container.querySelectorAll('[data-kind="call"]')).toHaveLength(1);
  });

  it("chip_shows_new_rows_when_follow_is_off", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    setSteps([message("r1", "user", "go", 100)]);
    const { container } = render(<StepLog />);
    await frame();
    const scroller = container.querySelector("[data-log-scroll]") as HTMLElement;
    // jsdom has no layout: fake a scrolled-up view
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 200 });
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    await frame();
    expect(follow.value).toBe(false);
    setSteps([message("r1", "user", "go", 100), message("r1", "assistant", "one", 101), message("r1", "assistant", "two", 102)]);
    await frame();
    expect(scroller.scrollTop).toBe(100);
    const chip = screen.getByText("2 new ↓");
    fireEvent.click(chip);
    await frame();
    expect(follow.value).toBe(true);
    expect(screen.queryByText(/new ↓/)).toBeNull();
  });
});

describe("StepLog rows for real core shapes (fix round 1)", () => {
  it("renders_acp_files_onshape_counts_hints_and_acp_decisions", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    setSteps([
      step("r1", "decision", { acp: "session_started", agent: "claude-code", session_id: "s-1", protocol_version: 1, agent_info: null }, 99),
      step("r1", "tool_call", { call_id: "t1", name: "Edit part.py", kind: "edit", status: "pending", input: {}, source: "acp" }, 100),
      step("r1", "diff", { call_id: "t1", path: "/w/part.py", old_text: "a\nb\n", new_text: "a\nc\nd\n", source: "acp" }, 101),
      step("r1", "tool_result", { call_id: "t1", name: "Edit part.py", status: "completed", ok: true, output: null, text: "", source: "acp" }, 102),
      step("r1", "decision", { acp: "permission", approval_id: "ap1", status: "approved", tool_call_id: "t1", option_id: "allow_once" }, 103),
      toolCall("r1", "o1", "onshape_get_features", {}, 104),
      step("r1", "onshape_call", { method: "GET", path: "/api/v10/documents/d1", status: 200, counted: true, ledger_id: 1 }, 104.1),
      step("r1", "onshape_call", { method: "GET", path: "/api/v10/documents/d2", status: 200, counted: true, ledger_id: 2 }, 104.2),
      toolResult("r1", "o1", "onshape_get_features", 105),
      toolCall("r1", "g1", "mirror_part_set_vars", {}, 106),
      step("r1", "error", { call_id: "g1", name: "mirror_part_set_vars", error: "out_of_scope", message: "outside this run's scope", hint: "ask the user to widen the scope", retryable: false }, 107),
    ]);
    const { container } = render(<StepLog />);
    await frame();
    const text = container.textContent ?? "";
    expect(text).toContain("ACP session started · claude-code");
    expect(text).toContain("permission approved · allow_once");
    const file = container.querySelector('[data-kind="file"]');
    expect(file?.textContent).toContain("/w/part.py");
    expect(file?.querySelector(".con-add")?.textContent).toBe("+2");
    expect(file?.querySelector(".con-del")?.textContent).toBe("−1");
    expect(container.querySelector('[data-key="c:o1"]')?.textContent).toContain("2 Onshape calls");
    expect(container.querySelectorAll('[data-kind="note"]')).toHaveLength(0);
    expect(container.querySelector('[data-key="c:g1"]')?.textContent).toContain("ask the user to widen the scope");
  });

  it("view_change_resets_show_earlier", async () => {
    setRuns([run("r1"), run("r2", { agent: "checker" })]);
    selectedRunId.value = "r1";
    const list: Step[] = [];
    for (let i = 0; i < 600; i++) list.push(toolCall("r1", `f${i}`, "m", {}, 100 + i), toolFailure("r1", `f${i}`, "m", 100.5 + i));
    for (let i = 0; i < 600; i++) list.push(toolCall("r2", `g${i}`, "m", {}, 100 + i), toolFailure("r2", `g${i}`, "m", 100.5 + i));
    setSteps(list);
    const { container } = render(<StepLog />);
    await frame();
    fireEvent.click(screen.getByText(/Show earlier/));
    await frame();
    expect(container.querySelectorAll("[data-row]")).toHaveLength(600);
    selectedRunId.value = "r2";                  // a different view: back to the last 500
    await frame();
    expect(container.querySelectorAll("[data-row]")).toHaveLength(500);
  });
});

describe("DiffView", () => {
  it("shows_removed_added_and_delta", () => {
    const s = step("r1", "diff", { call_id: "c2", name: "mirror_part_set_vars", output: {} }, 100);
    const { container } = render(<DiffView item={{ kind: "diff", step: s, partId: "mount_plate", vars: [{ name: "thickness", prev: 5, now: 6 }], delta: { mass_g: 0.8, volume_mm3: 800 } }} />);
    expect(container.textContent).toContain("Part · mount plate");
    const del = container.querySelector(".con-del");
    const add = container.querySelector(".con-add");
    expect(del?.textContent).toBe("- thickness = 5.00 mm");
    expect(add?.textContent).toBe("+ thickness = 6.00 mm");
    expect(container.textContent).toContain("Δ mass +0.80 g · volume +800 mm³");
  });
});

describe("Roster", () => {
  it("roster_totals_and_labels", async () => {
    setRuns([
      run("r1", { task: "stiffen the upright", input_tokens: 15000, output_tokens: 5000, usd: 0.25, created_at: 1792869400 }),
      run("r2", { agent: "designer", provider: "openai", model: "gpt-5", task: "stiffen the upright", input_tokens: 12000, output_tokens: 6000, usd: 0.16, created_at: 1792869410 }),
      run("r3", { agent: "checker", task: "add gusset 2", status: "done", created_at: 1792869420 }),
    ]);
    approvals.value = new Map([["a1", approval("a1", "r1")]]);
    const { container } = render(<Roster />);
    const head = container.querySelector("[data-roster-head]")?.textContent ?? "";
    expect(head).toContain("2 running");
    expect(head).toContain("1 needs you");
    expect(head).toContain("38k tok");
    expect(head).toContain("$0.41");
    const labels = [...container.querySelectorAll("[data-run-label]")].map((e) => e.textContent ?? "");
    expect(labels).toHaveLength(3);
    for (const l of labels) expect(isAutoNumbered(l)).toBe(false);
    expect(labels).toContain("checker · add gusset");
    expect(new Set(labels).size).toBe(3);
    expect(container.textContent).toContain("done");
    // provider · model faint below the label
    expect(container.textContent).toContain("openai · gpt-5");
  });

  it("a_claude_code_run_reads_claude_code_your_account", () => {
    setRuns([run("r1", { provider: "claude-code", model: "sonnet", external: true })]);
    const { container } = render(<Roster />);
    expect(container.querySelector("[data-run=r1] .con-ag-who")?.textContent).toBe("Claude Code (your account) · sonnet");
    expect(container.textContent).not.toContain("claude-code");
  });

  it("selects_a_run_and_opens_new_run", () => {
    setRuns([run("r1"), run("r2", { agent: "checker", created_at: 1792869500 })]);
    const { container } = render(<Roster />);
    fireEvent.click(container.querySelector('[data-run="r1"]') as Element);
    expect(selectedRunId.value).toBe("r1");
    fireEvent.click(screen.getByText("New run"));
    expect(newRunOpen.value).toBe(true);
  });

  it("cancel_one_run_from_roster", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${new URL(String(url)).pathname}`);
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    bindClient(new CoreClient({ baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "t", owned: false }, fetchImpl as unknown as typeof fetch));
    setRuns([run("r1"), run("r2", { agent: "checker", created_at: 1792869500 })]);
    actions.selectRun("r2");
    const { container } = render(<Roster />);
    fireEvent.click(container.querySelector('[data-run="r1"] [data-cancel]') as Element);
    await act(async () => { await Promise.resolve(); });
    expect(calls).toEqual(["POST /runs/r1/cancel"]);
    expect(selectedRunId.value).toBe("r2");       // the ✕ does not select the row
  });
});

describe("Filters", () => {
  it("switches_scope_kind_and_follow", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    setSteps([toolCall("r1", "f", "mirror_measure", {}, 100), toolFailure("r1", "f", "mirror_measure", 101)]);
    follow.value = false;
    render(<Filters />);
    await frame();
    expect(screen.getByText("designer · stiffen upright")).toBeTruthy();
    fireEvent.click(screen.getByText(/^errors/));
    expect(filter.value).toEqual({ scope: "run", kind: "errors" });
    await frame();
    expect(screen.getByText(/^errors/).textContent).toBe("errors 1");
    fireEvent.click(screen.getByText(/^errors/));                // again → everything
    expect(filter.value).toEqual({ scope: "run", kind: "everything" });
    fireEvent.click(screen.getByText("all"));
    expect(filter.value).toEqual({ scope: "all", kind: "everything" });
    fireEvent.click(screen.getByText("tool calls"));
    expect(filter.value).toEqual({ scope: "all", kind: "tool_calls" });
    fireEvent.click(screen.getByText("⤓ follow"));
    expect(follow.value).toBe(true);
  });
});

describe("ConsolePane", () => {
  it("renders_roster_filters_log_cards_and_command_line", async () => {
    setRuns([run("r1")]);
    selectedRunId.value = "r1";
    setSteps([message("r1", "user", "thicken the plate", 100)]);
    approvals.value = new Map([["a1", approval("a1", "r1")]]);
    const { container } = render(<ConsolePane />);
    await frame();
    expect(container.querySelector("[data-roster]")).not.toBeNull();
    expect(container.querySelector("[data-filters]")).not.toBeNull();
    expect(container.querySelector("[data-log-scroll]")).not.toBeNull();
    expect(container.querySelector("[data-command-line]")).not.toBeNull();
    const card = container.querySelector('[data-card][data-approval="a1"]');
    expect(card?.textContent).toContain("designer · stiffen upright");
    expect(card?.querySelector("button")).not.toBeNull(); // the real ApprovalCard, not a placeholder
  });

  it("roster_fallback_is_locked_in_so_a_new_run_does_not_steal_the_selection", async () => {
    setRuns([run("first", { created_at: 1 })]);
    const { container } = render(<ConsolePane />);
    await frame();
    expect(selectedRunId.value).toBe("first");          // the fallback became an explicit choice
    setRuns([run("first", { created_at: 1 }), run("second", { agent: "checker", created_at: 2 })]);
    await frame();
    expect(selectedRunId.value).toBe("first");
    expect(container.querySelector('[data-run="first"][aria-selected="true"]')).not.toBeNull();
    expect(container.querySelector('[data-run="second"][aria-selected="true"]')).toBeNull();
  });

  it("defaults_to_the_latest_running_run", async () => {
    setRuns([run("old", { created_at: 1 }), run("new", { agent: "checker", created_at: 2 }), run("done", { agent: "x", created_at: 3, status: "done" })]);
    setSteps([message("old", "user", "old msg", 1), message("new", "user", "new msg", 2), message("done", "user", "done msg", 3)]);
    const { container } = render(<ConsolePane />);
    await frame();
    expect(container.textContent).toContain("new msg");
    expect(container.textContent).not.toContain("old msg");
    expect(container.querySelector('[data-run="new"][aria-selected="true"]')).not.toBeNull();
  });
});

describe("cards of the app's own ui run", () => {
  afterEach(() => { resetOffListRuns(); });

  it("a print card on the ui run (left out of GET /runs) is labelled You (Mises), not agent", async () => {
    const uiRun = { ...run("ui1"), agent: "you", provider: "ui", model: "ui", task: "external agent: you" };
    const getRun = vi.fn(async () => uiRun);
    bindClient({ getRun } as unknown as CoreClient);
    expect(labelOf("ui1")).toBe("agent");                  // unknown yet: looked up once
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(getRun).toHaveBeenCalledTimes(1);
    expect(getRun).toHaveBeenCalledWith("ui1");
    expect(labelOf("ui1")).toBe("You (Mises)");
    labelOf("ui1");
    expect(getRun).toHaveBeenCalledTimes(1);
  });
});
