// G7: the app has no "No sign of the agent … Stop and retry" banner and no stall timer: stuck
// detection is the core's (its turn watchdog recovers a stuck turn and leaves the small
// "Recovered" note; a card shows only when its recovery budget is spent).
import { describe, expect, it } from "vitest";
import * as chat from "./chat";
import * as actions from "./MessageActions";

describe("no stall banner in the app (G7)", () => {
  it("has no stall timer or banner left", () => {
    expect("StallNote" in actions).toBe(false);
    for (const name of ["STALL_S", "isStalled", "quietSince", "seenRunningAt"]) expect(name in chat).toBe(false);
  });
});
