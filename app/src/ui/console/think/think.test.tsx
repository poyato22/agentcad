import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreClient } from "../../../api/client";
import type { AgentRun } from "../../../api/types";
import { effortChipText, effortChipTitle, effortOf } from "../../../api/think";
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

const open = () => fireEvent.click(screen.getByTestId("think-control").querySelector("button") as HTMLButtonElement);
const pick = (label: RegExp) => fireEvent.click(screen.getByRole("menuitemcheckbox", { name: label }));

describe("Think control (Plan 11 F8)", () => {
  it("loads the build's default and offers Auto · Normal · Hard · Max", async () => {
    render(<ThinkControl />);
    await waitFor(() => expect(client.effortDefault).toHaveBeenCalledWith("b1"));
    expect(screen.getByTestId("think-control").dataset.think).toBe("auto");
    open();
    const names = screen.getAllByRole("menuitemcheckbox").map((b) => b.textContent);
    expect(names.slice(0, 4).map((n) => n?.split(" ·")[0])).toEqual(["Auto", "Normal", "Hard", "Max"]);
  });

  it("a pick goes with the next message only, then the control is back on Auto", async () => {
    render(<CommandLine />);
    open();
    pick(/^Max/);
    expect(screen.getByTestId("think-control").dataset.think).toBe("max");
    fireEvent.input(screen.getByLabelText("Command line"), { target: { value: "make it really interesting" } });
    fireEvent.keyDown(screen.getByLabelText("Command line"), { key: "Enter" });
    await waitFor(() => expect(client.chat).toHaveBeenCalledWith("b1", "make it really interesting", { effort: "max" }));
    expect(thinkChoice.value).toBe("auto");
    fireEvent.input(screen.getByLabelText("Command line"), { target: { value: "rename it" } });
    fireEvent.keyDown(screen.getByLabelText("Command line"), { key: "Enter" });
    await waitFor(() => expect(client.chat).toHaveBeenLastCalledWith("b1", "rename it"));    // Auto sends no effort
  });

  it("'Every message in this build' saves the pick as the build default", async () => {
    render(<ThinkControl />);
    open();
    pick(/^Hard/);
    open();
    pick(/Every message in this build/);
    await waitFor(() => expect(client.setEffortDefault).toHaveBeenCalledWith("b1", "hard"));
    await waitFor(() => expect(buildThink.value.get("b1")).toBe("hard"));
    const el = screen.getByTestId("think-control");
    expect(el.dataset.think).toBe("hard");
    expect(el.dataset.thinkSticky).toBe("");
    open();
    pick(/Every message in this build/);
    await waitFor(() => expect(client.setEffortDefault).toHaveBeenLastCalledWith("b1", "auto"));
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
