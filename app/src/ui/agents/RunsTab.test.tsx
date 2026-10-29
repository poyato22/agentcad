import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { startFakeCore, type FakeCore } from "../../../test/fake-core/server";
import { CoreClient } from "../../api/client";
import type { AgentRun, Approval } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { bindClient, resetStore } from "../../state/store";
import { RunsTab } from "./RunsTab";

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
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Deleted 2. 1 couldn't be deleted yet"));
    expect([...core.state.runs.keys()]).toEqual(["c"]);
    expect(row("c").querySelector("[data-refusal=running]")).toBeTruthy();
  });

  it("clean up shows the dry-run count before deleting", async () => {
    seed({ id: "old1", status: "done" });
    seed({ id: "old2", status: "failed" });
    seed({ id: "live", status: "running" });
    render(<RunsTab />);
    await waitFor(() => expect(row("old1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Clean up…" }));
    const panel = document.querySelector("[data-cleanup]") as HTMLElement;
    fireEvent.click(within(panel).getByRole("button", { name: "Check" }));
    await waitFor(() => expect(panel.querySelector("[data-cleanup-preview]")?.textContent).toContain("2 runs would be deleted."));
    const dry = core.state.calls.find((c) => c.path === "/runs/cleanup") as { body: { dry_run: boolean } };
    expect(dry.body.dry_run).toBe(true);
    expect(core.state.runs.size).toBe(3);                             // nothing removed by the check
    fireEvent.click(within(panel).getByRole("button", { name: "Delete 2 runs" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Really delete 2?" }));
    await waitFor(() => expect([...core.state.runs.keys()]).toEqual(["live"]));
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
