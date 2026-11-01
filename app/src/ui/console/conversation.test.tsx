import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import { stepSchema } from "../../api/schemas";
import type { AgentRun, CoreStatusInfo, Step } from "../../api/types";
import { addCatalogChip, agentChips, clearAgentChips } from "../../state/agentChips";
import { actions, approvals, bindClient, coreStatus, focusedStepId, questions, resetStore, runs, selectedPartId, selectedRunId, steps } from "../../state/store";
import { resetCatalog } from "../dialogs/catalog";
import { showDockView } from "../profile/sections/DeveloperSection";
import { ConsolePane } from "./ConsolePane";
import { retryTextFor } from "./Conversation";
import type { ThreadItem } from "./threadModel";
import { expandedFolds, resetOffListRuns } from "./derived";
import { approval, message, resetIds, run, step, toolCall, toolFailure } from "./testSteps";
import { consoleView, resetConsoleView, VIEW_KEY } from "./view";
import { activeBuildId } from "../../state/builds";
import { resetChat } from "./chat";
import { resetMessageActions } from "./MessageActions";

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
    chat: vi.fn().mockResolvedValue({ run_id: "new1", role: "lead", new_lead: true, notes: [] }),
    cancelQueued: vi.fn().mockResolvedValue(true),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
    message: vi.fn().mockResolvedValue({ ok: true }),
    ...over,
  };
  bindClient(c as unknown as CoreClient);
  return c;
}

