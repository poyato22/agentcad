import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentInfo, ClaudeCodeStatus, CoreStatusInfo, KeyStatus, ModelsInfo, Profile } from "../../api/types";
import { bindClient, resetStore } from "../../state/store";
import { closeSetupWizard, setupWizardOpen } from "../setup/gate";
import { ProfilePane } from "./ProfilePane";

const SECRET = "sk-ant-api03-SECRET-0123456789";
const ACCESS = "on-access-AAAA1111";
const ONSHAPE_SECRET = "on-secret-BBBB2222";

const profile: Profile = {
  display_name: "Hartigan", units: "mm", default_material: "PLA", default_agent: "designer", default_model: null,
  onshape_calls_year_cap: 2500, onshape_calls_run_cap: 200, max_parallel_agents: 2,
  local_endpoints: { ollama: "http://localhost:11434/v1" }, theme: "system", setup_completed_at: 1790000000,
};
const keys: KeyStatus[] = [
  { vendor: "anthropic", present: true, source: "keychain" }, { vendor: "openai", present: true, source: "env" },
  { vendor: "gemini", present: false, source: "none" }, { vendor: "openrouter", present: false, source: "none" },
  { vendor: "ollama", present: false, source: "none" }, { vendor: "lmstudio", present: false, source: "none" },
  { vendor: "onshape", present: true, source: "keychain" },
];
const claude: ClaudeCodeStatus = { installed: true, version: "2.1.4", signed_in: true, auth_method: "claude.ai" };
const status: CoreStatusInfo = { core_version: "0.3.0", onshape: "connected", onshape_calls_year: 340, onshape_cap_year: 2500, runs_running: 0, approvals_pending: 0 };
const models: ModelsInfo = {
  default: "anthropic/claude-sonnet-5-5",
  vendors: [
    { vendor: "anthropic", key: "keychain" }, { vendor: "openai", key: "env" }, { vendor: "gemini", key: "none" },
    { vendor: "ollama", key: "not_needed" }, { vendor: "claude-code", key: "signed_in", models: ["sonnet", "opus"] },
  ],
};
const agents: AgentInfo[] = [
  { name: "designer", description: "d", model: "inherit" }, { name: "verifier", description: "v", model: "inherit" },
];

const client = {
  getProfile: vi.fn(), updateProfile: vi.fn(), listKeys: vi.fn(), setKey: vi.fn(), setOnshapeKeys: vi.fn(), deleteKey: vi.fn(),
  testOnshape: vi.fn(), testLocal: vi.fn(), claudeCodeStatus: vi.fn(), claudeCodeLogin: vi.fn(), claudeCodeLogout: vi.fn(),
  status: vi.fn(), models: vi.fn(), agents: vi.fn(),
};

let confirmSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  client.getProfile.mockReset().mockResolvedValue(profile);
  client.updateProfile.mockReset().mockImplementation(async (p: Partial<Profile>) => ({ ...profile, ...p }));
  client.listKeys.mockReset().mockResolvedValue(keys);
  client.setKey.mockReset().mockImplementation(async (vendor: string) => ({ vendor, present: true, source: "keychain" }));
  client.setOnshapeKeys.mockReset().mockResolvedValue({ vendor: "onshape", present: true, source: "keychain" });
  client.deleteKey.mockReset().mockImplementation(async (vendor: string) => ({ vendor, present: false, source: "none" }));
  client.testOnshape.mockReset().mockResolvedValue({ ok: true, user_name: "Hartigan Deely" });
  client.testLocal.mockReset().mockResolvedValue({ ok: true });
  client.claudeCodeStatus.mockReset().mockResolvedValue(claude);
  client.claudeCodeLogin.mockReset().mockResolvedValue(undefined);
  client.claudeCodeLogout.mockReset().mockResolvedValue(undefined);
  client.status.mockReset().mockResolvedValue(status);
  client.models.mockReset().mockResolvedValue(models);
  client.agents.mockReset().mockResolvedValue(agents);
  bindClient(client as unknown as CoreClient);
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => { cleanup(); closeSetupWizard(); resetStore(); vi.useRealTimers(); confirmSpy.mockRestore(); });

