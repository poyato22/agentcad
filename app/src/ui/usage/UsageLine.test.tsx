import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";
import type { MisesEvent } from "../../api/types";
import type { BuildUsage, RunUsage, TurnUsage } from "../../api/usage";
import { runUsageSchema } from "../../api/usage";
import { buildUsage, resetUsage, runUsage, startUsage } from "../../state/usage";
import { BuildUsageChip, UsageLine } from "./UsageLine";

const agent = (run_id: string, usd: number, role: string | null, model: string) => ({
  run_id, model, provider: "claude-code", agent: run_id === "lead" ? "lead" : "designer", title: null, role,
  input_tokens: 1000, output_tokens: 500, cache_read_tokens: 90_000, cache_write_tokens: 0, tokens: 91_500, usd, usd_source: "reported" as const,
});
const turn: TurnUsage = {
  root_id: "lead", turn_ts: 100, input_tokens: 2000, output_tokens: 1000, cache_read_tokens: 180_000, cache_write_tokens: 0,
  tokens: 183_000, usd: 0.42, usd_source: "reported",
  agents: [agent("lead", 0.3, "lead", "claude-opus-4-5"), agent("sub1", 0.12, "sub", "claude-sonnet-4-5")],
};

afterEach(() => { cleanup(); resetUsage(); });

describe("UsageLine", () => {
  it("shows the turn's dollars and tokens, labelled an API-price equivalent, and breaks it down by agent and model", () => {
    render(<UsageLine turn={turn} />);
    const btn = screen.getByRole("button", { name: /Usage of this message/ });
    expect(btn.textContent).toBe("≈$0.42 · 183k tokens");
    expect(btn.getAttribute("title")).toMatch(/API-price equivalent.*not a charge/);
    expect(document.querySelector("[data-usage-breakdown]")).toBeNull();
    fireEvent.click(btn);
    const rows = [...document.querySelectorAll("[data-usage-breakdown] tbody tr")].map((r) => r.textContent);
    expect(rows[0]).toContain("lead (lead)");
    expect(rows[0]).toContain("claude-opus-4-5");
    expect(rows[1]).toContain("claude-sonnet-4-5");
    expect(rows[1]).toContain("≈$0.12");
  });
});

describe("BuildUsageChip", () => {
  const build = (over: Partial<BuildUsage> = {}): BuildUsage => ({
    project_id: "p1", input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
    tokens: 183_000, usd: 0.42, usd_source: "reported", turns: [turn], recorded: true, unrecorded: null, note: "", ...over,
  });
  it("shows the build's running total and its turns", () => {
    render(<BuildUsageChip usage={build()} />);
    const chip = screen.getByRole("button", { name: /This build's usage/ });
    expect(chip.textContent).toBe("≈$0.42 · 183k tokens");
    fireEvent.click(chip);
    expect(document.querySelectorAll(".usage-turns li")).toHaveLength(1);
    expect(document.querySelector("[data-unrecorded]")).toBeNull();
  });
  it("says older runs were not recorded per message", () => {
    render(<BuildUsageChip usage={build({ recorded: false, turns: [], tokens: 0, usd: 0, unrecorded: { runs: 2, tokens: 5000, usd: 1.5 } })} />);
    const chip = screen.getByRole("button", { name: /This build's usage/ });
    expect(chip.textContent).toBe("usage not recorded");
    fireEvent.click(chip);
    expect(document.querySelector("[data-unrecorded]")?.textContent).toMatch(/Older runs \(2\): not recorded per message.*5k tokens, ≈\$1.50/);
  });
  it("shows nothing for a build with no usage at all", () => {
    const { container } = render(<BuildUsageChip usage={build({ recorded: false, turns: [], unrecorded: null })} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("startUsage", () => {
  it("reads the build's usage again when a run finishes a turn", async () => {
    let listener: ((e: MisesEvent) => void) | null = null;
    let calls = 0;
    const { activeBuildId } = await import("../../state/builds");
    activeBuildId.value = "p1";
    const stop = startUsage({
      subscribe: (cb) => { listener = cb; return () => {}; },
      subscribeState: () => () => {},
      fetchRun: async (id) => runUsageSchema.parse({ run_id: id, root_id: id, turns: [], note: "" }) as RunUsage,
      fetchBuild: async (id) => { calls++; return { project_id: id, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
        tokens: calls, usd: 0, usd_source: "reported", turns: [], recorded: true, unrecorded: null, note: "" }; },
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(buildUsage.value.get("p1")?.tokens).toBe(1);
    listener!({ type: "run_finished", run_id: "r1", data: { status: "done" }, ts: 2 });
    await new Promise((r) => setTimeout(r, 0));
    expect(buildUsage.value.get("p1")?.tokens).toBe(2);
    expect(runUsage.value.size).toBe(0);         // no run in the dock: nothing to read
    stop();
    activeBuildId.value = null;
  });
});
