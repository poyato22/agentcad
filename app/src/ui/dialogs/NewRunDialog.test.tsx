import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import type { AgentInfo, ModelsInfo, PartSummary } from "../../api/types";
import { actions, bindClient, newRunOpen, parts, resetStore, selectedRunId } from "../../state/store";
import { NewRunDialog } from "./NewRunDialog";

const agents: AgentInfo[] = [
  { name: "designer", description: "Main CAD agent.", model: "inherit" },
  { name: "verifier", description: "Skeptical checker.", model: "openai/gpt-x" },
];
const models: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [{ vendor: "anthropic", key: "env" }, { vendor: "openai", key: "none" }, { vendor: "ollama", key: "not_needed" }],
};

const client = { agents: vi.fn(), models: vi.fn(), createRun: vi.fn() };

beforeEach(() => {
  client.agents.mockReset().mockResolvedValue(agents);
  client.models.mockReset().mockResolvedValue(models);
  client.createRun.mockReset().mockResolvedValue({ run_id: "newrun" });
  bindClient(client as unknown as CoreClient);
  parts.value = [{ part_id: "mount_plate", vars: {}, metrics: null, mtime: 0 } as PartSummary];
});
afterEach(() => { cleanup(); resetStore(); });

async function open() {
  render(<NewRunDialog />);
  actions.openNewRun();
  await screen.findByRole("dialog");
  await screen.findByRole("option", { name: "designer" });
}

describe("NewRunDialog", () => {
  it("dialog_lists_agents_and_vendors", async () => {
    await open();
    const agentOpts = within("Agent");
    expect(agentOpts).toEqual(["designer", "verifier"]);
    expect(screen.getByText("Main CAD agent.")).toBeTruthy();
    expect(within("Vendor")).toEqual(["anthropic", "openai · no API key", "ollama"]);
  });

  it("vendor_without_key_disabled_with_hint", async () => {
    await open();
    const openai = screen.getByRole("option", { name: /openai/ }) as HTMLOptionElement;
    expect(openai.disabled).toBe(true);
    expect(openai.textContent).toContain("mises keys set openai");
  });

  it("prefills_agent_model_or_default", async () => {
    await open();
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("claude-sonnet-5-5");
    expect((screen.getByLabelText("Vendor") as HTMLSelectElement).value).toBe("anthropic");
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "verifier" } });
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gpt-x");
    expect((screen.getByLabelText("Vendor") as HTMLSelectElement).value).toBe("openai");
    // that vendor has no key: Start is blocked and the hint is shown
    expect((screen.getByText("Start") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/mises keys set openai/, { selector: "[data-hint]" })).toBeTruthy();
  });

  it("double_click_on_start_creates_one_run", async () => {
    let finish: (v: { run_id: string }) => void = () => {};
    client.createRun.mockImplementation(() => new Promise((r) => { finish = r; }));
    await open();
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "stiffen the upright" } });
    const btn = screen.getByText("Start");
    fireEvent.click(btn);
    fireEvent.click(btn); // same tick: no re-render in between
    expect(client.createRun).toHaveBeenCalledTimes(1);
    finish({ run_id: "newrun" });
    await waitFor(() => expect(newRunOpen.value).toBe(false));
  });

  it("bad_model_400_keeps_dialog_open_with_core_message", async () => {
    client.createRun.mockRejectedValue(new CoreError("invalid_input", "unknown vendor or empty model in \"anthropic/\"", { status: 400, hint: "vendors: anthropic, openai, ollama" }));
    await open();
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "stiffen the upright" } });
    fireEvent.click(screen.getByText("Start"));
    const err = await screen.findByRole("alert");
    expect(err.textContent).toContain("unknown vendor or empty model");
    expect(err.textContent).toContain("vendors: anthropic, openai, ollama");
    expect(newRunOpen.value).toBe(true);
    expect((screen.getByText("Start") as HTMLButtonElement).disabled).toBe(false); // can retry after fixing
  });

  it("start_posts_exact_body_and_selects_run", async () => {
    await open();
    expect((screen.getByText("Start") as HTMLButtonElement).disabled).toBe(true); // task required
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "stiffen the upright" } });
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(client.createRun).toHaveBeenCalledTimes(1));
    expect(client.createRun).toHaveBeenCalledWith({
      agent: "designer", prompt: "stiffen the upright", model_ref: "anthropic/claude-sonnet-5-5", edit_scope: ["*"], read_scope: ["*"],
    });
    await waitFor(() => expect(newRunOpen.value).toBe(false));
    expect(selectedRunId.value).toBe("newrun");
  });

  it("double_click_start_posts_once", async () => {
    await open();
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "x y" } });
    fireEvent.click(screen.getByText("Start"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(newRunOpen.value).toBe(false));
    expect(client.createRun).toHaveBeenCalledTimes(1);
  });

  it("edit_scope_specific_parts", async () => {
    await open();
    fireEvent.click(screen.getByLabelText("all parts"));
    expect((screen.getByText("Start") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "go" } });
    fireEvent.click(screen.getByLabelText("Part · mount plate"));
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(client.createRun).toHaveBeenCalled());
    expect(client.createRun.mock.calls[0][0].edit_scope).toEqual(["mount_plate"]);
  });

  it("create_error_403_denied_inline", async () => {
    client.createRun.mockRejectedValue(new CoreError("denied", "no API key for anthropic", { status: 403, hint: "run `mises keys set anthropic`" }));
    await open();
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "go" } });
    fireEvent.click(screen.getByText("Start"));
    const err = await screen.findByRole("alert");
    expect(err.textContent).toBe("no API key for anthropic — run `mises keys set anthropic`");
    expect(newRunOpen.value).toBe(true);
    expect(selectedRunId.value).toBeNull();
  });

  it("esc_closes_from_anywhere", async () => {
    await open();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(newRunOpen.value).toBe(false));
  });

  it("esc_closes_and_cmd_n_opens", async () => {
    await open();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(newRunOpen.value).toBe(false));
    fireEvent.keyDown(window, { key: "n", code: "KeyN", metaKey: true });
    await waitFor(() => expect(newRunOpen.value).toBe(true));
  });
});

function within(label: string): string[] {
  const sel = screen.getByLabelText(label) as HTMLSelectElement;
  return [...sel.options].map((o) => o.textContent?.replace(/ — .*/, "").trim() ?? "");
}
