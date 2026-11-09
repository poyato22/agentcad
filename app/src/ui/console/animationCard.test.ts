import { describe, expect, it } from "vitest";
import type { ThreadItem } from "./threadModel";
import { animationOf } from "./ViewStream";

const call = (data: Record<string, unknown>) => ({
  kind: "activity", key: "a1", text: "assembly animate · 4 joints", tone: "ok", ts: 1, stepIds: [1], diff: null, extra: [], hint: null,
  call: { name: "mcp__mises__assembly_animate", input: {}, result: { payload: { output: { data } } } },
}) as unknown as ThreadItem;

describe("View tab: an assembly animation", () => {
  it("is a playable motion, not the chat's text row; a presented one is left to its view card", () => {
    const anim = { project_id: "b29", sequence: [{ joint_id: "gear_l", from: 0, to: 90, duration_s: 2 }, { joint_id: "flap", to: 30 }], loops: 1 };
    const a = animationOf(call({ animation: anim }));
    expect(a?.spec?.steps.map((s) => s.joint_id)).toEqual(["gear_l", "flap"]);
    expect(a?.presented).toBe(false);
    expect(animationOf(call({ animation: anim, view_spec: {} }))?.presented).toBe(true);
    expect(animationOf({ kind: "note", key: "n" } as unknown as ThreadItem)).toBeNull();
  });
});
