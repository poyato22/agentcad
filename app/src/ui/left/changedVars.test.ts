import { describe, expect, it } from "vitest";
import type { Step } from "../../api/types";
import steps from "../../../test/fixtures/core-ui/steps.json";
import { changedVars } from "./changedVars";

const fixture = steps as unknown as Step[];

function step(id: number, kind: Step["kind"], payload: Record<string, unknown>): Step {
  return { id, run_id: "r", ts: id, kind, payload, evidence_hash: "", prev_hash: null, collapsed: false };
}

describe("changedVars", () => {
  it("changedVars_from_set_vars_diff", () => {
    const m = changedVars("mount_plate", fixture);
    const c = m.get("thickness");
    expect(c).toBeDefined();
    expect(c?.now).toBe(6);
    expect(c?.prev).toBe(5);
    expect(c?.stepId).toBe(7);
    expect(c?.runId).toBe(fixture[0].run_id);
    expect(changedVars("other_part", fixture).size).toBe(0);
  });

  it("changedVars_prev_unknown_is_null", () => {
    const s = [
      step(1, "tool_call", { call_id: "a", name: "mirror_part_set_vars", input: { part_id: "p", vars: { width: 9 } } }),
      step(2, "diff", { call_id: "a", name: "mirror_part_set_vars", output: { data: { part_id: "p", vars: { width: 9 } } } }),
    ];
    expect(changedVars("p", s).get("width")).toMatchObject({ now: 9, prev: null, stepId: 2 });
  });

  it("changedVars_latest_change_wins_and_prev_chains", () => {
    const s = [
      step(1, "tool_call", { call_id: "a", name: "mirror_part_set_vars", input: { part_id: "p", vars: { t: 6 } } }),
      step(2, "diff", { call_id: "a", name: "mirror_part_set_vars", output: { data: { part_id: "p", vars: { t: 6 } } } }),
      step(3, "tool_call", { call_id: "b", name: "mirror_part_set_vars", input: { part_id: "p", vars: { t: 7 } } }),
      step(4, "diff", { call_id: "b", name: "mirror_part_set_vars", output: { data: { part_id: "p", vars: { t: 7 } } } }),
    ];
    expect(changedVars("p", s).get("t")).toMatchObject({ now: 7, prev: 6, stepId: 4 });
  });

  it("changedVars_ignores_calls_without_diff_yet", () => {
    const s = [step(1, "tool_call", { call_id: "a", name: "mirror_part_set_vars", input: { part_id: "p", vars: { t: 6 } } })];
    expect(changedVars("p", s).size).toBe(0);
  });
});
