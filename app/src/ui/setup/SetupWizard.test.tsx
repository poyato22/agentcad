import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import type { ClaudeCodeStatus, KeyStatus, ModelsInfo, Profile, SetupState } from "../../api/types";
import { bindClient, resetStore } from "../../state/store";
import { closeSetupWizard, openSetupWizard } from "./gate";
import { SetupGate } from "./SetupGate";

const SECRET = "sk-ant-api03-SECRET-0123456789";

const fresh: SetupState = { complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"] };
const profile: Profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {}, setup_completed_at: null,
};
const keys: KeyStatus[] = ["anthropic", "openai", "gemini", "openrouter", "ollama", "lmstudio", "onshape"].map((vendor) => ({ vendor, present: false, source: "none" }));
const signedOut: ClaudeCodeStatus = { installed: true, version: "2.1.4", signed_in: false, auth_method: null };
const models: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [
    { vendor: "anthropic", key: "keychain" }, { vendor: "openai", key: "none" }, { vendor: "ollama", key: "not_needed" },
    { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] },
  ],
};

const client = {
  setupState: vi.fn(), getProfile: vi.fn(), updateProfile: vi.fn(), listKeys: vi.fn(), setKey: vi.fn(), setOnshapeKeys: vi.fn(),
  deleteKey: vi.fn(), testOnshape: vi.fn(), completeSetup: vi.fn(), claudeCodeStatus: vi.fn(), claudeCodeLogin: vi.fn(), models: vi.fn(),
};

beforeEach(() => {
  client.setupState.mockReset().mockResolvedValue(fresh);
  client.getProfile.mockReset().mockResolvedValue(profile);
  client.updateProfile.mockReset().mockImplementation(async (p: Partial<Profile>) => ({ ...profile, ...p }));
  client.listKeys.mockReset().mockResolvedValue(keys);
  client.setKey.mockReset().mockImplementation(async (vendor: string) => ({ vendor, present: true, source: "keychain" }));
  client.setOnshapeKeys.mockReset().mockResolvedValue({ vendor: "onshape", present: true, source: "keychain" });
  client.testOnshape.mockReset().mockResolvedValue({ ok: true, user_name: "Hartigan Deely" });
  client.completeSetup.mockReset().mockResolvedValue(undefined);
  client.claudeCodeStatus.mockReset().mockResolvedValue(signedOut);
  client.claudeCodeLogin.mockReset().mockResolvedValue(undefined);
  client.models.mockReset().mockResolvedValue(models);
  bindClient(client as unknown as CoreClient);
  try { sessionStorage.clear(); } catch { /* none */ }
});
afterEach(() => { cleanup(); closeSetupWizard(); resetStore(); vi.useRealTimers(); });

const wizard = () => screen.findByRole("dialog", { name: "Set up Mises" });
/** The serialized DOM (attributes included): a secret must not appear anywhere in it. */
const markup = () => new XMLSerializer().serializeToString(document.body);
const input = (name: string) => screen.getByLabelText(name, { selector: "input" }) as HTMLInputElement;

async function toModelStep() {
  render(<SetupGate />);
  await wizard();
  fireEvent.click(await screen.findByRole("button", { name: "Get started" }));
  await screen.findByRole("heading", { name: "Connect a model" });
}

