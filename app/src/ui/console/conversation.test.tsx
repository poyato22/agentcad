import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { stepSchema } from "../../api/schemas";
import type { AgentRun, CoreStatusInfo, Step } from "../../api/types";
import { actions, approvals, bindClient, coreStatus, focusedStepId, questions, resetStore, runs, selectedRunId, steps } from "../../state/store";
import { resetCatalog } from "../dialogs/catalog";
import { ConsolePane } from "./ConsolePane";
import { expandedFolds, resetOffListRuns } from "./derived";
import { approval, message, resetIds, run, step, toolCall, toolFailure } from "./testSteps";
import { composerOpen, consoleView, resetConsoleView, VIEW_KEY } from "./view";

const REAL: Step[] = (JSON.parse(readFileSync(resolve(__dirname, "../../../test/fixtures/core-ui/steps.json"), "utf8")) as unknown[]).map((s) => stepSchema.parse(s));
const RID = REAL[0].run_id;

function setRuns(list: AgentRun[]) { runs.value = new Map(list.map((r) => [r.id, r])); }
function setSteps(list: Step[]) {
  const m = new Map<string, Step[]>();
  for (const s of list) m.set(s.run_id, [...(m.get(s.run_id) ?? []), s]);
  steps.value = m;
}
async function frame() { await act(async () => { await new Promise((r) => setTimeout(r, 40)); }); }

const MODELS = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [{ vendor: "anthropic", key: "none" }, { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] }],
};

function client(over: Record<string, unknown> = {}) {
  const c = {
    agents: vi.fn().mockResolvedValue([
      { name: "designer", description: "Main CAD agent.", model: "inherit" },
      { name: "verifier", description: "Checker.", model: "inherit" },
    ]),
    models: vi.fn().mockResolvedValue(MODELS),
    localStatus: vi.fn().mockRejectedValue(new Error("404")),
    getProfile: vi.fn().mockResolvedValue({ default_agent: "verifier", default_model: "claude-code/opus" }),
    createRun: vi.fn().mockResolvedValue({ run_id: "new1" }),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
    message: vi.fn().mockResolvedValue({ ok: true }),
    ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

beforeEach(() => { resetIds(); try { localStorage.removeItem(VIEW_KEY); } catch { /* none */ } resetConsoleView(); });
afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); expandedFolds.value = new Set(); });

