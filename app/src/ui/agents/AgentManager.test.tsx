import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../api/client";
import { bindClient, resetStore } from "../../state/store";
import { AgentManager } from "./AgentManager";
import { agentManagerOpen, agentManagerTab, closeAgentManager, openAgentManager } from "./manager";

beforeEach(() => {
  bindClient({
    runsTree: vi.fn().mockResolvedValue([]), projectNames: vi.fn().mockResolvedValue(new Map()),
    agents: vi.fn().mockResolvedValue([]), getProfile: vi.fn().mockResolvedValue({ hidden_agents: [] }),
    models: vi.fn().mockResolvedValue({ default: "x/y", vendors: [] }), localStatus: vi.fn().mockRejectedValue(new Error("404")),
  } as unknown as CoreClient);
});
afterEach(() => { cleanup(); closeAgentManager(); resetStore(); });

describe("AgentManager", () => {
  it("is a modal slide-over with Runs and Agents tabs; Esc closes and returns focus", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    render(<AgentManager />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(() => { openAgentManager(); });
    const dlg = screen.getByRole("dialog", { name: "Agent manager" });
    expect(dlg.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(dlg);
    expect(screen.getByRole("tab", { name: "Agents" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.keyDown(screen.getByRole("tab", { name: "Agents" }), { key: "ArrowRight" });
    expect(agentManagerTab.value).toBe("tree");
    fireEvent.click(screen.getByRole("tab", { name: "Definitions" }));
    expect(agentManagerTab.value).toBe("agents");
    expect(await screen.findByText("Runs start from one of these agents.")).toBeTruthy();

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
    await act(() => { openAgentManager("agents"); });
    fireEvent.click(screen.getByRole("button", { name: "Close the agent manager" }));
    expect(agentManagerOpen.value).toBe(false);
  });
});
