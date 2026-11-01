import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { actions, beginCoreSession, bindClient, corePid, resetStore, runs, selectedRunId, sessionRuns } from "../../../state/store";
import { addCatalogChip, agentChips, clearAgentChips } from "../../../state/agentChips";
import { activeBuildId } from "../../../state/builds";
import { CommandLine } from "./CommandLine";

function run(id: string, agent: string, task: string, status: AgentRun["status"] = "done"): AgentRun {
  return { id, parent_id: null, agent, provider: "fake", model: "m", task, status, branch: null, onshape_calls: 0,
    input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1000, updated_at: 1000 };
}

// P10 E24: an older core without POST /projects/{id}/chat (null): the lead path is POST /runs
const client = { message: vi.fn(), cancel: vi.fn(), cancelAll: vi.fn(), chat: vi.fn() };

beforeEach(() => {
  client.message.mockReset().mockResolvedValue({ ok: true });
  client.cancel.mockReset().mockResolvedValue({ ok: true });
  client.cancelAll.mockReset().mockResolvedValue({ cancelled: [], failed: [] });
  client.chat.mockReset().mockResolvedValue(null);
  bindClient(client as unknown as CoreClient);
  runs.value = new Map([
    ["r1", run("r1", "designer", "stiffen upright", "done")],
    ["r2", run("r2", "planner", "plan the bracket", "running")],
    ["r3", run("r3", "verifier", "check mass", "done")],
    ["r4", { ...run("r4", "claude", "external work", "done"), external: true }],
    // an ACP provider name alone does not make a run external: the core says so with `external`
    ["r5", { ...run("r5", "reviewer", "acp named but own loop", "done"), provider: "acp", external: false }],
  ]);
  // started in this core session (seen by this UI): everything above
  sessionRuns.value = new Set(["r1", "r2", "r3", "r4", "r5"]);
  actions.selectRun("r1");
});
afterEach(() => { cleanup(); resetStore(); });

const box = () => screen.getByLabelText("Command line") as HTMLTextAreaElement;
function type(text: string) { fireEvent.input(box(), { target: { value: text } }); }
function enter(init: KeyboardEventInit = {}) { fireEvent.keyDown(box(), { key: "Enter", ...init }); }

