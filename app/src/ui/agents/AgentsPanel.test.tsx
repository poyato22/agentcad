import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startFakeCore, type FakeCore } from "../../../test/fake-core/server";
import { CoreClient } from "../../api/client";
import type { AgentRun } from "../../api/types";
import { recordActivity, resetActivity } from "../../state/activity";
import { bindClient, resetStore, selectedRunId } from "../../state/store";
import { AgentsPanel } from "./AgentsPanel";
import { agentManagerOpen } from "./manager";

let core: FakeCore;
let projectId: string;
let client: CoreClient;

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
const menu = (id: string, item: string) => {
  fireEvent.click(within(rowEl(id)).getByRole("button", { name: /^More for / }));
  fireEvent.click(within(rowEl(id)).getByRole("menuitem", { name: item }));
};

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: "base" });
  projectId = core.state.builds.builds[0].id;
  core.state.runs.clear();
  client = new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false });
  bindClient(client);
  agentManagerOpen.value = true;
});
afterEach(async () => { cleanup(); resetStore(); resetActivity(); agentManagerOpen.value = false; await core.stop(); });

describe("AgentsPanel (GET /projects/{id}/agents)", () => {
  it("shows the lead on top, its sub-agents indented by task title, then the build's other runs", async () => {
    seed({ id: "lead", role: "lead", task: "Build me a drone", created_at: 1_000_001 });
    seed({ id: "sub1", parent_id: "lead", role: "sub", agent: "designer", title: "Design motor mount", created_at: 1_000_002 });
    seed({ id: "other", agent: "verifier", task: "check the frame", status: "done" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    expect(within(rowEl("lead")).getByText("lead")).toBeTruthy();
    expect(rowEl("lead").textContent).toContain("Build me a drone");
    expect(rowEl("sub1").style.getPropertyValue("--depth")).toBe("1");
    expect(within(rowEl("sub1")).getByText("Design motor mount")).toBeTruthy();
    const others = screen.getByRole("list", { name: /^Other agents in / });
    expect(within(others).getByText("check the frame")).toBeTruthy();
  });

  it("a build whose agents can't be read says so instead of looking empty", async () => {
    seed({ id: "lead", role: "lead" });
    vi.spyOn(client, "projectAgents").mockRejectedValue(new Error("unexpected shape"));
    render(<AgentsPanel />);
    await waitFor(() => expect(document.querySelector("[data-build-error]")?.textContent).toMatch(/Couldn't read this build's agents: unexpected shape/));
    expect(screen.queryByText(/No agents yet/)).toBeNull();
  });

  it("shows live activity and the lead's waiting messages", async () => {
    seed({ id: "lead", role: "lead", routed: { agent: "engineer", reason: "system build", confidence: 0.85 } });
    await client.createRun({ prompt: "add landing gear", project_id: projectId });     // the lead is busy: queued
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    expect(within(rowEl("lead")).getByText("1 message waiting")).toBeTruthy();
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

  it("the ⋯ menu renames, pins and archives", async () => {
    seed({ id: "lead", role: "lead", status: "done" });
    seed({ id: "o1", agent: "verifier", status: "done", task: "old check" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    menu("lead", "Rename…");
    const box = within(rowEl("lead")).getByRole("textbox", { name: "Run title" });
    fireEvent.input(box, { target: { value: "Drone build" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(core.state.runs.get("lead")?.title).toBe("Drone build"));
    await waitFor(() => expect(rowEl("lead").textContent).toContain("Drone build"));
    menu("lead", "Pin");
    await waitFor(() => expect(core.state.runs.get("lead")?.pinned).toBe(true));
    menu("o1", "Archive");
    await waitFor(() => expect(core.state.runs.get("o1")?.archived).toBe(true));
    await waitFor(() => expect(rowEl("o1")).toBeNull());                 // archived others leave the simple view
  });

  it("Delete… asks once, then removes a finished sub-agent", async () => {
    seed({ id: "lead", role: "lead", status: "done" });
    seed({ id: "sub1", parent_id: "lead", role: "sub", agent: "designer", status: "done", title: "mount plate" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("sub1")).toBeTruthy());
    menu("sub1", "Delete…");
    fireEvent.click(within(rowEl("sub1")).getByRole("button", { name: "Delete mount plate" }));
    await waitFor(() => expect(rowEl("sub1")).toBeNull());
    expect(core.state.runs.has("sub1")).toBe(false);
    expect(screen.getByRole("status").textContent).toMatch(/Deleted designer · mount plate/);
  });

  it("a working run says so in plain words, then Cancel and delete removes it", async () => {
    seed({ id: "lead", role: "lead", status: "running", title: "Drone build" });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    menu("lead", "Delete…");
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Delete Drone build" }));
    await waitFor(() => expect(rowEl("lead").querySelector("[data-refusal=running]")?.textContent).toMatch(/still working/i));
    expect(core.state.runs.has("lead")).toBe(true);
    fireEvent.click(within(rowEl("lead")).getByRole("button", { name: "Cancel and delete" }));
    await waitFor(() => expect(rowEl("lead")).toBeNull());
    expect(core.state.runs.has("lead")).toBe(false);
  });

  it("Clean up finished… lists what would go before deleting", async () => {
    seed({ id: "lead", role: "lead", status: "done", updated_at: 1 });
    render(<AgentsPanel />);
    await waitFor(() => expect(rowEl("lead")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Clean up finished…" }));
    expect(screen.getByRole("group", { name: "Clean up finished runs" })).toBeTruthy();
  });
});
