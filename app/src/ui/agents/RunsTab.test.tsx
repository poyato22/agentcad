import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../test/fake-core/server";
import { CoreClient } from "../../api/client";
import type { AgentRun, Approval, MisesEvent } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { bindClient, resetStore, runs, selectedRunId } from "../../state/store";
import { RunsTab, waitStopped } from "./RunsTab";

let core: FakeCore;

function seed(over: Partial<AgentRun> & { id: string }): void {
  const run: AgentRun = {
    parent_id: null, agent: "designer", provider: "fake", model: "fake-model", task: `stiffen bracket ${over.id}`,
    status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1_000_000, updated_at: 1_000_000, owner_pid: null, external: false, session_ref: null, ...over,
  };
  core.state.runs.set(run.id, run);
}

const row = (id: string) => document.querySelector(`[data-run-row="${id}"]`) as HTMLElement;
const twice = (el: HTMLElement, label: string, confirm: string) => {
  fireEvent.click(within(el).getByRole("button", { name: label }));
  fireEvent.click(within(el).getByRole("button", { name: confirm }));
};

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: "base" });
  core.state.runs.clear();
  core.state.approvals.clear();
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});
afterEach(async () => { cleanup(); resetStore(); resetActivity(); await core.stop(); });

describe("RunsTab", () => {
  it("renders the orchestrator → worker tree and folds it", async () => {
    seed({ id: "orch", agent: "orchestrator", task: "build drone frame" });
    seed({ id: "w1", parent_id: "orch", agent: "designer", task: "design arm" });
    seed({ id: "w2", parent_id: "orch", agent: "verifier", task: "check arm" });
    render(<RunsTab />);
    await waitFor(() => expect(row("orch")).toBeTruthy());
    expect(row("orch").getAttribute("aria-level")).toBe("1");
    expect(row("w1").getAttribute("aria-level")).toBe("2");
    expect(row("orch").getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(within(row("orch")).getByRole("button", { name: "Hide 2 workers" }));
    expect(row("w1")).toBeNull();
    expect(row("orch").getAttribute("aria-expanded")).toBe("false");
  });

  it("shows live activity on a row", async () => {
    seed({ id: "r1", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    recordActivity({ type: "agent_activity", run_id: "r1", ts: 0, data: { state: "tool", detail: "measuring arm", since: 1 } });
    await waitFor(() => expect(row("r1").querySelector("[data-row-activity]")?.textContent).toBe("working · measuring arm"));
    recordActivity({ type: "agent_activity", run_id: "r1", ts: 0, data: { state: "waiting_approval", detail: "", since: 2 } });
    await waitFor(() => expect(row("r1").querySelector("[data-row-activity]")?.classList.contains("needs-you")).toBe(true));
  });

  it("a delete refused because the run is working says so, and Cancel and delete removes it", async () => {
    seed({ id: "r1", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal]")).toBeTruthy());
    expect(row("r1").querySelector("[data-refusal]")?.textContent).toContain("This run is still working. Stop it first, or use Cancel and delete.");
    expect(core.state.runs.has("r1")).toBe(true);
    twice(row("r1"), "Cancel and delete", "Stop it and delete?");
    await waitFor(() => expect(core.state.runs.has("r1")).toBe(false));
    expect(core.state.calls.some((c) => c.method === "POST" && c.path === "/runs/r1/cancel")).toBe(true);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Cancelled and deleted"));
  });

  it("names the busy worker that blocks an orchestrator's delete", async () => {
    seed({ id: "orch", agent: "orchestrator", task: "build drone frame" });
    seed({ id: "w1", parent_id: "orch", status: "running", task: "design arm" });
    render(<RunsTab />);
    await waitFor(() => expect(row("orch")).toBeTruthy());
    twice(row("orch"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("orch").querySelector("[data-refusal=running]")).toBeTruthy());
    expect(row("orch").querySelector("[data-refusal]")?.textContent).toContain("A worker of this run is still working (designer · design arm)");
  });

  it("a blocking worker that waits for approval is named as waiting, not working", async () => {
    seed({ id: "orch", agent: "orchestrator", task: "build drone frame" });
    seed({ id: "w1", parent_id: "orch", status: "running", task: "design arm" });
    render(<RunsTab />);
    await waitFor(() => expect(row("orch")).toBeTruthy());
    recordActivity({ type: "agent_activity", run_id: "w1", ts: 0, data: { state: "waiting_approval", detail: "", since: 1 } });
    twice(row("orch"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("orch").querySelector("[data-refusal]")).toBeTruthy());
    const text = row("orch").querySelector("[data-refusal]")?.textContent ?? "";
    expect(text).toContain("A worker of this run is waiting for your approval (designer · design arm)");
    expect(text).toContain("rejected, never applied");
    expect(text).not.toContain("still working");
  });

  it("a run awaiting approval is refused in plain words", async () => {
    seed({ id: "r1" });
    const ap: Approval = {
      id: "a1", run_id: "r1", action: "merge", payload: {}, payload_hash: "h", microversion: null, deltas: {},
      status: "pending", expires_at: 9e12, decided_by: null, decided_at: null, edited_payload: null,
    };
    core.state.approvals.set("a1", ap);
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal=pending_approval]")).toBeTruthy());
    expect(row("r1").textContent).toContain("waiting for your approval");
    expect(core.state.approvals.get("a1")?.status).toBe("pending");   // never decided by a delete
  });

  it("deletes a finished run after a confirm step", async () => {
    seed({ id: "r1" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Delete" }));
    expect(core.state.runs.has("r1")).toBe(true);                     // one press only arms it
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(row("r1")).toBeNull());
    expect(core.state.runs.has("r1")).toBe(false);
  });

  it("bulk-deletes the selected runs and reports the ones kept", async () => {
    seed({ id: "a" });
    seed({ id: "b" });
    seed({ id: "c", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("a")).toBeTruthy());
    for (const id of ["a", "b", "c"]) fireEvent.click(within(row(id)).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 selected" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 runs?" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Deleted 2 runs. 1 couldn't be deleted yet"));
    expect([...core.state.runs.keys()]).toEqual(["c"]);
    expect(row("c").querySelector("[data-refusal=running]")).toBeTruthy();
  });

  it("bulk delete carries on past a run deleted elsewhere (404) and reports it", async () => {
    seed({ id: "a" });
    seed({ id: "b" });
    seed({ id: "c" });
    render(<RunsTab />);
    await waitFor(() => expect(row("a")).toBeTruthy());
    for (const id of ["a", "b", "c"]) fireEvent.click(within(row(id)).getByRole("checkbox"));
    core.state.runs.delete("a");                                      // gone before the delete runs
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 selected" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 runs?" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Deleted 2 runs."));
    expect(screen.getByRole("status").textContent).toContain("1 failed:");
    expect(core.state.runs.size).toBe(0);
  });

  it("clean up lists the dry run, then deletes exactly those runs and reports any kept", async () => {
    seed({ id: "old1", status: "done" });
    seed({ id: "old2", status: "failed" });
    seed({ id: "live", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("old1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Clean up…" }));
    const panel = document.querySelector("[data-cleanup]") as HTMLElement;
    fireEvent.click(within(panel).getByRole("button", { name: "Check" }));
    await waitFor(() => expect(panel.querySelector("[data-cleanup-preview]")?.textContent).toContain("2 runs would be deleted:"));
    const dry = core.state.calls.find((c) => c.path === "/runs/cleanup") as { body: { dry_run: boolean } };
    expect(dry.body.dry_run).toBe(true);
    expect(core.state.runs.size).toBe(3);                             // nothing removed by the check
    // while the user reads the list: a fourth run finishes (it was never shown) and one listed run
    // starts again
    seed({ id: "fresh", status: "done" });
    core.state.runs.set("old2", { ...core.state.runs.get("old2")!, status: "running" });
    fireEvent.click(within(panel).getByRole("button", { name: "Delete 2 runs" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Really delete 2?" }));
    await waitFor(() => expect(panel.querySelector("[data-cleanup-outcome]")?.textContent).toContain("Deleted 1 of 2."));
    expect(core.state.runs.has("old1")).toBe(false);
    expect(core.state.runs.has("fresh")).toBe(true);                  // never deleted: not in the preview
    expect(core.state.runs.has("old2")).toBe(true);                   // kept, with its reason
    expect(panel.querySelector('[data-kept="old2"]')?.textContent).toContain("This run is still working.");
    expect(core.state.calls.filter((c) => c.path === "/runs/cleanup")).toHaveLength(1);   // only the dry run
    expect(core.state.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/runs/old1", "/runs/old2"]);
  });

  it("Cancel and delete on a run awaiting approval rejects the change (never approves) and deletes", async () => {
    seed({ id: "r1", status: "running" });
    core.state.approvals.set("a1", {
      id: "a1", run_id: "r1", action: "merge", payload: {}, payload_hash: "h", microversion: null, deltas: {},
      status: "pending", expires_at: 9e12, decided_by: null, decided_at: null, edited_payload: null,
    });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal]")).toBeTruthy());
    twice(row("r1"), "Cancel and delete", "Stop it and delete?");
    await waitFor(() => expect(core.state.runs.has("r1")).toBe(false));
    expect(core.state.calls.some((c) => c.path.includes("/approvals/") && c.path.endsWith("/decide"))).toBe(false);
  });

  it("the approval is rejected by the cancel, never approved", async () => {
    seed({ id: "r1", status: "running" });
    core.state.approvals.set("a1", {
      id: "a1", run_id: "r1", action: "merge", payload: {}, payload_hash: "h", microversion: null, deltas: {},
      status: "pending", expires_at: 9e12, decided_by: null, decided_at: null, edited_payload: null,
    });
    const seen: string[] = [];
    const orig = core.state.approvals.set.bind(core.state.approvals);
    core.state.approvals.set = (k, v) => { seen.push(`${v.status}:${v.decided_by}`); return orig(k, v); };
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal]")).toBeTruthy());
    twice(row("r1"), "Cancel and delete", "Stop it and delete?");
    await waitFor(() => expect(seen).toContain("rejected:system:cancel"));
    expect(seen.some((s) => s.startsWith("approved"))).toBe(false);
  });

  it("Cancel and delete stops at once when an approval is still pending after the cancel", async () => {
    // a finished run whose card is still open: cancel can't clear it, so no 5-second spin
    seed({ id: "r1", status: "done" });
    core.state.approvals.set("a1", {
      id: "a1", run_id: "r1", action: "merge", payload: {}, payload_hash: "h", microversion: null, deltas: {},
      status: "pending", expires_at: 9e12, decided_by: null, decided_at: null, edited_payload: null,
    });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal=pending_approval]")).toBeTruthy());
    const t0 = Date.now();
    twice(row("r1"), "Cancel and delete", "Stop it and delete?");
    await waitFor(() => expect(document.querySelector(".am-error")?.textContent).toContain("still waiting for your approval"));
    expect(Date.now() - t0).toBeLessThan(1500);
    expect(core.state.calls.filter((c) => c.method === "DELETE").length).toBeLessThanOrEqual(2);
    expect(core.state.approvals.get("a1")?.status).toBe("pending");    // never decided by a delete
    expect(core.state.runs.has("r1")).toBe(true);
  });

  it("Esc cancels a rename (nothing is saved)", async () => {
    seed({ id: "r1", task: "stiffen bracket" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Rename" }));
    const input = within(row("r1")).getByRole("textbox", { name: "Run title" });
    fireEvent.input(input, { target: { value: "Discard me" } });
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.blur(input);                                           // Chromium fires blur as the input goes
    await new Promise((r) => setTimeout(r, 30));
    expect(core.state.calls.some((c) => c.method === "PATCH")).toBe(false);
    expect(core.state.runs.get("r1")?.title ?? null).toBe(null);
  });

  it("arrow keys move through the tree; → / ← open, close and go to the parent", async () => {
    seed({ id: "orch", agent: "orchestrator", task: "build drone frame", last_activity_at: 3 });
    seed({ id: "w1", parent_id: "orch", task: "design arm", last_activity_at: 2 });
    seed({ id: "solo", task: "check holes", last_activity_at: 1 });
    render(<RunsTab />);
    await waitFor(() => expect(row("orch")).toBeTruthy());
    expect(row("orch").tabIndex).toBe(0);                             // one tab stop: the first row
    expect(row("w1").tabIndex).toBe(-1);
    row("orch").focus();
    fireEvent.keyDown(row("orch"), { key: "ArrowDown" });
    await waitFor(() => expect(document.activeElement).toBe(row("w1")));
    expect(row("w1").tabIndex).toBe(0);
    fireEvent.keyDown(row("w1"), { key: "ArrowLeft" });               // a worker: ← goes to its parent
    await waitFor(() => expect(document.activeElement).toBe(row("orch")));
    fireEvent.keyDown(row("orch"), { key: "ArrowLeft" });             // an open orchestrator: ← closes it
    await waitFor(() => expect(row("w1")).toBeNull());
    fireEvent.keyDown(row("orch"), { key: "ArrowRight" });            // → opens it again
    await waitFor(() => expect(row("w1")).toBeTruthy());
    fireEvent.keyDown(row("orch"), { key: "End" });
    await waitFor(() => expect(document.activeElement).toBe(row("solo")));
    fireEvent.keyDown(row("solo"), { key: " " });                     // Space selects
    expect(row("solo").getAttribute("aria-selected")).toBe("true");
  });

  it("renames, pins and archives a run", async () => {
    seed({ id: "r1" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Rename" }));
    const input = within(row("r1")).getByRole("textbox", { name: "Run title" });
    fireEvent.input(input, { target: { value: "Upright v2" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(core.state.runs.get("r1")?.title).toBe("Upright v2"));
    await waitFor(() => expect(within(row("r1")).getByRole("button", { name: "Upright v2" })).toBeTruthy());
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(core.state.runs.get("r1")?.pinned).toBe(true));
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(core.state.runs.get("r1")?.archived).toBe(true));
    await waitFor(() => expect(row("r1")).toBeNull());                 // archived runs hide by default
  });
});

describe("RunsTab keeps the app's runs in step (C-5, C-6, C-8)", () => {
  const known = (id: string, over: Partial<AgentRun> = {}) => {
    const r = core.state.runs.get(id) as AgentRun;
    runs.value = new Map([...runs.value, [id, { ...r, ...over }]]);
  };

  it("a deleted run leaves the app's run list and the selection", async () => {
    seed({ id: "r1" });
    known("r1");
    selectedRunId.value = "r1";
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(runs.value.has("r1")).toBe(false));
    expect(selectedRunId.value).toBe(null);
  });

  it("a rename shows in the app's run list at once", async () => {
    seed({ id: "r1" });
    known("r1");
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Rename" }));
    const input = within(row("r1")).getByRole("textbox", { name: "Run title" });
    fireEvent.input(input, { target: { value: "QA renamed run" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(runs.value.get("r1")?.title).toBe("QA renamed run"));
  });

  it("waitStopped resolves on the run's run_finished, before the poll says so", async () => {
    let fire: ((e: MisesEvent) => void) | null = null;
    const subscribe = (cb: (e: MisesEvent) => void) => { fire = cb; return () => { fire = null; }; };
    const status = vi.fn(async () => "running");
    const p = waitStopped(["r1"], status, { ms: 5000, pollMs: 2000, subscribe });
    await new Promise((r) => setTimeout(r, 10));
    (fire as unknown as (e: MisesEvent) => void)({ type: "run_finished", run_id: "r1", data: { status: "cancelled" }, ts: 1 });
    await expect(p).resolves.toBe(true);
    expect(fire).toBe(null); // unsubscribed
  });

  it("waitStopped gives up after its bound and treats a 404 as stopped", async () => {
    const subscribe = () => () => {};
    await expect(waitStopped(["a"], async () => "running", { ms: 60, pollMs: 10, subscribe })).resolves.toBe(false);
    await expect(waitStopped(["a", "b"], async (id) => (id === "a" ? null : "cancelled"), { ms: 60, pollMs: 10, subscribe })).resolves.toBe(true);
  });

  it("a stale 'still working' refusal clears once the run has stopped", async () => {
    seed({ id: "r1", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("r1")).toBeTruthy());
    twice(row("r1"), "Delete", "Confirm delete");
    await waitFor(() => expect(row("r1").querySelector("[data-refusal]")).toBeTruthy());
    core.state.runs.set("r1", { ...(core.state.runs.get("r1") as AgentRun), status: "cancelled" });
    fireEvent.click(within(row("r1")).getByRole("button", { name: "Pin" }));   // any reload
    await waitFor(() => expect(row("r1").querySelector("[data-refusal]")).toBeNull());
  });
});