describe("CommandLine", () => {
  it("enter_sends_message_to_selected", async () => {
    render(<CommandLine />);
    type("make it thicker");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r1", "make it thicker"));
    await waitFor(() => expect(box().value).toBe(""));
  });

  it("parts-directory chips ride along with a message to a running agent, then clear (M-6)", async () => {
    addCatalogChip("motor_2207", "2207 motor");
    render(<CommandLine />);
    expect(screen.getByTestId("cmd-agent-chips").textContent).toContain("2207 motor");
    type("use this motor");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r1", "use this motor\n\nParts directory items to use (read each with catalog_get): motor_2207"));
    await waitFor(() => expect(agentChips.value).toEqual([]));
    clearAgentChips();
  });

  it("shows_target_chip_for_selected_and_at", () => {
    render(<CommandLine />);
    expect(screen.getByTestId("target-chip").textContent).toBe("→ designer · stiffen upright");
    type("@verifier hello");
    expect(screen.getByTestId("target-chip").textContent).toBe("→ verifier · check mass");
    type("@all hello");
    expect(screen.getByTestId("target-chip").textContent).toBe("→ all idle runs");
  });

  it("at_target_routes_to_that_run", async () => {
    render(<CommandLine />);
    type("@verifier recheck");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r3", "recheck"));
  });

  it("shift_enter_does_not_send", () => {
    render(<CommandLine />);
    type("a");
    enter({ shiftKey: true });
    expect(client.message).not.toHaveBeenCalled();
  });

  it("at_all_skips_running_and_reports_count", async () => {
    render(<CommandLine />);
    type("@all wrap up");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledTimes(3));
    const ids = client.message.mock.calls.map((c) => c[0]).sort();
    expect(ids).toEqual(["r1", "r3", "r5"]);
    expect((await screen.findByTestId("cmd-info")).textContent).toBe("sent to 3 runs · skipped 1 still working · skipped 1 external");
  });

  it("at_all_only_resumes_done_runs_live_in_this_core_session", async () => {
    runs.value = new Map([
      ...runs.value,
      ["r6", run("r6", "designer", "abandoned try", "cancelled")],
      ["r7", run("r7", "verifier", "broken check", "failed")],
      ["r8", run("r8", "planner", "yesterday's plan", "done")], // done, but from an earlier core process
    ]);
    sessionRuns.value = new Set([...sessionRuns.value, "r6", "r7"]);
    render(<CommandLine />);
    type("@all wrap up");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledTimes(3));
    expect(client.message.mock.calls.map((c) => c[0]).sort()).toEqual(["r1", "r3", "r5"]);
    expect((await screen.findByTestId("cmd-info")).textContent).toBe(
      "sent to 3 runs · skipped 1 still working · skipped 2 stopped · skipped 1 external · skipped 1 from an earlier core session");
  });

  it("at_all_liveness_uses_the_core_pid_when_the_shell_knows_it", async () => {
    beginCoreSession(4242);
    expect(sessionRuns.value.size).toBe(0);
    runs.value = new Map([
      ["a", { ...run("a", "designer", "this core", "done"), owner_pid: 4242 }],
      ["b", { ...run("b", "verifier", "older core", "done"), owner_pid: 999 }],
      ["c", { ...run("c", "planner", "before owner tracking", "done"), owner_pid: null }],
    ]);
    expect(corePid.value).toBe(4242);
    render(<CommandLine />);
    type("@all go");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledTimes(1));
    expect(client.message).toHaveBeenCalledWith("a", "go");
    expect((await screen.findByTestId("cmd-info")).textContent).toBe("sent to 1 run · skipped 2 from an earlier core session");
  });

  it("at_all_includes_finished_claude_code_runs_the_core_can_resume", async () => {
    // Claude Code runs are external (the user's own claude drives them) but take messages: the
    // core continues the session, or resumes it with --resume after a restart
    beginCoreSession(4242);
    runs.value = new Map([
      ["cc1", { ...run("cc1", "designer", "own claude here", "done"), provider: "claude-code", external: true, owner_pid: 4242 }],
      ["cc2", { ...run("cc2", "verifier", "own claude earlier", "done"), provider: "claude-code", external: true, owner_pid: 999 }],
      ["cc3", { ...run("cc3", "planner", "own claude busy", "running"), provider: "claude-code", external: true, owner_pid: 4242 }],
      ["acp", { ...run("acp", "claude", "acp agent", "done"), provider: "acp", external: true, owner_pid: 4242 }],
    ]);
    render(<CommandLine />);
    type("@all go");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledTimes(2));
    expect(client.message.mock.calls.map((c) => c[0]).sort()).toEqual(["cc1", "cc2"]);
    expect((await screen.findByTestId("cmd-info")).textContent).toBe("sent to 2 runs · skipped 1 still working · skipped 1 external");
  });

  it("multiline_slash_is_sent_as_text_not_executed", async () => {
    render(<CommandLine />);
    type("/stopall\nrest");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r1", "/stopall\nrest"));
    expect(client.cancelAll).not.toHaveBeenCalled();
  });

  it("paste_with_newline_not_sent", async () => {
    render(<CommandLine />);
    fireEvent.paste(box(), { clipboardData: { getData: () => "hi\nthere" } });
    type("hi\nthere");
    // the browser inserts a pasted newline as text: no keydown Enter, so nothing is sent
    await new Promise((r) => setTimeout(r, 20));
    expect(client.message).not.toHaveBeenCalled();
    expect(client.cancelAll).not.toHaveBeenCalled();
    expect(box().value).toBe("hi\nthere");
    // Shift+Enter adds a line, never sends
    enter({ shiftKey: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(client.message).not.toHaveBeenCalled();
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r1", "hi\nthere"));
  });

  it("still_working_error_shows_hint", async () => {
    client.message.mockRejectedValue(new CoreError("invalid_input", "run is still working", { status: 400, hint: "wait or stop it" }));
    render(<CommandLine />);
    type("hello");
    enter();
    const err = await screen.findByRole("alert");
    expect(err.textContent).toBe("run is still working — wait or stop it");
    expect(box().value).toBe("hello"); // kept so it can be re-sent
  });

  it("unknown_target_error_clears_when_the_line_is_edited", async () => {
    render(<CommandLine />);
    type("@ghost hello");
    enter();
    expect((await screen.findByRole("alert")).textContent).toContain("@ghost");
    type("@designer hello");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("without_a_selection_targets_the_run_the_console_shows", async () => {
    actions.selectRun(null);
    render(<CommandLine />);
    // the console shows the newest running run when nothing is selected (console/derived.ts)
    expect(screen.getByTestId("target-chip").textContent).toMatch(/^→ planner · plan/);
    type("status?");
    enter();
    await waitFor(() => expect(client.message).toHaveBeenCalledWith("r2", "status?"));
  });

  it("slash_stopall_calls_cancel_all", async () => {
    render(<CommandLine />);
    type("/stopall");
    enter();
    await waitFor(() => expect(client.cancelAll).toHaveBeenCalledTimes(1));
  });

  it("slash_stop_cancels_selected", async () => {
    render(<CommandLine />);
    type("/stop");
    enter();
    await waitFor(() => expect(client.cancel).toHaveBeenCalledWith("r1"));
  });

  it("there is no /new (P10 E24: one chat per build); /follow and /filter work", async () => {
    render(<CommandLine />);
    type("/new");
    enter();
    expect((await screen.findByRole("alert")).textContent).toContain("unknown command /new");
    type("/follow off");
    enter();
    const { follow, filter } = await import("../../../state/store");
    await waitFor(() => expect(follow.value).toBe(false));
    type("/filter errors");
    enter();
    await waitFor(() => expect(filter.value.kind).toBe("errors"));
  });

  it("approve_is_rejected_and_never_requests", async () => {
    render(<CommandLine />);
    type("/approve");
    enter();
    expect((await screen.findByRole("alert")).textContent).toBe("approve on the card");
    expect(client.message).not.toHaveBeenCalled();
  });

  it("no build and no run: says to open a build (never 'select a run')", async () => {
    selectedRunId.value = null;
    runs.value = new Map(); // with runs, the console's fallback run is the target
    render(<CommandLine />);
    expect(screen.getByTestId("target-chip").textContent).not.toContain("no run selected");
    type("hello");
    enter();
    expect((await screen.findByRole("alert")).textContent).toContain("Open a build first");
  });

  it("in a build the composer always talks to its lead, whatever thread is shown", async () => {
    const lead = { ...run("L1", "engineer", "build a robot arm"), role: "lead" as const, project_id: "b1" };
    const sub = { ...run("S1", "verifier", "check the joints"), role: "sub" as const, project_id: "b1", parent_id: "L1" };
    runs.value = new Map<string, AgentRun>([["L1", lead], ["S1", sub]]);
    activeBuildId.value = "b1";
    onTestFinished(() => { activeBuildId.value = null; });
    actions.selectRun("S1");
    client.chat.mockResolvedValue({ run_id: "L1", role: "lead", new_lead: false, notes: [] });
    render(<CommandLine />);
    expect(screen.getByTestId("target-chip").textContent).toBe("→ engineer · build robot arm");
    type("go on");
    enter();
    await waitFor(() => expect(client.chat).toHaveBeenCalledWith("b1", "go on"));
    expect(client.message).not.toHaveBeenCalled();
    // the lead's thread shows your words
    await waitFor(() => expect(selectedRunId.value).toBe("L1"));
  });

  it("history_up_down", async () => {
    render(<CommandLine />);
    type("/help");
    enter();
    await waitFor(() => expect(box().value).toBe(""));
    type("/follow on");
    enter();
    await waitFor(() => expect(box().value).toBe(""));
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    await waitFor(() => expect(box().value).toBe("/follow on"));
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    await waitFor(() => expect(box().value).toBe("/help"));
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    await waitFor(() => expect(box().value).toBe("/follow on"));
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    await waitFor(() => expect(box().value).toBe(""));
  });

  // I2: after a core restart the core retires the lead it can't reach and starts a new one. The
  // console follows the new run (the message stays on screen) and says so in one line.
  it("a message to a retired lead follows the new lead the core started", async () => {
    const lead = { ...run("L1", "engineer", "build a robot arm"), role: "lead" as const, project_id: "b1", owner_pid: 111 };
    runs.value = new Map([["L1", lead]]);
    actions.selectRun("L1");
    corePid.value = 222;                          // a new core process: L1 belongs to the old one
    const c = { ...client, createRun: vi.fn().mockResolvedValue({ run_id: "L2", new_lead: true, routed: null, notes: [] }),
      getRun: vi.fn().mockResolvedValue({ ...lead, id: "L2", owner_pid: 222, status: "running" }) };
    bindClient(c as unknown as CoreClient);
    render(<CommandLine />);
    expect(screen.getByTestId("target-chip").textContent).toBe("→ new lead (the earlier one can't be reached)");
    type("add a gripper");
    enter();
    await waitFor(() => expect(c.createRun).toHaveBeenCalledWith({ prompt: "add a gripper", project_id: "b1" }));
    await waitFor(() => expect(selectedRunId.value).toBe("L2"));
    expect(c.message).not.toHaveBeenCalled();
    expect(screen.getByTestId("cmd-info").textContent).toContain("a new lead took over");
  });

  it("an archived lead still routes through the build, never /message (no 'unknown run')", async () => {
    const lead = { ...run("L1", "engineer", "build a robot arm"), role: "lead" as const, project_id: "b1", archived: true };
    runs.value = new Map([["L1", lead], ["L2", { ...lead, id: "L2", archived: false }]]);
    actions.selectRun("L1");
    const c = { ...client, createRun: vi.fn().mockResolvedValue({ run_id: "L2", new_lead: false, routed: null, notes: [] }), getRun: vi.fn() };
    bindClient(c as unknown as CoreClient);
    render(<CommandLine />);
    type("hello again");
    enter();
    await waitFor(() => expect(selectedRunId.value).toBe("L2"));
    expect(c.message).not.toHaveBeenCalled();
  });

  it("a run the restarted core no longer knows hands the message to its build's lead, never 'unknown run'", async () => {
    runs.value = new Map([["X1", { ...run("X1", "qa", "outside work"), external: false, project_id: "b1" }]]);
    actions.selectRun("X1");
    const c = { ...client, message: vi.fn().mockRejectedValue(new CoreError("not_found", "unknown run: X1", { status: 404 })),
      createRun: vi.fn().mockResolvedValue({ run_id: "L2", new_lead: false, routed: null, notes: [] }), getRun: vi.fn().mockResolvedValue(run("L2", "engineer", "x")) };
    bindClient(c as unknown as CoreClient);
    render(<CommandLine />);
    type("still there?");
    enter();
    await waitFor(() => expect(c.createRun).toHaveBeenCalledWith({ prompt: "still there?", project_id: "b1" }));
    await waitFor(() => expect(selectedRunId.value).toBe("L2"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("cmd-info").textContent).toContain("sent to the build's lead");
  });

  it("the same lead taking the message keeps the selection", async () => {
    const lead = { ...run("L1", "engineer", "build a robot arm"), role: "lead" as const, project_id: "b1" };
    runs.value = new Map([["L1", lead]]);
    actions.selectRun("L1");
    const c = { ...client, createRun: vi.fn().mockResolvedValue({ run_id: "L1", new_lead: false, routed: null, notes: [] }) };
    bindClient(c as unknown as CoreClient);
    render(<CommandLine />);
    expect(screen.getByTestId("target-chip").textContent).toBe("→ engineer · build robot arm");
    type("thicker");
    enter();
    await waitFor(() => expect(c.createRun).toHaveBeenCalled());
    expect(selectedRunId.value).toBe("L1");
    expect(screen.queryByTestId("cmd-info")).toBeNull();
  });

  it("cmd_k_focuses", () => {
    render(<CommandLine />);
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: "k", code: "KeyK", metaKey: true });
    expect(document.activeElement).toBe(box());
  });
});
