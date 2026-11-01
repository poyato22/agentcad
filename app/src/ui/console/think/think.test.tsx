import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { effortChipText, effortChipTitle, effortDefaultSchema, effortOf } from "../../../api/think";
import { activeBuildId } from "../../../state/builds";
import { actions, bindClient, resetStore, runs } from "../../../state/store";
import { CommandLine } from "../command/CommandLine";
import { EffortChip } from "./EffortChip";
import { buildThink, resetThink, thinkChoice, thinkSupported } from "./think";
import { ThinkControl } from "./ThinkControl";

const lead: AgentRun = { id: "L1", parent_id: null, agent: "engineer", provider: "claude-code", model: "sonnet", task: "egg drop",
  status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1000, updated_at: 1000,
  project_id: "b1", role: "lead" };

const client = { chat: vi.fn(), message: vi.fn(), cancel: vi.fn(), effortDefault: vi.fn(), setEffortDefault: vi.fn() };

beforeEach(() => {
  client.chat.mockReset().mockResolvedValue({ run_id: "L1", role: "lead", new_lead: false, notes: [] });
  client.effortDefault.mockReset().mockResolvedValue({ default: "auto", choices: [], levels: {} });
  client.setEffortDefault.mockReset().mockImplementation(async (_b: string, c: string) => ({ default: c, choices: [], levels: {} }));
  bindClient(client as unknown as CoreClient);
  runs.value = new Map([["L1", lead]]);
  activeBuildId.value = "b1";
  actions.selectRun("L1");
});
afterEach(() => { cleanup(); resetStore(); resetThink(); activeBuildId.value = null; });

const pick = (c: string) => fireEvent.click(screen.getByTestId(`think-${c}`));

describe("Think control (G8: Auto · Low · Medium · High · Max, always visible)", () => {
  it("shows every level at once with the build's pick checked", async () => {
    client.effortDefault.mockResolvedValue({ default: "high", choices: [], levels: {} });
    expect(effortDefaultSchema.parse({ default: "hard", choices: [], levels: {} }).default).toBe("high");   // a Plan 11 name
    render(<ThinkControl />);
    await waitFor(() => expect(client.effortDefault).toHaveBeenCalledWith("b1"));
    const radios = screen.getAllByRole("radio");
    expect(radios.map((b) => b.textContent)).toEqual(["Auto", "Low", "Medium", "High", "Max"]);
    await waitFor(() => expect(screen.getByTestId("think-control").dataset.think).toBe("high"));
    expect(screen.getByTestId("think-high").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByTestId("think-max-note")).toBeNull();
  });

  it("a pick persists for the build and goes with every message; Max warns about usage", async () => {
    render(<CommandLine />);
    pick("max");
    expect(screen.getByTestId("think-control").dataset.think).toBe("max");
    await waitFor(() => expect(client.setEffortDefault).toHaveBeenCalledWith("b1", "max"));
    expect(screen.getByTestId("think-max-note").textContent).toContain("much more");
    fireEvent.input(screen.getByLabelText("Command line"), { target: { value: "make it really interesting" } });
    fireEvent.keyDown(screen.getByLabelText("Command line"), { key: "Enter" });
    await waitFor(() => expect(client.chat).toHaveBeenCalledWith("b1", "make it really interesting", { effort: "max" }));
    expect(thinkChoice.value).toBe("max");                                  // it stays
    pick("auto");
    await waitFor(() => expect(client.setEffortDefault).toHaveBeenLastCalledWith("b1", "auto"));
    fireEvent.input(screen.getByLabelText("Command line"), { target: { value: "rename it" } });
    fireEvent.keyDown(screen.getByLabelText("Command line"), { key: "Enter" });
    await waitFor(() => expect(client.chat).toHaveBeenLastCalledWith("b1", "rename it"));    // Auto sends no effort
  });

  it("a build switch shows that build's own pick", async () => {
    render(<ThinkControl />);
    pick("low");
    await waitFor(() => expect(buildThink.value.get("b1")).toBe("low"));
    client.effortDefault.mockResolvedValue({ default: "high", choices: [], levels: {} });
    activeBuildId.value = "b2";
    await waitFor(() => expect(screen.getByTestId("think-control").dataset.think).toBe("high"));
    activeBuildId.value = "b1";
    await waitFor(() => expect(screen.getByTestId("think-control").dataset.think).toBe("low"));
  });

  it("hides on a core without the effort routes", async () => {
    client.effortDefault.mockResolvedValue(null);
    render(<ThinkControl />);
    await waitFor(() => expect(thinkSupported.value).toBe(false));
    expect(screen.queryByTestId("think-control")).toBeNull();
  });

  it("the effort chip says the level and why, the tooltip what the runtime was sent", () => {
    const e = effortOf({ kind: "decision", payload: { effort: { level: "max", choice: "auto", source: "auto", label: "Max",
      reasons: ["open-ended: an egg-drop challenge"], control: { runtime: "claude-code", param: "--effort / apply_flag_settings.effortLevel", value: "max" } } } });
    expect(e).not.toBeNull();
    if (!e) return;
    render(<EffortChip effort={e} />);
    const chip = screen.getByTestId("effort-chip");
    expect(chip.textContent).toBe("Thinking: Max · open-ended: an egg-drop challenge");
    expect(chip.title).toContain("claude-code: --effort / apply_flag_settings.effortLevel = max");
    expect(effortChipText({ ...e, source: "user" })).toBe("Thinking: Max · your pick");
    expect(effortChipTitle({ ...e, control: { supported: false, note: "no control" } })).toContain("no control");
    expect(effortOf({ kind: "decision", payload: { effort: { level: "ultra" } } })).toBeNull();
  });
});
