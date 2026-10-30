import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { startFakeCore, type FakeCore } from "../../../test/fake-core/server";
import { CoreClient } from "../../api/client";
import type { AgentRun } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { bindClient, resetStore, selectedRunId } from "../../state/store";
import { agentManagerOpen } from "./manager";
import { AgentsPanel } from "./AgentsPanel";

let core: FakeCore;
let projectId: string;

function seed(over: Partial<AgentRun> & { id: string }): void {
  const run: AgentRun = {
    parent_id: null, agent: "engineer", provider: "fake", model: "m", task: `task ${over.id}`,
    status: "running", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0,
    created_at: 1_000_000, updated_at: 1_000_000, owner_pid: null, external: false, session_ref: null,
    project_id: projectId, ...over,
  };
  core.state.runs.set(run.id, run);
}

const rowEl = (id: string) => document.querySelector(`[data-agent-row="${id}"]`) as HTMLElement;

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: "base" });
  projectId = core.state.builds.builds[0].id;
  core.state.runs.clear();
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
  agentManagerOpen.value = true;
});
afterEach(async () => { cleanup(); resetStore(); resetActivity(); agentManagerOpen.value = false; await core.stop(); });

describe("AgentsPanel", () => {
  it("shows the lead on top and its sub-agents indented, each with its task title", async () => {
    seed({ id: "lead", role: "lead", status: "running", task: "build the robotic arm" });
    seed({ id: "sub1", parent_id: "lead", role: "sub", agent: "designer", task_title: "design the gripper" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    expect(within(rowEl("lead")).getByText("lead")).toBeTruthy();
    expect(rowEl("lead").style.getPropertyValue("--depth")).toBe("0");
    await waitFor(() => expect(rowEl("sub1")).toBeTruthy());
    expect(rowEl("sub1").style.getPropertyValue("--depth")).toBe("1");
    expect(within(rowEl("sub1")).getByText("design the gripper")).toBeTruthy();
  });

  it("shows live activity on a row", async () => {
    seed({ id: "lead", role: "lead", status: "running" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    recordActivity({ type: "agent_activity", run_id: "lead", ts: 0, data: { state: "tool", detail: "placing a bearing", since: 1 } });
    await waitFor(() => expect(rowEl("lead").querySelector("[data-row-activity]")?.textContent).toBe("working · placing a bearing"));
  });

  it("opening a row selects the run and closes the manager", async () => {
    seed({ id: "lead", role: "lead", status: "done" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: /^Open / }));
    expect(selectedRunId.value).toBe("lead");
    expect(agentManagerOpen.value).toBe(false);
  });

  it("deletes a finished sub with a two-step confirm", async () => {
    seed({ id: "lead", role: "lead", status: "done" });
    seed({ id: "sub1", parent_id: "lead", role: "sub", agent: "designer", status: "done", task_title: "mount plate" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("sub1")).toBeTruthy());
    fireEvent.click(within(rowEl("sub1")).getByRole("button", { name: "Delete designer" }));
    fireEvent.click(within(rowEl("sub1")).getByRole("button", { name: "Confirm delete designer" }));
    await waitFor(() => expect(rowEl("sub1")).toBeNull());
    expect(core.state.runs.has("sub1")).toBe(false);
  });

  it("a delete refused because the run is working says so", async () => {
    seed({ id: "lead", role: "lead", status: "running" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Delete engineer" }));
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Confirm delete engineer" }));
    await waitFor(() => expect(within(rowEl("lead")).getByRole("alert").textContent).toMatch(/still working/i));
    expect(core.state.runs.has("lead")).toBe(true);
  });

  it("cancel and delete stops a working run then removes it", async () => {
    seed({ id: "lead", role: "lead", status: "running" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Delete engineer" }));
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Confirm delete engineer" }));
    await waitFor(() => expect(within(rowEl("lead")).getByRole("button", { name: "Cancel and delete" })).toBeTruthy());
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Cancel and delete" }));
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Stop it and delete?" }));
    await waitFor(() => expect(rowEl("lead")).toBeNull());
    expect(core.state.runs.has("lead")).toBe(false);
  });
});