beforeEach(() => { resetIds(); try { localStorage.removeItem(VIEW_KEY); } catch { /* none */ } resetConsoleView(); });
afterEach(() => { cleanup(); resetStore(); resetCatalog(); resetConsoleView(); resetOffListRuns(); expandedFolds.value = new Set(); clearAgentChips(); });

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
    // m6: "created mount plate" is the part_created card's, not a raw row too
    expect(thread.querySelector("[data-announce=part_created]")).toBeTruthy();
    expect(acts).toEqual([
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
    expect(err.querySelector("[data-error-message]")?.textContent).toBe("provider timed out");
    expect(err.querySelector(".cv-err-code")?.textContent).toBe("transient");
    // the run went on (an approval card followed): no Retry on an error it got past
    expect(within(err).queryByRole("button", { name: "Retry" })).toBeNull();
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
    // a small blue count in the Conversation header (Plan 9 D2)
    const needs = screen.getByRole("button", { name: "checker · check holes needs you · 1 waiting" });
    expect(needs.textContent).toBe("1");
    expect(needs.closest("[data-conversation-head]")).toBeTruthy();
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
    const needs = screen.getByRole("button", { name: "You (Mises) needs you · 1 waiting" });
    expect(needs.textContent).toBe("1");
    fireEvent.click(needs);
    await frame();
    expect(selectedRunId.value).toBe("ui1");
    expect(document.querySelector('[data-approval="apu"]')).toBeTruthy();
    // a thread that isn't the lead's names whose it is in the header
    expect(document.querySelector("[data-conversation-head] [data-run-label]")?.textContent).toBe("You (Mises)");
  });

  it("there_is_no_button_bar_and_no_menu_in_the_conversation_header", async () => {
    // Plan 9 D2: no agent dropdown, no Stop / New / Manage / Browser / Full log buttons, no
    // "provider · model → agent" line; P10 E24 took New run and E22 the agent browser and full
    // log (Profile & settings → Developer), so the ⋯ menu is gone too
    client();
    setRuns([run("r1", { created_at: 2, task: "stiffen the upright", routed: { agent: "engineer", reason: "system build", confidence: 0.9 } })]);
    const { container } = render(<ConsolePane />);
    await frame();
    for (const name of ["New", "Manage", "Browser", "Full log"]) expect(screen.queryByRole("button", { name, exact: true } as never)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Run:/ })).toBeNull();
    expect(container.querySelector("[data-run-switcher], .cv-who, .cv-routed")).toBeNull();
    expect(container.textContent).not.toContain("fake · m");
    expect(screen.queryByRole("button", { name: "Conversation options" })).toBeNull();
  });

  it("E22: Developer's Show full agent log opens the dense log (not remembered); Conversation returns", async () => {
    client();
    setRuns([run(RID)]);
    setSteps(REAL);
    const { container } = render(<ConsolePane />);
    await frame();
    const closed = vi.fn();
    act(() => showDockView("details", closed));
    await frame();
    expect(closed).toHaveBeenCalled();
    expect(container.querySelector("[data-filters]")).toBeTruthy();
    expect(container.querySelector("[data-roster]")).toBeTruthy();
    expect(localStorage.getItem(VIEW_KEY)).toBeNull();      // the dock starts on the Conversation next launch
    fireEvent.click(screen.getByRole("button", { name: "Conversation" }));
    await frame();
    expect(container.querySelector("[data-filters]")).toBeNull();
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

  it("agent_announcements_show_as_cards_woven_into_the_thread", async () => {
    client();
    setRuns([run("r1", { created_at: 0 })]);
    // the core stores every announcement as a DECISION step, so the card rebuilds from steps
    setSteps([step("r1", "decision", { agent_announce: { run_id: "r1", kind: "part_created", title: "quad_arm", detail: "", ref: { part_id: "quad_arm" } } }, 3),
      message("r1", "assistant", "done", 5)]);
    render(<ConsolePane />);
    await frame();
    const card = document.querySelector('[data-announce="part_created"]') as HTMLElement;
    expect(card).toBeTruthy();
    expect(card.textContent).toContain("Created a part");
    expect(card.textContent).toContain("quad_arm");
    expect(document.querySelector("[data-kind=note]")).toBeNull();      // never a bare "decided" row
  });

  it("a_message_sent_while_the_lead_works_shows_queued_then_delivered", async () => {
    client();
    setRuns([run("r1", { created_at: 0, status: "running", role: "lead" })]);
    const queued = step("r1", "decision", { queued_message: { message_id: "msg_1", text: "paint it red", position: 1 } }, 3);
    setSteps([message("r1", "user", "build a drone", 1), queued]);
    render(<ConsolePane />);
    await frame();
    const line = () => document.querySelector('[data-queued-message="msg_1"]') as HTMLElement;
    expect(line().querySelector("[data-queued-chip]")?.textContent).toMatch(/^queued · delivered when the agent finishes this step/);
    setSteps([message("r1", "user", "build a drone", 1), queued, message("r1", "assistant", "on it", 4),
      step("r1", "decision", { queued_message_delivered: { message_id: "msg_1" } }, 5), message("r1", "user", "paint it red", 6)]);
    await frame();
    expect(line().getAttribute("data-queued-status")).toBe("delivered");
    expect(line().querySelector("[data-queued-chip]")?.textContent).toBe("delivered");
    expect([...document.querySelectorAll("[data-msg=user]")].filter((m) => m.textContent?.includes("paint it red"))).toHaveLength(1);
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

describe("feedback pins in the thread (U2a)", () => {
  it("shows the words and the pin, never the location block; the pin selects the part", async () => {
    client();
    // U3c's steps: the stored pin (a decision {feedback}) and the user's message carrying it
    const pin = { part_id: "mount_plate", part: "mount_plate", point: [7, 0, 2.5], face_id: null, nearby_feature: null,
      nearby_hole: "Ø4.2 hole, 0.9 mm away", text: "add a cable slot here" };
    setRuns([run("r1", { status: "done" })]);
    const said = message("r1", "user", "add a cable slot here", 1792869401);
    setSteps([step("r1", "decision", { feedback: pin }, 1792869400), { ...said, payload: { ...said.payload, feedback: pin } }]);
    selectedRunId.value = "r1";
    const { container } = render(<ConsolePane />);
    await frame();
    const msg = container.querySelector('[data-msg="user"]') as HTMLElement;
    expect(msg.textContent).toContain("add a cable slot here");
    expect(msg.textContent).not.toContain("untrusted_user_data");
    const chip = within(msg).getByRole("button", { name: /Comment 1 · on mount_plate · Ø4.2 hole, 0.9 mm away/ });
    fireEvent.click(chip);
    expect(selectedPartId.value).toBe("mount_plate");
  });
});

describe("one chat per build (P10 E24)", () => {
  const box = () => screen.getByLabelText("Command line") as HTMLTextAreaElement;
  const send = (text: string) => { fireEvent.input(box(), { target: { value: text } }); fireEvent.keyDown(box(), { key: "Enter" }); };
  beforeEach(() => { activeBuildId.value = "b1"; });
  afterEach(() => { activeBuildId.value = null; resetChat(); resetMessageActions(); });

  it("no New run anywhere: the first message starts the build's lead through the chat", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: true, queued: false, notes: [] }) });
    render(<ConsolePane />);
    await frame();
    expect(screen.queryByRole("button", { name: "Conversation options" })).toBeNull();
    expect(screen.queryByRole("button", { name: /New run/ })).toBeNull();
    expect(screen.getByTestId("target-chip").textContent).toBe("→ the build's agent (starts with your first message)");
    expect(document.querySelector("[data-chat-empty]")?.textContent).toMatch(/first message starts this build's agent/);
    send("design a two-finger gripper");
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "design a two-finger gripper"));
    expect(c.createRun).not.toHaveBeenCalled();
    await waitFor(() => expect(selectedRunId.value).toBe("lead1"));
  });

  it("a message to a busy lead is queued for it; a full queue (429) says so and keeps the text", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, queued: true, message_id: "m1", position: 1, notes: [] }) });
    setRuns([run("lead1", { status: "running", role: "lead", project_id: "b1" })]);
    selectedRunId.value = "lead1";
    render(<ConsolePane />);
    await frame();
    send("paint it red");
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "paint it red"));
    expect(selectedRunId.value).toBe("lead1");
    c.chat.mockRejectedValue(new CoreError("invalid_input", "the lead agent already has 20 messages waiting", { status: 429 }));
    send("one more");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/full queue of messages waiting/));
    expect(box().value).toBe("one more");
  });

  it("parts-directory chips ride along with the message", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: true, notes: [] }) });
    addCatalogChip("motor_2207_class", "2207 brushless motor class");
    render(<ConsolePane />);
    await frame();
    send("design a motor mount");
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "design a motor mount\n\nParts directory items to use (read each with catalog_get): motor_2207_class"));
    await waitFor(() => expect(agentChips.value).toEqual([]));
  });

  it("a sub-agent's thread is read-only until you choose to message it", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, notes: [] }) });
    setRuns([run("lead1", { role: "lead", project_id: "b1", status: "done" }), run("sub1", { role: "sub", parent_id: "lead1", project_id: "b1", agent: "verifier", status: "done" })]);
    selectedRunId.value = "sub1";
    render(<ConsolePane />);
    await frame();
    expect(document.querySelector("[data-viewing-sub]")?.textContent).toContain("your message goes to the lead");
    send("for the lead");
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "for the lead"));
    expect(c.message).not.toHaveBeenCalled();
    selectedRunId.value = "sub1";
    await frame();
    fireEvent.click(screen.getByTestId("message-this-agent"));
    await frame();
    expect(screen.getByTestId("back-to-lead")).toBeTruthy();
    send("check the hinge again");
    await waitFor(() => expect(c.message).toHaveBeenCalledWith("sub1", "check the hinge again"));
  });

  it("Edit puts your words back; sending them is a new turn and marks the old message edited", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, notes: [] }) });
    setRuns([run("lead1", { role: "lead", project_id: "b1", status: "done" })]);
    setSteps([message("lead1", "user", "make it 5 mm", 1), message("lead1", "assistant", "done", 2)]);
    selectedRunId.value = "lead1";
    render(<ConsolePane />);
    await frame();
    fireEvent.click(screen.getByRole("button", { name: "Edit and resend" }));
    await frame();
    expect(box().value).toBe("make it 5 mm");
    expect(screen.getByTestId("editing-chip")).toBeTruthy();
    fireEvent.input(box(), { target: { value: "make it 6 mm" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "make it 6 mm"));
    await waitFor(() => expect(document.querySelector("[data-msg=user][data-edited]")?.textContent).toContain("edited"));
    expect(screen.queryByTestId("editing-chip")).toBeNull();
  });

  it("an error card is plain wrapped text with the cause, what to do and Retry (the same words again)", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, notes: [] }) });
    const ssl = "Claude Code: API Error: Unable to connect to API due to SSL error (UNABLE_TO_GET_ISSUER_CERT_LOCALLY)";
    setRuns([run("lead1", { role: "lead", project_id: "b1", status: "failed", provider: "claude-code" })]);
    setSteps([
      message("lead1", "user", "add a wrist joint", 1),
      message("lead1", "assistant", `API Error: ${ssl}`, 2),
      step("lead1", "error", { error: "tool_failed", message: ssl, source: "claude_code" }, 3),
    ]);
    selectedRunId.value = "lead1";
    render(<ConsolePane />);
    await frame();
    const err = document.querySelector("[data-error]") as HTMLElement;
    expect(err.getAttribute("data-error-kind")).toBe("tls");
    expect(err.querySelector("[data-error-message]")?.textContent).toBe(ssl);
    expect(err.querySelector("[data-error-hint]")?.textContent).toContain("NODE_EXTRA_CA_CERTS");
    expect(err.querySelector("em, strong")).toBeNull();
    // the echoed reply is plain text too: the code stays whole
    expect(document.querySelector("[data-plain-error]")?.textContent).toContain("UNABLE_TO_GET_ISSUER_CERT_LOCALLY");
    fireEvent.click(within(err).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "add a wrist joint"));
  });

  it("a queued message can be cancelled; a core without the route hides Cancel", async () => {
    const c = client({ cancelQueued: vi.fn().mockResolvedValue(true) });
    setRuns([run("lead1", { role: "lead", project_id: "b1", status: "running" })]);
    setSteps([
      message("lead1", "user", "build the base", 1),
      step("lead1", "decision", { queued_message: { message_id: "m1", text: "and a lid", position: 1 } }, 2),
      step("lead1", "decision", { queued_message: { message_id: "m2", text: "and a hinge", position: 2 } }, 3),
    ]);
    selectedRunId.value = "lead1";
    render(<ConsolePane />);
    await frame();
    fireEvent.click(document.querySelector('[data-cancel-queued="m1"]') as HTMLElement);
    await waitFor(() => expect(c.cancelQueued).toHaveBeenCalledWith("lead1", "m1"));
    await waitFor(() => expect(document.querySelector('[data-queued-message="m1"]')?.getAttribute("data-queued-status")).toBe("dropped"));
    expect(document.querySelector('[data-queued-message="m1"] [data-queued-chip]')?.textContent).toBe("cancelled");
    c.cancelQueued.mockResolvedValue(false);
    fireEvent.click(document.querySelector('[data-cancel-queued="m2"]') as HTMLElement);
    await waitFor(() => expect(document.querySelector("[data-cancel-queued]")).toBeNull());
    expect(document.querySelector('[data-queued-message="m2"]')?.getAttribute("data-queued-status")).toBe("queued");
  });
});