const markup = () => new XMLSerializer().serializeToString(document.body);
const pane = () => screen.findByRole("dialog", { name: "Profile" });
async function openSection(name: string, onClose = vi.fn()) {
  render(<ProfilePane open onClose={onClose} />);
  await pane();
  await screen.findByRole("button", { name: "Models" });
  if (name !== "Profile") fireEvent.click(screen.getByRole("button", { name }));
  return onClose;
}
const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const type = (label: string, value: string) => fireEvent.input(field(label), { target: { value } });

describe("ProfilePane shell", () => {
  it("renders_nothing_while_closed", () => {
    render(<ProfilePane open={false} onClose={() => undefined} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(client.getProfile).not.toHaveBeenCalled();
  });

  it("lists_the_eight_sections_and_marks_only_the_selected_one", async () => {
    render(<ProfilePane open onClose={() => undefined} />);
    await pane();
    const nav = await screen.findByRole("navigation", { name: "Profile sections" });
    const names = within(nav).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Profile", "Models", "Onshape", "Printers", "Agents & budgets", "Data", "Setup", "About"]);
    expect(within(nav).getByRole("button", { name: "Profile" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(within(nav).getByRole("button", { name: "Onshape" }));
    expect(within(nav).getByRole("button", { name: "Onshape" }).getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("button", { name: "Profile" }).getAttribute("aria-current")).toBeNull();
  });

  it("opens_at_the_section_it_was_asked_for_and_traps_focus", async () => {
    render(<ProfilePane open onClose={() => undefined} section="models" />);
    const dlg = await pane();
    await screen.findByRole("region", { name: "Models" });
    const current = screen.getByRole("button", { name: "Models" });
    expect(current.getAttribute("aria-current")).toBe("page");
    expect(document.activeElement).toBe(current);
    const all = [...dlg.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled])")] as HTMLElement[];
    all[all.length - 1].focus();
    fireEvent.keyDown(all[all.length - 1], { key: "Tab" });
    expect(document.activeElement).toBe(all[0]);
  });

  it("closes_from_the_button_and_from_escape", async () => {
    const onClose = await openSection("Profile");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("uses_no_blue_led_while_idle", async () => {
    await openSection("Models");
    expect(document.querySelectorAll(".led-blue, [data-tone='blue']").length).toBe(0);
  });

  it("shows_a_retry_when_the_core_cannot_be_reached", async () => {
    client.getProfile.mockRejectedValueOnce(new Error("core is down"));
    render(<ProfilePane open onClose={() => undefined} />);
    expect(await screen.findByText(/core is down/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("button", { name: "Models" });
  });
});

describe("Profile section", () => {
  it("reads_the_profile_and_shows_units_read_only", async () => {
    await openSection("Profile");
    expect(field("Name").value).toBe("Hartigan");
    expect(field("Default material").value).toBe("PLA");
    expect(screen.getByText("mm")).toBeTruthy();
    expect(screen.queryByLabelText("Units")).toBeNull();
  });

  it("saves_only_the_changed_fields_and_never_setup_completed_at", async () => {
    await openSection("Profile");
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    type("Name", "Hartigan D");
    fireEvent.click(save);
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledTimes(1));
    expect(client.updateProfile).toHaveBeenCalledWith({ display_name: "Hartigan D" });
    expect(await screen.findByText("saved")).toBeTruthy();
  });

  it("shows_the_core_error_when_saving_fails", async () => {
    client.updateProfile.mockRejectedValueOnce(new Error("invalid profile: nope"));
    await openSection("Profile");
    type("Default material", "PETG");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("invalid profile: nope");
  });
});

describe("Models section: Claude Code", () => {
  it("shows_status_version_and_auth_method", async () => {
    await openSection("Models");
    const card = screen.getByRole("region", { name: "Your Claude Code" });
    expect(card.textContent).toContain("signed in");
    expect(card.textContent).toContain("2.1.4");
    expect(card.textContent).toContain("claude.ai");
  });

  it("sign_out_calls_the_logout_route_and_refreshes_status", async () => {
    await openSection("Models");
    client.claudeCodeStatus.mockResolvedValue({ ...claude, signed_in: false, auth_method: null });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(client.claudeCodeLogout).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("sign_in_starts_the_login_and_polls_until_signed_in_with_a_blue_waiting_light", async () => {
    client.claudeCodeStatus.mockResolvedValue({ ...claude, signed_in: false, auth_method: null });
    await openSection("Models");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    client.claudeCodeStatus.mockResolvedValue({ ...claude, signed_in: false, auth_method: null });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await vi.advanceTimersByTimeAsync(0);
    expect(client.claudeCodeLogin).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/waiting for browser sign-in/)).toBeTruthy();
    expect(document.querySelectorAll(".led-blue").length).toBe(1);
    client.claudeCodeStatus.mockResolvedValue(claude);
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(document.querySelectorAll(".led-blue").length).toBe(0);
  });

  it("closing_the_panel_while_the_login_starts_never_starts_polling", async () => {
    client.claudeCodeStatus.mockResolvedValue({ ...claude, signed_in: false, auth_method: null });
    const { unmount } = render(<ProfilePane open onClose={() => undefined} section="models" />);
    await screen.findByRole("button", { name: "Sign in" });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    let finish: () => void = () => {};
    client.claudeCodeLogin.mockImplementation(() => new Promise<void>((r) => { finish = r; }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const before = client.claudeCodeStatus.mock.calls.length;
    unmount();
    await act(async () => { finish(); await vi.advanceTimersByTimeAsync(20_000); });
    expect(client.claudeCodeStatus.mock.calls.length).toBe(before);
  });

  it("not_installed_shows_the_install_hint_and_no_sign_in", async () => {
    client.claudeCodeStatus.mockResolvedValue({ installed: false, version: null, signed_in: false, auth_method: null });
    await openSection("Models");
    expect(screen.getByText(/npm install -g @anthropic-ai\/claude-code/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });
});

describe("Models section: API keys", () => {
  it("lists_vendor_status_and_source_without_any_secret", async () => {
    await openSection("Models");
    const table = screen.getByRole("table", { name: "API keys" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.querySelector("td")?.textContent)).toEqual(["anthropic", "openai", "gemini", "openrouter"]);
    // an allow-list: a vendor a newer core adds (or the user's Claude Code) never shows as an API key
    cleanup();
    client.listKeys.mockResolvedValue([...keys, { vendor: "claude-code", present: false, source: "none" }, { vendor: "future", present: false, source: "none" }]);
    await openSection("Models");
    const again = within(screen.getByRole("table", { name: "API keys" })).getAllByRole("row").slice(1);
    expect(again.map((r) => r.querySelector("td")?.textContent)).toEqual(["anthropic", "openai", "gemini", "openrouter"]);
    expect(rows[0].textContent).toContain("Keychain");
    expect(rows[1].textContent).toContain("environment");
    expect(rows[2].textContent).toContain("no key");
    expect(markup()).not.toMatch(/sk-/);
  });

  it("replace_sends_the_secret_once_then_clears_the_field_and_never_renders_it", async () => {
    await openSection("Models");
    fireEvent.click(screen.getByRole("button", { name: "Replace anthropic key" }));
    const input = field("anthropic API key");
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    type("anthropic API key", SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.setKey).toHaveBeenCalledWith("anthropic", SECRET));
    await waitFor(() => expect(screen.queryByLabelText("anthropic API key")).toBeNull());
    expect(markup()).not.toContain(SECRET);
  });

  it("add_works_for_a_vendor_without_a_key", async () => {
    await openSection("Models");
    fireEvent.click(screen.getByRole("button", { name: "Add gemini key" }));
    type("gemini API key", SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.setKey).toHaveBeenCalledWith("gemini", SECRET));
  });

  it("remove_asks_inside_the_panel_not_window_confirm_and_only_then_deletes", async () => {
    await openSection("Models");
    fireEvent.click(screen.getByRole("button", { name: "Remove anthropic key" }));
    expect(client.deleteKey).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/Remove the anthropic key from the Keychain/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(client.deleteKey).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove anthropic key" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.deleteKey).toHaveBeenCalledWith("anthropic"));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Add anthropic key" })).toBeTruthy();
  });

  it("an_env_key_row_says_the_variable_takes_precedence_and_a_replace_repeats_it", async () => {
    client.setKey.mockImplementation(async (vendor: string) => ({ vendor, present: true, source: "env" }));
    await openSection("Models");
    const row = screen.getByRole("row", { name: /openai/ });
    expect(row.textContent).toContain("environment variable (overrides Keychain)");
    fireEvent.click(screen.getByRole("button", { name: "Replace openai key" }));
    type("openai API key", SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved to Keychain. The environment variable still overrides it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove openai key" })).toBeNull();
  });

  it("a_key_from_the_environment_cannot_be_removed_here", async () => {
    await openSection("Models");
    expect(screen.queryByRole("button", { name: "Remove openai key" })).toBeNull();
  });

  it("shows_the_core_error_when_a_save_fails_and_keeps_no_secret_in_the_dom", async () => {
    client.setKey.mockRejectedValueOnce(new Error("could not store the key in the Keychain"));
    await openSection("Models");
    fireEvent.click(screen.getByRole("button", { name: "Add gemini key" }));
    type("gemini API key", SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not store");
    expect(markup()).not.toContain(SECRET);
  });
});

describe("Models section: local endpoints", () => {
  it("test_asks_the_core_about_the_typed_url_and_shows_the_answer", async () => {
    await openSection("Models");
    expect(field("ollama endpoint").value).toBe("http://localhost:11434/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test ollama" }));
    await waitFor(() => expect(client.testLocal).toHaveBeenCalledWith("http://localhost:11434/v1"));
    expect(await screen.findByText("answering")).toBeTruthy();
    client.testLocal.mockResolvedValue({ ok: false, error: "unreachable" });
    type("lmstudio endpoint", "http://localhost:1234/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test lmstudio" }));
    expect(await screen.findByText("no answer from http://localhost:1234/v1 — is LM Studio running?")).toBeTruthy();
  });

  it("save_writes_the_whole_local_endpoints_map", async () => {
    await openSection("Models");
    type("lmstudio endpoint", "http://localhost:1234/v1");
    fireEvent.click(screen.getByRole("button", { name: "Save lmstudio" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({
      local_endpoints: { ollama: "http://localhost:11434/v1", lmstudio: "http://localhost:1234/v1" },
    }));
  });

  it("remove_confirms_in_the_panel_then_drops_that_endpoint_only", async () => {
    await openSection("Models");
    fireEvent.click(screen.getByRole("button", { name: "Remove ollama endpoint" }));
    expect(client.updateProfile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ local_endpoints: {} }));
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});

describe("Onshape section", () => {
  it("shows_keys_present_and_usage_against_the_cap", async () => {
    await openSection("Onshape");
    expect(screen.getByText(/saved ●●●● from Keychain/)).toBeTruthy();
    expect(screen.getByText(/340 of 2,500 calls this year/)).toBeTruthy();
    const bar = screen.getByRole("progressbar", { name: "Onshape calls this year" });
    expect(bar.getAttribute("aria-valuenow")).toBe("340");
    expect(bar.getAttribute("aria-valuemax")).toBe("2500");
  });

  it("replace_sends_both_keys_and_clears_them", async () => {
    await openSection("Onshape");
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(field("Access key").type).toBe("password");
    expect(field("Secret key").type).toBe("password");
    type("Access key", ACCESS);
    type("Secret key", ONSHAPE_SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save keys" }));
    await waitFor(() => expect(client.setOnshapeKeys).toHaveBeenCalledWith(ACCESS, ONSHAPE_SECRET));
    await waitFor(() => expect(screen.queryByLabelText("Access key")).toBeNull());
    expect(markup()).not.toContain(ACCESS);
    expect(markup()).not.toContain(ONSHAPE_SECRET);
  });

  it("test_makes_one_call_and_names_the_user", async () => {
    await openSection("Onshape");
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/connected as Hartigan Deely/)).toBeTruthy();
    expect(client.testOnshape).toHaveBeenCalledTimes(1);
  });

  it("a_denied_test_is_a_clear_error", async () => {
    client.testOnshape.mockResolvedValue({ ok: false, error: "denied" });
    await openSection("Onshape");
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/rejected these keys/);
  });

  it.each([
    ["not_configured", /No Onshape keys saved/],
    ["unreachable", /Onshape did not answer/],
    ["budget", /call cap reached/],
    ["failed", /Onshape test failed/],
  ])("a_%s_test_reads_as_a_sentence", async (code, text) => {
    client.testOnshape.mockResolvedValue({ ok: false, error: code });
    await openSection("Onshape");
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(text);
    expect(alert.textContent).not.toContain(`: ${code}`);
  });

  it("remove_confirms_in_the_panel_then_deletes", async () => {
    await openSection("Onshape");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(client.deleteKey).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(client.deleteKey).toHaveBeenCalledWith("onshape"));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Add" })).toBeTruthy();
  });

  it("the_yearly_cap_is_editable_and_validated", async () => {
    await openSection("Onshape");
    const save = screen.getByRole("button", { name: "Save call cap" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    type("Yearly call cap", "-5");
    expect(save.disabled).toBe(true);
    type("Yearly call cap", "4000");
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ onshape_calls_year_cap: 4000 }));
  });

  it("env_onshape_keys_say_the_variable_takes_precedence_and_cannot_be_removed", async () => {
    client.listKeys.mockResolvedValue(keys.map((k) => (k.vendor === "onshape" ? { ...k, source: "env" } : k)));
    client.setOnshapeKeys.mockResolvedValue({ vendor: "onshape", present: true, source: "env" });
    await openSection("Onshape");
    expect(screen.getByText("environment variable (overrides Keychain)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    type("Access key", ACCESS);
    type("Secret key", ONSHAPE_SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Save keys" }));
    expect(await screen.findByText(/The environment variable still overrides it/)).toBeTruthy();
  });

  it("the_usage_bar_is_amber_near_the_cap", async () => {
    client.status.mockResolvedValue({ ...status, onshape_calls_year: 2300 });
    await openSection("Onshape");
    expect(screen.getByRole("progressbar").getAttribute("data-tone")).toBe("amber");
  });
});

describe("Agents & budgets section", () => {
  const autoStatus = { ...status, parallel: { limit: 3, auto: true, reason: "M5 Pro · 24 GB · cloud model → 3" } };

  it("parallel_agents_default_to_auto_with_the_core's_reason", async () => {
    client.getProfile.mockResolvedValue({ ...profile, max_parallel_agents: "auto" });
    client.status.mockResolvedValue(autoStatus);
    await openSection("Agents & budgets");
    expect((screen.getByLabelText("Default agent") as HTMLSelectElement).value).toBe("designer");
    const auto = screen.getByRole("radio", { name: "Auto (3 on this Mac)" }) as HTMLInputElement;
    expect(auto.checked).toBe(true);
    expect(screen.getByRole("region", { name: "Agents & budgets" }).textContent).toContain("M5 Pro · 24 GB · cloud model → 3");
    expect(screen.queryByLabelText("Max parallel agents")).toBeNull();    // no number to pick unless you want to
    expect(field("Onshape calls per run").value).toBe("200");
  });

  it("a_saved_number_shows_as_manual_and_auto_can_be_chosen_back", async () => {
    client.status.mockResolvedValue(autoStatus);
    await openSection("Agents & budgets");
    expect((screen.getByRole("radio", { name: "Manual" }) as HTMLInputElement).checked).toBe(true);
    expect(field("Max parallel agents").value).toBe("2");
    fireEvent.click(screen.getByRole("radio", { name: "Auto (3 on this Mac)" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ max_parallel_agents: "auto" }));
  });

  it("a_manual_limit_reads_manual_n_and_status_is_read_again_after_save", async () => {
    client.status.mockResolvedValue({ ...status, parallel: { limit: 2, auto: false, reason: "set in Profile" } });
    await openSection("Agents & budgets");
    const region = screen.getByRole("region", { name: "Agents & budgets" });
    expect(screen.getByRole("radio", { name: "Auto" })).toBeTruthy();       // no "(2 on this Mac)": that's the manual limit
    expect(region.textContent).toContain("Manual: 2");
    const before = client.status.mock.calls.length;
    client.status.mockResolvedValue(autoStatus);
    fireEvent.click(screen.getByRole("radio", { name: "Auto" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.status.mock.calls.length).toBe(before + 1));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Auto (3 on this Mac)" })).toBeTruthy());
  });

  it("an_older_core_without_parallel_info_still_offers_auto", async () => {
    client.getProfile.mockResolvedValue({ ...profile, max_parallel_agents: "auto" });
    await openSection("Agents & budgets");
    expect((screen.getByRole("radio", { name: "Auto" }) as HTMLInputElement).checked).toBe(true);
  });

  it("saves_the_changed_fields_with_the_model_picker", async () => {
    await openSection("Agents & budgets");
    fireEvent.change(screen.getByLabelText("Default agent"), { target: { value: "verifier" } });
    const region = screen.getByRole("region", { name: "Agents & budgets" });
    expect(within(region).getByRole("button", { name: /^Default model: App default/ })).toBeTruthy();
    fireEvent.click(within(region).getByRole("button", { name: /^Default model/ }));
    const claude = await within(region).findByRole("group", { name: "Your Claude Code" });
    fireEvent.click(within(claude).getByRole("button", { name: /opus/ }));
    type("Max parallel agents", "4");
    type("Onshape calls per run", "120");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({
      default_agent: "verifier", default_model: "claude-code/opus", max_parallel_agents: 4, onshape_calls_run_cap: 120,
    }));
  });

  it("choosing_the_app_default_sends_null", async () => {
    client.getProfile.mockResolvedValue({ ...profile, default_model: "claude-code/sonnet" });
    await openSection("Agents & budgets");
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.updateProfile).toHaveBeenCalledWith({ default_model: null }));
  });

  it("rejects_zero_or_too_many_parallel_agents", async () => {
    await openSection("Agents & budgets");
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    type("Max parallel agents", "0");
    expect(save.disabled).toBe(true);
    type("Max parallel agents", "17");
    expect(save.disabled).toBe(true);
    type("Max parallel agents", "16");
    expect(save.disabled).toBe(false);
  });
});

describe("Data, Setup and About", () => {
  it("data_shows_the_folder_read_only_and_copies_the_path", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await openSection("Data");
    const path = screen.getByTestId("data-path");
    expect(path.textContent).toBe("~/Library/Application Support/Mises");
    expect(document.querySelector("input[value*='Application Support']")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy path" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("~/Library/Application Support/Mises"));
    expect(await screen.findByText("copied")).toBeTruthy();
  });

  it("data_prefers_the_core_reported_folder", async () => {
    client.status.mockResolvedValue({ ...status, data_dir: "/Users/h/custom/Mises" });
    await openSection("Data");
    expect(screen.getByTestId("data-path").textContent).toBe("/Users/h/custom/Mises");
  });

  it("units_come_from_the_profile", async () => {
    client.getProfile.mockResolvedValue({ ...profile, units: "in" as "mm" });
    await openSection("Profile");
    expect(screen.getByText("in")).toBeTruthy();
  });

  it("run_setup_again_opens_the_wizard_and_closes_the_panel", async () => {
    const onClose = await openSection("Setup");
    fireEvent.click(screen.getByRole("button", { name: "Run setup again" }));
    expect(setupWizardOpen.value).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("about_lists_app_core_and_claude_versions", async () => {
    await openSection("About");
    const about = screen.getByRole("region", { name: "About" });
    expect(about.textContent).toContain("0.3.0");
    expect(about.textContent).toContain("2.1.4");
    expect(within(about).getByText("Mises app")).toBeTruthy();
  });
});
