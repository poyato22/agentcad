import { describe, expect, it } from "vitest";
import type { Approval, Step, StepKind } from "../../../api/types";
import { evidenceFor } from "./evidence";

const RID = "r1";
let n = 0;
function step(id: number, kind: StepKind, payload: Record<string, unknown>, runId = RID): Step {
  n++;
  return { id, run_id: runId, ts: 1000 + id, kind, payload, evidence_hash: `${String(n).padStart(2, "0")}ab`.repeat(16), prev_hash: null, collapsed: false };
}
const out = (name: string, partId?: string) => ({ call_id: `c${name}`, name, output: { ok: true, summary: "s", data: partId ? { part_id: partId } : {}, handles: [], warnings: [], step_kind: "measure" } });

const approval: Approval = {
  id: "ap", run_id: RID, action: "onshape_merge_request", payload: {}, payload_hash: "h".repeat(64), microversion: null,
  deltas: {}, status: "pending", expires_at: 9e9, decided_by: null, decided_at: null, edited_payload: null,
};

describe("evidenceFor", () => {
  it("evidence_links_point_to_prior_measure_steps", () => {
    const steps = [
      step(1, "message", { role: "user", text: "go" }),
      step(2, "tool_call", { call_id: "a", name: "mirror_measure", input: {} }),
      step(3, "measure", out("mirror_measure", "mount_plate")),
      step(4, "diff", out("mirror_part_set_vars", "upright")),
      step(5, "tool_result", out("mirror_export", "upright")),
      step(6, "verify", out("verify_fit")),
      step(7, "sim_run", out("sim_static", "upright")),
      step(8, "tool_call", { call_id: "m1", name: "onshape_merge_request", input: {} }),
      step(9, "measure", out("mirror_measure", "late_part")),
    ];
    const ev = evidenceFor(approval, steps);
    expect(ev.map((e) => e.stepId)).toEqual([3, 4, 6, 7]);
    expect(ev.map((e) => e.kind)).toEqual(["measure", "diff", "verify", "sim_run"]);
    expect(ev[0].label).toBe("measure · Part · mount plate");
    expect(ev[1].label).toBe("diff · Part · upright");
    expect(ev[2].label).toBe("verify · verify_fit");
    expect(ev[0].hash8).toBe(steps[2].evidence_hash.slice(0, 8));
    expect(ev[0].hash8).toHaveLength(8);
  });

  it("uses_the_latest_matching_tool_call", () => {
    const steps = [
      step(1, "measure", out("mirror_measure", "a_part")),
      step(2, "tool_call", { call_id: "m1", name: "onshape_merge_request", input: {} }),
      step(3, "measure", out("mirror_measure", "b_part")),
      step(4, "tool_call", { call_id: "m2", name: "onshape_merge_request", input: {} }),
      step(5, "measure", out("mirror_measure", "c_part")),
    ];
    expect(evidenceFor(approval, steps).map((e) => e.stepId)).toEqual([1, 3]);
  });

  it("ignores_other_runs_and_other_tool_calls", () => {
    const steps = [
      step(1, "measure", out("mirror_measure", "x_part"), "other"),
      step(2, "measure", out("mirror_measure", "mine")),
      step(3, "tool_call", { call_id: "z", name: "mirror_measure", input: {} }),
      step(4, "tool_call", { call_id: "m1", name: "onshape_merge_request", input: {} }),
    ];
    expect(evidenceFor(approval, steps).map((e) => e.stepId)).toEqual([2]);
  });

  it("without_the_tool_call_step_yet_uses_all_run_steps", () => {
    const steps = [step(1, "measure", out("mirror_measure", "p"))];
    expect(evidenceFor(approval, steps).map((e) => e.stepId)).toEqual([1]);
    expect(evidenceFor(approval, [])).toEqual([]);
  });
});
