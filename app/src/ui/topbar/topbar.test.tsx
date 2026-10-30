import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentRun, Approval, CancelAllResult, CoreStatusInfo } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { approvals, bindClient, conn, coreStatus, parts, questions, resetStore, retiredApprovals, runs, selectedPartId, shellStatus } from "../../state/store";
import { activePage, setPage, TAB_PAGES } from "../pages/router";
import { Breadcrumb } from "./Breadcrumb";
import { ModeTabs } from "./ModeTabs";
import { StatusLights } from "./StatusLights";
import { TopBar } from "./TopBar";
import { initials, ProfileButton } from "./ProfileButton";
import { closeProfile, openProfile, profileOpen, profileSection } from "../profile/open";
import { printersChanged } from "../../state/printers";
import { installStopAllShortcut, resetStopAll } from "./stopAll";

const invokeMock = vi.hoisted(() => vi.fn(async () => null));
vi.mock("@tauri-apps/api/core", async (orig) => ({
  ...(await orig<typeof import("@tauri-apps/api/core")>()),
  isTauri: () => true,
  invoke: invokeMock,
}));

const run = (id: string, status: AgentRun["status"] = "running"): AgentRun => ({
  id, parent_id: null, agent: "designer", provider: "fake", model: "m", task: "t", status, branch: null,
  onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 1,
});
const status = (over: Partial<CoreStatusInfo> = {}): CoreStatusInfo => ({
  core_version: "0", onshape: "connected", onshape_calls_year: 214, onshape_cap_year: 2500, runs_running: 0, approvals_pending: 0, ...over,
});

beforeEach(() => { resetStore(); resetStopAll(); });
afterEach(() => cleanup());

