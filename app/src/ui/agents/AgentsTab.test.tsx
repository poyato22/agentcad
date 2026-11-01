import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { startFakeCore, type FakeCore } from "../../../test/fake-core/server";
import { CoreClient } from "../../api/client";
import { bindClient, resetStore } from "../../state/store";
import { AgentsTab } from "./AgentsTab";

let core: FakeCore;
const agentRow = (name: string) => document.querySelector(`[data-agent="${name}"]`) as HTMLElement;

beforeEach(async () => {
  core = await startFakeCore({ port: 0, token: "t", scenario: "base" });
  // a Plan 5 core marks every agent built-in or custom (GET /agents builtin/hidden/tools)
  core.state.agents = [
    { name: "designer", description: "Main CAD agent.", model: "inherit", tools: null, builtin: true },
    { name: "verifier", description: "Read-only checker.", model: "inherit", tools: ["mirror_measure", "design_review"], builtin: true },
  ];
  bindClient(new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false }));
});
afterEach(async () => { cleanup(); resetStore(); await core.stop(); });

describe("AgentsTab", () => {
  it("lists built-ins (hide only) apart from custom agents", async () => {
    render(<AgentsTab />);
    await waitFor(() => expect(agentRow("designer")).toBeTruthy());
    expect(within(agentRow("designer")).queryByRole("button", { name: /^Delete/ })).toBeNull();
    expect(within(agentRow("designer")).getByRole("button", { name: "Hide" })).toBeTruthy();
    expect(screen.getByText(/None yet/)).toBeTruthy();
  });

  it("hides and shows a built-in via the profile", async () => {
    render(<AgentsTab />);
    await waitFor(() => expect(agentRow("verifier")).toBeTruthy());
    fireEvent.click(within(agentRow("verifier")).getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(agentRow("verifier").hasAttribute("data-hidden")).toBe(true));
    const put = core.state.calls.find((c) => c.method === "PUT" && c.path === "/profile") as { body: { hidden_agents: string[] } };
    expect(put.body).toEqual({ hidden_agents: ["verifier"] });
    fireEvent.click(within(agentRow("verifier")).getByRole("button", { name: "Show" }));
    await waitFor(() => expect(agentRow("verifier").hasAttribute("data-hidden")).toBe(false));
  });

  it("creates a custom agent with a model and a tools subset", async () => {
    render(<AgentsTab />);
    await waitFor(() => expect(agentRow("designer")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));
    const form = screen.getByRole("form", { name: "New agent" });
    const create = within(form).getByRole("button", { name: "Create agent" }) as HTMLButtonElement;
    fireEvent.input(within(form).getByPlaceholderText("e.g. rib-checker"), { target: { value: "Bad Name" } });
    expect(within(form).getByText(/Use 2–41 lowercase letters/)).toBeTruthy();
    fireEvent.input(within(form).getByPlaceholderText("e.g. rib-checker"), { target: { value: "rib-checker" } });
    fireEvent.input(within(form).getByPlaceholderText("One line: what this agent is for"), { target: { value: "Checks rib spacing" } });
    expect(create.disabled).toBe(true);                               // instructions are required
    fireEvent.input(within(form).getByPlaceholderText("How this agent should work"), { target: { value: "Measure every rib." } });
    fireEvent.click(within(form).getByRole("radio", { name: /Only these/ }));
    expect(create.disabled).toBe(true);                               // at least one tool when restricted
    fireEvent.click(within(form).getByRole("checkbox", { name: "mirror_measure" }));
    expect(create.disabled).toBe(false);
    fireEvent.click(create);
    await waitFor(() => expect(agentRow("rib-checker")).toBeTruthy());
    const post = core.state.calls.find((c) => c.method === "POST" && c.path === "/agents") as { body: Record<string, unknown> };
    expect(post.body).toEqual({ name: "rib-checker", description: "Checks rib spacing", model: "inherit", tools: ["mirror_measure"], body: "Measure every rib." });
  });

  it("edit pre-fills the current instructions; changing only the description keeps them", async () => {
    await new CoreClient({ baseUrl: core.url, wsUrl: "", token: "t", owned: false })
      .createAgent({ name: "ribber", description: "adds ribs", model: "inherit", tools: null, body: "Add a rib every 10 mm." });
    render(<AgentsTab />);
    await waitFor(() => expect(agentRow("ribber")).toBeTruthy());
    fireEvent.click(within(agentRow("ribber")).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit ribber" });
    const body = within(form).getByRole("textbox", { name: "Instructions" }) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toBe("Add a rib every 10 mm."));
    fireEvent.input(within(form).getByPlaceholderText("One line: what this agent is for"), { target: { value: "adds ribs fast" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(core.state.calls.some((c) => c.method === "PUT" && c.path === "/agents/ribber")).toBe(true));
    const put = core.state.calls.find((c) => c.method === "PUT" && c.path === "/agents/ribber") as { body: Record<string, unknown> };
    expect(put.body).toMatchObject({ description: "adds ribs fast", body: "Add a rib every 10 mm." });
    const after = await (await fetch(`${core.url}/agents/ribber`, { headers: { Authorization: "Bearer t" } })).json() as { body: string };
    expect(after.body).toBe("Add a rib every 10 mm.");
  });

  it("deletes a custom agent after a confirm", async () => {
    core.state.agents.push({ name: "ribber", description: "adds ribs", model: "inherit", tools: null, builtin: false });
    render(<AgentsTab />);
    await waitFor(() => expect(agentRow("ribber")).toBeTruthy());
    fireEvent.click(within(agentRow("ribber")).getByRole("button", { name: "Delete ribber" }));
    expect(core.state.agents.some((a) => a.name === "ribber")).toBe(true);
    fireEvent.click(within(agentRow("ribber")).getByRole("button", { name: "Confirm delete ribber" }));
    await waitFor(() => expect(agentRow("ribber")).toBeNull());
    expect(core.state.agents.some((a) => a.name === "ribber")).toBe(false);
  });
});
