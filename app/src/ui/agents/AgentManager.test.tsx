import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { bindClient, resetStore } from "../../state/store";
import { AgentManager } from "./AgentManager";
import { ADVANCED_KEY, agentManagerOpen, agentManagerTab, closeAgentManager, openAgentManager, setManagerAdvanced } from "./manager";

beforeEach(() => {
  bindClient({
    runsTree: vi.fn().mockResolvedValue([]), projectNames: vi.fn().mockResolvedValue(new Map()),
    agents: vi.fn().mockResolvedValue([]), getProfile: vi.fn().mockResolvedValue({ hidden_agents: [] }),
    models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }), localStatus: vi.fn().mockRejectedValue(new Error("404")),
  } as unknown as CoreClient);
});
afterEach(() => { cleanup(); closeAgentManager(); resetStore(); setManagerAdvanced(false); });

describe("AgentManager", () => {
  it("is a modal slide-over: the Agents view, Advanced adds All runs and Definitions; Esc closes and returns focus", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    render(<AgentManager />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(() => { openAgentManager(); });
    const dlg = screen.getByRole("dialog", { name: "Agent manager" });
    expect(dlg.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(dlg);
    // I7: one view by default — no tabs; Runs and Definitions sit behind "Advanced"
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(dlg.querySelector("[data-agents-view]")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Advanced ▸" }));
    expect(localStorage.getItem(ADVANCED_KEY)).toBe("1");                 // remembered
    expect(screen.getByRole("tab", { name: "Agents" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Agents" }), { key: "ArrowRight" });
    expect(agentManagerTab.value).toBe("runs");
    fireEvent.click(screen.getByRole("tab", { name: "Definitions" }));
    expect(agentManagerTab.value).toBe("definitions");
    expect(await screen.findByText("Runs start from one of these agents.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Advanced ▾" }));
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(agentManagerTab.value).toBe("agents");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(agentManagerOpen.value).toBe(false);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("traps Tab and Shift+Tab inside the slide-over", async () => {
    render(<><button type="button">behind</button><AgentManager /></>);
    await act(() => { openAgentManager("runs"); });
    const dlg = screen.getByRole("dialog", { name: "Agent manager" });
    const stops = [...dlg.querySelectorAll<HTMLElement>('button, input, select, textarea, [tabindex]:not([tabindex="-1"])')]
      .filter((el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1");
    const first = stops[0];
    const last = stops[stops.length - 1];
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    expect(dlg.contains(document.activeElement)).toBe(true);
  });

  it("the close button closes it", async () => {
    render(<AgentManager />);
    await act(() => { openAgentManager("definitions"); });
    fireEvent.click(screen.getByRole("button", { name: "Close the agent manager" }));
    expect(agentManagerOpen.value).toBe(false);
  });
});
