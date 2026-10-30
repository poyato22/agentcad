import { describe, expect, it } from "vitest";
import { displayName, isAutoNumbered, partLabel, providerLabel, runLabel } from "./names";
import type { AgentRun } from "../api/types";

function run(over: Partial<AgentRun>): AgentRun {
  return {
    id: "r", parent_id: null, agent: "designer", provider: "anthropic", model: "claude-sonnet-5-5",
    task: "Stiffen the upright", status: "running", branch: null, onshape_calls: 0, input_tokens: 0,
    output_tokens: 0, usd: 0, created_at: new Date(2026, 9, 24, 14, 2, 0).getTime() / 1000, updated_at: 0, ...over,
  };
}

describe("isAutoNumbered", () => {
  it.each(["Extrude 1", "Gusset1", "Part Studio 1", "#var1", "GEOM-01", "agent 2", "Sketch 12  "])("true for %s", (n) => {
    expect(isAutoNumbered(n)).toBe(true);
  });
  it.each(["Gusset · upright", "mount_plate", "Part · mount plate", "designer · stiffen upright"])("false for %s", (n) => {
    expect(isAutoNumbered(n)).toBe(false);
  });
});

describe("displayName", () => {
  it("replaces numbered names with Type · purpose", () => {
    expect(displayName("Extrude", "Extrude 1", { purpose: "plate" })).toBe("Extrude · plate");
  });
  it("keeps a descriptive name", () => {
    expect(displayName("Extrude", "base plate", { purpose: "x" })).toBe("base plate");
  });
  it("falls back to name/label then unnamed", () => {
    expect(displayName("Fillet", "Fillet 3", { name: " edge break " })).toBe("Fillet · edge break");
    expect(displayName("Fillet", "Fillet 3", { label: "rim" })).toBe("Fillet · rim");
    expect(displayName("Fillet", "Fillet 3")).toBe("Fillet · unnamed");
    expect(displayName("Fillet", "Fillet 3", { purpose: "   ", name: 4 })).toBe("Fillet · unnamed");
  });
});

describe("partLabel", () => {
  it("formats part ids", () => {
    expect(partLabel("mount_plate")).toBe("Part · mount plate");
    expect(partLabel("upright")).toBe("Part · upright");
  });
});

describe("runLabel", () => {
  it("the app's own ui run is You (Mises), not an agent", () => {
    const r = run({ agent: "you", provider: "ui", model: "ui", task: "external agent: you" });
    expect(runLabel(r, [r])).toBe("You (Mises)");
  });

  it("agent plus first words of the task", () => {
    const r = run({});
    expect(runLabel(r, [r])).toBe("designer · stiffen upright");
  });

  it("never ends in a number taken from the task", () => {
    const r = run({ task: "make the plate 6 mm thick" });
    expect(runLabel(r, [r])).toBe("designer · make plate 6 mm");
    const r2 = run({ task: "set thickness to 6" });
    const label = runLabel(r2, [r2]);
    expect(label).not.toMatch(/\d\s*$/);
    expect(label.startsWith("designer · ")).toBe(true);
  });

  it("a renamed run reads its title (C-5)", () => {
    const r = run({ title: "QA renamed run" });
    expect(runLabel(r, [r])).toBe("designer · QA renamed run");
    const blank = run({ title: "   " });
    expect(runLabel(blank, [blank])).toBe("designer · stiffen upright");
    // two runs with the same task: a rename alone tells them apart
    const a = run({ id: "a" }), b = run({ id: "b", title: "second try" });
    expect(runLabel(a, [a, b])).toBe("designer · stiffen upright");
    expect(runLabel(b, [a, b])).toBe("designer · second try");
  });

  it("empty task falls back", () => {
    const r = run({ task: "   " });
    expect(runLabel(r, [r])).toBe("designer · task");
  });

  it("runLabel_disambiguates_without_numbers", () => {
    const a = run({ id: "a", created_at: new Date(2026, 9, 24, 14, 2).getTime() / 1000 });
    const b = run({ id: "b", created_at: new Date(2026, 9, 24, 15, 40).getTime() / 1000 });
    const all = [a, b];
    const la = runLabel(a, all);
    const lb = runLabel(b, all);
    expect(la).not.toBe(lb);
    for (const l of [la, lb]) {
      const withoutTime = l.replace(/ · \d{2}:\d{2}$/, "");
      expect(withoutTime).not.toMatch(/\d+$/);
      expect(l).toMatch(/ · \d{2}:\d{2}$/);
    }
    expect(la).toBe("designer · stiffen upright · 14:02");
    expect(lb).toBe("designer · stiffen upright · 15:40");
  });

  it("prefers provider, then model, over time", () => {
    const a = run({ id: "a" });
    const b = run({ id: "b", provider: "ollama", model: "qwen3" });
    expect(runLabel(b, [a, b])).toBe("designer · stiffen upright · ollama");
    const c = run({ id: "c", model: "claude-haiku-5-5" });
    expect(runLabel(c, [a, c])).toBe("designer · stiffen upright · claude-haiku-5-5");
  });

  it("provider_label_names_the_users_claude_code_and_passes_others_through", () => {
    expect(providerLabel("claude-code")).toBe("Claude Code (your account)");
    expect(providerLabel("anthropic")).toBe("anthropic");
  });

  it("same minute adds seconds", () => {
    const a = run({ id: "a", created_at: new Date(2026, 9, 24, 14, 2, 5).getTime() / 1000 });
    const b = run({ id: "b", created_at: new Date(2026, 9, 24, 14, 2, 41).getTime() / 1000 });
    expect(runLabel(a, [a, b])).toBe("designer · stiffen upright · 14:02:05");
    expect(runLabel(b, [a, b])).toBe("designer · stiffen upright · 14:02:41");
  });
});
