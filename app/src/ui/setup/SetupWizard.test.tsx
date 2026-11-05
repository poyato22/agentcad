import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoreError, type CoreClient } from "../../api/client";
import type { ClaudeCodeStatus, KeyStatus, ModelsInfo, Profile, SetupState } from "../../api/types";
import { bindClient, conn, resetStore } from "../../state/store";
import { resetCatalog } from "../dialogs/catalog";
import { closeProfile, profileOpen, profileSection } from "../profile/open";
import { closeSetupWizard, openSetupWizard } from "./gate";
import { SetupGate } from "./SetupGate";

const SECRET = "sk-ant-api03-SECRET-0123456789";

const fresh: SetupState = { complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape", "profile"] };
const profile: Profile = {
  display_name: "", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2, local_endpoints: {}, theme: "system", setup_completed_at: null,
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
  testLocal: vi.fn(), localStatus: vi.fn(), localStart: vi.fn(), localPull: vi.fn(),
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
  client.testLocal.mockReset().mockResolvedValue({ ok: false, error: "unreachable" });
  client.localStatus.mockReset().mockRejectedValue(new CoreError("not_found", "Not Found", { status: 404 }));   // an older core
  client.localStart.mockReset().mockResolvedValue({ ok: true, running: true });
  client.localPull.mockReset().mockResolvedValue({ ok: true });
  resetCatalog();
  bindClient(client as unknown as CoreClient);
  try { sessionStorage.clear(); } catch { /* none */ }
});
afterEach(() => { cleanup(); closeSetupWizard(); closeProfile(); resetStore(); vi.useRealTimers(); });

const wizard = () => screen.findByRole("dialog", { name: "Set up AgentCAD" });
/** The serialized DOM (attributes included): a secret must not appear anywhere in it. */
const markup = () => new XMLSerializer().serializeToString(document.body);
const input = (name: string) => screen.getByLabelText(name, { selector: "input" }) as HTMLInputElement;

