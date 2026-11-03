import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { pinOf } from "../../api/measure";
import { stepSchema } from "../../api/schemas";
import type { Step } from "../../api/types";
import { activeBuildId } from "../../state/builds";
import { resetStore, runs, steps } from "../../state/store";
import { buildThread } from "../console/threadModel";
import { buildLog } from "../console/logModel";
import { input } from "../console/testSteps";
import { addLocalPin, buildPins, pinNumber, resetPins } from "./pins";

// What the real core stores for a pin (core/scripts/dump_measure_feedback_fixtures.py, U3c's
// route): a decision step {feedback}, then the user's message step with the same `feedback`.
const REAL: Step[] = (JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../../core/tests/fixtures/ui/project_feedback_steps.json"), "utf8")) as unknown[])
  .map((s) => stepSchema.parse(s));
const RUN = REAL[0].run_id;

const run = (id: string, project_id: string) => ({ id, parent_id: null, agent: "engineer", provider: "fake", model: "m", task: "t", status: "done" as const,
  branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: 1, updated_at: 2, project_id });

afterEach(() => { resetStore(); resetPins(); activeBuildId.value = null; });

describe("pins from the core's structured feedback", () => {
  it("the real steps carry a well-formed pin on the decision and the user message", () => {
    const withPin = REAL.filter((s) => pinOf(s.payload) !== null);
    expect(withPin.map((s) => s.kind)).toEqual(["decision", "message"]);
    expect(pinOf(withPin[0].payload)).toMatchObject({ part_id: "mount_plate", point: [7, 0, 2.5], text: "make these holes M3 clearance", nearby_hole: "Ø4.2 hole, 0.9 mm away" });
  });

  it("survive a reload: from the steps alone (no pin sent from this window) there is one pin, not two", () => {
    activeBuildId.value = "b1";
    runs.value = new Map([[RUN, run(RUN, "b1")]]);
    steps.value = new Map([[RUN, REAL]]);
    const pins = buildPins.value;
    expect(pins.map((p) => [p.n, p.text, p.point, p.stepId])).toEqual([[1, "make these holes M3 clearance", [7, 0, 2.5], REAL[0].id]]);
    expect(pinNumber(RUN, pinOf(REAL[1].payload)!)).toBe(1);
  });

  it("another build's pins are not shown; a pin just sent shows until its step arrives, then once", () => {
    activeBuildId.value = "b2";
    runs.value = new Map([[RUN, run(RUN, "b1")]]);
    steps.value = new Map([[RUN, REAL]]);
    expect(buildPins.value).toEqual([]);
    activeBuildId.value = "b1";
    addLocalPin({ buildId: "b1", runId: RUN, stepId: REAL[0].id, partId: "mount_plate", point: [7, 0, 2.5], faceId: "f2", text: "make these holes M3 clearance", ts: 1 });
    addLocalPin({ buildId: "b1", runId: RUN, stepId: 999, partId: "mount_plate", point: [-5, 1, 2.5], faceId: null, text: "thinner here", ts: 9e9 });
    expect(buildPins.value.map((p) => [p.n, p.text])).toEqual([[1, "make these holes M3 clearance"], [2, "thinner here"]]);
  });

  it("the thread: the user message carries the pin; the decision record is not a second line", () => {
    const thread = buildThread(buildLog(input({ stepList: REAL, selectedRunId: RUN, filter: { scope: "run", kind: "everything" } })));
    const users = thread.filter((t) => t.kind === "user");
    expect(users).toHaveLength(1);
    expect(users[0]).toMatchObject({ text: "make these holes M3 clearance", feedback: { part_id: "mount_plate" } });
    expect(thread.some((t) => t.kind === "note")).toBe(false);
  });
});

describe("H1: a pin on empty space in the assembly", () => {
  it("reads back from its decision step as a world-point pin (no part, no instance)", () => {
    activeBuildId.value = "b1";
    runs.value = new Map([[RUN, run(RUN, "b1")]]);
    const s = { ...REAL[0], id: 777, payload: { feedback: { part_id: "", part: "empty space", point: [1, 2, 3], world_point: [1, 2, 3], text: "gap here" } } } as Step;
    steps.value = new Map([[RUN, [s]]]);
    const [p] = buildPins.value;
    expect(p).toMatchObject({ n: 1, text: "gap here", instanceId: null, worldPoint: [1, 2, 3] });
  });
});
