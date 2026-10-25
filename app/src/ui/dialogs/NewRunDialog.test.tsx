import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import type { AgentInfo, ModelsInfo, PartSummary } from "../../api/types";
import { actions, bindClient, newRunOpen, parts, resetStore, selectedRunId } from "../../state/store";
import { closeProfile, profileOpen, profileSection } from "../profile/open";
import { NewRunDialog } from "./NewRunDialog";

const agents: AgentInfo[] = [
  { name: "designer", description: "Main CAD agent.", model: "inherit" },
  { name: "verifier", description: "Skeptical checker.", model: "openai/gpt-x" },
];
const models: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [{ vendor: "anthropic", key: "env" }, { vendor: "openai", key: "none" }, { vendor: "ollama", key: "not_needed" }],
};

const client = { agents: vi.fn(), models: vi.fn(), createRun: vi.fn(), getProfile: vi.fn() };

beforeEach(() => {
  client.agents.mockReset().mockResolvedValue(agents);
  client.models.mockReset().mockResolvedValue(models);
  client.createRun.mockReset().mockResolvedValue({ run_id: "newrun" });
  client.getProfile.mockReset().mockRejectedValue(new Error("no profile route"));
  bindClient(client as unknown as CoreClient);
  parts.value = [{ part_id: "mount_plate", vars: {}, metrics: null, mtime: 0 } as PartSummary];
});
afterEach(() => { cleanup(); resetStore(); closeProfile(); });

async function open() {
  render(<NewRunDialog />);
  actions.openNewRun();
  await screen.findByRole("dialog");
  await screen.findByRole("option", { name: "designer" });
}

describe("NewRunDialog", () => {
  it("start_is_a_neutral_ink_button_never_blue", async () => {
    // blue is only for what needs the human (Merge/Approve), not for starting a run
    await open();
    const start = screen.getByRole("button", { name: "Start" });
    expect(start.className).toContain("btn-ink");
    expect(start.className).not.toContain("btn-primary");
  });

  it("button_fills_use_palette_tokens_not_raw_black_or_white", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const css = readFileSync(resolve(import.meta.dirname, "../../styles/base.css"), "utf8");
    for (const sel of [".btn-primary", ".btn-ink"]) {
      const rule = css.split("\n").find((l) => l.startsWith(`${sel} {`)) ?? "";
      expect(rule).not.toBe("");
      expect(rule).not.toMatch(/#fff\b|#000\b|#ffffff|#000000|\bwhite\b|\bblack\b/i);
    }
  });

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

  it("lists_the_users_claude_code_with_its_models", async () => {
    client.models.mockResolvedValue({ ...models, vendors: [...models.vendors, { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] }] });
    await open();
    expect(within("Vendor")).toEqual(["anthropic", "openai · no API key", "ollama", "Claude Code (your account)"]);
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "claude-code" } });
    const model = screen.getByLabelText("Model") as HTMLSelectElement;
    expect(model.tagName).toBe("SELECT");
    expect([...model.options].map((o) => o.value)).toEqual(["sonnet", "opus"]);
    expect(model.value).toBe("sonnet");
    fireEvent.change(model, { target: { value: "opus" } });
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "stiffen the upright" } });
    fireEvent.click(screen.getByText("Start"));
    await waitFor(() => expect(client.createRun).toHaveBeenCalledTimes(1));
    expect(client.createRun.mock.calls[0][0].model_ref).toBe("claude-code/opus");
  });

  it.each([
    ["signed_out", "signed out"],
    ["not_installed", "not installed"],
  ] as const)("claude_code_%s_is_disabled_and_points_to_profile", async (key, words) => {
    client.agents.mockResolvedValue([{ name: "designer", description: "Main CAD agent.", model: "claude-code/sonnet" }]);
    client.models.mockResolvedValue({ ...models, vendors: [...models.vendors, { vendor: "claude-code", key, models: ["sonnet", "opus"] }] });
    await open();
    const opt = screen.getByRole("option", { name: /Claude Code \(your account\)/ }) as HTMLOptionElement;
    expect(opt.disabled).toBe(true);
    expect(opt.textContent).toContain(words);
    expect(opt.textContent).toContain("Sign in in Profile");
    // prefilled from the agent: Start is blocked, the hint links to Profile
    fireEvent.input(screen.getByLabelText("Task"), { target: { value: "go" } });
    expect((screen.getByText("Start") as HTMLButtonElement).disabled).toBe(true);
    const hint = screen.getByText(new RegExp(`Claude Code \\(your account\\) is ${words}`), { selector: "[data-hint] *" });
    expect(hint).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sign in in Profile" }));
    expect(newRunOpen.value).toBe(false);
    expect(profileOpen.value).toBe(true);
    expect(profileSection.value).toBe("models");
    expect(client.createRun).not.toHaveBeenCalled();
  });

  it("prefills_the_profile_default_model_when_the_agent_inherits", async () => {
    client.getProfile.mockResolvedValue({ default_model: "claude-code/haiku" });
    client.models.mockResolvedValue({ ...models, vendors: [...models.vendors, { vendor: "claude-code", key: "signed_in", models: ["sonnet", "haiku"] }] });
    await open();
    await waitFor(() => expect((screen.getByLabelText("Vendor") as HTMLSelectElement).value).toBe("claude-code"));
    expect((screen.getByLabelText("Model") as HTMLSelectElement).value).toBe("haiku");
    // an agent's own model still wins
    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "verifier" } });
    expect((screen.getByLabelText("Vendor") as HTMLSelectElement).value).toBe("openai");
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
