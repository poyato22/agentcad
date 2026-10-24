import { afterEach, describe, expect, it } from "vitest";
import type { AgentRun } from "../api/types";
import { effectiveRunId } from "./selection";
import { resetStore, runs, selectedRunId } from "./store";

const run = (id: string, created_at: number, status: AgentRun["status"] = "running"): AgentRun => ({
  id, parent_id: null, agent: "designer", provider: "fake", model: "m", task: "t", status, branch: null,
  onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at, updated_at: created_at,
});

afterEach(() => resetStore());

describe("effectiveRunId", () => {
  it("keeps_a_selected_run_that_is_not_loaded_yet", () => {
    // e.g. the new-run dialog selected the run it just created, before /runs lists it
    runs.value = new Map([["old", run("old", 1)]]);
    selectedRunId.value = "fresh";
    expect(effectiveRunId.value).toBe("fresh");
  });

  it("falls_back_to_the_newest_running_run_without_a_selection", () => {
    runs.value = new Map([["a", run("a", 1)], ["b", run("b", 2)], ["c", run("c", 3, "done")]]);
    expect(effectiveRunId.value).toBe("b");
  });
});