describe("SetupGate", () => {
  it("shows_nothing_when_setup_is_complete", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: false, missing: ["onshape"] });
    const { container } = render(<SetupGate />);
    await waitFor(() => expect(client.setupState).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); });
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows_the_wizard_full_screen_when_incomplete", async () => {
    render(<SetupGate />);
    const dlg = await wizard();
    expect(dlg.getAttribute("aria-modal")).toBe("true");
    await screen.findByRole("heading", { name: "Welcome to Mises" });
    expect(dlg.textContent).toContain("Everything stays on this Mac");
  });

  it("can_be_reopened_from_profile_even_when_complete", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: true, missing: [] });
    render(<SetupGate />);
    await waitFor(() => expect(client.setupState).toHaveBeenCalled());
    act(() => openSetupWizard());
    await wizard();
    await screen.findByRole("heading", { name: "Welcome to Mises" });
  });

  it("close_keeps_what_is_saved_and_hides", async () => {
    render(<SetupGate />);
    await wizard();
    fireEvent.click(screen.getByRole("button", { name: "Close — finish later" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("retries_when_the_core_is_not_ready", async () => {
    client.setupState.mockRejectedValueOnce(new CoreError("network", "down")).mockResolvedValue(fresh);
    render(<SetupGate />);
    await screen.findByRole("dialog", { name: "Set up Mises" }, { timeout: 4000 });
  });
});

describe("resume", () => {
  it("resumes_at_the_first_missing_step_after_a_reload", async () => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Connect Onshape" });
  });

  it("an_onshape_skip_survives_a_reload_in_the_same_session", async () => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    await screen.findByRole("heading", { name: "Profile basics" });
    cleanup();
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Profile basics" });
  });
});

describe("model step", () => {
  it("compliant_wording_for_the_users_claude_code", async () => {
    await toModelStep();
    const card = screen.getByRole("region", { name: "Use my Claude Code" });
    expect(card.textContent).toContain("Runs your Claude Code");
    // No Mises feature is called "Claude Code": the only heading is the user-owned phrasing
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings.filter((h) => /Claude Code/.test(h ?? ""))).toEqual(["Use my Claude Code"]);
  });

  it("api_key_is_a_password_field_cleared_after_save_and_never_shown", async () => {
    await toModelStep();
    const field = input("API key");
    expect(field.type).toBe("password");
    expect(field.getAttribute("autocomplete")).toBe("off");
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "anthropic" } });
    fireEvent.input(field, { target: { value: SECRET } });
    client.setupState.mockResolvedValue({ ...fresh, has_model: true, missing: ["onshape", "profile"] });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("saved ●●●● from Keychain");
    expect(client.setKey).toHaveBeenCalledWith("anthropic", SECRET);
    expect(input("API key").value).toBe("");
    expect(document.body.textContent).not.toContain(SECRET);
    expect(markup()).not.toContain(SECRET);
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("api_key_vendors_are_the_ones_with_keys_only", async () => {
    await toModelStep();
    const opts = [...(screen.getByLabelText("Vendor") as HTMLSelectElement).options].map((o) => o.value);
    expect(opts).toEqual(["anthropic", "openai", "gemini", "openrouter"]);
  });

  it("continue_is_disabled_until_a_model_works", async () => {
    await toModelStep();
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("local_only_skip_moves_on_without_a_model", async () => {
    await toModelStep();
    fireEvent.click(screen.getByRole("button", { name: "Skip — local models only" }));
    await screen.findByRole("heading", { name: "Connect Onshape" });
  });

  it("not_installed_shows_how_to_install", async () => {
    client.claudeCodeStatus.mockResolvedValue({ installed: false, version: null, signed_in: false, auth_method: null });
    await toModelStep();
    const card = screen.getByRole("region", { name: "Use my Claude Code" });
    await waitFor(() => expect(card.textContent).toContain("isn't installed on this Mac"));
    expect(card.textContent).toContain("npm install -g @anthropic-ai/claude-code");
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("sign_in_runs_the_login_then_polls_every_2s_until_signed_in", async () => {
    await toModelStep();
    vi.useFakeTimers();
    let polls = 0;
    client.claudeCodeStatus.mockImplementation(async () => (++polls >= 2 ? { ...signedOut, signed_in: true, auth_method: "claude.ai" } : signedOut));
    client.setupState.mockResolvedValue({ ...fresh, has_model: true, missing: ["onshape", "profile"] });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    expect(client.claudeCodeLogin).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/finish signing in in your browser/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(polls).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(polls).toBe(2);
    expect(screen.getByText("signed in · claude.ai")).toBeTruthy();
    expect(screen.queryByText(/finish signing in/)).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(polls).toBe(2);
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("sign_in_polling_stops_after_2_minutes", async () => {
    await toModelStep();
    vi.useFakeTimers();
    let polls = 0;
    client.claudeCodeStatus.mockImplementation(async () => { polls++; return signedOut; });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(polls).toBe(60);
    expect(screen.getByText(/sign-in timed out/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(polls).toBe(60);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("leaving_the_step_stops_polling", async () => {
    await toModelStep();
    vi.useFakeTimers();
    let polls = 0;
    client.claudeCodeStatus.mockImplementation(async () => { polls++; return signedOut; });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Skip — local models only" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(polls).toBe(0);
  });

  it("local_endpoint_test_saves_it_and_reports_the_core_answer", async () => {
    await toModelStep();
    fireEvent.change(screen.getByLabelText("Local app"), { target: { value: "ollama" } });
    expect(input("Endpoint").value).toBe("http://localhost:11434/v1");
    client.setupState.mockResolvedValue({ ...fresh, has_model: true, missing: ["onshape", "profile"] });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("answering");
    expect(client.updateProfile).toHaveBeenCalledWith({ local_endpoints: { ollama: "http://localhost:11434/v1" } });
  });

  it("local_endpoint_without_answer", async () => {
    await toModelStep();
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText(/no answer from/);
  });
});

describe("onshape step", () => {
  beforeEach(() => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
  });

  it("keys_are_password_fields_cleared_after_save_then_one_test_call", async () => {
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Connect Onshape" });
    expect(input("Access key").type).toBe("password");
    expect(input("Secret key").type).toBe("password");
    fireEvent.input(input("Access key"), { target: { value: "AK-SECRET-1" } });
    fireEvent.input(input("Secret key"), { target: { value: "SK-SECRET-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("saved ●●●● from Keychain");
    expect(client.setOnshapeKeys).toHaveBeenCalledWith("AK-SECRET-1", "SK-SECRET-2");
    expect(input("Access key").value).toBe("");
    expect(input("Secret key").value).toBe("");
    expect(markup()).not.toMatch(/AK-SECRET-1|SK-SECRET-2/);
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("connected as Hartigan Deely");
    expect(client.testOnshape).toHaveBeenCalledTimes(1);
  });

  it("wrong_keys_show_a_clear_error", async () => {
    client.listKeys.mockResolvedValue(keys.map((k) => (k.vendor === "onshape" ? { ...k, present: true, source: "keychain" } : k)));
    client.testOnshape.mockResolvedValue({ ok: false, error: "denied" });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Connect Onshape" });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    const err = await screen.findByRole("alert");
    expect(err.textContent).toContain("Onshape refused these keys");
  });

  it("skip_for_now_goes_to_profile_without_onshape_calls", async () => {
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    await screen.findByRole("heading", { name: "Profile basics" });
    expect(client.setOnshapeKeys).not.toHaveBeenCalled();
    expect(client.testOnshape).not.toHaveBeenCalled();
  });
});

describe("profile and done", () => {
  beforeEach(() => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: true, missing: ["profile"] });
  });

  it("offers_only_available_models_and_1_to_4_agents_then_saves", async () => {
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Profile basics" });
    const modelOpts = [...(screen.getByLabelText("Default model") as HTMLSelectElement).options].map((o) => o.value);
    expect(modelOpts).toEqual(["", "anthropic/claude-sonnet-5-5", "claude-code/sonnet", "claude-code/opus"]);
    const agentOpts = [...(screen.getByLabelText("Parallel agents") as HTMLSelectElement).options].map((o) => o.value);
    expect(agentOpts).toEqual(["1", "2", "3", "4"]);
    const save = screen.getByRole("button", { name: "Save and continue" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.input(input("Display name"), { target: { value: "Hartigan" } });
    fireEvent.input(input("Default material"), { target: { value: "PETG" } });
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "claude-code/sonnet" } });
    fireEvent.change(screen.getByLabelText("Parallel agents"), { target: { value: "3" } });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: true, missing: [] });
    fireEvent.click(save);
    await screen.findByRole("heading", { name: "Ready" });
    expect(client.updateProfile).toHaveBeenCalledWith({ display_name: "Hartigan", default_material: "PETG", default_model: "claude-code/sonnet", max_parallel_agents: 3 });
  });

  it("done_marks_setup_complete_and_closes", async () => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: true, missing: [] });
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Start using Mises" }));
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: true, missing: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(client.completeSetup).toHaveBeenCalledTimes(1);
  });

  it("done_without_a_model_says_local_only_and_still_closes", async () => {
    client.setupState.mockResolvedValue({ complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape"] });
    sessionStorage.setItem("mises.setup.skip.model", "1");
    sessionStorage.setItem("mises.setup.skip.onshape", "1");
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Ready" });
    expect(screen.getByRole("dialog").textContent).toContain("local models only");
    fireEvent.click(screen.getByRole("button", { name: "Start using Mises" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