describe("ModeTabs", () => {
  afterEach(() => { setPage("design"); });

  it("every_tab_is_clickable_and_navigates", async () => {
    render(<ModeTabs />);
    // no tab is disabled any more
    for (const n of ["Design", "Studies", "Sketch", "Drawing", "Print"]) {
      expect(screen.getByRole("tab", { name: n }).getAttribute("aria-disabled")).toBeNull();
    }
    expect(screen.getByRole("tab", { name: "Design" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Studies" }));
    expect(activePage.value).toBe("studies");
    await waitFor(() => expect(screen.getByRole("tab", { name: "Studies" }).getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByRole("tab", { name: "Design" }).getAttribute("aria-selected")).toBe("false");
  });

  it("arrow_keys_move_between_tabs_with_wrap_and_home_end", async () => {
    render(<ModeTabs />);
    const tab = (n: string) => screen.getByRole("tab", { name: n });
    tab("Design").focus();
    fireEvent.keyDown(tab("Design"), { key: "ArrowRight" });
    expect(activePage.value).toBe("studies");
    await waitFor(() => expect(document.activeElement).toBe(tab("Studies")));
    fireEvent.keyDown(tab("Studies"), { key: "ArrowLeft" });
    fireEvent.keyDown(tab("Design"), { key: "ArrowLeft" });   // wraps to the last
    expect(activePage.value).toBe(TAB_PAGES[TAB_PAGES.length - 1].id);
    fireEvent.keyDown(tab(TAB_PAGES[TAB_PAGES.length - 1].label), { key: "Home" });
    expect(activePage.value).toBe("design");
    fireEvent.keyDown(tab("Design"), { key: "End" });
    expect(activePage.value).toBe(TAB_PAGES[TAB_PAGES.length - 1].id);
    await waitFor(() => expect(tab(TAB_PAGES[TAB_PAGES.length - 1].label).getAttribute("tabindex")).toBe("0"));
  });
});

describe("StatusLights", () => {
  const light = (name: string) => document.querySelector(`[data-light=${name}]`) as HTMLElement;
  const led = (name: string) => light(name).querySelector(".led") as HTMLElement;

  it("core_light_states", async () => {
    render(<StatusLights />);
    act(() => { conn.value = "open"; });
    await waitFor(() => expect(led("core").className).toContain("led-green"));
    conn.value = "reconnecting";
    await waitFor(() => expect(led("core").className).toContain("led-amber"));
    act(() => { conn.value = "open"; });
    shellStatus.value = { state: "crashed", code: 1, stderrTail: "boom: port in use", restarts: 2 };
    await waitFor(() => expect(led("core").className).toContain("led-red"));
    expect(light("core").getAttribute("title")).toContain("boom: port in use");
    conn.value = "unauthorized";
    shellStatus.value = { state: "starting" };
    await waitFor(() => expect(led("core").className).toContain("led-red"));
  });

  it("attached_core_lost_for_5s_while_shell_ready_turns_red_and_restarts_on_click", async () => {
    vi.useFakeTimers();
    try {
      const info = { baseUrl: "http://127.0.0.1:1", wsUrl: "ws://127.0.0.1:1/events", token: "t", owned: false };
      shellStatus.value = { state: "ready", info };
      conn.value = "open";
      render(<StatusLights />);
      conn.value = "reconnecting";
      await vi.advanceTimersByTimeAsync(4900);
      expect(led("core").className).toContain("led-amber");
      expect(light("core").getAttribute("role")).toBeNull();
      await vi.advanceTimersByTimeAsync(200);
      expect(led("core").className).toContain("led-red");
      expect(light("core").getAttribute("role")).toBe("button");
      expect(light("core").getAttribute("title")).toContain("click to restart");
      fireEvent.click(light("core"));
      expect(invokeMock).toHaveBeenCalledWith("core_restart");
      // back online: green again, and a later short blip stays amber
      conn.value = "open";
      await vi.advanceTimersByTimeAsync(0);
      expect(led("core").className).toContain("led-green");
      conn.value = "closed";
      await vi.advanceTimersByTimeAsync(1000);
      expect(led("core").className).toContain("led-amber");
    } finally {
      vi.useRealTimers();
    }
  });

  it("onshape_states_and_amber_at_80_percent", async () => {
    render(<StatusLights />);
    coreStatus.value = status({ onshape: "not_configured" });
    await waitFor(() => expect(light("onshape").textContent).toContain("not set up"));
    expect(led("onshape").className).toContain("led-faint");
    coreStatus.value = status();
    await waitFor(() => expect(light("onshape").textContent).toContain("connected · 214/2,500"));
    expect(led("onshape").className).toContain("led-green");
    coreStatus.value = status({ onshape_calls_year: 2000 });
    await waitFor(() => expect(led("onshape").className).toContain("led-amber"));
    coreStatus.value = status({ onshape_calls_year: 2500 });
    await waitFor(() => expect(led("onshape").className).toContain("led-red"));
  });

  it("agents_count_and_needs_you", async () => {
    render(<StatusLights />);
    expect(light("agents").textContent).toContain("0 agents");
    expect(led("agents").className).toContain("led-faint");
    runs.value = new Map([["a", run("a")], ["b", run("b")], ["c", run("c", "done")]]);
    await waitFor(() => expect(light("agents").textContent).toContain("2 agents"));
    expect(led("agents").className).toContain("led-blue");
    expect(light("agents").textContent).not.toContain("needs you");
    const ap = { id: "x", run_id: "a", status: "pending" } as unknown as Approval;
    approvals.value = new Map([["x", ap], ["y", { ...ap, id: "y", status: "approved" } as Approval]]);
    await waitFor(() => expect(light("agents").textContent).toContain("· 1 needs you"));
  });

  it("needs_you_matches_the_roster_questions_count_retired_cards_do_not", async () => {
    render(<StatusLights />);
    runs.value = new Map([["a", run("a")]]);
    const ap = { id: "x", run_id: "a", status: "pending" } as unknown as Approval;
    approvals.value = new Map([["x", ap]]);
    await waitFor(() => expect(light("agents").textContent).toContain("· 1 needs you"));
    retiredApprovals.value = new Set(["x"]); // replaced by another proposal; the store still says pending
    await waitFor(() => expect(light("agents").textContent).not.toContain("needs you"));
    questions.value = new Map([["q", { question_id: "q", run_id: "a", question: "which side?", options: [] }]]);
    await waitFor(() => expect(light("agents").textContent).toContain("· 1 needs you"));
  });

  it("printer_is_faint", () => {
    render(<StatusLights />);
    expect(light("printer").textContent).toContain("no printer");
  });

  it("printer_light_reads_printers_and_the_first_printers_status", async () => {
    const printers = vi.fn(async () => [] as unknown[]);
    const printerStatus = vi.fn(async () => ({
      printer_id: "mk4", reachable: true, state: "IDLE", sendable: true, temp_nozzle: 24, temp_bed: 23, job: null, error: null,
    }));
    bindClient({ printers, printerStatus } as unknown as CoreClient);
    conn.value = "open";
    render(<StatusLights />);
    await waitFor(() => expect(printers).toHaveBeenCalledTimes(1));
    expect(light("printer").textContent).toContain("no printer");     // GET /printers → []
    printers.mockResolvedValue([{ id: "mk4", kind: "prusalink", host: "10.0.0.5", name: "MK4", model: "MK4", has_key: true }]);
    act(() => { printersChanged(); });                                 // Profile added one
    await waitFor(() => expect(light("printer").textContent).toContain("MK4 · idle"));
    expect(printerStatus).toHaveBeenCalledWith("mk4");
    expect(light("printer").querySelector("[data-tone='green']")).not.toBeNull();
  });
});

describe("stop all", () => {
  function bindFake(cancelAll: () => Promise<CancelAllResult>): void {
    bindClient({ cancelAll } as unknown as CoreClient);
  }

  it("stop_all_button_calls_cancel_all_once", async () => {
    let resolveIt: (v: CancelAllResult) => void = () => {};
    const cancelAll = vi.fn(() => new Promise<CancelAllResult>((r) => { resolveIt = r; }));
    bindFake(cancelAll);
    runs.value = new Map([["a", run("a")]]);
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(1);
    resolveIt({ cancelled: ["a", "b"], failed: [] });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("stopped 2"));
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(2);
  });

  it("button_is_red_only_while_a_run_is_running", async () => {
    bindFake(() => Promise.resolve({ cancelled: [], failed: [] }));
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    expect(btn.className).not.toContain("btn-danger");
    runs.value = new Map([["a", run("a")]]);
    await waitFor(() => expect(btn.className).toContain("btn-danger"));
  });

  it("shortcut_works_while_typing_in_command_line", async () => {
    const cancelAll = vi.fn(() => Promise.resolve({ cancelled: ["a"], failed: [] }));
    bindFake(cancelAll);
    const off = installStopAllShortcut();
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    fireEvent.keyDown(input, { key: ">", code: "Period", metaKey: true, shiftKey: true });
    await waitFor(() => expect(cancelAll).toHaveBeenCalledTimes(1));
    off();
    fireEvent.keyDown(input, { key: ">", code: "Period", metaKey: true, shiftKey: true });
    expect(cancelAll).toHaveBeenCalledTimes(1);
    input.remove();
  });

  it("toast_counts_runs_the_core_could_not_cancel", async () => {
    bindFake(() => Promise.resolve({ cancelled: ["a", "b"], failed: [{ run_id: "c", error: "RuntimeError: stuck" }] }));
    render(<TopBar />);
    fireEvent.click(screen.getByRole("button", { name: /stop all/i }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("stopped 2 · 1 failed"));
  });

  it("failure_shows_a_toast_and_releases_busy", async () => {
    const cancelAll = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue({ cancelled: [], failed: [] });
    bindFake(cancelAll);
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("stop all failed"));
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(2);
  });
});

const plate = (part_id: string) => ({ part_id, vars: {}, metrics: null, mtime: 1 });

describe("Breadcrumb", () => {
  it("shows_no_part_without_selection", () => {
    render(<Breadcrumb />);
    expect(document.body.textContent).toContain("◇ LOCAL / no part");
  });
  it("shows_part_label", () => {
    parts.value = [plate("bolt_m3"), plate("mount_plate")];
    selectedPartId.value = "mount_plate";
    render(<Breadcrumb />);
    expect(document.body.textContent).toContain("◇ LOCAL / Part · mount plate");
    expect(document.querySelector("[data-crumb=part]")?.getAttribute("data-fallback")).toBeNull();
  });
  it("shows_the_fallback_part_plainly_like_viewer_and_variables", () => {
    // nothing selected: the viewer and Variables show the first part, so does the breadcrumb (not as selected)
    parts.value = [plate("bolt_m3"), plate("mount_plate")];
    render(<Breadcrumb />);
    const el = document.querySelector("[data-crumb=part]") as HTMLElement;
    expect(el.textContent).toBe("Part · bolt m3");
    expect(el.getAttribute("data-fallback")).toBe("true");
    expect(el.className).toBe("crumb-fallback");
  });
  it("breadcrumb_never_shows_auto_numbered", () => {
    // labels come from partLabel unchanged; ids are chosen names (the core rejects numbered ids)
    parts.value = [plate("mount_plate"), plate("bolt_m3")];
    for (const [id, label] of [["mount_plate", "Part · mount plate"], ["bolt_m3", "Part · bolt m3"]]) {
      selectedPartId.value = id;
      const { unmount } = render(<Breadcrumb />);
      expect(document.querySelector("[data-crumb=part]")?.textContent).toBe(label);
      unmount();
    }
    selectedPartId.value = null;
    const { unmount } = render(<Breadcrumb />);
    expect(isAutoNumbered(document.querySelector("[data-crumb=part]")?.textContent ?? "")).toBe(false);
    unmount();
  });
});

describe("ProfileButton", () => {
  afterEach(() => { closeProfile(); });

  it("shows_initials_of_the_display_name_or_profile_and_opens_the_panel", async () => {
    const getProfile = vi.fn().mockResolvedValue({ display_name: "Hartigan Deely" });
    bindClient({ getProfile } as unknown as CoreClient);
    render(<ProfileButton />);
    const btn = screen.getByRole("button", { name: "Profile" });
    expect(btn.textContent).toBe("Profile");
    await waitFor(() => expect(btn.textContent).toBe("HD"));
    expect(btn.getAttribute("title")).toBe("Profile · Hartigan Deely");
    fireEvent.click(btn);
    expect(profileOpen.value).toBe(true);
    expect(profileSection.value).toBe("profile");
  });

  it("re_reads_the_name_when_profile_closes_and_after_a_reconnect", async () => {
    const getProfile = vi.fn().mockResolvedValue({ display_name: "" });
    bindClient({ getProfile } as unknown as CoreClient);
    render(<ProfileButton />);
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(1));
    getProfile.mockResolvedValue({ display_name: "hartigan" });
    act(() => openProfile());
    act(() => closeProfile());
    await waitFor(() => expect(screen.getByRole("button", { name: "Profile" }).textContent).toBe("H"));
    act(() => { conn.value = "open"; });               // the first connect: the boot read answered
    await act(async () => { await Promise.resolve(); });
    expect(getProfile).toHaveBeenCalledTimes(2);
    act(() => { conn.value = "reconnecting"; });
    act(() => { conn.value = "open"; });               // a reconnect (the core may have restarted)
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(3));
  });

  it("a_boot_read_before_the_core_was_ready_is_retried_on_the_first_connect", async () => {
    const getProfile = vi.fn().mockRejectedValueOnce(new Error("core client not ready")).mockResolvedValue({ display_name: "Ada" });
    bindClient({ getProfile } as unknown as CoreClient);
    render(<ProfileButton />);
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(1));
    act(() => { conn.value = "open"; });
    await waitFor(() => expect(screen.getByRole("button", { name: "Profile" }).textContent).toBe("A"));
  });

  it("initials_take_at_most_two_words", () => {
    expect(initials("  ada  lovelace byron ")).toBe("AL");
    expect(initials("")).toBe("");
  });
});