describe("Retry only where the run did not get past the error (review fix 1)", () => {
  const u = (key: string, text: string): ThreadItem => ({ kind: "user", key, text, ts: 1, stepIds: [] });
  const e = (key: string): ThreadItem => ({ kind: "error", key, code: "tool_failed", message: "boom", hint: null, ts: 2, stepIds: [] });
  const a = (key: string): ThreadItem => ({ kind: "agent", key, text: "recovered, done", ts: 3, live: false, stepIds: [] });
  const n = (key: string): ThreadItem => ({ kind: "note", key, text: "note", stepIds: [] });

  it("the last item (notes aside) offers the words before it", () => {
    expect(retryTextFor([u("u1", "make it"), e("e1"), n("n1")], "e1", null, "running")).toBe("make it");
  });
  it("an error the agent recovered from never offers Retry (no resend of a finished turn)", () => {
    expect(retryTextFor([u("u1", "make it"), e("e1"), a("a1")], "e1", null, "done")).toBeNull();
    expect(retryTextFor([u("u1", "make it"), e("e1"), a("a1")], "e1", null, "running")).toBeNull();
  });
  it("a failed / cancelled run offers it on its last error only", () => {
    const items = [u("u1", "make it"), e("e1"), a("a1"), e("e2"), a("a2")];
    expect(retryTextFor(items, "e2", null, "failed")).toBe("make it");
    expect(retryTextFor(items, "e1", null, "failed")).toBeNull();
    expect(retryTextFor(items, "e2", null, "cancelled")).toBe("make it");
    expect(retryTextFor(items, "e2", null, "done")).toBeNull();
  });
  it("nothing after a message of yours", () => {
    expect(retryTextFor([u("u1", "a"), e("e1"), u("u2", "b")], "e1", null, "failed")).toBeNull();
  });

  it("the dock shows no Retry on a recovered error", async () => {
    client();
    setRuns([run("r1", { status: "done" })]);
    setSteps([
      message("r1", "user", "thin the wall", 1),
      step("r1", "error", { error: "tool_failed", message: "build failed", hint: null }, 2),
      message("r1", "assistant", "Fixed it on the second try.", 3),
    ]);
    render(<ConsolePane />);
    await frame();
    const err = screen.getByRole("log", { name: "Conversation" }).querySelector("[data-error]") as HTMLElement;
    expect(err).toBeTruthy();
    expect(within(err).queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

describe("Edit never loses a draft (review minor 4)", () => {
  beforeEach(() => { activeBuildId.value = "b1"; });
  afterEach(() => { activeBuildId.value = null; resetChat(); resetMessageActions(); });
  const box = () => screen.getByLabelText("Command line") as HTMLTextAreaElement;

  it("keeps what you were typing: back on Cancel edit, and back after the edit is sent", async () => {
    const c = client({ chat: vi.fn().mockResolvedValue({ run_id: "lead1", role: "lead", new_lead: false, notes: [] }) });
    setRuns([run("lead1", { role: "lead", project_id: "b1", status: "done" })]);
    setSteps([message("lead1", "user", "make it 5 mm", 1), message("lead1", "assistant", "done", 2)]);
    selectedRunId.value = "lead1";
    render(<ConsolePane />);
    await frame();
    fireEvent.input(box(), { target: { value: "half a thought" } });
    fireEvent.click(screen.getByRole("button", { name: "Edit and resend" }));
    await frame();
    expect(box().value).toBe("make it 5 mm");
    expect(screen.getByTestId("editing-chip").textContent).toContain("your draft comes back after");
    fireEvent.click(screen.getByRole("button", { name: "Cancel edit" }));
    await frame();
    expect(box().value).toBe("half a thought");
    // again, and this time send the edit: the draft returns once it is sent
    fireEvent.click(screen.getByRole("button", { name: "Edit and resend" }));
    await frame();
    fireEvent.input(box(), { target: { value: "make it 6 mm" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(c.chat).toHaveBeenCalledWith("b1", "make it 6 mm"));
    await waitFor(() => expect(box().value).toBe("half a thought"));
  });
});