/** Profile done (a name is saved), no model yet: the wizard resumes at "Connect a model". */
async function toModelStep() {
  client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
  render(<SetupGate />);
  await wizard();
  await screen.findByRole("heading", { name: "Model" });
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
    await screen.findByRole("heading", { name: "Welcome to AgentCAD" });
    expect(dlg.textContent).toContain("Stored on this Mac");
  });

  it("can_be_reopened_from_profile_even_when_complete", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: true, missing: [] });
    render(<SetupGate />);
    await waitFor(() => expect(client.setupState).toHaveBeenCalled());
    act(() => openSetupWizard());
    await wizard();
    await screen.findByRole("heading", { name: "Welcome to AgentCAD" });
  });

  it("close_keeps_what_is_saved_and_hides", async () => {
    render(<SetupGate />);
    await wizard();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("retries_when_the_core_is_not_ready", async () => {
    client.setupState.mockRejectedValueOnce(new CoreError("network", "down")).mockResolvedValue(fresh);
    render(<SetupGate />);
    await screen.findByRole("dialog", { name: "Set up AgentCAD" }, { timeout: 4000 });
  });

  it("retries_back_off_while_the_core_is_down", async () => {
    vi.useFakeTimers();
    client.setupState.mockRejectedValue(new CoreError("network", "down"));
    render(<SetupGate />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(client.setupState).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(client.setupState).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(client.setupState).toHaveBeenCalledTimes(2);    // 2 s before the third try
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(client.setupState).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(client.setupState.mock.calls.length).toBeLessThan(12); // capped backoff, not every second
  });

  it("stops_on_401_and_checks_again_when_the_session_reconnects", async () => {
    vi.useFakeTimers();
    client.setupState.mockRejectedValue(new CoreError("unauthorized", "missing or bad token", { status: 401 }));
    render(<SetupGate />);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(client.setupState).toHaveBeenCalledTimes(1);
    client.setupState.mockResolvedValue(fresh);
    await act(async () => { conn.value = "open"; await vi.advanceTimersByTimeAsync(0); });
    expect(client.setupState).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
    await wizard();
  });

  it("checks_once_at_boot_and_again_only_on_a_reconnect", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: true, missing: [] });
    render(<SetupGate />);
    await waitFor(() => expect(client.setupState).toHaveBeenCalledTimes(1));
    act(() => { conn.value = "open"; });                 // the boot check already answered
    await act(async () => { await Promise.resolve(); });
    expect(client.setupState).toHaveBeenCalledTimes(1);
    act(() => { conn.value = "reconnecting"; });
    act(() => { conn.value = "open"; });
    await waitFor(() => expect(client.setupState).toHaveBeenCalledTimes(2));
  });

  it("complete_without_a_model_shows_a_connect_a_model_notice_not_the_wizard", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: false, has_onshape: false, missing: ["model", "onshape"] });
    render(<SetupGate />);
    const notice = await screen.findByRole("status", { name: "Connect a model" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(notice.textContent).toContain("No model connected");
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(profileOpen.value).toBe(true);
    expect(profileSection.value).toBe("models");
  });

  it("the_notice_goes_once_a_model_is_connected_and_can_be_dismissed", async () => {
    client.setupState.mockResolvedValue({ complete: true, has_model: false, has_onshape: false, missing: ["model", "onshape"] });
    render(<SetupGate />);
    await screen.findByRole("status", { name: "Connect a model" });
    // the user connects one in Profile: the gate checks again when Profile closes
    act(() => { profileOpen.value = true; });
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: false, missing: ["onshape"] });
    act(() => { profileOpen.value = false; });
    await waitFor(() => expect(screen.queryByRole("status", { name: "Connect a model" })).toBeNull());
    cleanup();
    client.setupState.mockResolvedValue({ complete: true, has_model: false, has_onshape: false, missing: ["model", "onshape"] });
    render(<SetupGate />);
    await screen.findByRole("status", { name: "Connect a model" });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status", { name: "Connect a model" })).toBeNull();
  });

  it("wizard_traps_focus_inside_it", async () => {
    render(<SetupGate />);
    const dlg = await wizard();
    await screen.findByRole("button", { name: "Start" });
    await waitFor(() => expect(dlg.contains(document.activeElement)).toBe(true));
    const buttons = [...dlg.querySelectorAll("button:not([disabled])")] as HTMLElement[];
    const last = buttons[buttons.length - 1];
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(buttons[0]);
  });
});

describe("order: welcome → profile → model → onshape → done", () => {
  it("get_started_opens_the_profile_first", async () => {
    render(<SetupGate />);
    await wizard();
    const steps = [...screen.getByRole("navigation", { name: "Setup steps" }).querySelectorAll(".setup-step-name")].map((el) => el.textContent);
    expect(steps).toEqual(["Welcome", "Profile", "Model", "Onshape", "Done"]);
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await screen.findByRole("heading", { name: "Profile" });
    expect(screen.getByRole("navigation", { name: "Setup steps" }).querySelector("[aria-current=step] .setup-step-name")?.textContent).toBe("Profile");
  });

  it("profile_saves_name_units_material_theme_then_goes_to_connect_a_model", async () => {
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await screen.findByRole("heading", { name: "Profile" });
    expect([...(screen.getByLabelText("Units") as HTMLSelectElement).options].map((o) => o.value)).toEqual(["mm"]);
    expect([...(screen.getByLabelText("Theme") as HTMLSelectElement).options].map((o) => o.value)).toEqual(["system", "light", "dark"]);
    expect(screen.queryByLabelText("Parallel agents")).toBeNull();       // automatic now (Profile → Agents)
    const save = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.input(input("Display name"), { target: { value: "Hartigan" } });
    fireEvent.input(input("Default material"), { target: { value: "PETG" } });
    fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "dark" } });
    fireEvent.click(save);
    await screen.findByRole("heading", { name: "Model" });
    expect(client.updateProfile).toHaveBeenCalledWith({ display_name: "Hartigan", default_material: "PETG", theme: "dark" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await screen.findByRole("heading", { name: "Profile" });
    expect(input("Display name").value).toBe("Hartigan");
  });
});

