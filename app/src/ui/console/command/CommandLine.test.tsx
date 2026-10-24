import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { actions, bindClient, newRunOpen, resetStore, runs, selectedRunId } from "../../../state/store";
import { CommandLine } from "./CommandLine";

function run(id: string, agent: string, task: string, status: AgentRun["status"] = "done"): AgentRun {
  return { id, parent_id: null, agent, provider: "fake", model: "m", task, status, branch: null, onshape_calls: 0,
    input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1000, updated_at: 1000 };
}

const client = { message: vi.fn(), cancel: vi.fn(), cancelAll: vi.fn() };

beforeEach(() => {
  client.message.mockReset().mockResolvedValue({ ok: true });
  client.cancel.mockReset().mockResolvedValue({ ok: true });
  client.cancelAll.mockReset().mockResolvedValue({ cancelled: [] });
  bindClient(client as unknown as CoreClient);
  runs.value = new Map([
    ["r1", run("r1", "designer", "stiffen upright", "done")],
    ["r2", run("r2", "planner", "plan the bracket", "running")],
    ["r3", run("r3", "verifier", "check mass", "done")],
    ["r4", { ...run("r4", "claude", "external work", "done"), provider: "acp" }],
  ]);
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
    await waitFor(() => expect(client.message).toHaveBeenCalledTimes(2));
    const ids = client.message.mock.calls.map((c) => c[0]).sort();
    expect(ids).toEqual(["r1", "r3"]);
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
    await Promise.resolve();
    expect(client.message).not.toHaveBeenCalled();
    expect(box().value).toBe("hi\nthere");
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

  it("slash_new_opens_dialog_and_follow_filter_work", async () => {
    render(<CommandLine />);
    type("/new");
    enter();
    await waitFor(() => expect(newRunOpen.value).toBe(true));
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

  it("no_selection_explains", async () => {
    selectedRunId.value = null;
    render(<CommandLine />);
    type("hello");
    enter();
    expect((await screen.findByRole("alert")).textContent).toContain("select a run");
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

  it("cmd_k_focuses", () => {
    render(<CommandLine />);
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: "k", code: "KeyK", metaKey: true });
    expect(document.activeElement).toBe(box());
  });
});
