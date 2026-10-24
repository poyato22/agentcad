import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import type { AgentRun, Approval, CoreStatusInfo } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { approvals, bindClient, conn, coreStatus, parts, resetStore, runs, selectedPartId, shellStatus } from "../../state/store";
import { Breadcrumb } from "./Breadcrumb";
import { ModeTabs } from "./ModeTabs";
import { StatusLights } from "./StatusLights";
import { TopBar } from "./TopBar";
import { installStopAllShortcut, resetStopAll } from "./stopAll";

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
  it("disabled_tabs_not_clickable", () => {
    render(<ModeTabs />);
    const studies = screen.getByRole("tab", { name: "Studies" });
    expect(studies.getAttribute("aria-disabled")).toBe("true");
    expect(studies.getAttribute("title")).toBe("later");
    fireEvent.click(studies);
    expect(screen.getByRole("tab", { name: "Design" }).getAttribute("aria-selected")).toBe("true");
    expect(studies.getAttribute("aria-selected")).toBe("false");
    for (const n of ["Sketch", "Drawing", "Print"]) expect(screen.getByRole("tab", { name: n }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("tab", { name: "Design" }).getAttribute("aria-disabled")).toBeNull();
  });
});

describe("StatusLights", () => {
  const light = (name: string) => document.querySelector(`[data-light=${name}]`) as HTMLElement;
  const led = (name: string) => light(name).querySelector(".led") as HTMLElement;

  it("core_light_states", async () => {
    render(<StatusLights />);
    conn.value = "open";
    await waitFor(() => expect(led("core").className).toContain("led-green"));
    conn.value = "reconnecting";
    await waitFor(() => expect(led("core").className).toContain("led-amber"));
    conn.value = "open";
    shellStatus.value = { state: "crashed", code: 1, stderrTail: "boom: port in use", restarts: 2 };
    await waitFor(() => expect(led("core").className).toContain("led-red"));
    expect(light("core").getAttribute("title")).toContain("boom: port in use");
    conn.value = "unauthorized";
    shellStatus.value = { state: "starting" };
    await waitFor(() => expect(led("core").className).toContain("led-red"));
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

  it("printer_is_faint", () => {
    render(<StatusLights />);
    expect(light("printer").textContent).toContain("no printer");
  });
});

describe("stop all", () => {
  function bindFake(cancelAll: () => Promise<{ cancelled: string[] }>): void {
    bindClient({ cancelAll } as unknown as CoreClient);
  }

  it("stop_all_button_calls_cancel_all_once", async () => {
    let resolveIt: (v: { cancelled: string[] }) => void = () => {};
    const cancelAll = vi.fn(() => new Promise<{ cancelled: string[] }>((r) => { resolveIt = r; }));
    bindFake(cancelAll);
    runs.value = new Map([["a", run("a")]]);
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(1);
    resolveIt({ cancelled: ["a", "b"] });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("stopped 2"));
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(2);
  });

  it("button_is_red_only_while_a_run_is_running", async () => {
    bindFake(() => Promise.resolve({ cancelled: [] }));
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    expect(btn.className).not.toContain("btn-danger");
    runs.value = new Map([["a", run("a")]]);
    await waitFor(() => expect(btn.className).toContain("btn-danger"));
  });

  it("shortcut_works_while_typing_in_command_line", async () => {
    const cancelAll = vi.fn(() => Promise.resolve({ cancelled: ["a"] }));
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

  it("failure_shows_a_toast_and_releases_busy", async () => {
    const cancelAll = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValue({ cancelled: [] });
    bindFake(cancelAll);
    render(<TopBar />);
    const btn = screen.getByRole("button", { name: /stop all/i });
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("stop all failed"));
    fireEvent.click(btn);
    expect(cancelAll).toHaveBeenCalledTimes(2);
  });
});

describe("Breadcrumb", () => {
  it("shows_no_part_without_selection", () => {
    render(<Breadcrumb />);
    expect(document.body.textContent).toContain("◇ LOCAL / no part");
  });
  it("shows_part_label", () => {
    selectedPartId.value = "mount_plate";
    parts.value = [];
    render(<Breadcrumb />);
    expect(document.body.textContent).toContain("◇ LOCAL / Part · mount plate");
  });
  it("breadcrumb_never_shows_auto_numbered", () => {
    // labels come from partLabel unchanged; ids are chosen names (the core rejects numbered ids)
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