describe("conversation view (default)", () => {
  it("renders_the_real_steps_as_a_thread_with_compact_activity_lines", async () => {
    client();
    setRuns([run(RID, { task: "thicken the mount plate to 6 mm" })]);
    setSteps(REAL);
    const { container } = render(<ConsolePane />);
    await frame();
    expect(consoleView.value).toBe("chat");
    expect(container.querySelector("[data-filters]")).toBeNull();      // the dense log is behind "Full log"
    const thread = screen.getByRole("log", { name: "Conversation" });
    const you = within(thread).getAllByText("thicken the mount plate to 6 mm");
    expect(you[0].closest("[data-msg=user]")).toBeTruthy();
    const acts = [...thread.querySelectorAll("[data-activity]")].map((a) => a.querySelector("[data-activity-text]")?.textContent);
    expect(acts).toEqual([
      "created mount plate · 3.9 cm³",
      "changed mount plate · thickness 5 → 6 · +0.8 cm³",
      "measured mount plate · 4.6 cm³",
      "asking to merge mount plate",
    ]);
    // the agent reply is rendered as Mises markup (so "6 mm" is tinted); match the whole message
    const agentMsg = thread.querySelector("[data-msg=agent]") as HTMLElement;
    expect(agentMsg.textContent).toContain("Thickened the plate to 6 mm; asking to merge.");
    expect(agentMsg.querySelector("[data-value]")?.textContent).toBe("6 mm");
    // the running call reads as being changed by an agent
    expect(thread.querySelectorAll("[data-activity][data-tone=running]")[0].hasAttribute("data-changing")).toBe(true);
  });

  it("expand_shows_raw_tool_io_and_the_diff", async () => {
    client();
    setRuns([run(RID)]);
    setSteps(REAL);
    render(<ConsolePane />);
    await frame();
    const line = screen.getByText("changed mount plate · thickness 5 → 6 · +0.8 cm³").closest("[data-activity]") as HTMLElement;
    const toggle = within(line).getByRole("button", { name: /^Show details/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(line.querySelector("[data-diff]")?.textContent).toContain("+ thickness = 6.00 mm");
    expect(line.querySelector("[data-call-details]")?.textContent).toContain('"thickness": 6');
  });

  it("approval_card_and_failed_call_are_visible_in_the_conversation", async () => {
    client();
    setRuns([run("r1")]);
    setSteps([
      message("r1", "user", "thin the wall", 1),
      toolCall("r1", "f1", "mirror_part_set_vars", { part_id: "mount_plate", vars: { wall: 0.4 } }, 2),
      toolFailure("r1", "f1", "mirror_part_set_vars", 2.5, "wall 0.4 mm is below the 0.8 mm minimum"),
      step("r1", "error", { stop_reason: "error", error: "transient", message: "provider timed out", hint: "try again later", retryable: true }, 3),
    ]);
    approvals.value = new Map([["ap1", approval("ap1", "r1")]]);
    questions.value = new Map([["q1", { question_id: "q1", run_id: "r1", question: "merge now?", options: ["yes", "no"] }]]);
    render(<ConsolePane />);
    await frame();
    const thread = screen.getByRole("log", { name: "Conversation" });
    const fail = thread.querySelector("[data-activity][data-tone=fail]") as HTMLElement;
    expect(fail.textContent).toContain("changing mount plate failed · wall 0.4 mm is below the 0.8 mm minimum");
    expect(fail.textContent).toContain("try again");
    expect(fail.querySelector("[data-activity-text]")?.classList.contains("cv-fail")).toBe(true);
    const err = thread.querySelector("[data-error]") as HTMLElement;
    expect(err.textContent).toContain("transient · provider timed out");
    expect(err.textContent).toContain("try again later");
    expect(thread.querySelector('[data-approval="ap1"]')).toBeTruthy();
    expect(thread.querySelector('[data-question="q1"]')).toBeTruthy();
  });

  it("another_run_that_needs_you_is_shown_and_one_click_away", async () => {
    client();
    setRuns([run("r1", { created_at: 2, task: "stiffen the upright" }), run("r2", { created_at: 1, agent: "checker", task: "check the holes", status: "done" })]);
    selectedRunId.value = "r1";
    approvals.value = new Map([["ap2", approval("ap2", "r2")]]);
    render(<ConsolePane />);
    await frame();
    const needs = screen.getByRole("button", { name: "checker · check holes needs you" });
    expect(needs.textContent).toContain("▲ 1 needs you");
    fireEvent.click(needs);
    await frame();
    expect(selectedRunId.value).toBe("r2");
    expect(document.querySelector('[data-approval="ap2"]')).toBeTruthy();
  });

  it("a_card_on_the_off_list_ui_run_is_reachable_from_the_switcher", async () => {
    // A1: a print card sits on the "ui" run, which GET /runs leaves out. The needs-you chip
    // must still list it (labelled "You (Mises)", not "starting…") and reach it in one click.
    const uiRun = { ...run("ui1"), agent: "you", provider: "ui", model: "ui", task: "external agent: you" };
    client({ getRun: vi.fn(async () => uiRun) });
    setRuns([run("r1", { created_at: 2, task: "stiffen the upright" })]);
    selectedRunId.value = "r1";
    approvals.value = new Map([["apu", approval("apu", "ui1")]]);
    render(<ConsolePane />);
    await frame();
    const needs = screen.getByRole("button", { name: "You (Mises) needs you" });
    expect(needs.textContent).toContain("▲ 1 needs you");
    fireEvent.click(needs);
    await frame();
    expect(selectedRunId.value).toBe("ui1");
    expect(document.querySelector('[data-approval="apu"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Run: You \(Mises\)/ })).toBeTruthy();
  });

  it("run_switcher_is_one_dropdown_with_status_dots", async () => {
    client();
    setRuns([run("r1", { created_at: 2, task: "stiffen the upright" }), run("r2", { created_at: 1, agent: "checker", task: "check the holes", status: "failed" })]);
    render(<ConsolePane />);
    await frame();
    const sw = screen.getByRole("button", { name: /^Run: designer · stiffen upright/ });
    expect(sw.querySelector("[data-tone=blue]")).toBeTruthy();       // running: being changed by an agent
    fireEvent.click(sw);
    const list = screen.getByRole("listbox", { name: "Runs" });
    const opts = within(list).getAllByRole("option");
    expect(opts.map((o) => o.querySelector("[data-run-label]")?.textContent)).toEqual(["designer · stiffen upright", "checker · check holes"]);
    expect(opts[1].querySelector("[data-tone=red]")).toBeTruthy();
    fireEvent.click(opts[1]);
    expect(selectedRunId.value).toBe("r2");
    expect(screen.queryByRole("listbox", { name: "Runs" })).toBeNull();
  });

  it("details_toggle_shows_the_dense_log_and_is_remembered", async () => {
    client();
    setRuns([run(RID)]);
    setSteps(REAL);
    const { container } = render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "Full log" }));
    await frame();
    expect(container.querySelector("[data-filters]")).toBeTruthy();
    expect(container.querySelector("[data-roster]")).toBeTruthy();
    expect(localStorage.getItem(VIEW_KEY)).toBe("details");
    fireEvent.click(screen.getByRole("button", { name: "Conversation" }));
    await frame();
    expect(container.querySelector("[data-filters]")).toBeNull();
    expect(localStorage.getItem(VIEW_KEY)).toBe("chat");
  });

  it("an_evidence_link_opens_details_at_that_step_without_remembering_it", async () => {
    client();
    setRuns([run(RID)]);
    setSteps(REAL);
    const { container } = render(<ConsolePane />);
    await frame();
    act(() => actions.focusStep(10));
    for (let i = 0; i < 4; i++) await frame();
    expect(consoleView.value).toBe("details");
    expect(container.querySelector('[data-row][data-key="c:c3"]')).toBeTruthy();
    expect(focusedStepId.value).toBeNull();
    expect(localStorage.getItem(VIEW_KEY)).toBeNull();
  });

  it("a_queued_run_says_it_waits_for_a_slot", async () => {
    client();
    setRuns([run("r1", { status: "queued" })]);
    coreStatus.value = { parallel: { limit: 3, auto: true, reason: "M5 Pro · 24 GB · cloud model → 3" } } as CoreStatusInfo;
    render(<ConsolePane />);
    await frame();
    expect(screen.getByRole("log", { name: "Conversation" }).textContent).toContain("waiting for a free agent slot · Auto: 3 on this Mac");
  });

  it("scrolled_up_new_rows_show_a_chip_and_a_new_card_is_scrolled_into_view", async () => {
    client();
    setRuns([run("r1")]);
    const base = Array.from({ length: 5 }, (_, i) => message("r1", "assistant", `line ${i}`, 10 + i));
    setSteps(base);
    const scrolled: string[] = [];
    const proto = HTMLElement.prototype as unknown as { scrollIntoView?: (o?: unknown) => void };
    const orig = proto.scrollIntoView;
    proto.scrollIntoView = function (this: HTMLElement) { scrolled.push(this.getAttribute("data-key") ?? ""); };
    try {
      render(<ConsolePane />);
      await frame();
      const log = screen.getByRole("log", { name: "Conversation" });
      // the user scrolls up (jsdom has no layout: give the box a size)
      Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
      Object.defineProperty(log, "clientHeight", { configurable: true, value: 100 });
      log.scrollTop = 0;
      fireEvent.scroll(log);
      setSteps([...base, message("r1", "assistant", "late a", 30), message("r1", "assistant", "late b", 31)]);
      await frame();
      expect(log.scrollTop).toBe(0);                                     // no jump
      const chip = screen.getByRole("button", { name: "2 new ↓" });
      // an approval for the shown run: always brought into view
      approvals.value = new Map([["ap9", approval("ap9", "r1")]]);
      await frame();
      expect(scrolled).toContain("a:ap9");
      fireEvent.click(chip);
      expect(log.scrollTop).toBe(1000);
      await frame();
      expect(screen.queryByRole("button", { name: /new ↓/ })).toBeNull();
    } finally {
      proto.scrollIntoView = orig;
    }
  });

  it("long_threads_show_the_last_500_rows_with_show_earlier", async () => {
    client();
    setRuns([run("r1")]);
    setSteps(Array.from({ length: 620 }, (_, i) => message("r1", "assistant", `line ${i}`, 10 + i)));
    render(<ConsolePane />);
    await frame();
    const log = screen.getByRole("log", { name: "Conversation" });
    expect(log.querySelectorAll("[data-msg]")).toHaveLength(500);
    fireEvent.click(screen.getByRole("button", { name: "Show earlier (120)" }));
    await frame();
    expect(log.querySelectorAll("[data-msg]")).toHaveLength(620);
    expect(screen.queryByRole("button", { name: /^Show earlier/ })).toBeNull();
  });

  it("agent_text_renders_as_text_never_html", async () => {
    client();
    setRuns([run("r1")]);
    setSteps([message("r1", "assistant", "<img src=x onerror=alert(1)> **bold**", 1)]);
    render(<ConsolePane />);
    await frame();
    const thread = screen.getByRole("log", { name: "Conversation" });
    // markup is rendered (so **bold** becomes <strong>), but raw HTML stays escaped text
    expect(thread.querySelector("img")).toBeNull();
    expect(thread.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(thread.querySelector("[data-msg=agent] strong")?.textContent).toBe("bold");
  });
});

describe("inline composer", () => {
  it("+new_opens_the_composer_with_profile_defaults_and_starts_a_run", async () => {
    const c = client();
    render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(composerOpen.value).toBe(true);
    const form = screen.getByRole("form", { name: "New run" });
    await waitFor(() => expect((within(form).getByLabelText("Agent") as HTMLSelectElement).value).toBe("verifier"));
    await waitFor(() => expect(within(form).getByRole("button", { name: /^Model: Claude Code · opus/ })).toBeTruthy());
    expect(screen.queryByRole("dialog")).toBeNull();             // inline, not the dialog
    fireEvent.input(within(form).getByLabelText("Task"), { target: { value: "stiffen the upright" } });
    fireEvent.keyDown(within(form).getByLabelText("Task"), { key: "Enter" });
    await waitFor(() => expect(c.createRun).toHaveBeenCalledTimes(1));
    expect(c.createRun.mock.calls[0][0]).toEqual({
      agent: "verifier", prompt: "stiffen the upright", model_ref: "claude-code/opus", edit_scope: ["*"], read_scope: ["*"],
    });
    await waitFor(() => expect(composerOpen.value).toBe(false));
    expect(selectedRunId.value).toBe("new1");
  });

  it("a_model_that_cannot_run_blocks_start_and_says_why", async () => {
    const c = client({ getProfile: vi.fn().mockResolvedValue({ default_agent: "designer", default_model: "anthropic/claude-sonnet-5-5" }),
      models: vi.fn().mockResolvedValue({ ...MODELS, vendors: [{ vendor: "anthropic", key: "none" }] }) });
    render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    const form = screen.getByRole("form", { name: "New run" });
    await waitFor(() => expect(form.textContent).toContain("no API key — add one in Profile"));
    fireEvent.input(within(form).getByLabelText("Task"), { target: { value: "go" } });
    expect((within(form).getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
    expect(c.createRun).not.toHaveBeenCalled();
  });

  it("enter_in_the_picker_never_starts_a_run_and_a_typed_ref_sticks", async () => {
    const c = client();
    render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    const form = screen.getByRole("form", { name: "New run" });
    await waitFor(() => expect(within(form).getByRole("button", { name: /^Model: Claude Code · opus/ })).toBeTruthy());
    fireEvent.input(within(form).getByLabelText("Task"), { target: { value: "go" } });
    fireEvent.click(within(form).getByRole("button", { name: /^Model:/ }));
    const other = within(form).getByLabelText("Other model");
    fireEvent.input(other, { target: { value: "gemini/gemini-3-pro" } });
    fireEvent.keyDown(other, { key: "Enter" });
    await frame();
    expect(within(form).getByRole("button", { name: /^Model: gemini · gemini-3-pro/ })).toBeTruthy();
    expect(c.createRun).not.toHaveBeenCalled();
  });

  it("a_core_error_shows_inline_and_keeps_the_task", async () => {
    client({ createRun: vi.fn().mockRejectedValue(new Error("unknown agent")) });
    render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    const form = screen.getByRole("form", { name: "New run" });
    await waitFor(() => expect(within(form).getByRole("button", { name: /^Model: Claude Code · opus/ })).toBeTruthy());
    fireEvent.input(within(form).getByLabelText("Task"), { target: { value: "go" } });
    fireEvent.click(within(form).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(within(form).getByRole("alert").textContent).toBe("unknown agent"));
    expect((within(form).getByLabelText("Task") as HTMLTextAreaElement).value).toBe("go");
  });
});