describe("resume", () => {
  it("resumes_at_the_first_missing_step_after_a_reload", async () => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Onshape" });
  });

  it("a_model_saved_without_a_profile_resumes_at_the_profile", async () => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Profile" });
  });

  it("an_onshape_skip_survives_a_reload_in_the_same_session", async () => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await screen.findByRole("heading", { name: "Done" });
    cleanup();
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Done" });
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
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    await screen.findByRole("heading", { name: "Onshape" });
  });

  it("not_installed_shows_how_to_install", async () => {
    client.claudeCodeStatus.mockResolvedValue({ installed: false, version: null, signed_in: false, auth_method: null });
    await toModelStep();
    const card = screen.getByRole("region", { name: "Use my Claude Code" });
    await waitFor(() => expect(card.textContent).toContain("not installed"));
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
    expect(screen.getByText(/waiting for browser sign-in/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(polls).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(polls).toBe(2);
    expect(screen.getByText("signed in · claude.ai")).toBeTruthy();
    expect(screen.queryByText(/waiting for browser sign-in/)).toBeNull();
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
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Skip" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(polls).toBe(0);
  });

  it("local_endpoint_test_asks_the_core_then_saves_an_answering_endpoint", async () => {
    await toModelStep();
    fireEvent.change(screen.getByLabelText("Local app"), { target: { value: "ollama" } });
    expect(input("Endpoint").value).toBe("http://localhost:11434/v1");
    client.testLocal.mockResolvedValue({ ok: true });
    client.setupState.mockResolvedValue({ ...fresh, has_model: true, missing: ["onshape", "profile"] });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("answering — saved");
    expect(client.testLocal).toHaveBeenCalledWith("http://localhost:11434/v1");
    expect(client.updateProfile).toHaveBeenCalledWith({ local_endpoints: { ollama: "http://localhost:11434/v1" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("local_endpoint_without_answer_is_not_saved", async () => {
    await toModelStep();
    fireEvent.input(input("Endpoint"), { target: { value: "http://localhost:9999/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText("no answer from http://localhost:9999/v1 — is Ollama running? Not saved.");
    expect(client.updateProfile).not.toHaveBeenCalled();
  });

  it("a_bad_local_url_shows_the_core_reason", async () => {
    await toModelStep();
    client.testLocal.mockRejectedValue(new CoreError("invalid_input", "a local model URL must be http(s) on this Mac (localhost or 127.0.0.1)", { status: 400, hint: "e.g. http://localhost:11434/v1" }));
    fireEvent.input(input("Endpoint"), { target: { value: "http://example.com/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await screen.findByText(/must be http\(s\) on this Mac/);
    expect(client.updateProfile).not.toHaveBeenCalled();
  });

  it("a_key_saved_while_another_save_was_running_is_not_lost", async () => {
    // stale closure: the second save must merge into the keys the first one already updated
    await toModelStep();
    let release: (v: KeyStatus) => void = () => {};
    client.setKey.mockImplementationOnce(() => new Promise<KeyStatus>((r) => { release = r; }));
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "anthropic" } });
    fireEvent.input(input("API key"), { target: { value: "sk-a" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await act(async () => { release({ vendor: "anthropic", present: true, source: "keychain" }); await Promise.resolve(); });
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "openai" } });
    fireEvent.input(input("API key"), { target: { value: "sk-o" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("saved ●●●● from Keychain");
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "anthropic" } });
    expect(screen.getByRole("region", { name: "API key" }).textContent).toContain("saved ●●●● from Keychain");
  });

  it("sign_in_started_then_leaving_the_step_never_starts_polling", async () => {
    // unmount race: the login request is still in flight when the card goes away
    await toModelStep();
    vi.useFakeTimers();
    let finishLogin: () => void = () => {};
    client.claudeCodeLogin.mockImplementation(() => new Promise<void>((r) => { finishLogin = r; }));
    let polls = 0;
    client.claudeCodeStatus.mockImplementation(async () => { polls++; return signedOut; });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Skip" })); });
    await act(async () => { finishLogin(); await vi.advanceTimersByTimeAsync(20_000); });
    expect(polls).toBe(0);
  });

  it("default_model_picker_appears_once_a_model_works_and_saves_to_the_profile", async () => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Onshape" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await screen.findByRole("heading", { name: "Model" });
    const card = screen.getByRole("region", { name: "Default model" });
    fireEvent.click(within(card).getByRole("button", { name: /^Default model/ }));
    const claude = await within(card).findByRole("group", { name: "Your Claude Code" });
    fireEvent.click(within(claude).getByRole("button", { name: /opus/ }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ default_model: "claude-code/opus" }));
    await waitFor(() => expect(within(card).getByRole("button", { name: /^Default model: Claude Code · opus/ })).toBeTruthy());
  });

  it("local_card_shows_ollama_on_this_mac_and_offers_to_start_it", async () => {
    client.localStatus.mockResolvedValue({ installed: true, running: false, version: "0.12.3", models: [], recommended: [{ name: "qwen3-coder:30b", size_gb: 19, fits: true, note: null }] });
    await toModelStep();
    const card = screen.getByRole("region", { name: "Local model" });
    await waitFor(() => expect(card.textContent).toContain("Ollama not running"));
    fireEvent.click(within(card).getByRole("button", { name: "Start Ollama" }));
    await waitFor(() => expect(client.localStart).toHaveBeenCalledTimes(1));
  });

  it("each_status_check_has_a_deadline_so_a_hung_request_does_not_stall_sign_in", async () => {
    await toModelStep();
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    client.claudeCodeStatus.mockImplementation((signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? new Promise(() => {}) : Promise.resolve({ ...signedOut, signed_in: true, auth_method: "claude.ai" });
    });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000 + 2000); });
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText("signed in · claude.ai")).toBeTruthy();
  });
});

describe("onshape step", () => {
  beforeEach(() => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
  });

  it("keys_are_password_fields_cleared_after_save_then_one_test_call", async () => {
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Onshape" });
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
    await screen.findByRole("heading", { name: "Onshape" });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    const err = await screen.findByRole("alert");
    expect(err.textContent).toContain("Onshape rejected these keys");
  });

  it("skip_for_now_goes_to_done_without_onshape_calls", async () => {
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await screen.findByRole("heading", { name: "Done" });
    expect(client.setOnshapeKeys).not.toHaveBeenCalled();
    expect(client.testOnshape).not.toHaveBeenCalled();
  });
});

describe("profile and done", () => {
  beforeEach(() => {
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: true, missing: ["profile"] });
  });
  const named = () => client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });

  it("a_saved_name_resumes_at_ready_since_the_core_keeps_profile_missing_until_complete", async () => {
    named();
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Done" });
  });

  it("an_unnamed_profile_is_asked_for_even_when_everything_else_is_saved", async () => {
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Profile" });
    fireEvent.input(input("Display name"), { target: { value: "Hartigan" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // a model already works: the model step shows it, with the default model picker
    await screen.findByRole("heading", { name: "Model" });
    expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("done_marks_setup_complete_and_closes", async () => {
    named();
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: true, missing: [] });
    render(<SetupGate />);
    fireEvent.click(await screen.findByRole("button", { name: "Finish" }));
    client.setupState.mockResolvedValue({ complete: true, has_model: true, has_onshape: true, missing: [] });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(client.completeSetup).toHaveBeenCalledTimes(1);
  });

  it("done_without_a_model_says_local_only_and_still_closes", async () => {
    named();
    client.setupState.mockResolvedValue({ complete: false, has_model: false, has_onshape: false, missing: ["model", "onshape"] });
    sessionStorage.setItem("mises.setup.skip.model", "1");
    sessionStorage.setItem("mises.setup.skip.onshape", "1");
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Done" });
    expect(screen.getByRole("dialog").textContent).toContain("no model connected");
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("step bar: Welcome · Profile · Model · Onshape · Done", () => {
  const stepBtn = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}( \\((done|to do)\\))?$`) });

  it("every_step_is_a_button_that_jumps_to_its_step", async () => {
    render(<SetupGate />);
    await wizard();
    await screen.findByRole("heading", { name: "Welcome to AgentCAD" });
    await waitFor(() => expect((stepBtn("Onshape") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(stepBtn("Onshape"));
    await screen.findByRole("heading", { name: "Onshape" });
    expect(stepBtn("Onshape").getAttribute("aria-current")).toBe("step");
    fireEvent.click(stepBtn("Model"));
    await screen.findByRole("heading", { name: "Model" });
    fireEvent.click(stepBtn("Done"));
    await screen.findByRole("heading", { name: "Done" });
    fireEvent.click(stepBtn("Welcome"));
    await screen.findByRole("heading", { name: "Welcome to AgentCAD" });
  });

  it("steps_show_done_current_and_to_do", async () => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    client.setupState.mockResolvedValue({ complete: false, has_model: true, has_onshape: false, missing: ["onshape", "profile"] });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Onshape" });   // resumes at the first missing piece
    const status = (n: string) => stepBtn(n).getAttribute("data-status");
    expect(["Welcome", "Profile", "Model", "Onshape", "Done"].map(status)).toEqual(["done", "done", "done", "current", "todo"]);
    expect(stepBtn("Profile").textContent).toContain("✓");
    expect(screen.getByRole("button", { name: "Profile (done)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Done (to do)" })).toBeTruthy();
    // only the current step carries aria-current
    const current = screen.getByRole("navigation", { name: "Setup steps" }).querySelectorAll("[aria-current]");
    expect([...current].map((el) => el.getAttribute("aria-current"))).toEqual(["step"]);
  });

  it("arrow_keys_move_focus_between_steps_and_enter_opens_one", async () => {
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Welcome to AgentCAD" });
    await waitFor(() => expect((stepBtn("Welcome") as HTMLButtonElement).disabled).toBe(false));
    stepBtn("Welcome").focus();
    fireEvent.keyDown(stepBtn("Welcome"), { key: "ArrowRight" });
    expect(document.activeElement).toBe(stepBtn("Profile"));
    fireEvent.keyDown(stepBtn("Profile"), { key: "ArrowLeft" });
    fireEvent.keyDown(stepBtn("Welcome"), { key: "ArrowLeft" });           // wraps to the last
    expect(document.activeElement).toBe(stepBtn("Done"));
    fireEvent.keyDown(stepBtn("Done"), { key: "Home" });
    expect(document.activeElement).toBe(stepBtn("Welcome"));
    fireEvent.keyDown(stepBtn("Welcome"), { key: "End" });
    expect(document.activeElement).toBe(stepBtn("Done"));
    // a step is a native <button> in the Tab order: Enter/Space activate it (a click)
    expect(stepBtn("Model").tagName).toBe("BUTTON");
    expect(stepBtn("Model").getAttribute("tabindex")).toBeNull();
    fireEvent.click(stepBtn("Model"));
    await screen.findByRole("heading", { name: "Model" });
  });

  it("resume_logic_is_unchanged_by_the_step_bar", async () => {
    client.getProfile.mockResolvedValue({ ...profile, display_name: "Hartigan" });
    render(<SetupGate />);
    await screen.findByRole("heading", { name: "Model" });
    expect(stepBtn("Model").getAttribute("aria-current")).toBe("step");
  });

  it("close_is_a_plain_one_word_button", async () => {
    render(<SetupGate />);
    await wizard();
    const close = screen.getByRole("button", { name: "Close" });
    expect(close.className).toContain("btn");
    expect(screen.queryByText(/finish later/)).toBeNull();
    expect(screen.queryByText(/There's no account/)).toBeNull();
  });
});
